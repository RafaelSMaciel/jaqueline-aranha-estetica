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
import logging
import os
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/calendar']
GOOGLE_OAUTH_CLIENT_ID = os.environ.get('GOOGLE_OAUTH_CLIENT_ID', '')
GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET', '')
GOOGLE_OAUTH_REDIRECT_URI = os.environ.get('GOOGLE_OAUTH_REDIRECT_URI', '')


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


def gcal_disponivel():
    if not (GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET):
        return False
    try:
        import google_auth_oauthlib  # noqa
        return True
    except ImportError:
        return False


def build_authorization_url(state):
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


def handle_oauth_callback(profissional, code):
    """Troca code por tokens, salva refresh_token no profissional."""
    if not gcal_disponivel():
        return False, 'libs nao disponiveis'
    flow = _flow()
    try:
        flow.fetch_token(code=code)
    except Exception as e:
        logger.exception('gcal callback falhou: %s', e)
        return False, str(e)
    creds = flow.credentials
    profissional.gcal_refresh_token = creds.refresh_token or profissional.gcal_refresh_token
    profissional.save(update_fields=['gcal_refresh_token'])
    return True, 'ok'


def _service(profissional):
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


def push_atendimento(atendimento):
    """Cria/atualiza evento no Google Calendar do profissional."""
    prof = atendimento.profissional
    svc = _service(prof)
    if not svc:
        return False
    body = {
        'summary': f'{atendimento.cliente.nome_completo} - {atendimento.procedimento.nome}',
        'description': f'Status: {atendimento.get_status_display()}',
        'start': {'dateTime': atendimento.data_hora_inicio.isoformat(), 'timeZone': 'America/Sao_Paulo'},
        'end': {'dateTime': atendimento.data_hora_fim.isoformat(), 'timeZone': 'America/Sao_Paulo'},
        'extendedProperties': {'private': {'shivazen_atendimento_id': str(atendimento.pk)}},
    }
    try:
        svc.events().insert(calendarId=prof.gcal_calendar_id or 'primary', body=body).execute()
        return True
    except Exception as e:
        logger.exception('gcal push falhou: %s', e)
        return False


def pull_eventos_externos(profissional):
    """Importa eventos do GCal como BloqueioAgenda (sem shivazen_atendimento_id)."""
    from ..models import BloqueioAgenda
    svc = _service(profissional)
    if not svc:
        return 0
    agora = timezone.now()
    inicio = agora - timedelta(days=1)
    fim = agora + timedelta(days=60)
    try:
        events = svc.events().list(
            calendarId=profissional.gcal_calendar_id or 'primary',
            timeMin=inicio.isoformat(),
            timeMax=fim.isoformat(),
            singleEvents=True,
            orderBy='startTime',
        ).execute()
    except Exception as e:
        logger.exception('gcal pull falhou: %s', e)
        return 0
    importados = 0
    for ev in events.get('items', []):
        priv = ev.get('extendedProperties', {}).get('private', {})
        if priv.get('shivazen_atendimento_id'):
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
