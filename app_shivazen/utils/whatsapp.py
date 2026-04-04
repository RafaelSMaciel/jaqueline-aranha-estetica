"""
WhatsApp Notification Service — Shiva Zen

Envia mensagens via WhatsApp usando a API configurada.
Em desenvolvimento, apenas loga as mensagens.
Em producao, integra com Meta Business API / Evolution API / Z-API.
"""
import os
import logging
import secrets
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


def enviar_whatsapp(telefone, mensagem):
    """
    Envia mensagem via WhatsApp.
    Em dev (sem token), apenas loga.
    Em prod (com token), usa a API configurada.
    """
    telefone_formatado = formatar_telefone(telefone)

    if not WHATSAPP_TOKEN or settings.DEBUG:
        # Modo desenvolvimento — apenas loga
        logger.info(
            f'[WHATSAPP DEV] Para: {telefone_formatado[-4:]}\n'
            f'Mensagem: {mensagem[:200]}...'
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
            logger.info(f'[WHATSAPP] Enviado para ***{telefone_formatado[-4:]}')
            return True
        else:
            logger.error(
                f'[WHATSAPP] Erro {response.status_code}: {response.text[:200]}'
            )
            return False
    except Exception as e:
        logger.error(f'[WHATSAPP] Falha ao enviar: {e}')
        return False


def enviar_lembrete_agendamento(atendimento):
    """
    Envia lembrete de agendamento com link de confirmacao/cancelamento.
    Cria registro de Notificacao com token unico.
    """
    from ..models import Notificacao

    token = gerar_token()
    link_confirmar = f'{SITE_URL}/confirmar/{token}/?acao=confirmar'
    link_cancelar = f'{SITE_URL}/confirmar/{token}/?acao=cancelar'

    data_formatada = atendimento.data_hora_inicio.strftime('%d/%m/%Y')
    hora_formatada = atendimento.data_hora_inicio.strftime('%H:%M')

    mensagem = (
        f"Ola {atendimento.cliente.nome_completo}! "
        f"Lembrando do seu agendamento na Shiva Zen:\n\n"
        f"Procedimento: {atendimento.procedimento.nome}\n"
        f"Data: {data_formatada} as {hora_formatada}\n"
        f"Profissional: {atendimento.profissional.nome}\n\n"
        f"Confirmar presenca:\n{link_confirmar}\n\n"
        f"Cancelar agendamento:\n{link_cancelar}\n\n"
        f"Shiva Zen - Clinica de Estetica"
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
    """
    Notifica o admin quando um cliente confirma ou cancela.
    """
    data_formatada = atendimento.data_hora_inicio.strftime('%d/%m/%Y as %H:%M')
    emoji = "Confirmou" if acao == 'CONFIRMOU' else "CANCELOU"

    mensagem = (
        f"[SHIVA ZEN - NOTIFICACAO ADMIN]\n\n"
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
    """
    Notifica o cliente que seu agendamento foi cancelado pelo admin.
    """
    data_formatada = atendimento.data_hora_inicio.strftime('%d/%m/%Y as %H:%M')

    mensagem = (
        f"Ola {atendimento.cliente.nome_completo},\n\n"
        f"Informamos que seu agendamento foi cancelado:\n\n"
        f"Procedimento: {atendimento.procedimento.nome}\n"
        f"Data: {data_formatada}\n"
        f"Profissional: {atendimento.profissional.nome}\n\n"
        f"Para reagendar, acesse:\n"
        f"{SITE_URL}/agendamento/\n\n"
        f"Ou fale conosco pelo WhatsApp.\n\n"
        f"Shiva Zen - Clinica de Estetica"
    )

    from ..models import Notificacao

    sucesso = enviar_whatsapp(atendimento.cliente.telefone, mensagem)

    Notificacao.objects.create(
        atendimento=atendimento,
        tipo='CANCELAMENTO',
        canal='WHATSAPP',
        status_envio='ENVIADO' if sucesso else 'FALHOU',
        token=gerar_token(),
        enviado_em=timezone.now(),
        mensagem=mensagem,
    )
