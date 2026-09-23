"""
WhatsApp Notification Service — Plataforma de Clinicas

Canal WhatsApp (Meta Cloud API, templates pre-aprovados) usado para:
  - Confirmacao D-1 (lembrete com link de confirmacao/cancelamento)
  - Pesquisa NPS (24h apos atendimento REALIZADO)
  - Aviso de vaga da lista de espera

Todas as demais mensagens usam EMAIL.

Falha fechada: fora de DEBUG, sem WHATSAPP_TOKEN + WHATSAPP_PHONE_ID o envio
retorna False (nada de marcar ENVIADO sem ter enviado) e nada com token/nome
do cliente vai para o log.
"""
import logging
import os
import secrets

import requests
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .datas import fmt_local
from .pii import mask_telefone

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
# Versao da Graph API (versoes antigas sao desligadas pela Meta ~2 anos apos o lancamento).
WHATSAPP_API_VERSION_PADRAO = 'v23.0'

# Nomes dos templates aprovados no Meta Business API
TEMPLATE_CONFIRMACAO_D1 = os.environ.get('WHATSAPP_TEMPLATE_D1', 'confirmacao_d1')
TEMPLATE_NPS = os.environ.get('WHATSAPP_TEMPLATE_NPS', 'nps_pos_atendimento')
TEMPLATE_LISTA_ESPERA = os.environ.get('WHATSAPP_TEMPLATE_LISTA_ESPERA', 'lista_espera_vaga')


def _credenciais():
    """(token, url da API) lidos do ambiente em tempo de chamada."""
    token = os.environ.get('WHATSAPP_TOKEN', '')
    # WHATSAPP_PHONE_NUMBER_ID aceito como alias (nome usado no .env.example antigo).
    phone_id = os.environ.get('WHATSAPP_PHONE_ID') or os.environ.get('WHATSAPP_PHONE_NUMBER_ID', '')
    api_url = os.environ.get('WHATSAPP_API_URL', '')
    if not api_url and phone_id:
        versao = os.environ.get('WHATSAPP_API_VERSION', WHATSAPP_API_VERSION_PADRAO)
        api_url = f'https://graph.facebook.com/{versao}/{phone_id}/messages'
    return token, api_url


def whatsapp_configurado() -> bool:
    """True se ha token + phone id (ou URL) da Meta configurados."""
    token, api_url = _credenciais()
    return bool(token and api_url)


def pode_enviar_whatsapp() -> bool:
    """Envio 'conta' como feito: DEBUG (so loga) ou provedor configurado."""
    return bool(settings.DEBUG) or whatsapp_configurado()


def gerar_token():
    """Gera token unico para link de confirmacao."""
    return secrets.token_urlsafe(32)


def formatar_telefone(telefone):
    """Formata telefone para padrao internacional (55...)."""
    digits = ''.join(filter(str.isdigit, telefone or ''))
    if len(digits) in (10, 11):
        digits = '55' + digits
    return digits


def enviar_template_whatsapp(telefone, template_name, components=None):
    """Envia mensagem via template pre-aprovado no Meta Business API.

    components: lista no formato WhatsApp Cloud API, ex:
      [{'type': 'body', 'parameters': [{'type': 'text', 'text': 'Joao'}]}]
    Retorna True so se a Meta aceitou a mensagem (ou em DEBUG, que so loga).
    """
    telefone_formatado = formatar_telefone(telefone)
    if not telefone_formatado:
        return False

    if settings.DEBUG:
        # Dev: so loga metadados (nunca components: nome do cliente e links com token).
        logger.info(
            'whatsapp_dev_template',
            extra={'template': template_name, 'telefone_mask': mask_telefone(telefone_formatado)},
        )
        return True

    token, api_url = _credenciais()
    if not (token and api_url):
        logger.warning('whatsapp_nao_configurado', extra={'template': template_name})
        return False

    payload = {
        'messaging_product': 'whatsapp',
        'to': telefone_formatado,
        'type': 'template',
        'template': {
            'name': template_name,
            'language': {'code': 'pt_BR'},
        },
    }
    if components:
        payload['template']['components'] = components

    try:
        response = requests.post(
            api_url,
            json=payload,
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            timeout=10,
        )
    except requests.exceptions.RequestException as e:
        logger.error('whatsapp_template_request_exception', extra={'template': template_name, 'erro': type(e).__name__})
        return False

    if response.status_code in (200, 201):
        logger.info(
            'whatsapp_template_enviado',
            extra={'template': template_name, 'telefone_mask': mask_telefone(telefone_formatado)},
        )
        return True
    logger.error(
        'whatsapp_template_erro_http',
        extra={'template': template_name, 'status': response.status_code, 'body': response.text[:200]},
    )
    return False


