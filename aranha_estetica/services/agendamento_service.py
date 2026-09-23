"""Application Service de agendamento — aprovacao/rejeicao pelo painel.

A criacao do agendamento publico vive em views/booking_public.py (OTP por
telefone, validacao de slot via services.disponibilidade e lock de slot).
Aqui ficam as transicoes PENDENTE -> AGENDADO/CANCELADO disparadas pela
recepcao, com auditoria e e-mail ao cliente.
"""
import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction

from ..models import Atendimento
from ..utils.audit import registrar_log
from ..utils.datas import fmt_local

logger = logging.getLogger(__name__)

FORMATO_DATA_HORA = '%d/%m/%Y às %H:%M'


def formatar_brl(valor) -> str:
    """Valor monetario em pt-BR ('R$ 1.234,50'); '' se vazio/invalido."""
    if valor in (None, ''):
        return ''
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return ''
    texto = f'{numero:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return f'R$ {texto}'


def formatar_data_hora(dt) -> str:
    """Data/hora no fuso da clinica p/ textos ao cliente/equipe."""
    return fmt_local(dt, FORMATO_DATA_HORA)


class AgendamentoService:
    """Transicoes de agendamento feitas pela recepcao (painel)."""

    @transaction.atomic
    def aprovar(self, atendimento: Atendimento, by_user=None) -> bool:
        """Aprova atendimento PENDENTE -> AGENDADO + dispara email confirmacao.

        Returns:
            True se transitou. False se ja estava em outro estado (no-op).
        """
        if atendimento.status != Atendimento.STATUS_PENDENTE:
            return False

        atendimento.aprovar(by_user=by_user)
        registrar_log(by_user, 'Aprovou agendamento', 'atendimento', atendimento.pk)

        if atendimento.cliente.email:
            valor = atendimento.valor_cobrado
            dados = {
                'nome': atendimento.cliente.nome,
                'procedimento': atendimento.procedimento.nome,
                'profissional': atendimento.profissional.nome,
                'data_hora': formatar_data_hora(atendimento.data_hora_inicio),
                'valor': formatar_brl(valor) if valor else 'A consultar',
            }
            email = atendimento.cliente.email
            transaction.on_commit(
                lambda: self._enviar_email_confirmacao(email, dados), robust=True,
            )
        return True

    @transaction.atomic
    def rejeitar(self, atendimento: Atendimento, motivo: str = '', by_user=None) -> bool:
        """Rejeita atendimento PENDENTE -> CANCELADO.

        Returns:
            True se transitou. False se ja estava em outro estado.
        """
        if atendimento.status != Atendimento.STATUS_PENDENTE:
            return False
        atendimento.cancelar(motivo=motivo or 'rejeitado pela recepcao', by_user=by_user)
        registrar_log(by_user, 'Rejeitou agendamento', 'atendimento', atendimento.pk)

        if atendimento.cliente.email:
            dados = {
                'nome': atendimento.cliente.nome,
                'procedimento': atendimento.procedimento.nome,
                'profissional': atendimento.profissional.nome,
                'data_hora': formatar_data_hora(atendimento.data_hora_inicio),
            }
            email = atendimento.cliente.email
            transaction.on_commit(
                lambda: self._enviar_email_cancelamento(email, dados), robust=True,
            )
        return True

    @staticmethod
    def _enviar_email_cancelamento(email: str, dados: dict) -> None:
        """Enfileira email de cancelamento via Celery."""
        from ..tasks import send_email_async
        send_email_async.delay('enviar_cancelamento_email', email, dados)

    @staticmethod
    def _enviar_email_confirmacao(email: str, dados: dict) -> None:
        """Enfileira email de confirmacao via Celery."""
        from ..tasks import send_email_async
        send_email_async.delay('enviar_confirmacao_agendamento_email', email, dados)
