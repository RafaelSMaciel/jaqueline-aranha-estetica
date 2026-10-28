"""Application Service de agendamento — aprovacao/rejeicao pelo painel.

A criacao do agendamento publico vive em views/publico/agendamento.py (OTP por
telefone, validacao de slot via services.disponibilidade e lock de slot).
Aqui ficam as transicoes PENDENTE -> AGENDADO/CANCELADO disparadas pela
recepcao, com auditoria e e-mail ao cliente (fila: services/comunicacao).
"""
import logging

from django.db import transaction

from ..models import Atendimento, Notificacao
from ..utils.auditoria import registrar_log
from ..utils.datas import fmt_local
from ..utils.formatos import formatar_brl

logger = logging.getLogger(__name__)

FORMATO_DATA_HORA = '%d/%m/%Y às %H:%M'


def avisar_cancelamento(atendimento, mensagem: str = '') -> None:
    """E-mail a cliente quando a CLINICA cancela (painel, rejeicao, Django admin, pedido
    PENDENTE que venceu sem aprovacao). Cancelamento pela propria cliente nao avisa.

    Fila de e-mail (services/comunicacao): sai no on_commit; falha de envio nunca desfaz o
    cancelamento e o cron reenvia.
    """
    from .comunicacao import enfileirar_email
    enfileirar_email(atendimento, Notificacao.Tipo.CANCELAMENTO, mensagem)


def formatar_data_hora(dt) -> str:
    """Data/hora no fuso da clinica p/ textos ao cliente/equipe."""
    return fmt_local(dt, FORMATO_DATA_HORA)


def formatar_valor_atendimento(atendimento) -> str:
    """Valor p/ o cliente: retorno gratuito nao vira 'A consultar' (R$ 0) e sessao que um pacote
    dela cobre aparece como do pacote, nunca com o preco cheio (services/pacotes)."""
    if atendimento.eh_retorno:
        return 'Sem custo (retorno)'
    from .pacotes import sessoes_previstas
    previsto = sessoes_previstas([atendimento]).get(atendimento.pk)
    if previsto:
        return f"Sessão do pacote {previsto['pacote']} (restam {previsto['restam']})"
    if atendimento.valor_cobrado is None:
        return 'A consultar'
    return formatar_brl(atendimento.valor_cobrado)


class AgendamentoService:
    """Transicoes de agendamento feitas pela recepcao (painel)."""

    @transaction.atomic
    def aprovar(self, atendimento: Atendimento, by_user=None, request=None) -> bool:
        """Aprova atendimento PENDENTE -> AGENDADO + dispara email confirmacao.

        request (opcional) vai p/ a auditoria (IP de origem).

        Returns:
            True se transitou. False se ja estava em outro estado (no-op).
        """
        if atendimento.status != Atendimento.STATUS_PENDENTE:
            return False

        atendimento.aprovar(by_user=by_user)
        registrar_log(by_user, 'Aprovou agendamento', 'atendimento', atendimento.pk, request=request)

        from .comunicacao import enfileirar_email
        enfileirar_email(atendimento, Notificacao.Tipo.CONFIRMACAO)
        return True

    @transaction.atomic
    def rejeitar(self, atendimento: Atendimento, motivo: str = '', by_user=None, request=None) -> bool:
        """Rejeita atendimento PENDENTE -> CANCELADO.

        request (opcional) vai p/ a auditoria (IP de origem).

        Returns:
            True se transitou. False se ja estava em outro estado.
        """
        if atendimento.status != Atendimento.STATUS_PENDENTE:
            return False
        atendimento.cancelar(motivo=motivo or 'rejeitado pela recepcao', by_user=by_user)
        registrar_log(by_user, 'Rejeitou agendamento', 'atendimento', atendimento.pk, request=request)

        avisar_cancelamento(atendimento)
        return True

