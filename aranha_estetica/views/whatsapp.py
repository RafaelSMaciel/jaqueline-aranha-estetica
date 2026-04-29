import hashlib
import hmac
import json
import logging
import os
from datetime import timedelta

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from ..models import AvaliacaoNPS, Notificacao
from ..utils.precos import mask_telefone

logger = logging.getLogger(__name__)

# Janela para correlacionar resposta WhatsApp com Notificacao NPS enviada.
NPS_JANELA_RESPOSTA = timedelta(days=7)

WHATSAPP_APP_SECRET = os.environ.get('WHATSAPP_APP_SECRET', '')


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
    logger.info('[ZENVIA] msg=%s to=%s status=%s',
                message_id, mask_telefone(to), status)
    return JsonResponse({'status': 'ok'})


@csrf_exempt
@require_http_methods(["POST"])
@ratelimit(key='ip', rate='60/m', method='POST', block=True)
def whatsapp_webhook(request):
    """
    Webhook para receber respostas de notificacoes do WhatsApp.
    Processa confirmacoes, cancelamentos e notas NPS.
    """
    if not _verify_signature(request):
        logger.warning('WhatsApp webhook: assinatura invalida')
        return JsonResponse({'error': 'Assinatura invalida'}, status=403)

    try:
        data = json.loads(request.body)
        telefone = data.get('from', data.get('From', '')).strip()
        mensagem = data.get('body', data.get('Body', '')).strip()

        if not mensagem:
            return JsonResponse({'error': 'Mensagem vazia'}, status=400)

        telefone_limpo = ''.join(filter(str.isdigit, telefone))

        # Processar resposta NPS (escala 0-10)
        if mensagem.strip().isdigit() and 0 <= int(mensagem.strip()) <= 10:
            nota = int(mensagem.strip())
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
                    status_envio='ENVIADO',
                    criado_em__gte=timezone.now() - NPS_JANELA_RESPOSTA,
                    atendimento__cliente__telefone__in=candidatos,
                )
                .select_related('atendimento')
                .order_by('-criado_em')
                .first()
            )
            if notif:
                avaliacao, criada = AvaliacaoNPS.objects.get_or_create(
                    atendimento=notif.atendimento,
                    defaults={'nota': nota},
                )
                if criada:
                    logger.info(
                        'NPS registrado via WhatsApp: atendimento=%s nota=%s',
                        notif.atendimento_id, nota,
                    )

        logger.info('WhatsApp webhook: mensagem de %s', mask_telefone(telefone_limpo))

        return JsonResponse({'status': 'ok'})

    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalido'}, status=400)
    except Exception as e:
        logger.error(f'Erro no webhook WhatsApp: {e}', exc_info=True)
        return JsonResponse({'error': 'Erro interno'}, status=500)

