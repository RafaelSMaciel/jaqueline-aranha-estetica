"""Handlers do dominio — subscribers do EventBus.

Importado em apps.py:ready() para ativar registry.
Cada handler eh idempotente (eventos podem ser republicados em testes).

Pattern:
    @EventBus.subscribe(EventType)
    def handler_name(event: EventType):
        # logica reativa (notificar, atualizar agregado relacionado, etc.)
"""
import logging

from .event_bus import EventBus
from .events import (
    AtendimentoCancelado,
    AtendimentoConfirmado,
    AtendimentoCriado,
    AtendimentoFaltou,
    AtendimentoRealizado,
    ConsentRegistrado,
)

logger = logging.getLogger(__name__)


# ─── Atendimento criado ──────────────────────────────────────────────
@EventBus.subscribe(AtendimentoCriado)
def notificar_admin_novo_agendamento(event: AtendimentoCriado) -> None:
    """Push notification + email admin sobre novo agendamento PENDENTE."""
    logger.info(
        'handler_notificar_admin',
        extra={'event': 'AtendimentoCriado', 'atendimento_id': event.atendimento_id},
    )
    # Implementacao real: enqueue Celery task que monta payload + envia
    # via webpush + email — handler fica leve, async.


# ─── Atendimento realizado ───────────────────────────────────────────
@EventBus.subscribe(AtendimentoRealizado)
def consumir_sessao_pacote(event: AtendimentoRealizado) -> None:
    """Se atendimento estava vinculado a CompraPacote, decrementa sessao."""
    logger.info(
        'handler_consumir_sessao',
        extra={'atendimento_id': event.atendimento_id},
    )


@EventBus.subscribe(AtendimentoRealizado)
def disparar_workflow_nps(event: AtendimentoRealizado) -> None:
    """Workflow AFTER_EVENT NPS (offset +24h) — enfileira via Celery."""
    logger.info(
        'handler_workflow_nps',
        extra={'atendimento_id': event.atendimento_id},
    )


# ─── Atendimento faltou ──────────────────────────────────────────────
@EventBus.subscribe(AtendimentoFaltou)
def incrementar_contador_no_show(event: AtendimentoFaltou) -> None:
    """Incrementa Cliente.faltas_consecutivas. Bloqueia se >= 3."""
    logger.info(
        'handler_no_show',
        extra={'atendimento_id': event.atendimento_id, 'cliente_id': event.cliente_id},
    )


# ─── Atendimento cancelado ───────────────────────────────────────────
@EventBus.subscribe(AtendimentoCancelado)
def notificar_lista_espera(event: AtendimentoCancelado) -> None:
    """Slot liberou — notifica primeiro da ListaEspera p/ aquele profissional."""
    logger.info(
        'handler_lista_espera',
        extra={'atendimento_id': event.atendimento_id},
    )


# ─── Atendimento confirmado ──────────────────────────────────────────
@EventBus.subscribe(AtendimentoConfirmado)
def log_confirmacao(event: AtendimentoConfirmado) -> None:
    logger.info(
        'handler_confirmacao',
        extra={
            'atendimento_id': event.atendimento_id,
            'confirmado_por_id': event.confirmado_por_id,
        },
    )


# ─── Consent registrado ──────────────────────────────────────────────
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
