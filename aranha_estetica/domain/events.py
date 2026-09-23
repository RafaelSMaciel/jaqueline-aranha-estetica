"""Domain events — fatos imutaveis que ocorreram no sistema.

Publicados pela FSM de Atendimento (models/agendamentos._publish_event, por
nome) e pelos services apos o commit. Handlers reagem via EventBus.
So existem eventos com publicador real (os orfaos foram removidos).

Padrao naming: <Agregado><Acao no passado> (ex: AtendimentoConfirmado).
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class DomainEvent:
    """Base — todos eventos herdam timestamp + correlation_id opcional."""
    occurred_at: datetime
    correlation_id: Optional[str] = None


# ─── Atendimento ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class AtendimentoConfirmado(DomainEvent):
    atendimento_id: int = 0
    confirmado_por_id: Optional[int] = None  # None = bot WhatsApp


@dataclass(frozen=True)
class AtendimentoRealizado(DomainEvent):
    atendimento_id: int = 0
    cliente_id: int = 0
    profissional_id: int = 0


@dataclass(frozen=True)
class AtendimentoCancelado(DomainEvent):
    atendimento_id: int = 0
    motivo: str = ''
    cancelado_por_cliente: bool = False


@dataclass(frozen=True)
class AtendimentoFaltou(DomainEvent):
    atendimento_id: int = 0
    cliente_id: int = 0


# ─── Retorno (F-RET) ─────────────────────────────────────────────────
@dataclass(frozen=True)
class RetornoSugerido(DomainEvent):
    atendimento_origem_id: int = 0
    atendimento_retorno_id: int = 0
    cliente_id: int = 0
    procedimento_id: int = 0


# ─── Cashback (F-CSB) ────────────────────────────────────────────────
@dataclass(frozen=True)
class CashbackLiberado(DomainEvent):
    movimento_id: int = 0
    cliente_indicador_id: int = 0
    cliente_indicada_id: int = 0
    valor: str = '0.00'  # Decimal serializado p/ frozen


@dataclass(frozen=True)
class CashbackEstornado(DomainEvent):
    movimento_estorno_id: int = 0
    cliente_indicador_id: int = 0
    valor: str = '0.00'
    motivo: str = ''


# ─── Comissao ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ComissaoCalculada(DomainEvent):
    movimento_comissao_id: int = 0
    profissional_id: int = 0
    atendimento_id: int = 0
    valor: str = '0.00'
