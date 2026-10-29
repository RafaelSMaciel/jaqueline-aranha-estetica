"""CRUD de usuarios da equipe (administradores e profissionais com login).

A validacao e o Form (forms/cadastros.UsuarioForm/UsuarioEdicaoForm: e-mail unico, vinculo do
profissional, ultimo admin e auto-rebaixamento no clean); a view so persiste, audita e, no
desligamento (login desativado ou vinculo trocado), revoga o feed ICS do profissional que saiu.
"""
import logging
import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django_ratelimit.decorators import ratelimit

from ...decorators import LIMITE_ESCRITA_EQUIPE, escrita_painel, staff_required
from ...forms.cadastros import UsuarioEdicaoForm, UsuarioForm
from ...forms.painel import avisar_erros, erros_por_campo
from ...models import Profissional, Usuario
from ...services import links
from ...utils.auditoria import registrar_log
from ...utils.branding import get_branding
from ...utils.email import email_configurado

logger = logging.getLogger(__name__)


def _profissionais_para(usuario=None):
    """Profissionais ativos + o ja vinculado (mesmo inativo), p/ nao desvincular ao salvar."""
    filtro = Q(ativo=True)
    if usuario is not None and usuario.profissional_id:
        filtro |= Q(pk=usuario.profissional_id)
    return Profissional.objects.filter(filtro).order_by('nome')


def _render_form(request, modo, usuario=None, form_data=None, erros=None):
    return render(request, 'painel/usuario_form.html', {
        'usuario': usuario,
        'modo': modo,
        'papeis': Usuario.PAPEL_CHOICES,
        'profissionais': _profissionais_para(usuario),
        'form_data': form_data if form_data is not None else {},
        'erros': erros or {},
        'email_configurado': email_configurado(),
    })


def _recusar(request, form, modo, usuario=None):
    """Envio recusado: a tela volta com o digitado, o aviso no topo e o erro em cada campo."""
    avisar_erros(request, form)
    return _render_form(request, modo, usuario, form_data=request.POST, erros=erros_por_campo(form))


def _revogar_feed(request, profissional, motivo):
    """Feed ICS do profissional que perdeu o login (decisao 30): o calendario de quem saiu para."""
    if profissional is not None and links.revogar_feed_ics(profissional, motivo=motivo, request=request):
        messages.info(request, f'O link do calendário de {profissional.nome} foi desativado. '
                               'Para voltar a usar, gere um link novo em Profissionais.')


def _link_definir_senha(usuario):
    uid = urlsafe_base64_encode(force_bytes(usuario.pk))
    token = default_token_generator.make_token(usuario)
    caminho = reverse('aranha:password_reset_confirm', kwargs={'uidb64': uid, 'token': token})
    return f'{settings.SITE_URL}{caminho}'


