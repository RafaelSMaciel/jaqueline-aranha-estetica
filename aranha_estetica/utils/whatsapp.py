"""WhatsApp (Meta Cloud API) — so o cliente HTTP do canal.

Dois formatos de mensagem:
  - template pre-aprovado (lembrete D-1, NPS, aviso de vaga): ``enviar_template``;
  - texto livre, so como resposta dentro da janela de 24 h que a propria cliente abriu ao
    escrever (ex.: a confirmacao do 'PARAR'): ``enviar_texto``.

O caso de uso (quem recebe, com que link, o registro em Notificacao) mora em
services/comunicacao; este modulo nao le o banco nem importa services.

Falha fechada: fora de DEBUG, sem WHATSAPP_TOKEN + WHATSAPP_PHONE_ID o envio retorna False (nada
de marcar ENVIADO sem ter enviado). No ambiente de testes publicado so os DESTINATARIOS_TESTE
recebem (utils/seguranca.destino_permitido). Nada com token ou nome da cliente vai para o log.
"""
import logging
import os

import requests
from django.conf import settings

from .pii import mask_telefone
from .seguranca import destino_permitido

logger = logging.getLogger(__name__)

# Versao da Graph API (versoes antigas sao desligadas pela Meta ~2 anos apos o lancamento).
WHATSAPP_API_VERSION_PADRAO = 'v23.0'
TIMEOUT_SEGUNDOS = 10

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


def formatar_telefone(telefone):
    """Formata telefone para padrao internacional (55...)."""
    digits = ''.join(filter(str.isdigit, telefone or ''))
    if len(digits) in (10, 11):
        digits = '55' + digits
    return digits


def _enviar(telefone, mensagem: dict, rotulo: str) -> bool:
    """POST de uma mensagem (template ou texto) p/ a Cloud API. True so se a Meta aceitou (ou DEBUG).

    rotulo: o que aparece no log (nome do template ou 'texto') — nunca o conteudo."""
    telefone_formatado = formatar_telefone(telefone)
    if not telefone_formatado:
        return False

    if settings.DEBUG:
        # Dev: so loga metadados (nunca o conteudo: nome da cliente e links com token).
        logger.info('whatsapp_dev', extra={'rotulo': rotulo, 'telefone_mask': mask_telefone(telefone_formatado)})
        return True

    token, api_url = _credenciais()
    if not (token and api_url):
        logger.warning('whatsapp_nao_configurado', extra={'rotulo': rotulo})
        return False
    if not destino_permitido(telefone):
        logger.info('envio_bloqueado_ambiente_teste', extra={'canal': 'whatsapp'})
        return False

    payload = {'messaging_product': 'whatsapp', 'to': telefone_formatado, **mensagem}
    try:
        response = requests.post(
            api_url,
            json=payload,
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            timeout=TIMEOUT_SEGUNDOS,
        )
    except requests.exceptions.RequestException as e:
        logger.error('whatsapp_request_exception', extra={'rotulo': rotulo, 'erro': type(e).__name__})
        return False

    if response.status_code in (200, 201):
        logger.info('whatsapp_enviado', extra={'rotulo': rotulo, 'telefone_mask': mask_telefone(telefone_formatado)})
        return True
    logger.error(
        'whatsapp_erro_http',
        extra={'rotulo': rotulo, 'status': response.status_code, 'body': response.text[:200]},
    )
    return False


def enviar_template(telefone, template_name, components=None) -> bool:
    """Mensagem por template pre-aprovado (idioma pt_BR).

    components: lista no formato da Cloud API, ex.:
      [{'type': 'body', 'parameters': [{'type': 'text', 'text': 'Joao'}]}]
    """
    template = {'name': template_name, 'language': {'code': 'pt_BR'}}
    if components:
        template['components'] = components
    return _enviar(telefone, {'type': 'template', 'template': template}, template_name)


def enviar_texto(telefone, texto: str) -> bool:
    """Texto livre — so vale como resposta dentro da janela de 24 h aberta pela cliente."""
    return _enviar(telefone, {'type': 'text', 'text': {'body': texto, 'preview_url': False}}, 'texto')
