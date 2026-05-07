"""Helper para logs estruturados consistentes.

Padronizar `logger.info(event, extra={...})` em vez de tags ad-hoc tipo
`[WA NPS] ignorado: motivo X`. JSON formatter (prod) consome `extra={}`.

Exemplo::

    from .structured_logging import log_event
    log_event(logger, 'wa_nps_skipped', cliente_id=cli.pk, reason='no_consent')
"""
import logging
from typing import Any


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **kwargs: Any,
) -> None:
    """Emite log estruturado.

    Args:
        logger: instancia de logger do modulo chamador.
        event: nome canonico do evento (snake_case, ex: 'agendamento_criado').
        level: nivel logging (default INFO).
        **kwargs: campos adicionais que viram `extra={}` (JSON em prod).
    """
    logger.log(level, event, extra=kwargs)


def log_warn(logger: logging.Logger, event: str, **kwargs: Any) -> None:
    log_event(logger, event, level=logging.WARNING, **kwargs)


def log_error(logger: logging.Logger, event: str, **kwargs: Any) -> None:
    log_event(logger, event, level=logging.ERROR, **kwargs)
