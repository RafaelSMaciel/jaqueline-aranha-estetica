"""
WhatsApp Notification Service — Plataforma de Clinicas

Canal WhatsApp usado APENAS para (conforme estrategia aprovada 2026-04-18):
  - Confirmacao D-1 (lembrete com link de confirmacao/cancelamento)
  - Pesquisa NPS (24h apos atendimento REALIZADO)

Todas as demais mensagens usam EMAIL:
  OTP, confirmacao pos-agendamento, cancelamento, pacotes, fila,
  aniversario, aprovacao profissional, termos pendentes.
Notificacoes para admin usam EMAIL ou painel interno (nao WhatsApp).
"""
import os
import logging
import secrets
import time
import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# Configuracoes via variaveis de ambiente
WHATSAPP_TOKEN = os.environ.get('WHATSAPP_TOKEN', '')
WHATSAPP_PHONE_ID = os.environ.get('WHATSAPP_PHONE_ID', '')
WHATSAPP_API_URL = os.environ.get(
    'WHATSAPP_API_URL',
    f'https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_ID}/messages'
)
SITE_URL = os.environ.get('SITE_URL', 'http://127.0.0.1:8000')
CLINIC_NAME = os.environ.get('CLINIC_NAME', 'Shiva Zen')
MAX_RETRIES = 3

# Nomes dos templates aprovados no Meta Business API
TEMPLATE_CONFIRMACAO_D1 = os.environ.get('WHATSAPP_TEMPLATE_D1', 'confirmacao_d1')
TEMPLATE_NPS = os.environ.get('WHATSAPP_TEMPLATE_NPS', 'nps_pos_atendimento')


def gerar_token():
    """Gera token unico para link de confirmacao."""
    return secrets.token_urlsafe(32)


def formatar_telefone(telefone):
    """Formata telefone para padrao internacional (55...)."""
    digits = ''.join(filter(str.isdigit, telefone or ''))
    if len(digits) == 11:
        digits = '55' + digits
    elif len(digits) == 10:
        digits = '55' + digits
    return digits


def enviar_whatsapp(telefone, mensagem, _tentativa=1):
    """
    Envia mensagem free-form via WhatsApp Cloud API.
    Uso restrito a janela de 24h apos interacao do cliente.
    Para mensagens iniciadas pela clinica, usar enviar_template_whatsapp.
    Em dev (sem token), apenas loga.
    """
    telefone_formatado = formatar_telefone(telefone)

    if not WHATSAPP_TOKEN or settings.DEBUG:
        from .precos import mask_telefone
        logger.info(
            '[WHATSAPP DEV] Para: %s | Mensagem: %s...',
            mask_telefone(telefone_formatado), mensagem[:200],
        )
        return True

    try:
        headers = {
            'Authorization': f'Bearer {WHATSAPP_TOKEN}',
            'Content-Type': 'application/json',
        }
        payload = {
            'messaging_product': 'whatsapp',
            'to': telefone_formatado,
            'type': 'text',
            'text': {'body': mensagem}
        }
        response = requests.post(
            WHATSAPP_API_URL,
            json=payload,
            headers=headers,
            timeout=10
        )
        if response.status_code in (200, 201):
            from .precos import mask_telefone
            logger.info('[WHATSAPP] Enviado para %s', mask_telefone(telefone_formatado))
            return True
        elif response.status_code >= 500 and _tentativa < MAX_RETRIES:
            wait = 2 ** _tentativa
            logger.warning(
                '[WHATSAPP] Erro %d, retry %d/%d em %ds',
                response.status_code, _tentativa, MAX_RETRIES, wait,
            )
            time.sleep(wait)
            return enviar_whatsapp(telefone, mensagem, _tentativa=_tentativa + 1)
        else:
            logger.error(
                f'[WHATSAPP] Erro {response.status_code}: {response.text[:200]}'
            )
            return False
    except requests.exceptions.Timeout:
        if _tentativa < MAX_RETRIES:
            wait = 2 ** _tentativa
            logger.warning('[WHATSAPP] Timeout, retry %d/%d em %ds', _tentativa, MAX_RETRIES, wait)
            time.sleep(wait)
            return enviar_whatsapp(telefone, mensagem, _tentativa=_tentativa + 1)
        logger.error('[WHATSAPP] Timeout apos %d tentativas', MAX_RETRIES)
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f'[WHATSAPP] Falha ao enviar: {e}')
        return False


