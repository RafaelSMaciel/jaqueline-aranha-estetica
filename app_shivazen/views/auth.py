from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout, authenticate
from django.contrib.auth.decorators import login_required


def usuarioLogin(request):
    """Login exclusivo para administradores da clínica."""
    # Se já logado, vai direto pro painel
    if request.user.is_authenticated and request.user.is_staff:
        return redirect('shivazen:painel_overview')

    if request.method == 'POST':
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

            next_url = request.GET.get('next') or request.POST.get('next')
            messages.success(request, f'Bem-vindo(a), {usuario.nome}!')
            return redirect(next_url or 'shivazen:painel_overview')
        else:
            messages.error(request, 'Credenciais inválidas ou acesso não autorizado.')
            return redirect('shivazen:usuarioLogin')

    return render(request, 'usuario/login.html')


@login_required
def usuarioLogout(request):
    auth_logout(request)
    messages.info(request, 'Você saiu da sua conta.')
    return redirect('shivazen:inicio')
