"""CRUD de usuarios admin (recepcionistas, gerentes, profissionais com login)."""
import secrets

from django.contrib import messages
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.http import require_POST

from ..decorators import staff_required
from ..models import Perfil, Profissional, Usuario
from ..utils.audit import registrar_log


@staff_required
def admin_usuarios(request):
    """Lista todos os usuarios do sistema."""
    busca = request.GET.get('q', '').strip()
    perfil_filter = request.GET.get('perfil', '')

    qs = Usuario.objects.select_related('perfil', 'profissional').order_by('nome')
    if busca:
        qs = qs.filter(nome__icontains=busca) | qs.filter(email__icontains=busca)
    if perfil_filter:
        qs = qs.filter(perfil_id=perfil_filter)

    paginator = Paginator(qs, 30)
    page = request.GET.get('page', 1)
    usuarios = paginator.get_page(page)

    perfis = Perfil.objects.order_by('nome')

    context = {
        'usuarios': usuarios,
        'perfis': perfis,
        'busca': busca,
        'perfil_filter': perfil_filter,
    }
    return render(request, 'painel/usuarios.html', context)


@staff_required
def admin_criar_usuario(request):
    """Cria um novo usuario admin (recepcionista, gerente, profissional logado)."""
    perfis = Perfil.objects.order_by('nome')
    profissionais = Profissional.objects.filter(ativo=True).order_by('nome')

    if request.method == 'POST':
        nome = request.POST.get('nome', '').strip()
        email = request.POST.get('email', '').strip().lower()
        perfil_id = request.POST.get('perfil_id') or None
        profissional_id = request.POST.get('profissional_id') or None
        senha = request.POST.get('senha', '')

        if not nome or not email:
            messages.error(request, 'Nome e email sao obrigatorios.')
            return render(request, 'painel/usuario_form.html', {
                'perfis': perfis, 'profissionais': profissionais,
                'form_data': request.POST, 'modo': 'criar',
            })

        if Usuario.objects.filter(email=email).exists():
            messages.error(request, 'Ja existe usuario com esse email.')
            return render(request, 'painel/usuario_form.html', {
                'perfis': perfis, 'profissionais': profissionais,
                'form_data': request.POST, 'modo': 'criar',
            })

        if not senha:
            senha = secrets.token_urlsafe(12)

        usuario = Usuario.objects.create_user(
            email=email,
            password=senha,
            nome=nome,
            perfil_id=perfil_id,
            profissional_id=profissional_id,
            ativo=True,
        )
        registrar_log(
            request.user, f'Criou usuario: {usuario.email}',
            'usuario', usuario.pk,
            detalhes={'perfil_id': perfil_id, 'profissional_id': profissional_id},
        )

        _enviar_email_boas_vindas(request, usuario, senha)

        messages.success(
            request,
            f'Usuario {usuario.nome} criado. Senha inicial enviada por email.'
        )
        return redirect('shivazen:admin_usuarios')

    return render(request, 'painel/usuario_form.html', {
        'perfis': perfis, 'profissionais': profissionais,
        'modo': 'criar',
    })


@staff_required
def admin_editar_usuario(request, pk):
    """Edita dados do usuario (nome, email, perfil, profissional vinculado)."""
    usuario = get_object_or_404(Usuario, pk=pk)
    perfis = Perfil.objects.order_by('nome')
    profissionais = Profissional.objects.filter(ativo=True).order_by('nome')

    if request.method == 'POST':
        usuario.nome = request.POST.get('nome', usuario.nome).strip()
        novo_email = request.POST.get('email', usuario.email).strip().lower()
        if novo_email != usuario.email and Usuario.objects.filter(email=novo_email).exists():
            messages.error(request, 'Email ja em uso por outro usuario.')
        else:
            usuario.email = novo_email
            usuario.perfil_id = request.POST.get('perfil_id') or None
            usuario.profissional_id = request.POST.get('profissional_id') or None
            usuario.ativo = request.POST.get('ativo') == '1'
            usuario.save()
            registrar_log(request.user, f'Editou usuario: {usuario.email}', 'usuario', usuario.pk)
            messages.success(request, 'Usuario atualizado.')
            return redirect('shivazen:admin_usuarios')

    return render(request, 'painel/usuario_form.html', {
        'usuario': usuario,
        'perfis': perfis, 'profissionais': profissionais,
        'modo': 'editar',
    })


@staff_required
@require_POST
def admin_resetar_senha_usuario(request, pk):
    """Envia email de reset de senha ao usuario."""
    usuario = get_object_or_404(Usuario, pk=pk)

    uid = urlsafe_base64_encode(force_bytes(usuario.pk))
    token = default_token_generator.make_token(usuario)
    url_reset = request.build_absolute_uri(
        reverse('shivazen:password_reset_confirm', kwargs={'uidb64': uid, 'token': token})
    )

    assunto = 'Redefinicao de senha — acesso ao painel'
    corpo_txt = (
        f'Ola {usuario.nome},\n\n'
        f'Acesse o link abaixo para redefinir sua senha (validade 1 hora):\n'
        f'{url_reset}\n\n'
        f'Se voce nao solicitou, ignore este email.'
    )
    try:
        msg = EmailMultiAlternatives(assunto, corpo_txt, to=[usuario.email])
        msg.send(fail_silently=False)
        registrar_log(request.user, f'Enviou reset senha: {usuario.email}', 'usuario', usuario.pk)
        messages.success(request, f'Email de reset enviado para {usuario.email}.')
    except Exception as exc:
        messages.error(request, f'Falha ao enviar email: {exc}')

    return redirect('shivazen:admin_usuarios')


@staff_required
@require_POST
def admin_desativar_usuario(request, pk):
    """Desativa (soft delete) o usuario — nao permite login."""
    usuario = get_object_or_404(Usuario, pk=pk)
    if usuario.pk == request.user.pk:
        messages.error(request, 'Voce nao pode desativar sua propria conta.')
        return redirect('shivazen:admin_usuarios')
    usuario.ativo = not usuario.ativo
    usuario.save(update_fields=['ativo'])
    acao = 'Ativou' if usuario.ativo else 'Desativou'
    registrar_log(request.user, f'{acao} usuario: {usuario.email}', 'usuario', usuario.pk)
    messages.success(request, f'{acao} {usuario.nome}.')
    return redirect('shivazen:admin_usuarios')


def _enviar_email_boas_vindas(request, usuario, senha):
    """Envia credenciais iniciais por email."""
    login_url = request.build_absolute_uri(reverse('shivazen:usuario_login'))
    assunto = 'Bem-vindo(a) ao painel administrativo'
    corpo = (
        f'Ola {usuario.nome},\n\n'
        f'Sua conta de acesso foi criada.\n\n'
        f'Email: {usuario.email}\n'
        f'Senha inicial: {senha}\n\n'
        f'Acesse: {login_url}\n\n'
        f'Recomendamos trocar a senha no primeiro acesso.'
    )
    try:
        EmailMultiAlternatives(assunto, corpo, to=[usuario.email]).send(fail_silently=True)
    except Exception:
        pass
