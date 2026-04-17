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
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)


def _check_rate_limit(request, max_attempts=5, window=60):
    """Rate limiting simples usando Django cache (funciona com qualquer backend)."""
    try:
        ip = request.META.get('REMOTE_ADDR', '0.0.0.0')
        cache_key = f'login_attempts_{ip}'
        attempts = cache.get(cache_key, 0)
        if attempts >= max_attempts:
            return True  # bloqueado
        cache.set(cache_key, attempts + 1, window)
    except Exception:
        pass  # Se o cache falhar, não bloquear o login
    return False


@require_http_methods(["GET", "POST"])
def usuario_login(request):
    """Login exclusivo para administradores da clínica."""
    # Se já logado, vai direto pro painel
    if request.user.is_authenticated and request.user.is_staff:
        return redirect('shivazen:painel_overview')

    if request.method == 'POST':
        # SEGURANÇA: Rate limiting — bloquear após 5 tentativas por minuto
        if _check_rate_limit(request):
            messages.error(request, 'Muitas tentativas de login. Aguarde um momento e tente novamente.')
            return redirect('shivazen:usuario_login')

        email = request.POST.get('username', '').strip()
        senha = request.POST.get('password', '')

        if not email or not senha:
            messages.error(request, 'Preencha e-mail e senha.')
            return redirect('shivazen:usuario_login')

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
            return redirect(next_url or 'shivazen:painel_overview')
        else:
            # SEGURANÇA: Log sem PII (apenas últimos 4 chars do email para rastreabilidade)
            email_masked = f'***{email[-4:]}' if len(email) > 4 else '***'
            logger.warning(f'Login falho para: {email_masked} | IP: {request.META.get("REMOTE_ADDR")}')
            messages.error(request, 'Credenciais inválidas ou acesso não autorizado.')
            return redirect('shivazen:usuario_login')

    return render(request, 'usuario/login.html')


@login_required
@require_http_methods(["GET", "POST"])
def usuario_logout(request):
    """Logout — aceita GET e POST por praticidade."""
    auth_logout(request)
    messages.info(request, 'Você saiu da sua conta.')
    return redirect('shivazen:inicio')


# ─── Password Recovery (Class-Based Views) ───

class ShivaZenPasswordResetView(PasswordResetView):
    template_name = 'usuario/password_reset.html'
    email_template_name = 'usuario/password_reset_email.html'
    subject_template_name = 'usuario/password_reset_subject.txt'
    success_url = reverse_lazy('shivazen:password_reset_done')


class ShivaZenPasswordResetDoneView(PasswordResetDoneView):
    template_name = 'usuario/password_reset_done.html'


class ShivaZenPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = 'usuario/password_reset_confirm.html'
    success_url = reverse_lazy('shivazen:password_reset_complete')

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields['new_password1'].widget.attrs.update({'class': 'form-input'})
        form.fields['new_password2'].widget.attrs.update({'class': 'form-input'})
        return form


class ShivaZenPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = 'usuario/password_reset_complete.html'
