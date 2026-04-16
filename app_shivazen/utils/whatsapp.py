"""
WhatsApp Notification Service — Plataforma de Clinicas

Canal WhatsApp usado APENAS para:
  - Lembrete D-1 (com link de confirmacao/cancelamento)
  - Pesquisa NPS (24h apos atendimento)

Demais mensagens vao por email (OTP, confirmacao, cancelamento, pacotes, fila).
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
CLINIC_NAME = os.environ.get('CLINIC_NAME', 'Clinica Estetica')
MAX_RETRIES = 3


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
    Envia mensagem via WhatsApp.
    Em dev (sem token), apenas loga.
    Em prod (com token), usa a API configurada com retry exponencial.
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
    except Exception as e:
        logger.error(f'[WHATSAPP] Falha ao enviar: {e}')
        return False


def enviar_template_whatsapp(telefone, template_name, components=None):
    """Envia mensagem via template pre-aprovado no Meta Business API."""
    telefone_formatado = formatar_telefone(telefone)

    if not WHATSAPP_TOKEN or settings.DEBUG:
        from .precos import mask_telefone
        logger.info(
            '[WHATSAPP DEV] Template "%s" para: %s',
            template_name, mask_telefone(telefone_formatado),
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
    except Exception as e:
        logger.error('[WHATSAPP] Falha ao enviar template: %s', e)
        return False


# ─── MENSAGENS ESPECIFICAS (WhatsApp apenas D-1 e NPS) ───

def enviar_lembrete_agendamento(atendimento):
    """
    Lembrete D-1 via WhatsApp com link de confirmacao/cancelamento.
    UNICA mensagem de lembrete (sem lembrete 2h).
    """
    from ..models import Notificacao

    token = gerar_token()
    site_url = SITE_URL.rstrip('/')
    link_confirmar = f'{site_url}/confirmar/{token}/?acao=confirmar'
    link_cancelar = f'{site_url}/confirmar/{token}/?acao=cancelar'

    data_formatada = atendimento.data_hora_inicio.strftime('%d/%m/%Y')
    hora_formatada = atendimento.data_hora_inicio.strftime('%H:%M')

    mensagem = (
        f"Ola {atendimento.cliente.nome_completo}! "
        f"Lembrando do seu agendamento no {CLINIC_NAME}:\n\n"
        f"Procedimento: {atendimento.procedimento.nome}\n"
        f"Data: {data_formatada} as {hora_formatada}\n"
        f"Profissional: {atendimento.profissional.nome}\n\n"
        f"Confirmar presenca:\n{link_confirmar}\n\n"
        f"Precisa remarcar?\n{link_cancelar}\n\n"
        f"{CLINIC_NAME}"
    )

    sucesso = enviar_whatsapp(atendimento.cliente.telefone, mensagem)

    notif = Notificacao.objects.create(
        atendimento=atendimento,
        tipo='LEMBRETE',
        canal='WHATSAPP',
        status_envio='ENVIADO' if sucesso else 'FALHOU',
        token=token,
        enviado_em=timezone.now(),
        mensagem=mensagem,
    )

    return notif


def enviar_confirmacao_admin(atendimento, acao, telefone_admin=None):
    """Notifica o admin quando um cliente confirma ou cancela."""
    data_formatada = atendimento.data_hora_inicio.strftime('%d/%m/%Y as %H:%M')
    emoji = "Confirmou" if acao == 'CONFIRMOU' else "CANCELOU"

    mensagem = (
        f"[{CLINIC_NAME} - NOTIFICACAO ADMIN]\n\n"
        f"Cliente: {atendimento.cliente.nome_completo}\n"
        f"Procedimento: {atendimento.procedimento.nome}\n"
        f"Data: {data_formatada}\n"
        f"Profissional: {atendimento.profissional.nome}\n\n"
        f"Status: {emoji}\n"
        f"Telefone: {atendimento.cliente.telefone}"
    )

    from ..models import ConfiguracaoSistema
    if not telefone_admin:
        config = ConfiguracaoSistema.objects.filter(chave='whatsapp_admin').first()
        telefone_admin = config.valor if config else None

    if telefone_admin:
        enviar_whatsapp(telefone_admin, mensagem)

    logger.info(f'[ADMIN NOTIF] {emoji} - {atendimento.cliente.nome_completo} - {data_formatada}')


def enviar_cancelamento_cliente(atendimento):
    """Notifica o cliente que seu agendamento foi cancelado — via EMAIL."""
    from .email import enviar_cancelamento_email

    data_formatada = atendimento.data_hora_inicio.strftime('%d/%m/%Y as %H:%M')

    if atendimento.cliente.email:
        enviar_cancelamento_email(atendimento.cliente.email, {
            'nome': atendimento.cliente.nome_completo,
            'procedimento': atendimento.procedimento.nome,
            'data_hora': data_formatada,
            'profissional': atendimento.profissional.nome,
        })
    else:
        # Fallback WhatsApp se nao tem email
        mensagem = (
            f"Ola {atendimento.cliente.nome_completo},\n\n"
            f"Seu agendamento foi cancelado:\n\n"
            f"Procedimento: {atendimento.procedimento.nome}\n"
            f"Data: {data_formatada}\n\n"
            f"Para reagendar: {SITE_URL.rstrip('/')}/agendamento/\n\n"
            f"{CLINIC_NAME}"
        )
        enviar_whatsapp(atendimento.cliente.telefone, mensagem)

    from ..models import Notificacao
    Notificacao.objects.create(
        atendimento=atendimento,
        tipo='CANCELAMENTO',
        canal='EMAIL' if atendimento.cliente.email else 'WHATSAPP',
        status_envio='ENVIADO',
        token=gerar_token(),
        enviado_em=timezone.now(),
    )
