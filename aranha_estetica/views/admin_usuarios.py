"""CRUD de usuarios da equipe (administradores e profissionais com login)."""
import logging
import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.http import require_POST

from ..decorators import staff_required
from ..models import Profissional, Usuario
from ..utils.audit import registrar_log
from ..utils.branding import get_branding
from ..utils.email import email_configurado

logger = logging.getLogger(__name__)

# RECEPCAO ainda nao tem telas proprias (nao consegue logar): fica fora das
# opcoes de criacao ate existirem. O CHECK do banco continua aceitando.
PAPEIS_DISPONIVEIS = [
    (valor, rotulo) for valor, rotulo in Usuario.PAPEL_CHOICES
    if valor != Usuario.PAPEL_RECEPCAO
]


def _papeis_para(usuario=None):
    """Choices do select. Quem ja e RECEPCAO mantem a opcao (nao troca em silencio)."""
    if usuario is not None and usuario.papel == Usuario.PAPEL_RECEPCAO:
        return list(Usuario.PAPEL_CHOICES)
    return list(PAPEIS_DISPONIVEIS)


def _profissionais_para(usuario=None):
    """Profissionais ativos + o ja vinculado (mesmo inativo), p/ nao desvincular ao salvar."""
    filtro = Q(ativo=True)
    if usuario is not None and usuario.profissional_id:
        filtro |= Q(pk=usuario.profissional_id)
    return Profissional.objects.filter(filtro).order_by('nome')


def _render_form(request, modo, usuario=None, form_data=None):
    return render(request, 'painel/usuario_form.html', {
        'usuario': usuario,
        'modo': modo,
        'papeis': _papeis_para(usuario),
        'profissionais': _profissionais_para(usuario),
        'form_data': form_data if form_data is not None else {},
        'email_configurado': email_configurado(),
    })


def _ler_profissional_id(raw):
    """(id|None, erro|None) a partir do POST."""
    raw = (raw or '').strip()
    if not raw:
        return None, None
    if not raw.isdigit() or not Profissional.objects.filter(pk=int(raw)).exists():
        return None, 'Profissional inválido.'
    return int(raw), None


def _erro_vinculo(papel, profissional_id, usuario=None):
    if papel == Usuario.PAPEL_PROFISSIONAL and not profissional_id:
        return 'Usuário com papel Profissional precisa de um profissional vinculado.'
    if profissional_id:
        outros = Usuario.objects.filter(profissional_id=profissional_id)
        if usuario is not None:
            outros = outros.exclude(pk=usuario.pk)
        if outros.exists():
            return 'Esse profissional já está vinculado a outro usuário.'
    return None


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
def admin_criar_usuario(request):
    """Cria um usuario da equipe (administrador ou profissional com login)."""
    if request.method == 'POST':
        nome = request.POST.get('nome', '').strip()
        email = request.POST.get('email', '').strip().lower()
        papel = request.POST.get('papel') or Usuario.PAPEL_PROFISSIONAL
        senha = request.POST.get('senha', '')
        profissional_id, erro = _ler_profissional_id(request.POST.get('profissional_id'))

        if not erro and (not nome or not email):
            erro = 'Nome e e-mail são obrigatórios.'
        if not erro and papel not in dict(PAPEIS_DISPONIVEIS):
            erro = 'Papel inválido.'
        if not erro and Usuario.objects.filter(email__iexact=email).exists():
            erro = 'Já existe usuário com esse e-mail.'
        if not erro:
            erro = _erro_vinculo(papel, profissional_id)
        if not erro and not senha and not email_configurado():
            # Sem provedor o link nao chega: a conta ficaria inutilizavel
            erro = 'O envio de e-mail não está configurado: defina uma senha inicial.'
        if not erro and senha:
            try:
                validate_password(senha, Usuario(email=email, nome=nome))
            except ValidationError as exc:
                erro = ' '.join(exc.messages)

        if erro:
            messages.error(request, erro)
            return _render_form(request, 'criar', form_data=request.POST)

        usuario = Usuario.objects.create_user(
            email=email,
            # Sem senha informada: aleatoria (usavel p/ "Esqueci minha senha"),
            # a pessoa define a propria pelo link do e-mail.
            password=senha or secrets.token_urlsafe(24),
            nome=nome,
            papel=papel,
            profissional_id=profissional_id,
            ativo=True,
        )
        registrar_log(
            request.user, f'Criou usuario: {usuario.email}',
            'usuario', usuario.pk,
            detalhes={'papel': papel, 'profissional_id': profissional_id}, request=request,
        )

        if senha:
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

    return _render_form(request, 'criar')


