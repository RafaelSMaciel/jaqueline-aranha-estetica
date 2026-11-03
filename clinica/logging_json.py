"""Formatter JSON do log em producao, no formato que o Railway le como log estruturado.

O Railway colore e filtra cada linha pelo campo `level` (debug, info, warn, error); sem ele, tudo
o que sai no stderr vira "error" no painel e um INFO de rotina (comissao_calculada) aparece
vermelho ao lado das falhas de verdade. Ligado no handler `console` (settings/base.py LOGGING)
quando DEBUG=False.
"""
from pythonjsonlogger.json import JsonFormatter

# Nomes do logging -> severidades do Railway (as demais so passam para minusculas)
_NIVEIS_RAILWAY = {'WARNING': 'warn', 'CRITICAL': 'error'}


def nivel_railway(levelname: str) -> str:
    return _NIVEIS_RAILWAY.get(levelname, levelname.lower())


class JsonRailway(JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record['level'] = nivel_railway(record.levelname)
