"""Endpoints Web Push: subscribe, unsubscribe, public key."""
import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST, require_GET

from ..models import AssinaturaPush
from ..services.push import get_vapid_public_key

logger = logging.getLogger(__name__)


@require_GET
def webpush_public_key(request):
    """Retorna VAPID public key para o front subscrever."""
    return JsonResponse({'public_key': get_vapid_public_key()})


def _payload_json(request):
    """Corpo JSON como dict (None se invalido ou nao-objeto: '[]', 'null', 'x')."""
    try:
        payload = json.loads(request.body)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _texto(valor) -> str:
    return valor.strip() if isinstance(valor, str) else ''


def _falta_2fa(request) -> bool:
    """Sessao que ainda deve o desafio 2FA (mesma regra do Enforce2FAMiddleware).

    O push leva nome de cliente/procedimento/horario: sessao so com senha de
    quem tem TOTP (ou ADMIN com cadastro de 2FA pendente) nao assina.
    """
    from django.db import OperationalError, ProgrammingError

    from ..utils import dois_fatores

    if request.session.get(dois_fatores.SESSION_CADASTRO_PENDENTE):
        return True
    if dois_fatores.sessao_verificada(request):
        return False
    try:
        return dois_fatores.tem_2fa(request.user)
    except (ImportError, OperationalError, ProgrammingError):
        return False


@require_POST
@csrf_protect
@login_required
def webpush_subscribe(request):
    if _falta_2fa(request):
        return JsonResponse({'ok': False, 'detail': '2fa_required'}, status=403)
    payload = _payload_json(request)
    if payload is None:
        return JsonResponse({'ok': False, 'erro': 'payload invalido'}, status=400)
    keys = payload.get('keys') if isinstance(payload.get('keys'), dict) else {}
    endpoint = _texto(payload.get('endpoint'))
    p256dh = _texto(keys.get('p256dh'))
    auth = _texto(keys.get('auth'))

    if not endpoint or not p256dh or not auth:
        return JsonResponse({'ok': False, 'erro': 'campos obrigatorios ausentes'}, status=400)
    # Push services reais sao sempre https; evita o servidor fazer request p/ URL arbitraria.
    if not endpoint.startswith('https://') or len(endpoint) > 600:
        return JsonResponse({'ok': False, 'erro': 'endpoint invalido'}, status=400)

    user_agent = request.META.get('HTTP_USER_AGENT', '')[:300]

    sub, created = AssinaturaPush.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            'user': request.user,
            'p256dh': p256dh,
            'auth': auth,
            'user_agent': user_agent,
            'ativo': True,
            'ultima_falha_em': None,
        },
    )
    return JsonResponse({'ok': True, 'created': created, 'id': sub.pk})


@require_POST
@csrf_protect
@login_required
def webpush_unsubscribe(request):
    payload = _payload_json(request)
    if payload is None:
        return JsonResponse({'ok': False, 'erro': 'payload invalido'}, status=400)
    endpoint = _texto(payload.get('endpoint'))
    if not endpoint:
        return JsonResponse({'ok': False, 'erro': 'endpoint ausente'}, status=400)

    deleted, _ = AssinaturaPush.objects.filter(
        user=request.user, endpoint=endpoint
    ).delete()
    return JsonResponse({'ok': True, 'deleted': deleted})
