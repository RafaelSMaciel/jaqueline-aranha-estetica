"""Logger do gunicorn que redige tokens de links magicos no access log.

Uso (Dockerfile CMD e railway.json startCommand):
    gunicorn ... --access-logfile - --logger-class clinica.gunicorn_logger.LoggerSemTokens

O formato padrao do access log grava a linha da requisicao ("%(r)s": path +
query) e o Referer ("%(f)s"). Links magicos (/reagendar/<token>/,
/confirmar/<token>/, /termo/<token>/, reset de senha, feed ICS ?token=...)
iam em claro p/ os logs do Railway — inclusive no Referer dos assets
estaticos pedidos a partir dessas paginas. Mesma redacao do Sentry
(utils/pii.redigir_tokens_url).

Os HTTP logs do edge do Railway continuam registrando o path: isto remove so
a copia do gunicorn.
"""
from gunicorn.glogging import Logger

from aranha_estetica.utils.pii import redigir_tokens_url


class LoggerSemTokens(Logger):
    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        # Todos os atomos texto: r/U/q/f do formato padrao e tambem os de
        # header/environ ({referer}i, {raw_uri}e...) de um formato customizado.
        return {
            chave: redigir_tokens_url(valor) if isinstance(valor, str) else valor
            for chave, valor in atoms.items()
        }
