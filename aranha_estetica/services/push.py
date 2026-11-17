"""Web Push (VAPID) — envia notificacoes browser-native.

Configuracao (settings, env de mesmo nome):
  WEBPUSH_VAPID_PUBLIC_KEY  (P-256 public, base64url)
  WEBPUSH_VAPID_PRIVATE_KEY (P-256 private, base64url)
  WEBPUSH_VAPID_CLAIMS_EMAIL (mailto:... — contato da clinica p/ os push services; sem ele vale o
                              ADMIN_EMAIL e depois o CLINIC_EMAIL da marca)

Gerar chaves localmente:
  py_vapid Vapid().generate_keys() ou usar https://web-push-codelab.glitch.me/
"""
from __future__ import annotations

import json
import logging
from typing import Any, Mapping, TYPE_CHECKING
from urllib.parse import urlsplit

from django.conf import settings
from django.utils import timezone

from ..utils.branding import get_branding

if TYPE_CHECKING:
    from ..models import Usuario, AssinaturaPush

logger = logging.getLogger(__name__)

# Push services dos navegadores (Chrome/Edge/Opera, Firefox, Safari, Windows). O servidor faz POST
# para o endpoint da assinatura: fora desta lista o envio viraria request p/ um host arbitrario.
PUSH_HOSTS = frozenset({
    'fcm.googleapis.com', 'android.googleapis.com',
    'updates.push.services.mozilla.com', 'web.push.apple.com',
})
PUSH_SUFIXOS = ('.notify.windows.com', '.push.apple.com')
ENDPOINT_MAX = 600


def endpoint_de_push_valido(endpoint: str) -> bool:
    """https, porta padrao, sem credencial na URL e host de um push service conhecido."""
    if not endpoint or len(endpoint) > ENDPOINT_MAX:
        return False
    try:
        partes = urlsplit(endpoint)
        porta = partes.port
    except ValueError:
        return False
    if partes.scheme != 'https' or partes.username or partes.password or porta not in (None, 443):
        return False
    host = (partes.hostname or '').lower()
    return host in PUSH_HOSTS or host.endswith(PUSH_SUFIXOS)


def get_vapid_public_key() -> str:
    """Retorna chave publica VAPID (base64url) ou string vazia se nao configurada."""
    return settings.WEBPUSH_VAPID_PUBLIC_KEY


def _claims() -> dict[str, str] | None:
    """Claim 'sub' do VAPID: contato da clinica que o push service (FCM, Mozilla) usa p/ avisar de
    abuso ou falha de entrega. None sem nenhum contato configurado (o push nao sai)."""
    sub = (settings.WEBPUSH_VAPID_CLAIMS_EMAIL or settings.ADMIN_EMAIL
           or get_branding().get('CLINIC_EMAIL') or '').strip()
    if not sub:
        return None
    # py_vapid exige 'sub' comecando com mailto: ou https:// (senao VapidException em TODO envio)
    if not sub.startswith(('mailto:', 'https://')):
        sub = f'mailto:{sub}'
    return {'sub': sub}


# Roda no thread do request (on_commit do agendamento): push service lento nao
# pode segurar o worker ate o timeout do gunicorn.
WEBPUSH_TIMEOUT_SEGUNDOS = 5


def send_push(subscription: 'AssinaturaPush', payload: Mapping[str, Any]) -> bool:
    """Envia push notification para uma subscription.

    Args:
        subscription: registro AssinaturaPush do banco.
        payload: dict serializavel JSON com {head, body, url, icon}.

    Returns:
        True se entregue ao push service. False em qualquer falha.

    Side effects:
        Em status 404/410 (Gone) marca subscription como inativa.
    """
    private_key = settings.WEBPUSH_VAPID_PRIVATE_KEY
    if not private_key:
        logger.warning('webpush_vapid_key_missing')
        return False
    claims = _claims()
    if claims is None:
        logger.warning('webpush_sem_contato')
        return False

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.warning('webpush_pywebpush_missing')
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
            vapid_claims=claims,
            timeout=WEBPUSH_TIMEOUT_SEGUNDOS,
        )
        return True
    except WebPushException as exc:
        status = getattr(exc.response, 'status_code', None) if hasattr(exc, 'response') else None
        if status in (404, 410):
            subscription.ativo = False
            subscription.ultima_falha_em = timezone.now()
            subscription.save(update_fields=['ativo', 'ultima_falha_em'])
            logger.info(
                'webpush_subscription_expired',
                extra={'sub_id': subscription.pk, 'status': status},
            )
        else:
            logger.warning(
                'webpush_send_failed',
                extra={'sub_id': subscription.pk, 'status': status, 'error': str(exc)},
            )
        return False
    except Exception as exc:  # noqa: BLE001 — requests.RequestException, VapidException etc.
        logger.warning(
            'webpush_network_error',
            extra={'sub_id': subscription.pk, 'error': type(exc).__name__},
        )
        return False


def send_push_to_user(user: 'Usuario', payload: Mapping[str, Any]) -> int:
    """Envia push para todas inscricoes ativas do usuario.

    Args:
        user: destinatario.
        payload: dict serializavel JSON com {head, body, url, icon}.

    Returns:
        Numero de entregas bem-sucedidas (inscricoes inativas ignoradas).
    """
    from ..models import AssinaturaPush
    enviadas = 0
    subscriptions = (
        AssinaturaPush.objects
        .filter(user=user, ativo=True)
        .only('id', 'endpoint', 'p256dh', 'auth', 'ativo', 'ultima_falha_em')
    )
    for sub in subscriptions:
        if send_push(sub, payload):
            enviadas += 1
    return enviadas
