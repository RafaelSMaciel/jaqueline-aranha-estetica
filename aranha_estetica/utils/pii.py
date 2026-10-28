"""Mascaramento de PII para logs.

LGPD: dados pessoais (email, telefone, CPF) nao devem aparecer em log
estruturado/Sentry em forma legivel. Helpers abaixo retornam versao
mascarada determinista — preserva prefixo/sufixo p/ debug, oculta corpo.

Uso::

    from .pii import mask_email, mask_telefone, mask_cpf
    logger.warning('booking_falha', extra={'email': mask_email(email)})
"""
from __future__ import annotations

import os
import re
from typing import Optional

from .telefone import mascarar as _mascarar_telefone

_EMAIL_RX = re.compile(r'^([^@]+)@(.+)$')
_DIGITS_RX = re.compile(r'\D+')


def mask_email(value: Optional[str]) -> str:
    """Mascara local-part. `joao.silva@gmail.com` -> `jo***@gmail.com`."""
    if not value:
        return ''
    m = _EMAIL_RX.match(value.strip())
    if not m:
        return '***'
    local, domain = m.group(1), m.group(2)
    if len(local) <= 2:
        return f'{local[0]}***@{domain}'
    return f'{local[:2]}***@{domain}'


def mask_telefone(value: Optional[str]) -> str:
    """Mascara meio do telefone. `17999990000` -> `17****0000` (utils/telefone.mascarar)."""
    return _mascarar_telefone(value)


def mask_cpf(value: Optional[str]) -> str:
    """Mascara meio do CPF. `12345678900` -> `123*****00`."""
    if not value:
        return ''
    digits = _DIGITS_RX.sub('', value)
    if len(digits) != 11:
        return '***'
    return f'{digits[:3]}*****{digits[-2:]}'


_SENTRY_PII_KEYS = {
    'email', 'telefone', 'cpf', 'phone', 'celular',
    'data_nascimento', 'nome',
    'destinatario', 'from_email', 'to',
}

# Segmentos de URL que carregam token de acesso (link magico): /confirmar/<tok>/,
# /reagendar/<tok>/, /nps/<tok>/, /termo/<tok>/, /pesquisa/<tok>/,
# /anamnese/<tok>/, /lgpd/unsubscribe/<tok>/ e ?token=... (ICS).
_TOKEN_PATH_RX = re.compile(
    r'(/(?:confirmar|reagendar|nps|termo|pesquisa|anamnese|lgpd/unsubscribe|admin-login/recuperar/[^/]+)/)'
    r'(?!obrigado/)[^/?#]+'
)
_TOKEN_QUERY_RX = re.compile(r'((?:^|[?&])token=)[^&#]*', re.IGNORECASE)


def redigir_tokens_url(valor: Optional[str]) -> str:
    """Troca tokens de links magicos por [token] (URL ou query string)."""
    if not valor or not isinstance(valor, str):
        return valor or ''
    valor = _TOKEN_PATH_RX.sub(lambda m: m.group(1) + '[token]', valor)
    return _TOKEN_QUERY_RX.sub(lambda m: m.group(1) + '[token]', valor)


def _mascarar_valor(chave: str, valor: str) -> str:
    chave = chave.lower()
    if 'email' in chave or '@' in valor:
        return mask_email(valor)
    if 'cpf' in chave:
        return mask_cpf(valor)
    if chave == 'nome':
        return f'{valor[:1]}***' if valor else ''
    return mask_telefone(valor)


# Chaves removidas pelo EventScrubber do SDK (recursivo: request, extra, contexts, breadcrumbs,
# spans), alem do DEFAULT_DENYLIST dele (senha, token, cookie, sessao...): dado pessoal e,
# principalmente, dado de saude (art. 11), que nao pode sair para o operador no exterior.
SENTRY_DENYLIST_EXTRA = [
    'email', 'telefone', 'cpf', 'rg', 'celular', 'phone', 'nome', 'data_nascimento', 'endereco', 'cep',
    'alergias', 'contraindicacoes', 'historico_saude', 'medicamentos_uso', 'observacoes_gerais',
    'respostas', 'respostas_extras', 'respostas_json', 'dados', 'dados_antes', 'dados_depois',
    'texto', 'mensagem', 'comentario', 'codigo', 'otp', 'gcal_refresh_token', 'refresh_token',
]

_KEY_DETAIL_RX = re.compile(r'(Key \([^)]*\)=\()[^)]*(\))')   # DETAIL do IntegrityError do Postgres
_EMAIL_TEXTO_RX = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
_CPF_TEXTO_RX = re.compile(r'\b\d{3}\.\d{3}\.\d{3}-\d{2}\b')
_TELEFONE_TEXTO_RX = re.compile(r'\(?\b\d{2}\)?[ -]?9?\d{4}-\d{4}\b')
_NUMERO_LONGO_RX = re.compile(r'\b\d{10,13}\b')                    # telefone/CPF so com digitos


def redigir_texto(valor):
    """Texto livre de evento (mensagem de excecao, log, breadcrumb): tokens de link, valores do
    DETAIL do Postgres, e-mail, CPF e telefone viram marcador. Nao-str volta igual."""
    if not isinstance(valor, str) or not valor:
        return valor
    valor = redigir_tokens_url(valor)
    valor = _KEY_DETAIL_RX.sub(r'\1[redigido]\2', valor)
    valor = _EMAIL_TEXTO_RX.sub(lambda m: mask_email(m.group(0)), valor)
    valor = _CPF_TEXTO_RX.sub('[cpf]', valor)
    valor = _TELEFONE_TEXTO_RX.sub('[telefone]', valor)
    return _NUMERO_LONGO_RX.sub('[numero]', valor)


