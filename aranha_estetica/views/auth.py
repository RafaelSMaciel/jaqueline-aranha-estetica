import hashlib
import logging

from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import (
    PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
)
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

logger = logging.getLogger(__name__)


LOGIN_RL_MAX_ATTEMPTS = 5
LOGIN_RL_WINDOW = 60


def _login_rl_key(request):
    ip = request.META.get('REMOTE_ADDR', '0.0.0.0')
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


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
@ratelimit(key='post:username', rate='5/m', method='POST', block=True)
@require_http_methods(["GET", "POST"])
def usuario_login(request):
    """Login exclusivo para administradores da clínica."""
    # Se já logado, vai direto pro painel
    if request.user.is_authenticated and request.user.is_staff:
        return redirect('aranha:painel_overview')

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

        if usuario is not None and usuario.is_staff:
            auth_login(request, usuario)
            # SEGURANÇA: Regenerar sessão para prevenir Session Fixation
            request.session.cycle_key()
            request.session['usuario_id'] = usuario.pk
            request.session['usuario_nome'] = usuario.nome

            # SEGURANÇA: Validar redirect URL para prevenir Open Redirect
            next_url = request.GET.get('next') or request.POST.get('next')
            if next_url and not url_has_allowed_host_and_scheme(
                next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
            ):
                next_url = None  # URL maliciosa descartada

            messages.success(request, f'Bem-vindo(a), {usuario.nome}!')
            return redirect(next_url or 'aranha:painel_overview')
        else:
            # SEGURANÇA: contar apenas tentativas FALHAS para o rate-limit por IP
            _register_failed_login(request)
            # SEGURANÇA: Log sem PII (apenas últimos 4 chars do email para rastreabilidade)
            email_masked = f'***{email[-4:]}' if len(email) > 4 else '***'
            logger.warning(f'Login falho para: {email_masked} | IP: {request.META.get("REMOTE_ADDR")}')
            messages.error(request, 'Credenciais inválidas ou acesso não autorizado.')
            return redirect('aranha:usuario_login')

    return render(request, 'usuario/login.html')


@login_required
@require_http_methods(["GET", "POST"])
def usuario_logout(request):
    """Logout — aceita GET e POST por praticidade."""
    auth_logout(request)
    messages.info(request, 'Você saiu da sua conta.')
    return redirect('aranha:inicio')


# ─── Password Recovery (Class-Based Views) ───
# Hardening: rate limit por IP + audit log, alem do token one-time + TTL Django.
# Resposta sempre generica (Django default) — nao revela se email existe.


@method_decorator(
    ratelimit(key='ip', rate='3/15m', method='POST', block=True),
    name='dispatch'
)
class ClinicaPasswordResetView(PasswordResetView):
    template_name = 'usuario/password_reset.html'
    email_template_name = 'usuario/password_reset_email.html'
    subject_template_name = 'usuario/password_reset_subject.txt'
    success_url = reverse_lazy('aranha:password_reset_done')

    def form_valid(self, form):
        email = form.cleaned_data.get('email', '').strip().lower()
        ip = self.request.META.get('REMOTE_ADDR', '0.0.0.0')
        # Hash estavel entre processos (hash() builtin tem seed aleatorio por processo)
        email_hash = hashlib.sha256(email.encode()).hexdigest()[:12]
        email_masked = f'***{email[-4:]}' if len(email) > 4 else '***'
        logger.info('password_reset_requested', extra={'email_hash': email_hash, 'ip': ip})
        try:
            from ..models import LogAuditoria
            LogAuditoria.objects.create(
                # PII mascarada: nao persistir email em claro na trilha de auditoria
                acao=f'Solicitacao de reset de senha (email: {email_masked})',
                tabela='usuario',
                ip_origem=ip,
            )
        except Exception:
            logger.warning('falha ao registrar LogAuditoria de reset de senha', exc_info=True)
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
        ip = self.request.META.get('REMOTE_ADDR', '0.0.0.0')
        logger.info('password_reset_effective', extra={'user_id': user.pk, 'ip': ip})
        try:
            from ..models import LogAuditoria
            LogAuditoria.objects.create(
                usuario=user,
                acao='Senha redefinida via reset de senha',
                tabela='usuario',
                registro_id=user.pk,
                ip_origem=ip,
            )
        except Exception:
            logger.warning('falha ao registrar LogAuditoria de redefinicao de senha', exc_info=True)
        return super().form_valid(form)


class ClinicaPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = 'usuario/password_reset_complete.html'
