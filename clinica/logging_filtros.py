"""Filtro de log que redige dado pessoal e token de link magico antes de sair no stdout.

O Django loga o caminho da requisicao em toda resposta 4xx/5xx (django.request: 'Not Found:
/confirmar/<token>/'; django.security.csrf: 'Forbidden (CSRF cookie not set.): /termo/<token>/')
e o formatter JSON serializa tambem os atributos extra do registro (o `request` vira
"<WSGIRequest: GET '/confirmar/<token>/'>"). Sem este filtro o token ia em claro para os logs do
Railway: quem le o log abria o termo, a anamnese ou a confirmacao da cliente.

Mesma redacao do Sentry e do access log do gunicorn (utils/pii.redigir_texto): tokens de link,
DETAIL do Postgres, e-mail, CPF e telefone. Ligado no handler `console` (settings/base.py LOGGING).
"""
import logging

from aranha_estetica.utils.pii import redigir_texto

# Atributos que todo LogRecord tem (os demais vieram de `extra=`)
_ATRIBUTOS_PADRAO = frozenset(vars(logging.LogRecord('', 0, '', 0, '', None, None))) | {'message', 'asctime'}
_ESCALARES = (int, float, bool, type(None))


class RedigirLog(logging.Filter):
    """Mensagem, traceback e atributos extra em texto passam por redigir_texto. Nunca descarta o
    registro nem levanta excecao (filtro que quebra derrubaria o codigo que chamou o logger)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            mensagem = record.getMessage()
        except Exception:  # noqa: BLE001 — args que nao casam com a mensagem: loga o molde
            mensagem = str(record.msg)
        record.msg = redigir_texto(mensagem)
        record.args = None
        if record.exc_info:
            # pre-formatado e redigido; sem exc_info o formatter (texto ou JSON) usa o exc_text
            record.exc_text = redigir_texto(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redigir_texto(record.exc_text)
        for chave, valor in list(vars(record).items()):
            if chave in _ATRIBUTOS_PADRAO or isinstance(valor, _ESCALARES):
                continue
            if isinstance(valor, str):
                setattr(record, chave, redigir_texto(valor))
            elif not isinstance(valor, (dict, list, tuple)):
                # objeto (request, model...): o JSON usaria str(); redigido aqui
                setattr(record, chave, redigir_texto(str(valor)))
        return True
