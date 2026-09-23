import hashlib
import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.views import (
    PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
)
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from ..models import Usuario
from ..utils import dois_fatores
from ..utils.audit import registrar_log
from ..utils.branding import get_branding
# Contrato unico de "e-mail sai de verdade" (sem provedor, links de senha NAO
# podem ir para o log — falha fechada).
from ..utils.email import email_configurado
from ..utils.security import client_ip

logger = logging.getLogger(__name__)


LOGIN_RL_MAX_ATTEMPTS = 5
LOGIN_RL_WINDOW = 60


def _login_rl_key(request):
    ip = client_ip(request) or '0.0.0.0'
    return f'login_attempts_{ip}'


def _check_rate_limit(request, max_attempts=LOGIN_RL_MAX_ATTEMPTS):
    """Verifica (sem incrementar) se o IP excedeu as tentativas FALHAS de login.

    O contador so e incrementado em falha de autenticacao (ver
    _register_failed_login); logins bem-sucedidos nao contam, evitando
    falso-positivo de bloqueio para uma recepcao que alterna varias contas.
    """
    try:
        attempts = cache.get(_login_rl_key(request), 0)
        return attempts >= max_attempts
    except Exception:
        # Cache indisponivel: nao bloquear (django-axes + @ratelimit ainda cobrem),
        # mas registrar para nao desativar a protecao silenciosamente.
        logger.warning('login_rate_limit_cache_indisponivel', exc_info=True)
        return False


def _register_failed_login(request, window=LOGIN_RL_WINDOW):
    """Incrementa o contador de tentativas falhas para o IP."""
    try:
        cache_key = _login_rl_key(request)
        attempts = cache.get(cache_key, 0)
        cache.set(cache_key, attempts + 1, window)
    except Exception:
        logger.warning('login_rate_limit_cache_indisponivel', exc_info=True)


# ─── Quem entra e para onde vai ───

def pode_acessar(usuario) -> bool:
    """Login da equipe: ADMIN, ou PROFISSIONAL vinculado a um profissional ativo.

    RECEPCAO ainda nao tem telas proprias — fica de fora ate existirem.
    """
    if usuario is None or not usuario.is_active:
        return False
    if usuario.is_staff:
        return True
    if usuario.papel == Usuario.PAPEL_PROFISSIONAL:
        try:
            prof = usuario.profissional
        except ObjectDoesNotExist:
            prof = None
        return bool(prof and prof.ativo)
    return False


def destino_padrao(usuario) -> str:
    """URL name da area inicial do papel."""
    return 'aranha:painel_overview' if usuario.is_staff else 'aranha:profissional_agenda'


def _next_permitido(request, usuario):
    """?next= validado (open redirect) e compativel com o papel; None se descartado."""
    raw = request.POST.get('next') or request.GET.get('next')
    nxt = dois_fatores.safe_next(request, raw, None)
    if not nxt or nxt.startswith(reverse('aranha:usuario_login')):
        return None
    # Profissional so navega no proprio portal (evita loop /painel/ <-> login)
    if not usuario.is_staff and not nxt.startswith('/profissional/'):
        return None
    return nxt


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
@ratelimit(key='post:username', rate='5/m', method='POST', block=True)
@require_http_methods(["GET", "POST"])
def usuario_login(request):
    """Login da equipe: ADMIN -> painel; PROFISSIONAL -> portal /profissional/."""
    # Ja logado: segue p/ o ?next= validado ou p/ a area do papel
    if request.user.is_authenticated and pode_acessar(request.user):
        return redirect(_next_permitido(request, request.user) or destino_padrao(request.user))

    if request.method == 'POST':
        # SEGURANÇA: Rate limiting — bloquear após 5 tentativas por minuto
        if _check_rate_limit(request):
            messages.error(request, 'Muitas tentativas de login. Aguarde um momento e tente novamente.')
            return redirect('aranha:usuario_login')

        email = request.POST.get('username', '').strip()
        senha = request.POST.get('password', '')

        if not email or not senha:
            messages.error(request, 'Preencha e-mail e senha.')
            return redirect('aranha:usuario_login')

        usuario = authenticate(request, email=email, password=senha)

        if pode_acessar(usuario):
            # SEGURANÇA: next validado ANTES do login (open redirect / papel)
            destino = _next_permitido(request, usuario) or reverse(destino_padrao(usuario))
            auth_login(request, usuario)
            # SEGURANÇA: Regenerar sessão para prevenir Session Fixation
            request.session.cycle_key()
            # Marca da sessao da equipe: o Enforce2FAMiddleware confere o 2FA
            # obrigatorio a cada request (promocao a ADMIN, valvula religada)
            request.session[dois_fatores.SESSION_LOGIN_EQUIPE] = usuario.pk
            request.session['usuario_nome'] = usuario.nome

            messages.success(request, f'Bem-vindo(a), {usuario.nome}!')

            # 2FA obrigatório (ADMIN; PROFISSIONAL se ligado) sem TOTP: prende a sessão no cadastro
            if dois_fatores.obrigatorio_para(usuario) and not dois_fatores.tem_2fa(usuario):
                request.session[dois_fatores.SESSION_CADASTRO_PENDENTE] = True
                messages.warning(
                    request, 'Para continuar, ative a verificação em duas etapas (2FA).'
                )
                setup = reverse('aranha:admin_2fa_setup')
                return redirect(f'{setup}?{urlencode({"next": destino})}')
            return redirect(destino)

        # SEGURANÇA: contar apenas tentativas FALHAS para o rate-limit por IP
        _register_failed_login(request)
        # SEGURANÇA: Log sem PII (apenas últimos 4 chars do email para rastreabilidade)
        email_masked = f'***{email[-4:]}' if len(email) > 4 else '***'
        logger.warning(f'Login falho para: {email_masked} | IP: {client_ip(request)}')
        messages.error(request, 'Credenciais inválidas ou acesso não autorizado.')
        return redirect('aranha:usuario_login')

    return render(request, 'usuario/login.html')