def enviar_template_whatsapp(telefone, template_name, components=None):
    """Envia mensagem via template pre-aprovado no Meta Business API.

    components: lista no formato WhatsApp Cloud API, ex:
      [{'type': 'body', 'parameters': [{'type': 'text', 'text': 'Joao'}]}]
    """
    telefone_formatado = formatar_telefone(telefone)

    if not WHATSAPP_TOKEN or settings.DEBUG:
        from .precos import mask_telefone
        logger.info(
            '[WHATSAPP DEV] Template "%s" para: %s | components: %s',
            template_name, mask_telefone(telefone_formatado), components,
        )
        return True

    try:
        headers = {
            'Authorization': f'Bearer {WHATSAPP_TOKEN}',
            'Content-Type': 'application/json',
        }
        payload = {
            'messaging_product': 'whatsapp',
            'to': telefone_formatado,
            'type': 'template',
            'template': {
                'name': template_name,
                'language': {'code': 'pt_BR'},
            }
        }
        if components:
            payload['template']['components'] = components

        response = requests.post(WHATSAPP_API_URL, json=payload, headers=headers, timeout=10)
        if response.status_code in (200, 201):
            from .precos import mask_telefone
            logger.info('[WHATSAPP] Template "%s" enviado para %s', template_name, mask_telefone(telefone_formatado))
            return True
        else:
            logger.error('[WHATSAPP] Template erro %d: %s', response.status_code, response.text[:200])
            return False
    except requests.exceptions.RequestException as e:
        logger.error('[WHATSAPP] Falha ao enviar template: %s', e)
        return False


# ─── MENSAGENS PERMITIDAS (WhatsApp apenas D-1 e NPS) ───

def enviar_confirmacao_d1(atendimento):
    """
    Lembrete/confirmacao D-1 via WhatsApp com link de confirmacao/cancelamento.
    Usa template aprovado TEMPLATE_CONFIRMACAO_D1 (categoria UTILITY).
    Parametros do template (ordem):
      {{1}} nome do cliente
      {{2}} data formatada (dd/mm/aaaa)
      {{3}} hora formatada (HH:MM)
      {{4}} procedimento
      {{5}} profissional
      {{6}} link confirmar
      {{7}} link cancelar
    """
    from ..models import Notificacao

    token = gerar_token()
    site_url = SITE_URL.rstrip('/')
    link_confirmar = f'{site_url}/confirmar/{token}/?acao=confirmar'
    link_cancelar = f'{site_url}/confirmar/{token}/?acao=cancelar'

    data_formatada = atendimento.data_hora_inicio.strftime('%d/%m/%Y')
    hora_formatada = atendimento.data_hora_inicio.strftime('%H:%M')

    components = [{
        'type': 'body',
        'parameters': [
            {'type': 'text', 'text': atendimento.cliente.nome_completo},
            {'type': 'text', 'text': data_formatada},
            {'type': 'text', 'text': hora_formatada},
            {'type': 'text', 'text': atendimento.procedimento.nome},
            {'type': 'text', 'text': atendimento.profissional.nome},
            {'type': 'text', 'text': link_confirmar},
            {'type': 'text', 'text': link_cancelar},
        ],
    }]

    sucesso = enviar_template_whatsapp(
        atendimento.cliente.telefone, TEMPLATE_CONFIRMACAO_D1, components
    )

    mensagem_preview = (
        f'[Template {TEMPLATE_CONFIRMACAO_D1}] '
        f'{atendimento.cliente.nome_completo} / '
        f'{data_formatada} {hora_formatada} / '
        f'{atendimento.procedimento.nome} c/ {atendimento.profissional.nome}'
    )

    return Notificacao.objects.create(
        atendimento=atendimento,
        tipo='LEMBRETE',
        canal='WHATSAPP',
        status_envio='ENVIADO' if sucesso else 'FALHOU',
        token=token,
        enviado_em=timezone.now(),
        mensagem=mensagem_preview,
    )


def enviar_nps_whatsapp(atendimento, link_nps, token_notif):
    """Envia pesquisa NPS via WhatsApp (template MARKETING/UTILITY aprovado).

    Parametros do template (ordem):
      {{1}} nome do cliente
      {{2}} procedimento
      {{3}} link NPS
    """
    from ..models import Notificacao

    components = [{
        'type': 'body',
        'parameters': [
            {'type': 'text', 'text': atendimento.cliente.nome_completo},
            {'type': 'text', 'text': atendimento.procedimento.nome},
            {'type': 'text', 'text': link_nps},
        ],
    }]

    sucesso = enviar_template_whatsapp(
        atendimento.cliente.telefone, TEMPLATE_NPS, components
    )

    try:
        notif = Notificacao.objects.get(token=token_notif)
        notif.status_envio = 'ENVIADO' if sucesso else 'FALHOU'
        notif.enviado_em = timezone.now()
        notif.mensagem = f'[Template {TEMPLATE_NPS}] NPS {atendimento.procedimento.nome}'
        notif.save(update_fields=['status_envio', 'enviado_em', 'mensagem'])
    except Notificacao.DoesNotExist:
        logger.warning('[NPS WA] Notificacao token %s nao encontrada', token_notif)
    return sucesso
