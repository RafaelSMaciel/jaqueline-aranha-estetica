"""Web Push (VAPID) — envia notificacoes browser-native.

Requer env vars:
  WEBPUSH_VAPID_PUBLIC_KEY  (P-256 public, base64url)
  WEBPUSH_VAPID_PRIVATE_KEY (P-256 private, base64url)
  WEBPUSH_VAPID_CLAIMS_EMAIL (mailto:...)

Gerar chaves localmente:
  py_vapid Vapid().generate_keys() ou usar https://web-push-codelab.glitch.me/
"""
import json
import logging
import os

from django.utils import timezone

logger = logging.getLogger(__name__)


def get_vapid_public_key():
    return os.environ.get('WEBPUSH_VAPID_PUBLIC_KEY', '')


def _claims():
    return {
        'sub': os.environ.get('WEBPUSH_VAPID_CLAIMS_EMAIL', 'mailto:rafelsebas@gmail.com'),
    }


def send_push(subscription, payload):
    """Envia push p/ subscription. Retorna True se OK.

    payload: dict com {head, body, url, icon}.
    Marca subscription inativa em 410/404 (gone).
    """
    private_key = os.environ.get('WEBPUSH_VAPID_PRIVATE_KEY', '')
    if not private_key:
        logger.warning('Webpush: VAPID private key ausente')
        return False

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.warning('Webpush: pywebpush nao instalado')
        return False

    sub_info = {
        'endpoint': subscription.endpoint,
        'keys': {'p256dh': subscription.p256dh, 'auth': subscription.auth},
    }

    try:
        webpush(
            subscription_info=sub_info,
            data=json.dumps(payload),
            vapid_private_key=private_key,
            vapid_claims=_claims(),
        )
        return True
    except WebPushException as e:
        status = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
        if status in (404, 410):
            subscription.ativo = False
            subscription.ultima_falha_em = timezone.now()
            subscription.save(update_fields=['ativo', 'ultima_falha_em'])
            logger.info('Webpush: subscription %s expirou (HTTP %s)', subscription.pk, status)
        else:
            logger.warning('Webpush erro: %s', e)
        return False
    except Exception as e:
        logger.exception('Webpush envio falhou: %s', e)
        return False


def send_push_to_user(user, payload):
    """Envia push p/ todas subscriptions ativas do usuario. Retorna count enviadas."""
    from ..models import WebPushSubscription
    enviadas = 0
    for sub in WebPushSubscription.objects.filter(user=user, ativo=True):
        if send_push(sub, payload):
            enviadas += 1
    return enviadas
