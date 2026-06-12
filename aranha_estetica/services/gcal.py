"""Google Calendar sync (scaffolding).

Bidirectional sync entre Atendimento e Google Calendar do profissional.

ENV vars necessarias:
  GOOGLE_OAUTH_CLIENT_ID
  GOOGLE_OAUTH_CLIENT_SECRET
  GOOGLE_OAUTH_REDIRECT_URI

Setup:
  1. Console Google Cloud -> Credentials -> OAuth 2.0 Client ID (Web)
  2. Authorized redirect URI: https://<dominio>/painel/integrations/google/callback/
  3. Habilitar Calendar API no projeto
  4. pip install google-auth google-auth-oauthlib google-api-python-client

Uso:
  from .gcal import build_authorization_url, handle_oauth_callback,
                    push_atendimento, pull_eventos_externos
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import Optional, Tuple, TYPE_CHECKING

from django.utils import timezone

if TYPE_CHECKING:
    from ..models import Atendimento, Profissional

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/calendar']
GOOGLE_OAUTH_CLIENT_ID = os.environ.get('GOOGLE_OAUTH_CLIENT_ID', '')
GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET', '')
GOOGLE_OAUTH_REDIRECT_URI = os.environ.get('GOOGLE_OAUTH_REDIRECT_URI', '')

# Janela default de pull (passado/futuro a partir de "agora")
PULL_JANELA_PASSADO = timedelta(days=1)
PULL_JANELA_FUTURO = timedelta(days=60)


def _flow():
    """Lazy import — google libs sao opcionais."""
    from google_auth_oauthlib.flow import Flow
    return Flow.from_client_config(
        client_config={
            'web': {
                'client_id': GOOGLE_OAUTH_CLIENT_ID,
                'client_secret': GOOGLE_OAUTH_CLIENT_SECRET,
                'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
                'token_uri': 'https://oauth2.googleapis.com/token',
                'redirect_uris': [GOOGLE_OAUTH_REDIRECT_URI],
            },
        },
        scopes=SCOPES,
        redirect_uri=GOOGLE_OAUTH_REDIRECT_URI,
    )


def gcal_disponivel() -> bool:
    """Retorna True se libs google estiverem instaladas e env vars setadas."""
    if not (GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET):
        return False
    try:
        import google_auth_oauthlib  # noqa: F401
        return True
    except ImportError:
        return False


def build_authorization_url(state: str) -> Optional[str]:
    """Gera URL de consentimento OAuth para o profissional autorizar GCal."""
    if not gcal_disponivel():
        return None
    flow = _flow()
    auth_url, _ = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent',
        state=state,
    )
    return auth_url


def handle_oauth_callback(profissional: 'Profissional', code: str) -> Tuple[bool, str]:
    """Troca code por tokens, persiste refresh_token no profissional.

    Returns:
        (True, 'ok') ou (False, motivo).
    """
    if not gcal_disponivel():
        return False, 'libs nao disponiveis'
    flow = _flow()
    try:
        flow.fetch_token(code=code)
    except (ConnectionError, TimeoutError) as exc:
        logger.warning(
            'gcal_oauth_network_error',
            extra={'profissional_id': profissional.pk, 'error': str(exc)},
        )
        return False, 'network'
    except (ValueError, KeyError) as exc:
        logger.exception(
            'gcal_oauth_payload_error',
            extra={'profissional_id': profissional.pk, 'error': str(exc)},
        )
        return False, 'payload'
    creds = flow.credentials
    profissional.gcal_refresh_token = creds.refresh_token or profissional.gcal_refresh_token
    profissional.save(update_fields=['gcal_refresh_token'])
    return True, 'ok'


def _service(profissional: 'Profissional'):
    """Retorna client da Calendar API ou None se nao configurado."""
    if not profissional.gcal_refresh_token:
        return None
    if not gcal_disponivel():
        return None
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(
        None,
        refresh_token=profissional.gcal_refresh_token,
        client_id=GOOGLE_OAUTH_CLIENT_ID,
        client_secret=GOOGLE_OAUTH_CLIENT_SECRET,
        token_uri='https://oauth2.googleapis.com/token',
        scopes=SCOPES,
    )
    return build('calendar', 'v3', credentials=creds, cache_discovery=False)


def push_atendimento(atendimento: 'Atendimento') -> bool:
    """Cria/atualiza evento no Google Calendar do profissional.

    Returns:
        True se criado/atualizado. False em qualquer falha.
    """
    prof = atendimento.profissional
    svc = _service(prof)
    if not svc:
        return False
    body = {
        'summary': f'{atendimento.cliente.nome} - {atendimento.procedimento.nome}',
        'description': f'Status: {atendimento.get_status_display()}',
        'start': {'dateTime': atendimento.data_hora_inicio.isoformat(), 'timeZone': 'America/Sao_Paulo'},
        'end': {'dateTime': atendimento.data_hora_fim.isoformat(), 'timeZone': 'America/Sao_Paulo'},
        'extendedProperties': {'private': {'aranha_atendimento_id': str(atendimento.pk)}},
    }
    try:
        svc.events().insert(calendarId=prof.gcal_calendar_id or 'primary', body=body).execute()
        return True
    except (ConnectionError, TimeoutError) as exc:
        logger.warning(
            'gcal_push_network_error',
            extra={'atendimento_id': atendimento.pk, 'error': str(exc)},
        )
        return False
    except Exception as exc:  # googleapiclient.errors.HttpError + RefreshError
        # libs google levantam classes proprias (HttpError, RefreshError) —
        # captura larga aqui evita import condicional. Logamos e seguimos.
        logger.exception(
            'gcal_push_api_error',
            extra={'atendimento_id': atendimento.pk, 'error': str(exc)},
        )
        return False


def pull_eventos_externos(profissional: 'Profissional') -> int:
    """Importa eventos do GCal como BloqueioAgenda (sem aranha_atendimento_id).

    Returns:
        Numero de bloqueios importados (zero em caso de falha).
    """
    from ..models import BloqueioAgenda
    svc = _service(profissional)
    if not svc:
        return 0
    agora = timezone.now()
    inicio = agora - PULL_JANELA_PASSADO
    fim = agora + PULL_JANELA_FUTURO
    try:
        events = svc.events().list(
            calendarId=profissional.gcal_calendar_id or 'primary',
            timeMin=inicio.isoformat(),
            timeMax=fim.isoformat(),
            singleEvents=True,
            orderBy='startTime',
        ).execute()
    except (ConnectionError, TimeoutError) as exc:
        logger.warning(
            'gcal_pull_network_error',
            extra={'profissional_id': profissional.pk, 'error': str(exc)},
        )
        return 0
    except Exception as exc:  # HttpError, RefreshError, etc
        logger.exception(
            'gcal_pull_api_error',
            extra={'profissional_id': profissional.pk, 'error': str(exc)},
        )
        return 0
    importados = 0
    for ev in events.get('items', []):
        priv = ev.get('extendedProperties', {}).get('private', {})
        if priv.get('aranha_atendimento_id'):
            continue  # ignora proprios eventos
        start = ev.get('start', {}).get('dateTime')
        end = ev.get('end', {}).get('dateTime')
        if not start or not end:
            continue
        from django.utils.dateparse import parse_datetime
        s, e = parse_datetime(start), parse_datetime(end)
        if not s or not e:
            continue
        BloqueioAgenda.objects.get_or_create(
            profissional=profissional,
            data_hora_inicio=s,
            data_hora_fim=e,
            defaults={'motivo': f'gcal:{ev.get("summary", "evento externo")[:80]}'},
        )
        importados += 1
    profissional.gcal_ultimo_sync_em = agora
    profissional.save(update_fields=['gcal_ultimo_sync_em'])
    return importados
