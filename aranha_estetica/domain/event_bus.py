"""Event bus sincrono in-process.

Uso::

    # Em services apos commit
    from .events import AtendimentoRealizado
    from .event_bus import EventBus

    EventBus.publish(AtendimentoRealizado(
        atendimento_id=at.pk,
        cliente_id=at.cliente_id,
        profissional_id=at.profissional_id,
        occurred_at=timezone.now(),
    ))

    # Em handlers/atendimento_handlers.py
    from .events import AtendimentoRealizado
    from .event_bus import EventBus

    @EventBus.subscribe(AtendimentoRealizado)
    def disparar_workflow_nps(event: AtendimentoRealizado):
        ...

Para handlers async, ainda usar Celery `.delay()` dentro do handler.
Bus mantem sincronicidade — simplifica testes (sem broker em test env).
"""
import logging
from collections import defaultdict
from typing import Callable, Type, TypeVar

from .events import DomainEvent

logger = logging.getLogger(__name__)

E = TypeVar('E', bound=DomainEvent)
Handler = Callable[[E], None]


class EventBus:
    _handlers: dict[Type[DomainEvent], list[Handler]] = defaultdict(list)

    @classmethod
    def subscribe(cls, event_type: Type[E]) -> Callable[[Handler], Handler]:
        """Decorator que registra handler para um tipo de evento."""
        def decorator(fn: Handler) -> Handler:
            cls._handlers[event_type].append(fn)
            logger.debug(
                'event_handler_subscribed',
                extra={'event': event_type.__name__, 'handler': fn.__name__},
            )
            return fn
        return decorator

    @classmethod
    def publish(cls, event: DomainEvent) -> None:
        """Dispara todos os handlers registrados.

        Falhas individuais sao logadas mas nao quebram a publicacao
        (handler que falha nao impede outros de rodar — best-effort).
        """
        handlers = cls._handlers.get(type(event), [])
        if not handlers:
            logger.debug('event_no_handlers', extra={'event': type(event).__name__})
            return

        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:
                logger.exception(
                    'event_handler_failed',
                    extra={
                        'event': type(event).__name__,
                        'handler': handler.__name__,
                        'error': str(exc),
                    },
                )

    @classmethod
    def reset(cls) -> None:
        """Limpa handlers — util em tests."""
        cls._handlers.clear()
