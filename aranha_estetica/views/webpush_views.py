"""Endpoints Web Push: subscribe, unsubscribe, public key."""
import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST, require_GET

from ..models import WebPushSubscription
from ..services.push import get_vapid_public_key

logger = logging.getLogger(__name__)


@require_GET
def webpush_public_key(request):
    """Retorna VAPID public key para o front subscrever."""
    return JsonResponse({'public_key': get_vapid_public_key()})


@require_POST
@csrf_protect
@login_required
def webpush_subscribe(request):
    try:
        payload = json.loads(request.body)
        endpoint = payload.get('endpoint', '').strip()
        keys = payload.get('keys') or {}
        p256dh = keys.get('p256dh', '').strip()
        auth = keys.get('auth', '').strip()
    except (ValueError, KeyError, AttributeError):
        return JsonResponse({'ok': False, 'erro': 'payload invalido'}, status=400)

    if not endpoint or not p256dh or not auth:
        return JsonResponse({'ok': False, 'erro': 'campos obrigatorios ausentes'}, status=400)

    user_agent = request.META.get('HTTP_USER_AGENT', '')[:300]

    sub, created = WebPushSubscription.objects.update_or_create(
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
    try:
        payload = json.loads(request.body)
        endpoint = payload.get('endpoint', '').strip()
    except (ValueError, KeyError):
        return JsonResponse({'ok': False, 'erro': 'payload invalido'}, status=400)
    if not endpoint:
        return JsonResponse({'ok': False, 'erro': 'endpoint ausente'}, status=400)

    deleted, _ = WebPushSubscription.objects.filter(
        user=request.user, endpoint=endpoint
    ).delete()
    return JsonResponse({'ok': True, 'deleted': deleted})