@staff_required
def admin_editar_usuario(request, pk):
    """Edita dados do usuario (nome, email, papel, profissional vinculado, ativo)."""
    usuario = get_object_or_404(Usuario, pk=pk)

    if request.method == 'POST':
        nome = request.POST.get('nome', '').strip()
        novo_email = request.POST.get('email', '').strip().lower()
        novo_papel = request.POST.get('papel') or usuario.papel
        novo_ativo = request.POST.get('ativo') == '1'
        profissional_id, erro = _ler_profissional_id(request.POST.get('profissional_id'))

        eh_admin_ativo = usuario.papel == Usuario.PAPEL_ADMIN and usuario.ativo
        perde_admin = eh_admin_ativo and (novo_papel != Usuario.PAPEL_ADMIN or not novo_ativo)

        if not erro and (not nome or not novo_email):
            erro = 'Nome e e-mail são obrigatórios.'
        if not erro and novo_papel not in dict(_papeis_para(usuario)):
            erro = 'Papel inválido.'
        if not erro and Usuario.objects.filter(
            email__iexact=novo_email
        ).exclude(pk=usuario.pk).exists():
            erro = 'E-mail já em uso por outro usuário.'
        if not erro and usuario.pk == request.user.pk and (
            novo_papel != usuario.papel or not novo_ativo
        ):
            # Evita lockout: ninguem rebaixa nem desativa a propria conta
            erro = 'Você não pode alterar o próprio papel nem desativar a própria conta.'
        if not erro and perde_admin and not Usuario.objects.filter(
            papel=Usuario.PAPEL_ADMIN, ativo=True
        ).exclude(pk=usuario.pk).exists():
            erro = 'É preciso manter ao menos um administrador ativo.'
        if not erro:
            erro = _erro_vinculo(novo_papel, profissional_id, usuario)

        if erro:
            messages.error(request, erro)
            return _render_form(request, 'editar', usuario, form_data=request.POST)

        usuario.nome = nome
        usuario.email = novo_email
        usuario.papel = novo_papel
        usuario.profissional_id = profissional_id
        usuario.ativo = novo_ativo
        usuario.save()
        registrar_log(
            request.user, f'Editou usuario: {usuario.email}', 'usuario', usuario.pk,
            detalhes={'papel': novo_papel, 'ativo': novo_ativo, 'profissional_id': profissional_id},
            request=request,
        )
        messages.success(request, 'Usuário atualizado.')
        return redirect('aranha:admin_usuarios')

    return _render_form(request, 'editar', usuario)


@staff_required
@require_POST
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


@staff_required
@require_POST
def admin_desativar_usuario(request, pk):
    """Desativa (soft delete) o usuario — nao permite login."""
    usuario = get_object_or_404(Usuario, pk=pk)
    if usuario.pk == request.user.pk:
        messages.error(request, 'Você não pode desativar sua própria conta.')
        return redirect('aranha:admin_usuarios')
    usuario.ativo = not usuario.ativo
    usuario.save(update_fields=['ativo'])
    acao = 'Ativou' if usuario.ativo else 'Desativou'
    registrar_log(request.user, f'{acao} usuario: {usuario.email}', 'usuario', usuario.pk, request=request)
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
