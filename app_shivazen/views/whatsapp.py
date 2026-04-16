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