@require_http_methods(["GET", "POST"])
def usuario_logout(request):
    """Logout só via POST (com CSRF). GET mostra a confirmação.

    Evita logout forçado por link/imagem de terceiros e mantém funcionando
    links antigos <a href> para esta rota.
    """
    if not request.user.is_authenticated:
        return redirect('aranha:inicio')
    if request.method == 'POST':
        auth_logout(request)
        messages.info(request, 'Você saiu da sua conta.')
        resposta = redirect('aranha:inicio')
        # Computador compartilhado (recepcao): tira do cache/bfcache do navegador
        # as telas com dado de saude antes que o "voltar" as reexiba.
        resposta['Clear-Site-Data'] = '"cache"'
        return resposta
    voltar = destino_padrao(request.user) if pode_acessar(request.user) else 'aranha:inicio'
    return render(request, 'usuario/logout.html', {'voltar_url': reverse(voltar)})


# ─── Password Recovery (Class-Based Views) ───
# Hardening: rate limit por IP + audit log, alem do token one-time + TTL Django.
# Resposta sempre generica (Django default) — nao revela se email existe.


class ClinicaPasswordResetForm(PasswordResetForm):
    """O get_users do Django filtra is_active no ORM; em Usuario is_active e
    @property (campo real: `ativo`) -> FieldError/500. Filtra pelo campo real."""

    def get_users(self, email):
        qs = Usuario.objects.filter(email__iexact=email, ativo=True)
        return (u for u in qs if u.has_usable_password())


@method_decorator(
    ratelimit(key='ip', rate='3/15m', method='POST', block=True),
    name='dispatch'
)
class ClinicaPasswordResetView(PasswordResetView):
    template_name = 'usuario/password_reset.html'
    email_template_name = 'usuario/password_reset_email.html'
    subject_template_name = 'usuario/password_reset_subject.txt'
    form_class = ClinicaPasswordResetForm
    success_url = reverse_lazy('aranha:password_reset_done')

    def form_valid(self, form):
        email = form.cleaned_data.get('email', '').strip().lower()
        ip = client_ip(self.request) or None
        # Hash estavel entre processos (hash() builtin tem seed aleatorio por processo)
        email_hash = hashlib.sha256(email.encode()).hexdigest()[:12]
        email_masked = f'***{email[-4:]}' if len(email) > 4 else '***'
        logger.info('password_reset_requested', extra={'email_hash': email_hash, 'ip': ip})
        # PII mascarada: nao persistir email em claro na trilha de auditoria.
        # registrar_log grava o IP (client_ip) e nunca derruba a requisicao.
        registrar_log(
            None, f'Solicitacao de reset de senha (email: {email_masked})', 'usuario',
            request=self.request,
        )

        if not email_configurado():
            # Falha fechada: sem provedor o link iria parar no log do servidor.
            # Resposta continua generica (nao revela se o e-mail existe).
            logger.warning('password_reset_sem_email_configurado', extra={'email_hash': email_hash})
            return HttpResponseRedirect(self.get_success_url())

        # O form renderiza o e-mail sem request (context processor nao roda)
        self.extra_email_context = {
            'CLINIC_NAME': get_branding()['CLINIC_NAME'],
            'site_url': settings.SITE_URL,
        }
        return super().form_valid(form)


class ClinicaPasswordResetDoneView(PasswordResetDoneView):
    template_name = 'usuario/password_reset_done.html'


class ClinicaPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = 'usuario/password_reset_confirm.html'
    success_url = reverse_lazy('aranha:password_reset_complete')

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        _input_cls = 'rounded-md border border-borda bg-superficie px-3 py-2 text-texto w-full'
        form.fields['new_password1'].widget.attrs.update({'class': _input_cls})
        form.fields['new_password2'].widget.attrs.update({'class': _input_cls})
        return form

    def form_valid(self, form):
        user = form.user
        ip = client_ip(self.request) or None
        logger.info('password_reset_effective', extra={'user_id': user.pk, 'ip': ip})
        registrar_log(
            user, 'Senha redefinida via reset de senha', 'usuario', user.pk, request=self.request,
        )
        return super().form_valid(form)


class ClinicaPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = 'usuario/password_reset_complete.html'
