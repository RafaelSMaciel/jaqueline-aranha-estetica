"""Handlers do dominio — subscribers do EventBus (apenas logging/auditoria).

IMPORTANTE: a logica de negocio reativa NAO mora aqui. Ela vive em:
- `signals.py` (post_save de Atendimento): debito de sessao de pacote, reset/registro
  de faltas (3-strike).
- `services/comissao_service.py` e `services/fidelidade_service.py`: assinam o EventBus
  diretamente (calculo/estorno de comissao e cashback).

Estes handlers cobrem somente o logging estruturado para auditoria. (Antes havia
stubs que apenas logavam e davam falsa impressao de arquitetura orientada a eventos —
removidos para nao confundir com a logica real acima.)

Importado em apps.py:ready() para ativar o registry.
"""
import logging

from .event_bus import EventBus
from .events import AtendimentoConfirmado, ConsentRegistrado

logger = logging.getLogger(__name__)


@EventBus.subscribe(AtendimentoConfirmado)
def log_confirmacao(event: AtendimentoConfirmado) -> None:
    logger.info(
        'handler_confirmacao',
        extra={
            'atendimento_id': event.atendimento_id,
            'confirmado_por_id': event.confirmado_por_id,
        },
    )


@EventBus.subscribe(ConsentRegistrado)
def log_consent_audit(event: ConsentRegistrado) -> None:
    """Log estruturado p/ auditoria LGPD (Art. 37)."""
    logger.info(
        'consent_registrado',
        extra={
            'cliente_id': event.cliente_id,
            'canal': event.canal,
            'aceito': event.aceito,
            'ip': event.ip,
        },
    )
