import hashlib
import hmac
import json
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from ...models import AvaliacaoNPS, Notificacao
from ...utils.pii import mask_telefone
from ...utils.seguranca import client_ip, segredo_confere

logger = logging.getLogger(__name__)

# Janela para correlacionar resposta WhatsApp com Notificacao NPS enviada.
NPS_JANELA_RESPOSTA = timedelta(days=7)


def _verify_signature(request):
    """Verifica X-Hub-Signature-256 da Meta Business API.

    SEGURANCA: se o secret nao estiver configurado, negamos SEMPRE (nao ha
    fallback DEBUG). Isto evita que uma variavel de ambiente ausente em
    producao transforme o webhook em endpoint aberto.
    """
    secret = settings.WHATSAPP_APP_SECRET
    if not secret:
        logger.error('WHATSAPP_APP_SECRET nao configurado — webhook rejeitando requisicoes')
        return False

    signature = request.headers.get('X-Hub-Signature-256', '')
    if not signature.startswith('sha256='):
        return False

    expected = hmac.new(
        secret.encode(),
        request.body,
        hashlib.sha256,
    ).hexdigest()

    return segredo_confere(expected, signature[7:])


def _ip_in_allowlist(ip: str, allowlist: list) -> bool:
    import ipaddress
    if not ip or not allowlist:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for entry in allowlist:
        try:
            if '/' in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            elif addr == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False


@csrf_exempt
@require_http_methods(["POST"])
@ratelimit(key='ip', rate='120/m', method='POST', block=True)
def zenvia_sms_webhook(request):
    """Recebe status de entrega da Zenvia.

    Defesa em camadas:
      1. allowlist de IP/CIDR (se ZENVIA_ALLOWED_IPS configurado);
      2. segredo compartilhado no header X-Zenvia-Signature, o valor configurado na assinatura do
         webhook na Zenvia (obrigatorio se ZENVIA_WEBHOOK_SECRET definido). O corpo NAO e assinado:
         e um segredo estatico, comparado em tempo constante;
      3. sem segredo E sem allowlist = recusa fora de DEBUG (503).
    """
    ip = client_ip(request)
    allowlist = settings.ZENVIA_ALLOWED_IPS
    secret = settings.ZENVIA_WEBHOOK_SECRET

    if allowlist and not _ip_in_allowlist(ip, allowlist):
        logger.warning('Zenvia webhook: IP %s nao permitido', ip)
        return JsonResponse({'error': 'ip nao permitido'}, status=403)

    if not secret and not allowlist and not settings.DEBUG:
        logger.error('Zenvia webhook: sem secret E sem allowlist em prod — rejeitando')
        return JsonResponse({'error': 'webhook nao configurado'}, status=503)

    if secret and not segredo_confere(secret, request.headers.get('X-Zenvia-Signature', '')):
        logger.warning('Zenvia webhook: segredo invalido')
        return JsonResponse({'error': 'assinatura invalida'}, status=403)

    try:
        data = json.loads(request.body)
    except ValueError:  # JSONDecodeError e corpo que nao e UTF-8
        return JsonResponse({'error': 'json invalido'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'error': 'json invalido'}, status=400)

    message_id = data.get('messageId') or data.get('id', '')
    status_info = data.get('messageStatus')
    status = (status_info.get('code') if isinstance(status_info, dict) else None) or data.get('status', '')
    to = data.get('to', '')
    logger.info(
        'zenvia_status_callback',
        extra={'message_id': message_id, 'to_mask': mask_telefone(to), 'status': status},
    )
    return JsonResponse({'status': 'ok'})


