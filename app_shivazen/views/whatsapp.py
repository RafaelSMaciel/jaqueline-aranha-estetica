import hashlib
import hmac
import json
import logging
import os

from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

logger = logging.getLogger(__name__)

WHATSAPP_APP_SECRET = os.environ.get('WHATSAPP_APP_SECRET', '')
WHATSAPP_VERIFY_TOKEN = os.environ.get('WHATSAPP_VERIFY_TOKEN', '')


def _verify_signature(request):
    """Verifica X-Hub-Signature-256 da Meta Business API."""
    if not WHATSAPP_APP_SECRET:
        # Dev sem secret configurado — aceitar (apenas em DEBUG)
        from django.conf import settings
        return settings.DEBUG

    signature = request.headers.get('X-Hub-Signature-256', '')
    if not signature.startswith('sha256='):
        return False

    expected = hmac.new(
        WHATSAPP_APP_SECRET.encode(),
        request.body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(signature[7:], expected)


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
            from ..models import AvaliacaoNPS, Cliente
            nota = int(mensagem.strip())
            cliente = Cliente.objects.filter(telefone__icontains=telefone_limpo).first()
            if cliente:
                avaliacao = AvaliacaoNPS.objects.filter(
                    atendimento__cliente=cliente,
                    nota=0
                ).order_by('-criado_em').first()
                if avaliacao:
                    avaliacao.nota = nota
                    avaliacao.save()
                    logger.info(f'NPS registrado: cliente {cliente.pk}, nota {nota}')

        logger.info(f'WhatsApp webhook: mensagem de ***{telefone_limpo[-4:] if len(telefone_limpo) > 4 else "????"}')

        return JsonResponse({'status': 'ok'})

    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalido'}, status=400)
    except Exception as e:
        logger.error(f'Erro no webhook WhatsApp: {e}', exc_info=True)
        return JsonResponse({'error': 'Erro interno'}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def whatsapp_webhook_verify(request):
    """
    Verificacao de webhook (handshake) -- Meta Business API.
    """
    mode = request.GET.get('hub.mode', '')
    token = request.GET.get('hub.verify_token', '')
    challenge = request.GET.get('hub.challenge', '')

    if not WHATSAPP_VERIFY_TOKEN:
        logger.error('WHATSAPP_VERIFY_TOKEN nao configurado')
        return JsonResponse({'error': 'Token nao configurado'}, status=500)

    if mode == 'subscribe' and token == WHATSAPP_VERIFY_TOKEN:
        return HttpResponse(challenge, content_type='text/plain')

    return JsonResponse({'error': 'Verificacao falhou'}, status=403)
