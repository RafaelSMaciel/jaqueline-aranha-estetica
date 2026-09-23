"""OTP endpoints para fluxo publico de agendamento + login Meus Agendamentos.

Canal exclusivo SMS via Zenvia. O desafio e SEMPRE preso ao telefone que
recebe o SMS: e-mail digitado nunca vira identidade (senao qualquer um
receberia no proprio celular o codigo que libera o cadastro de outra pessoa).
"""
import logging

from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from ..models import Cliente, CodigoOtp
from ..services import otp as otp_service
from ..utils.captcha import turnstile_enabled, verificar_turnstile
from ..utils.pii import mask_telefone
from ..utils.security import client_ip as _client_ip
from ..utils.sms import sms_disponivel

logger = logging.getLogger(__name__)

SESSAO_MEUS_AGENDAMENTOS = 'meus_agendamentos_telefone'


def _captcha_invalido(request):
    token = request.POST.get('cf-turnstile-response', '')
    return turnstile_enabled() and not verificar_turnstile(token, ip=_client_ip(request))


def _resposta_falha_envio(motivo):
    if motivo == 'aguarde':
        return JsonResponse({'ok': False, 'erro': 'aguarde'}, status=429)
    if motivo == 'limite_sms':
        # Quota de SMS da hora esgotada: nenhum codigo novo foi gerado, o ultimo
        # recebido continua valendo (o front sugere usa-lo ou o WhatsApp).
        return JsonResponse({'ok': False, 'erro': 'limite_sms'}, status=429)
    if motivo == 'sms_falha':
        return JsonResponse({'ok': False, 'erro': 'sms_falha'}, status=503)
    return JsonResponse({'ok': False, 'erro': motivo}, status=400)


@require_POST
@ratelimit(key='ip', rate='5/m', method='POST', block=True)
def solicitar_otp_agendamento(request):
    """AJAX: envia OTP por SMS ao telefone informado (unica identidade do booking).

    Resposta nao revela se o telefone ja e cliente (anti-enumeracao).
    """
    if _captcha_invalido(request):
        return JsonResponse({'ok': False, 'erro': 'captcha'}, status=400)

    digitos = otp_service.normalizar_telefone_br(request.POST.get('telefone'))
    if not otp_service.eh_celular_br(digitos):
        # Fixo nao recebe SMS: recusa antes de gastar quota.
        return JsonResponse({'ok': False, 'erro': 'telefone_invalido'}, status=400)

    ok, motivo, canal_usado = otp_service.solicitar_otp_telefone(
        digitos, request=request, proposito=CodigoOtp.PROPOSITO_AGENDAMENTO,
    )
    if not ok:
        if motivo != 'aguarde':
            logger.info('otp_solicitar_falhou', extra={
                'telefone': mask_telefone(digitos), 'motivo': motivo,
            })
        return _resposta_falha_envio(motivo)
    return JsonResponse({'ok': True, 'canal': canal_usado})


@require_POST
@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def verificar_otp_agendamento(request):
    """AJAX: valida o codigo do telefone; devolve pre-fill do cadastro DAQUELE telefone."""
    digitos = otp_service.normalizar_telefone_br(request.POST.get('telefone'))
    codigo = str(request.POST.get('codigo') or '').strip()
    if not digitos or not codigo:
        return JsonResponse({'ok': False, 'erro': 'dados_ausentes'}, status=400)

    ok, motivo = otp_service.verificar_otp_telefone(
        digitos, codigo, proposito=CodigoOtp.PROPOSITO_AGENDAMENTO,
    )
    if not ok:
        return JsonResponse({'ok': False, 'erro': motivo}, status=400)

    otp_service.registrar_verificacao_agendamento(request, digitos)

    cliente = Cliente.objects.filter(telefone=digitos).first()
    prefill = None
    aviso = ''
    if cliente:
        if cliente.bloqueado_online or not cliente.ativo:
            aviso = 'bloqueado_online'
        prefill = {
            'nome': cliente.nome,
            'telefone': cliente.telefone or '',
            'email': cliente.email or '',
            'data_nascimento': cliente.data_nascimento.isoformat() if cliente.data_nascimento else '',
            # Estado atual dos opt-ins: o wizard mostra os checkboxes como estao
            # (desmarcar = revogar), nunca "pre-marcados" nem zerados as cegas.
            'consents': {
                'consent_email_marketing': bool(cliente.consent_email_marketing),
                'consent_whatsapp_confirmacao': bool(cliente.consent_whatsapp_confirmacao),
                'consent_whatsapp_nps': bool(cliente.consent_whatsapp_nps),
            },
        }
    return JsonResponse({'ok': True, 'prefill': prefill, 'aviso': aviso})


