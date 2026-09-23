"""Handlers do dominio — subscribers do EventBus (apenas logging/auditoria).

IMPORTANTE: a logica de negocio reativa NAO mora aqui. Ela vive em:
- `signals.py` (post_save de Atendimento): debito de sessao de pacote, reset/registro
  de faltas (3-strike) e aviso da lista de espera (services/lista_espera_service).
- `services/comissao_service.py` e `services/fidelidade_service.py`: assinam o EventBus
  diretamente (calculo/estorno de comissao e cashback).

Estes handlers cobrem somente o logging estruturado. (Consentimentos LGPD sao
auditados nos proprios campos consent_*_em/_ip do Cliente; o antigo handler de
ConsentRegistrado nunca disparava e foi removido.)

Importado em apps.py:ready() para ativar o registry.
"""
import logging

from .event_bus import EventBus
from .events import AtendimentoConfirmado

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

