import hashlib
import hmac
import json
import logging
import os
from datetime import timedelta

from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from ..models import AvaliacaoNPS, Notificacao
from ..utils.pii import mask_telefone

logger = logging.getLogger(__name__)

# Janela para correlacionar resposta WhatsApp com Notificacao NPS enviada.
NPS_JANELA_RESPOSTA = timedelta(days=7)

WHATSAPP_APP_SECRET = os.environ.get('WHATSAPP_APP_SECRET', '')
# Token do handshake de verificacao do webhook (GET hub.challenge da Meta)
WHATSAPP_VERIFY_TOKEN = os.environ.get('WHATSAPP_VERIFY_TOKEN', '')


def _verify_signature(request):
    """Verifica X-Hub-Signature-256 da Meta Business API.

    SEGURANCA: se o secret nao estiver configurado, negamos SEMPRE (nao ha
    fallback DEBUG). Isto evita que uma variavel de ambiente ausente em
    producao transforme o webhook em endpoint aberto.
    """
    if not WHATSAPP_APP_SECRET:
        logger.error('WHATSAPP_APP_SECRET nao configurado — webhook rejeitando requisicoes')
        return False

    signature = request.headers.get('X-Hub-Signature-256', '')
    if not signature.startswith('sha256='):
        return False

    expected = hmac.new(
        WHATSAPP_APP_SECRET.encode(),
        request.body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(signature[7:], expected)


ZENVIA_WEBHOOK_SECRET = os.environ.get('ZENVIA_WEBHOOK_SECRET', '')
# IPs/CIDRs da Zenvia separados por virgula (consultar docs Zenvia)
ZENVIA_ALLOWED_IPS = [
    ip.strip() for ip in os.environ.get('ZENVIA_ALLOWED_IPS', '').split(',') if ip.strip()
]


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

    Defense-in-depth:
      1. IP allowlist (se configurado ZENVIA_ALLOWED_IPS)
      2. HMAC via X-Zenvia-Signature (obrigatorio se ZENVIA_WEBHOOK_SECRET setado)
      3. Sem secret E sem allowlist = rejeita em prod
    """
    from ..utils.security import client_ip
    from django.conf import settings

    ip = client_ip(request)

    if ZENVIA_ALLOWED_IPS and not _ip_in_allowlist(ip, ZENVIA_ALLOWED_IPS):
        logger.warning('Zenvia webhook: IP %s nao permitido', ip)
        return JsonResponse({'error': 'ip nao permitido'}, status=403)

    if not ZENVIA_WEBHOOK_SECRET and not ZENVIA_ALLOWED_IPS and not settings.DEBUG:
        logger.error('Zenvia webhook: sem secret E sem allowlist em prod — rejeitando')
        return JsonResponse({'error': 'webhook nao configurado'}, status=503)

    if ZENVIA_WEBHOOK_SECRET:
        provided = request.headers.get('X-Zenvia-Signature', '')
        if not hmac.compare_digest(provided, ZENVIA_WEBHOOK_SECRET):
            logger.warning('Zenvia webhook: assinatura invalida')
            return JsonResponse({'error': 'assinatura invalida'}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'json invalido'}, status=400)

    message_id = data.get('messageId') or data.get('id', '')
    status = data.get('messageStatus', {}).get('code') or data.get('status', '')
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
    # Handshake da Meta: ecoa hub.challenge quando o verify_token confere.
    if request.method == 'GET':
        if (request.GET.get('hub.mode') == 'subscribe'
                and WHATSAPP_VERIFY_TOKEN
                and request.GET.get('hub.verify_token') == WHATSAPP_VERIFY_TOKEN):
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
                telefone = ''.join(filter(str.isdigit, str(msg.get('from') or '')))
                texto = _texto_mensagem(msg)
                if telefone and texto:
                    yield telefone, texto


def _processar_resposta_nps(telefone_limpo: str, mensagem: str) -> None:
    """Resposta 0-10 vira AvaliacaoNPS do ultimo NPS enviado a este telefone."""
    logger.info('WhatsApp webhook: mensagem de %s', mask_telefone(telefone_limpo))
    if not (mensagem.isdigit() and 0 <= int(mensagem) <= 10):
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
            tipo='NPS',
            canal='WHATSAPP',
            status='ENVIADO',
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

