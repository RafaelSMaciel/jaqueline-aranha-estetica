"""Domain events — fatos imutaveis que ocorreram no sistema.

Eventos sao publicados por services apos commit de transacao bem-sucedida.
Handlers reagem assincrona ou sincronamente (via EventBus).

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
class AtendimentoCriado(DomainEvent):
    atendimento_id: int = 0
    cliente_id: int = 0
    profissional_id: int = 0
    procedimento_id: int = 0


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


@dataclass(frozen=True)
class AtendimentoReagendado(DomainEvent):
    atendimento_anterior_id: int = 0
    atendimento_novo_id: int = 0


# ─── Cliente ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ClienteCadastrado(DomainEvent):
    cliente_id: int = 0
    via: str = 'admin'  # 'admin' | 'booking_publico' | 'import'


@dataclass(frozen=True)
class ConsentRegistrado(DomainEvent):
    cliente_id: int = 0
    canal: str = ''  # 'email_marketing' | 'whatsapp_nps' | 'whatsapp_confirmacao'
    aceito: bool = True
    ip: Optional[str] = None


# ─── Pacote ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class PacoteVendido(DomainEvent):
    pacote_cliente_id: int = 0
    cliente_id: int = 0


@dataclass(frozen=True)
class SessaoConsumida(DomainEvent):
    pacote_cliente_id: int = 0
    atendimento_id: int = 0