def _redigir_request(request: dict) -> None:
    if 'data' in request:
        request['data'] = '[scrubbed]'
    for campo in ('url', 'query_string'):
        if isinstance(request.get(campo), str):
            request[campo] = redigir_tokens_url(request[campo])
    headers = request.get('headers')
    if isinstance(headers, dict):
        for nome in list(headers):
            if nome.lower() == 'referer' and isinstance(headers[nome], str):
                headers[nome] = redigir_tokens_url(headers[nome])


def _redigir_breadcrumb(crumb: dict) -> dict:
    if crumb.get('category') == 'query':
        # SQL sem os parametros (o texto vem com %s; os valores nunca saem)
        crumb.pop('data', None)
    crumb['message'] = redigir_texto(crumb.get('message'))
    dados = crumb.get('data')
    if isinstance(dados, dict):
        for chave in ('url', 'to', 'from'):
            if isinstance(dados.get(chave), str):
                dados[chave] = redigir_texto(dados[chave])
    return crumb


def _redigir_evento(event: dict) -> dict:
    extra = event.get('extra') or {}
    for key in list(extra.keys()):
        if key.lower() in _SENTRY_PII_KEYS:
            value = extra[key]
            if isinstance(value, str):
                extra[key] = _mascarar_valor(key, value)

    request = event.get('request')
    if isinstance(request, dict):
        _redigir_request(request)

    for exc in (event.get('exception') or {}).get('values') or []:
        exc['value'] = redigir_texto(exc.get('value'))
        for frame in (exc.get('stacktrace') or {}).get('frames') or []:
            frame.pop('vars', None)          # variaveis locais: prontuario, respostas, OTP...
    logentry = event.get('logentry')
    if isinstance(logentry, dict):
        for campo in ('message', 'formatted'):
            logentry[campo] = redigir_texto(logentry.get(campo))
        logentry.pop('params', None)
    if isinstance(event.get('message'), str):
        event['message'] = redigir_texto(event['message'])

    crumbs = event.get('breadcrumbs')
    lista = crumbs.get('values') if isinstance(crumbs, dict) else crumbs
    for crumb in lista or []:
        if isinstance(crumb, dict):
            _redigir_breadcrumb(crumb)

    if isinstance(event.get('transaction'), str):
        event['transaction'] = redigir_tokens_url(event['transaction'])
    for span in event.get('spans') or []:
        if isinstance(span, dict):
            span['description'] = redigir_texto(span.get('description'))
    return event


def sentry_before_send(event: dict, hint: dict) -> dict:
    """Filtra PII de eventos de erro do Sentry antes do envio.

    Mascara campos de `extra` cujas keys batam com a lista PII, remove o corpo do
    request (POST) e as variaveis locais dos frames, redige tokens de links magicos
    (URL, query string, Referer, nome da transacao) e mascara e-mail/CPF/telefone e o
    DETAIL do Postgres na mensagem da excecao, no log e nos breadcrumbs.
    """
    return _redigir_evento(event)


def sentry_before_send_transaction(event: dict, hint: dict) -> dict:
    """Mesmo filtro para as transacoes de performance (traces_sample_rate): sem ele a URL
    com o token (/reagendar/<token>/, feed.ics?token=) saia em toda transacao amostrada."""
    return _redigir_evento(event)


def sentry_before_breadcrumb(crumb: dict, hint: dict) -> dict:
    return _redigir_breadcrumb(crumb)


def opcoes_sentry(dsn: str) -> dict:
    """kwargs do sentry_sdk.init (settings/base.py). Funcao propria p/ o teste conferir a
    configuracao que vai para producao sem precisar de DSN."""
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

    return {
        'dsn': dsn,
        'integrations': [DjangoIntegration()],
        # Nome do ambiente no Railway ('production', 'dev') antes do DJANGO_ENV, que a imagem fixa
        # em prod: sem isso os erros do ambiente de testes caiam no mesmo 'prod' da producao
        'environment': (os.environ.get('SENTRY_ENVIRONMENT') or os.environ.get('RAILWAY_ENVIRONMENT_NAME')
                        or os.environ.get('DJANGO_ENV') or 'prod'),
        # Railway preenche RAILWAY_GIT_COMMIT_SHA: o erro aponta o deploy que o introduziu
        'release': os.environ.get('SENTRY_RELEASE') or os.environ.get('RAILWAY_GIT_COMMIT_SHA') or None,
        'traces_sample_rate': float(os.environ.get('SENTRY_TRACES_SAMPLE_RATE', '0.2')),
        'send_default_pii': False,
        # variaveis locais dos frames levariam prontuario, respostas e OTP para o Sentry
        'include_local_variables': False,
        'max_request_body_size': 'never',
        'event_scrubber': EventScrubber(denylist=DEFAULT_DENYLIST + SENTRY_DENYLIST_EXTRA, recursive=True),
        'before_send': sentry_before_send,
        'before_send_transaction': sentry_before_send_transaction,
        'before_breadcrumb': sentry_before_breadcrumb,
    }