# ─── MENSAGENS ───

def enviar_confirmacao_d1(atendimento):
    """
    Lembrete/confirmacao D-1 via WhatsApp com link de confirmacao/cancelamento.
    Usa template aprovado TEMPLATE_CONFIRMACAO_D1 (categoria UTILITY).
    Parametros do template (ordem):
      {{1}} nome do cliente
      {{2}} data formatada (dd/mm/aaaa, hora local)
      {{3}} hora formatada (HH:MM, hora local)
      {{4}} procedimento
      {{5}} profissional
      {{6}} link confirmar
      {{7}} link cancelar
    """
    from ..models import Notificacao

    site_url = settings.SITE_URL.rstrip('/')
    # Banco devolve UTC: formata SEMPRE no fuso da clinica.
    data_formatada = fmt_local(atendimento.data_hora_inicio, '%d/%m/%Y')
    hora_formatada = fmt_local(atendimento.data_hora_inicio, '%H:%M')
    mensagem_preview = (
        f'[Template {TEMPLATE_CONFIRMACAO_D1}] '
        f'{data_formatada} {hora_formatada} / '
        f'{atendimento.procedimento.nome} c/ {atendimento.profissional.nome}'
    )

    # Persistir a Notificacao com token ANTES de enviar: o link so chega ao
    # cliente apos o token estar gravado, e a colisao de unique (improvavel com
    # token_urlsafe(32)) e tratada com retry atomico em vez de virar 500.
    notif = None
    for _ in range(MAX_RETRIES):
        token = gerar_token()
        try:
            with transaction.atomic():
                notif = Notificacao.objects.create(
                    atendimento=atendimento,
                    tipo='LEMBRETE',
                    canal='WHATSAPP',
                    status='PENDENTE',
                    token=token,
                    mensagem=mensagem_preview,
                )
            break
        except IntegrityError:
            logger.warning('whatsapp_d1_token_colisao', extra={'atendimento_id': atendimento.pk})
            notif = None
    if notif is None:
        logger.error('whatsapp_d1_token_falha', extra={'atendimento_id': atendimento.pk})
        return None

    link_confirmar = f'{site_url}/confirmar/{notif.token}/?acao=confirmar'
    link_cancelar = f'{site_url}/confirmar/{notif.token}/?acao=cancelar'

    components = [{
        'type': 'body',
        'parameters': [
            {'type': 'text', 'text': atendimento.cliente.nome},
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

    notif.status = 'ENVIADO' if sucesso else 'FALHOU'
    notif.enviado_em = timezone.now() if sucesso else None
    notif.save(update_fields=['status', 'enviado_em'])
    return notif


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
            {'type': 'text', 'text': atendimento.cliente.nome},
            {'type': 'text', 'text': atendimento.procedimento.nome},
            {'type': 'text', 'text': link_nps},
        ],
    }]

    sucesso = enviar_template_whatsapp(
        atendimento.cliente.telefone, TEMPLATE_NPS, components
    )

    notif = Notificacao.objects.filter(token=token_notif).first()
    if notif is None:
        logger.warning('nps_wa_notificacao_nao_encontrada', extra={'atendimento_id': atendimento.pk})
        return sucesso
    notif.status = 'ENVIADO' if sucesso else 'FALHOU'
    notif.enviado_em = timezone.now() if sucesso else None
    notif.mensagem = f'[Template {TEMPLATE_NPS}] NPS {atendimento.procedimento.nome}'
    notif.save(update_fields=['status', 'enviado_em', 'mensagem'])
    return sucesso
