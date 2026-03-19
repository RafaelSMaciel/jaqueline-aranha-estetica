from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout, authenticate
from django.contrib.auth.decorators import login_required
from django.utils.http import url_has_allowed_host_and_scheme
from django_ratelimit.decorators import ratelimit
import logging

logger = logging.getLogger(__name__)


@ratelimit(key='ip', rate='5/m', method='POST', block=False)
def usuarioLogin(request):
    """Login exclusivo para administradores da clínica."""
    # Se já logado, vai direto pro painel
    if request.user.is_authenticated and request.user.is_staff:
        return redirect('shivazen:painel_overview')

    if request.method == 'POST':
        # SEGURANÇA: Rate limiting — bloquear após 5 tentativas por minuto
        was_limited = getattr(request, 'limited', False)
        if was_limited:
            messages.error(request, 'Muitas tentativas de login. Aguarde um momento e tente novamente.')
            return redirect('shivazen:usuarioLogin')

        email = request.POST.get('login', '').strip()
        senha = request.POST.get('senha', '')

        if not email or not senha:
            messages.error(request, 'Preencha e-mail e senha.')
            return redirect('shivazen:usuarioLogin')

        usuario = authenticate(request, email=email, password=senha)

        if usuario is not None and usuario.is_staff:
            auth_login(request, usuario)
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
            # SEGURANÇA: Log de tentativa falha (sem expor detalhes ao usuário)
            logger.warning(f'Tentativa de login falha para email: {email} | IP: {request.META.get("REMOTE_ADDR")}')
            messages.error(request, 'Credenciais inválidas ou acesso não autorizado.')
            return redirect('shivazen:usuarioLogin')

    return render(request, 'usuario/login.html')


@login_required
def usuarioLogout(request):
    """Logout — aceita GET e POST por praticidade."""
    auth_logout(request)
    messages.info(request, 'Você saiu da sua conta.')
    return redirect('shivazen:inicio')