def _validade_link():
    horas = max(1, int(getattr(settings, 'PASSWORD_RESET_TIMEOUT', 3600)) // 3600)
    return f'{horas} hora' if horas == 1 else f'{horas} horas'


@staff_required
def admin_usuarios(request):
    """Lista todos os usuarios do sistema."""
    busca = request.GET.get('q', '').strip()
    papel_filter = request.GET.get('papel', '')

    qs = Usuario.objects.select_related('profissional').order_by('nome')
    if busca:
        qs = qs.filter(Q(nome__icontains=busca) | Q(email__icontains=busca))
    if papel_filter:
        qs = qs.filter(papel=papel_filter)

    paginator = Paginator(qs, 30)
    page = request.GET.get('page', 1)
    usuarios = paginator.get_page(page)

    context = {
        'usuarios': usuarios,
        'papeis': Usuario.PAPEL_CHOICES,
        'busca': busca,
        'papel_filter': papel_filter,
    }
    return render(request, 'painel/usuarios.html', context)


@staff_required
@ratelimit(key='user', rate=LIMITE_ESCRITA_EQUIPE, method='POST', block=True)
def admin_criar_usuario(request):
    """Cria um usuario da equipe (administrador ou profissional com login)."""
    if request.method != 'POST':
        return _render_form(request, 'criar')

    form = UsuarioForm(request.POST)
    if not form.is_valid():
        return _recusar(request, form, 'criar')
    d = form.cleaned_data
    usuario = Usuario.objects.create_user(
        email=d['email'],
        # Sem senha informada: aleatoria (usavel p/ "Esqueci minha senha"),
        # a pessoa define a propria pelo link do e-mail.
        password=d['senha'] or secrets.token_urlsafe(24),
        nome=d['nome'],
        papel=d['papel'],
        profissional=d['profissional'],
        ativo=True,
    )
    registrar_log(
        request.user, f'Criou usuario: {usuario.email}',
        'usuario', usuario.pk,
        detalhes={'papel': usuario.papel, 'profissional_id': usuario.profissional_id}, request=request,
    )

    if d['senha']:
        messages.success(
            request, f'Usuário {usuario.nome} criado. Informe a senha inicial à pessoa.'
        )
    elif _enviar_email_boas_vindas(usuario):
        messages.success(
            request,
            f'Usuário {usuario.nome} criado. Link para definir a senha enviado por e-mail.',
        )
    else:
        messages.warning(
            request,
            f'Usuário {usuario.nome} criado, mas o e-mail com o link não pôde ser enviado. '
            'Use "Enviar reset de senha" mais tarde.',
        )
    return redirect('aranha:admin_usuarios')


@staff_required
@ratelimit(key='user', rate=LIMITE_ESCRITA_EQUIPE, method='POST', block=True)
def admin_editar_usuario(request, pk):
    """Edita dados do usuario (nome, email, papel, profissional vinculado, ativo)."""
    usuario = get_object_or_404(Usuario.objects.select_related('profissional'), pk=pk)
    if request.method != 'POST':
        return _render_form(request, 'editar', usuario)

    form = UsuarioEdicaoForm(request.POST, instance=usuario, request_user=request.user)
    if not form.is_valid():
        return _recusar(request, form, 'editar', usuario)
    d = form.cleaned_data
    email_anterior, papel_anterior = usuario.email, usuario.papel
    profissional_anterior, ativo_anterior = usuario.profissional, usuario.ativo
    usuario.nome = d['nome']
    usuario.email = d['email']
    usuario.papel = d['papel']
    usuario.profissional = d['profissional']
    usuario.ativo = d['ativo']
    usuario.save()
    detalhes = {'papel': usuario.papel, 'ativo': usuario.ativo, 'profissional_id': usuario.profissional_id}
    # Trocar o e-mail de outra pessoa + "enviar reset" = assumir a conta:
    # a trilha guarda o de antes (e a mudanca de papel) p/ reconstruir.
    if email_anterior != usuario.email:
        detalhes['email_anterior'] = email_anterior
    if papel_anterior != usuario.papel:
        detalhes['papel_anterior'] = papel_anterior
    registrar_log(
        request.user, f'Editou usuario: {usuario.email}', 'usuario', usuario.pk,
        detalhes=detalhes, request=request,
    )
    if profissional_anterior is not None and profissional_anterior.pk != usuario.profissional_id:
        _revogar_feed(request, profissional_anterior, 'vínculo do login trocado')
    elif ativo_anterior and not usuario.ativo:
        _revogar_feed(request, profissional_anterior, 'login desativado')
    messages.success(request, 'Usuário atualizado.')
    return redirect('aranha:admin_usuarios')


@escrita_painel
def admin_resetar_senha_usuario(request, pk):
    """Envia email de reset de senha ao usuario."""
    usuario = get_object_or_404(Usuario, pk=pk)

    if not email_configurado():
        messages.error(
            request,
            'O envio de e-mail não está configurado. Não foi possível enviar o link de redefinição.',
        )
        return redirect('aranha:admin_usuarios')

    clinica = get_branding()['CLINIC_NAME']
    assunto = f'{clinica} — Redefinição de senha'
    corpo_txt = (
        f'Olá, {usuario.nome}.\n\n'
        f'Acesse o link abaixo para redefinir sua senha (validade de {_validade_link()}):\n'
        f'{_link_definir_senha(usuario)}\n\n'
        f'Se você não esperava este e-mail, ignore-o.\n\n'
        f'{clinica}'
    )
    try:
        msg = EmailMultiAlternatives(assunto, corpo_txt, to=[usuario.email])
        msg.send(fail_silently=False)
        registrar_log(
            request.user, f'Enviou reset senha: {usuario.email}', 'usuario', usuario.pk, request=request,
        )
        messages.success(request, f'E-mail de redefinição enviado para {usuario.email}.')
    except Exception:
        logger.warning('admin_reset_senha_envio_falhou', extra={'usuario_id': usuario.pk}, exc_info=True)
        messages.error(request, 'Não foi possível enviar o e-mail agora. Tente novamente mais tarde.')

    return redirect('aranha:admin_usuarios')


@escrita_painel
def admin_desativar_usuario(request, pk):
    """Desativa (soft delete) o usuario — nao permite login. Desativar revoga o feed ICS do
    profissional vinculado; reativar nao devolve o link antigo (gera-se outro)."""
    usuario = get_object_or_404(Usuario.objects.select_related('profissional'), pk=pk)
    if usuario.pk == request.user.pk:
        messages.error(request, 'Você não pode desativar sua própria conta.')
        return redirect('aranha:admin_usuarios')
    usuario.ativo = not usuario.ativo
    usuario.save(update_fields=['ativo'])
    acao = 'Ativou' if usuario.ativo else 'Desativou'
    registrar_log(request.user, f'{acao} usuario: {usuario.email}', 'usuario', usuario.pk, request=request)
    if not usuario.ativo:
        _revogar_feed(request, usuario.profissional, 'login desativado')
    messages.success(request, f'{acao} {usuario.nome}.')
    return redirect('aranha:admin_usuarios')


def _enviar_email_boas_vindas(usuario) -> bool:
    """Envia LINK para o usuario definir a propria senha. True se saiu.

    Nunca envia senha em texto plano (e-mail nao e canal seguro e a credencial
    ficaria persistida na caixa do destinatario). Reusa o token de reset.
    Sem provedor de e-mail configurado nao envia (o link iria para o log).
    """
    if not email_configurado():
        return False
    clinica = get_branding()['CLINIC_NAME']
    assunto = f'{clinica} — Defina sua senha de acesso'
    corpo = (
        f'Olá, {usuario.nome}.\n\n'
        f'Sua conta de acesso à equipe da {clinica} foi criada (e-mail: {usuario.email}).\n\n'
        f'Defina sua senha pelo link abaixo (validade de {_validade_link()}):\n'
        f'{_link_definir_senha(usuario)}\n\n'
        f'Se o link expirar, use "Esqueci minha senha" na tela de login.\n'
        f'Se você não esperava este e-mail, ignore-o.\n\n'
        f'{clinica}'
    )
    try:
        enviados = EmailMultiAlternatives(assunto, corpo, to=[usuario.email]).send(fail_silently=False)
    except Exception:
        logger.warning('boas_vindas_envio_falhou', extra={'usuario_id': usuario.pk}, exc_info=True)
        return False
    return bool(enviados)
