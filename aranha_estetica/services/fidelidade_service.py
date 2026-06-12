"""F-CSB — Programa de Fidelidade por Indicacao.

Reusa CreditoCliente + MovimentoCredito existentes.
Origem 'CASHBACK_INDICACAO' = credito gerado por 1o atendimento pago da indicada.
Origem 'CASHBACK_ESTORNO' = anulacao em cancelamento.

Idempotencia via UNIQUE(credito, atendimento, origem='CASHBACK_INDICACAO').
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..constants import VALOR_CASHBACK_INDICACAO
from ..domain.event_bus import EventBus
from ..domain.events import (
    AtendimentoCancelado,
    AtendimentoRealizado,
    CashbackEstornado,
    CashbackLiberado,
)
from ..models import Atendimento, CreditoCliente, MovimentoCredito

logger = logging.getLogger(__name__)


class FidelidadeService:
    """Application Service de fidelidade — credita indicador no 1o pago da indicada."""

    @staticmethod
    @transaction.atomic
    def liberar_cashback_indicacao(atendimento: Atendimento) -> Optional[MovimentoCredito]:
        """Credita VALOR_CASHBACK_INDICACAO no indicador.

        Pre-condicoes (qualquer falha -> None):
          - atendimento.status == REALIZADO
          - eh_retorno == False
          - cliente.indicado_por preenchido
          - 1o REALIZADO + pago da cliente (valor_cobrado > 0)

        Idempotencia: UNIQUE(credito, atendimento, origem='CASHBACK_INDICACAO').
        """
        cliente = atendimento.cliente
        if not cliente.indicado_por_id:
            return None
        if atendimento.eh_retorno:
            return None
        if atendimento.status != Atendimento.STATUS_REALIZADO:
            return None
        if not atendimento.valor_cobrado or atendimento.valor_cobrado <= 0:
            return None

        # 1o pago: nenhum outro REALIZADO+pago anterior
        if Atendimento.objects.filter(
            cliente=cliente,
            status=Atendimento.STATUS_REALIZADO,
            eh_retorno=False,
            valor_cobrado__gt=0,
        ).exclude(pk=atendimento.pk).exists():
            return None

        credito, _ = CreditoCliente.objects.select_for_update().get_or_create(
            cliente_id=cliente.indicado_por_id,
        )
        novo_saldo = credito.saldo + VALOR_CASHBACK_INDICACAO

        try:
            movimento = MovimentoCredito.objects.create(
                credito=credito,
                tipo='CREDITO',
                origem='CASHBACK_INDICACAO',
                valor=VALOR_CASHBACK_INDICACAO,
                saldo_resultante=novo_saldo,
                atendimento=atendimento,
                observacoes=f'Indicacao: {cliente.nome_completo} (cli #{cliente.pk})',
            )
        except IntegrityError:
            logger.info(
                'cashback_credito_duplicado_ignorado',
                extra={'atendimento_id': atendimento.pk},
            )
            return None

        credito.saldo = novo_saldo
        credito.save(update_fields=['saldo', 'atualizado_em'])

        transaction.on_commit(lambda: EventBus.publish(CashbackLiberado(
            occurred_at=timezone.now(),
            movimento_id=movimento.pk,
            cliente_indicador_id=cliente.indicado_por_id,
            cliente_indicada_id=cliente.pk,
            valor=str(VALOR_CASHBACK_INDICACAO),
        )))
        logger.info(
            'cashback_liberado',
            extra={
                'movimento_id': movimento.pk,
                'indicador_id': cliente.indicado_por_id,
                'valor': str(VALOR_CASHBACK_INDICACAO),
            },
        )
        return movimento

    @staticmethod
    @transaction.atomic
    def estornar_cashback_de_atendimento(atendimento: Atendimento, motivo: str) -> int:
        """Anula creditos gerados por este atendimento.

        Cria movimento DEBITO origem CASHBACK_ESTORNO. Saldo nunca negativo.
        Returns: numero de movimentos estornados.
        """
        creditos = MovimentoCredito.objects.select_for_update().filter(
            atendimento=atendimento,
            origem='CASHBACK_INDICACAO',
            tipo='CREDITO',
        )
        estornados = 0
        for cred_mov in creditos:
            credito = cred_mov.credito
            saldo_anterior = credito.saldo
            valor = cred_mov.valor
            saldo_pos = max(Decimal('0.00'), saldo_anterior - valor)

            MovimentoCredito.objects.create(
                credito=credito,
                tipo='DEBITO',
                origem='CASHBACK_ESTORNO',
                valor=valor,
                saldo_resultante=saldo_pos,
                atendimento=atendimento,
                observacoes=f'Estorno credito #{cred_mov.pk}: {motivo}',
            )

            credito.saldo = saldo_pos
            credito.save(update_fields=['saldo', 'atualizado_em'])

            if saldo_anterior < valor:
                logger.warning(
                    'cashback_estorno_saldo_insuficiente',
                    extra={
                        'movimento_origem_id': cred_mov.pk,
                        'credito_id': credito.pk,
                        'saldo_anterior': str(saldo_anterior),
                        'valor_estorno': str(valor),
                    },
                )

            transaction.on_commit(lambda v=valor, c=credito: EventBus.publish(
                CashbackEstornado(
                    occurred_at=timezone.now(),
                    movimento_estorno_id=cred_mov.pk,
                    cliente_indicador_id=c.cliente_id,
                    valor=str(v),
                    motivo=motivo,
                ),
            ))
            estornados += 1
        return estornados


@EventBus.subscribe(AtendimentoRealizado)
def _on_realizado_libera_cashback(event: AtendimentoRealizado) -> None:
    """Handler: AtendimentoRealizado -> credita indicador."""
    try:
        atendimento = Atendimento.objects.select_related('cliente').get(pk=event.atendimento_id)
    except Atendimento.DoesNotExist:
        return
    FidelidadeService.liberar_cashback_indicacao(atendimento)


@EventBus.subscribe(AtendimentoCancelado)
def _on_cancelado_estorna_cashback(event: AtendimentoCancelado) -> None:
    """Handler: AtendimentoCancelado -> estorna credito gerado."""
    try:
        atendimento = Atendimento.objects.get(pk=event.atendimento_id)
    except Atendimento.DoesNotExist:
        return
    FidelidadeService.estornar_cashback_de_atendimento(
        atendimento, motivo='atendimento cancelado',
    )