@csrf_exempt
@require_http_methods(["GET", "POST"])
@ratelimit(key='ip', rate='60/m', method='POST', block=True)
def whatsapp_webhook(request):
    """
    Webhook do WhatsApp (Meta Cloud API).
    GET = handshake de verificacao (hub.challenge); POST = eventos (respostas/NPS).
    """
    # Handshake da Meta: ecoa hub.challenge quando o verify_token confere (tempo constante).
    if request.method == 'GET':
        if (request.GET.get('hub.mode') == 'subscribe'
                and segredo_confere(settings.WHATSAPP_VERIFY_TOKEN, request.GET.get('hub.verify_token', ''))):
            return HttpResponse(request.GET.get('hub.challenge', ''), content_type='text/plain')
        return HttpResponse('Forbidden', status=403)

    if not _verify_signature(request):
        logger.warning('WhatsApp webhook: assinatura invalida')
        return JsonResponse({'error': 'Assinatura invalida'}, status=403)

    try:
        data = json.loads(request.body)
    except ValueError:
        return JsonResponse({'error': 'JSON invalido'}, status=400)

    # Evento assinado sempre recebe 200: status (sent/delivered/read) e
    # mensagens nao-NPS sao ignorados. Responder 4xx/5xx faz a Meta reenviar
    # e, persistindo, desativar o webhook.
    try:
        for telefone, texto in _mensagens_recebidas(data):
            _processar_resposta_nps(telefone, texto)
    except Exception as e:  # noqa: BLE001
        logger.error('whatsapp_webhook_erro: %s', type(e).__name__, exc_info=True)
    return JsonResponse({'status': 'ok'})


def _texto_mensagem(msg: dict) -> str:
    """Texto de uma mensagem da Cloud API (texto livre ou resposta de botao)."""
    tipo = msg.get('type')
    if tipo == 'text':
        return ((msg.get('text') or {}).get('body') or '').strip()
    if tipo == 'button':
        return ((msg.get('button') or {}).get('text') or '').strip()
    if tipo == 'interactive':
        inter = msg.get('interactive') or {}
        resposta = inter.get('button_reply') or inter.get('list_reply') or {}
        return (resposta.get('title') or resposta.get('id') or '').strip()
    return ''


def _mensagens_recebidas(data):
    """Gera (telefone, texto) do payload da Meta Cloud API.

    Formato: {'object': 'whatsapp_business_account', 'entry': [{'changes':
    [{'value': {'messages': [{'from': '55...', 'type': 'text',
    'text': {'body': '9'}}], 'statuses': [...]}}]}]}
    """
    if not isinstance(data, dict):
        return
    for entry in data.get('entry') or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get('changes') or []:
            valor = (change or {}).get('value') if isinstance(change, dict) else None
            if not isinstance(valor, dict):
                continue
            for msg in valor.get('messages') or []:
                if not isinstance(msg, dict):
                    continue
                # so digitos ASCII (str.isdigit deixaria passar '²' e afins)
                telefone = re.sub(r'[^0-9]', '', str(msg.get('from') or ''))
                texto = _texto_mensagem(msg)
                if telefone and texto:
                    yield telefone, texto


def _processar_resposta_nps(telefone_limpo: str, mensagem: str) -> None:
    """Resposta 0-10 vira AvaliacaoNPS do ultimo NPS enviado a este telefone."""
    logger.info('WhatsApp webhook: mensagem de %s', mask_telefone(telefone_limpo))
    # fullmatch: isdigit() aceitava '²' (int() -> ValueError) e '07'
    if not re.fullmatch(r'10|[0-9]', mensagem):
        return
    nota = int(mensagem)
    # SEGURANCA: match exato por telefone (com e sem codigo do pais BR 55)
    # para evitar colisao entre clientes diferentes (anti-IDOR).
    candidatos = [telefone_limpo]
    if telefone_limpo.startswith('55') and len(telefone_limpo) > 11:
        candidatos.append(telefone_limpo[2:])  # sem codigo do pais
    elif len(telefone_limpo) <= 11:
        candidatos.append('55' + telefone_limpo)  # com codigo do pais
    notif = (
        Notificacao.objects
        .filter(
            tipo=Notificacao.Tipo.NPS,
            canal=Notificacao.Canal.WHATSAPP,
            status=Notificacao.Status.ENVIADO,
            criado_em__gte=timezone.now() - NPS_JANELA_RESPOSTA,
            atendimento__cliente__telefone__in=candidatos,
        )
        .select_related('atendimento')
        .order_by('-criado_em')
        .first()
    )
    if not notif:
        return
    _avaliacao, criada = AvaliacaoNPS.objects.get_or_create(
        atendimento=notif.atendimento,
        defaults={'nota': nota},
    )
    if criada:
        logger.info(
            'NPS registrado via WhatsApp: atendimento=%s nota=%s',
            notif.atendimento_id, nota,
        )

