"""Parse defensivo de parametros vindos de GET/POST."""
import re

_ID_RE = re.compile(r'[0-9]{1,18}')


def id_int(valor):
    """int de um id (so digitos ASCII, ate 18) ou None.

    str.isdigit() aceita '²' e outros digitos Unicode que int() recusa
    (ValueError -> HTTP 500); 18 digitos cabem no bigint do Postgres.
    """
    s = str(valor if valor is not None else '').strip()
    return int(s) if _ID_RE.fullmatch(s) else None
