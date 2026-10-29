"""Application Service de agendamento — aprovacao/rejeicao pelo painel.

O pedido feito pelo site (caso de uso inteiro) vive em services/agendamento_online.py.
Aqui ficam as transicoes PENDENTE -> AGENDADO/CANCELADO disparadas pela
recepcao, com auditoria e e-mail ao cliente (fila: services/comunicacao).
"""
import logging

from django.db import DatabaseError, transaction

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
            True se transitou. False se ja estava em outro estado (no-op) — inclusive quando outra
            tela decidiu entre a leitura e a trava da linha (sem excecao p/ a view tratar).
        """
        if atendimento.status != Atendimento.STATUS_PENDENTE:
            return False
        try:
            atendimento.aprovar(by_user=by_user)
        except Atendimento.TransicaoInvalida:   # outra tela decidiu entre a leitura e a trava
            return False
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
        try:
            atendimento.cancelar(motivo=motivo or 'rejeitado pela recepcao', by_user=by_user)
        except Atendimento.TransicaoInvalida:   # outra tela decidiu entre a leitura e a trava
            return False
        registrar_log(by_user, 'Rejeitou agendamento', 'atendimento', atendimento.pk, request=request)

        avisar_cancelamento(atendimento)
        return True

    def decidir_em_lote(self, ids, acao: str, *, by_user=None, request=None) -> int:
        """Aprova (acao='aprovar') ou rejeita os PENDENTES de `ids`, um a um e cada um na sua
        transacao: item que outra tela decidiu no meio do caminho (ou que falhou no banco) fica de
        fora sem desfazer os outros. Devolve quantos foram decididos."""
        pendentes = (
            Atendimento.objects.select_related('cliente', 'procedimento', 'profissional')
            .filter(pk__in=ids, status=Atendimento.STATUS_PENDENTE)
        )
        decididos = 0
        for atendimento in pendentes:
            try:
                if acao == 'aprovar':
                    ok = self.aprovar(atendimento, by_user=by_user, request=request)
                else:
                    ok = self.rejeitar(atendimento, motivo='Rejeitado em lote', by_user=by_user, request=request)
            except DatabaseError:
                logger.exception('decisao_em_lote_falhou', extra={'atendimento_id': atendimento.pk})
                ok = False
            decididos += ok
        return decididos