def _telefone_do_identificador(identificador):
    """(digitos, cliente) p/ login do portal: aceita celular OU e-mail cadastrado.

    Com e-mail, o codigo vai SEMPRE ao telefone do cadastro (nunca a um
    telefone informado). Identificador desconhecido -> ('', None).
    """
    ident = (identificador or '').strip()
    if '@' in ident:
        cliente = (
            Cliente.objects.filter(email__iexact=ident.lower(), ativo=True)
            .exclude(telefone__isnull=True).exclude(telefone='')
            .first()
        )
        if not cliente:
            return '', None
        return otp_service.normalizar_telefone_br(cliente.telefone), cliente
    digitos = otp_service.normalizar_telefone_br(ident)
    if not digitos:
        return '', None
    return digitos, Cliente.objects.filter(telefone=digitos, ativo=True).first()


def _identificador(request):
    return str(request.POST.get('identificador') or request.POST.get('email') or '').strip()


@require_POST
@ratelimit(key='ip', rate='5/m', method='POST', block=True)
def meus_agendamentos_enviar_otp(request):
    """Envia OTP p/ login em 'Meus Agendamentos' (celular ou e-mail cadastrado).

    Resposta identica exista ou nao o cadastro (anti-enumeracao LGPD).
    """
    if _captcha_invalido(request):
        return JsonResponse({'ok': False, 'erro': 'captcha'}, status=400)

    ident = _identificador(request)
    if '@' not in ident and not otp_service.eh_celular_br(otp_service.normalizar_telefone_br(ident)):
        # Erro de formato (nao depende de existir cadastro); fixo nao recebe SMS.
        return JsonResponse({'ok': False, 'erro': 'identificador_invalido'}, status=400)

    # Checagem global do canal — nao vaza se o cadastro existe.
    if not sms_disponivel():
        return JsonResponse({'ok': False, 'erro': 'sms_falha'}, status=503)

    digitos, cliente = _telefone_do_identificador(ident)
    if cliente and digitos:
        ok, motivo, _canal = otp_service.solicitar_otp_telefone(
            digitos, request=request, proposito=CodigoOtp.PROPOSITO_LOGIN,
        )
        if not ok and motivo != 'aguarde':
            logger.warning('otp_login_sms_falha', extra={
                'telefone': mask_telefone(digitos), 'motivo': motivo,
            })
    return JsonResponse({
        'ok': True,
        'mensagem': 'Se houver cadastro, o código chegará por SMS no celular cadastrado.',
    })


@require_POST
@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def meus_agendamentos_verificar_otp(request):
    """Valida OTP; cria sessao do portal presa ao TELEFONE do cadastro."""
    digitos, _cliente = _telefone_do_identificador(_identificador(request))
    codigo = str(request.POST.get('codigo') or '').strip()
    if not codigo:
        return JsonResponse({'ok': False, 'erro': 'dados_ausentes'}, status=400)
    if not digitos:
        return JsonResponse({'ok': False, 'erro': 'expirado'}, status=400)

    ok, motivo = otp_service.verificar_otp_telefone(
        digitos, codigo, proposito=CodigoOtp.PROPOSITO_LOGIN,
    )
    if not ok:
        return JsonResponse({'ok': False, 'erro': motivo}, status=400)

    request.session.cycle_key()  # anti session-fixation
    request.session.pop('meus_agendamentos_email', None)  # chave legada
    request.session[SESSAO_MEUS_AGENDAMENTOS] = digitos
    request.session.set_expiry(3600)
    return JsonResponse({'ok': True, 'redirect': reverse('aranha:meus_agendamentos')})


def meus_agendamentos_logout(request):
    request.session.pop(SESSAO_MEUS_AGENDAMENTOS, None)
    request.session.pop('meus_agendamentos_email', None)
    return redirect('aranha:meus_agendamentos')
