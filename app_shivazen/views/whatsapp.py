import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

logger = logging.getLogger(__name__)

# WhatsApp — Webhook API (apenas notificacoes, sem chatbot)
# Integracao com WhatsApp Business API (Meta)


@csrf_exempt
@require_http_methods(["POST"])
@ratelimit(key='ip', rate='60/m', method='POST', block=True)
def whatsapp_webhook(request):
    """
    Webhook para receber respostas de notificacoes do WhatsApp.
    Processa confirmacoes, cancelamentos e notas NPS.
    """
    try:
        data = json.loads(request.body)
        telefone = data.get('from', data.get('From', '')).strip()
        mensagem = data.get('body', data.get('Body', '')).strip()

        if not mensagem:
            return JsonResponse({'error': 'Mensagem vazia'}, status=400)

        telefone_limpo = ''.join(filter(str.isdigit, telefone))

        # Processar resposta NPS (nota 1-5)
        if mensagem.strip() in ['1', '2', '3', '4', '5']:
            from ..models import AvaliacaoNPS, Cliente
            nota = int(mensagem.strip())
            cliente = Cliente.objects.filter(telefone__icontains=telefone_limpo).first()
            if cliente:
                avaliacao = AvaliacaoNPS.objects.filter(
                    atendimento__cliente=cliente,
                    nota=0
                ).order_by('-data_avaliacao').first()
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
    Verificacao de webhook (handshake) — Meta Business API.
    """
    mode = request.GET.get('hub.mode', '')
    token = request.GET.get('hub.verify_token', '')
    challenge = request.GET.get('hub.challenge', '')

    VERIFY_TOKEN = 'shivazen_whatsapp_verify_2024'

    if mode == 'subscribe' and token == VERIFY_TOKEN:
        return JsonResponse(int(challenge) if challenge.isdigit() else challenge, safe=False)

    return JsonResponse({'error': 'Verificacao falhou'}, status=403)
