"""F-CSB — Programa de Fidelidade por Indicacao.

Reusa Carteira + MovimentoCarteira existentes.
Origem 'CASHBACK_INDICACAO' = carteira gerado por 1o atendimento pago da indicada.
Origem 'CASHBACK_ESTORNO' = anulacao em cancelamento.

Idempotencia via UNIQUE(carteira, atendimento, origem='CASHBACK_INDICACAO').
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from django.db import IntegrityError, transaction

from ..constants import VALOR_CASHBACK_INDICACAO
from ..models import Atendimento, Carteira, MovimentoCarteira

logger = logging.getLogger(__name__)


class FidelidadeService:
    """Application Service de fidelidade — credita indicador no 1o pago da indicada."""

    @staticmethod
    @transaction.atomic
    def liberar_cashback_indicacao(atendimento: Atendimento) -> Optional[MovimentoCarteira]:
        """Credita VALOR_CASHBACK_INDICACAO no indicador.

        Pre-condicoes (qualquer falha -> None):
          - atendimento.status == REALIZADO
          - eh_retorno == False
          - cliente.indicado_por preenchido
          - 1o REALIZADO + pago da cliente (valor_cobrado > 0)

        Idempotencia: UNIQUE(carteira, atendimento, origem='CASHBACK_INDICACAO').
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

        carteira, _ = Carteira.objects.select_for_update().get_or_create(
            cliente_id=cliente.indicado_por_id,
        )
        novo_saldo = carteira.saldo + VALOR_CASHBACK_INDICACAO

        try:
            # savepoint: o credito duplicado (UNIQUE) nao aborta a transacao da transicao
            with transaction.atomic():
                movimento = MovimentoCarteira.objects.create(
                    carteira=carteira,
                    tipo=MovimentoCarteira.Tipo.CREDITO,
                    origem='CASHBACK_INDICACAO',
                    valor=VALOR_CASHBACK_INDICACAO,
                    saldo_resultante=novo_saldo,
                    atendimento=atendimento,
                    # So o id: o ledger e imutavel (trigger) e o esquecimento LGPD nunca
                    # apagaria o nome da indicada daqui (a 0053 saneou as linhas antigas).
                    observacoes=f'Indicacao (cli #{cliente.pk})',
                )
        except IntegrityError:
            logger.info(
                'cashback_credito_duplicado_ignorado',
                extra={'atendimento_id': atendimento.pk},
            )
            return None

        carteira.saldo = novo_saldo
        carteira.save(update_fields=['saldo', 'atualizado_em'])

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
        creditos = list(MovimentoCarteira.objects.select_for_update().filter(
            atendimento=atendimento,
            origem='CASHBACK_INDICACAO',
            tipo=MovimentoCarteira.Tipo.CREDITO,
        ))
        # Idempotente: cancelamento publicado 2x (corrida/duplo clique) nao
        # debita de novo — o lock acima serializa e a 2a chamada ve o estorno.
        if not creditos or MovimentoCarteira.objects.filter(
            atendimento=atendimento, origem=MovimentoCarteira.Origem.CASHBACK_ESTORNO, tipo=MovimentoCarteira.Tipo.DEBITO,
        ).exists():
            return 0
        estornados = 0
        for cred_mov in creditos:
            carteira = cred_mov.carteira
            saldo_anterior = carteira.saldo
            valor = cred_mov.valor
            saldo_pos = max(Decimal('0.00'), saldo_anterior - valor)

            MovimentoCarteira.objects.create(
                carteira=carteira,
                tipo=MovimentoCarteira.Tipo.DEBITO,
                origem='CASHBACK_ESTORNO',
                valor=valor,
                saldo_resultante=saldo_pos,
                atendimento=atendimento,
                observacoes=f'Estorno credito #{cred_mov.pk}: {motivo}',
            )

            carteira.saldo = saldo_pos
            carteira.save(update_fields=['saldo', 'atualizado_em'])

            if saldo_anterior < valor:
                logger.warning(
                    'cashback_estorno_saldo_insuficiente',
                    extra={
                        'movimento_origem_id': cred_mov.pk,
                        'credito_id': carteira.pk,
                        'saldo_anterior': str(saldo_anterior),
                        'valor_estorno': str(valor),
                    },
                )

            estornados += 1
        return estornados


    # ─── uso do credito (a cliente abate o proprio saldo num atendimento) ───────
    @staticmethod
    def saldos(cliente_ids) -> dict:
        """{cliente_id: saldo > 0} das carteiras (1 consulta p/ a lista)."""
        return dict(Carteira.objects.filter(cliente_id__in=set(cliente_ids), saldo__gt=0)
                    .values_list('cliente_id', 'saldo'))

    @staticmethod
    def creditos_usados(atendimento_ids) -> dict:
        """{atendimento_id: valor abatido e nao devolvido}."""
        from django.db.models import Sum
        usos = dict(MovimentoCarteira.objects.filter(
            atendimento_id__in=set(atendimento_ids), origem=MovimentoCarteira.Origem.USO_AGENDAMENTO,
            tipo=MovimentoCarteira.Tipo.DEBITO,
        ).values('atendimento_id').annotate(total=Sum('valor')).values_list('atendimento_id', 'total'))
        devolvidos = dict(MovimentoCarteira.objects.filter(
            atendimento_id__in=set(usos), origem=MovimentoCarteira.Origem.CANCELAMENTO,
            tipo=MovimentoCarteira.Tipo.CREDITO,
        ).values('atendimento_id').annotate(total=Sum('valor')).values_list('atendimento_id', 'total'))
        return {at: total - devolvidos.get(at, Decimal('0')) for at, total in usos.items()
                if total - devolvidos.get(at, Decimal('0')) > 0}

    @staticmethod
    @transaction.atomic
    def usar_credito(atendimento: Atendimento, valor: Decimal, usuario=None) -> MovimentoCarteira:
        """Abate `valor` do credito da cliente no atendimento (DEBITO USO_AGENDAMENTO, com autor).

        Limites: ate o saldo e ate o valor do atendimento; 1 uso por atendimento; nao em retorno,
        cancelado, faltou ou reagendado. O valor_cobrado segue cheio (base da comissao: a
        profissional nao paga o marketing da clinica) — o desconto aparece no painel e no
        financeiro como "credito de indicacao usado". Cancelar/faltar/reagendar devolve.
        """
        from ..excecoes import RegraDeNegocio

        if atendimento.eh_retorno or atendimento.status in (
                Atendimento.STATUS_CANCELADO, Atendimento.STATUS_FALTOU, Atendimento.STATUS_REAGENDADO):
            raise RegraDeNegocio('Este atendimento não aceita crédito de indicação.', codigo='credito_invalido')
        if FidelidadeService.creditos_usados([atendimento.pk]):
            raise RegraDeNegocio('O crédito já foi usado neste atendimento.', codigo='credito_ja_usado')
        carteira = Carteira.objects.select_for_update().filter(cliente_id=atendimento.cliente_id).first()
        saldo = carteira.saldo if carteira else Decimal('0')
        limite = min(saldo, atendimento.valor_cobrado or Decimal('0'))
        if carteira is None or valor <= 0 or valor > limite:
            raise RegraDeNegocio(f'Crédito disponível para este atendimento: até R$ {limite:.2f}.'.replace('.', ','),
                                 codigo='credito_insuficiente')
        movimento = MovimentoCarteira.objects.create(
            carteira=carteira, tipo=MovimentoCarteira.Tipo.DEBITO, origem=MovimentoCarteira.Origem.USO_AGENDAMENTO,
            valor=valor, saldo_resultante=saldo - valor, atendimento=atendimento,
            usuario=usuario if getattr(usuario, 'pk', None) else None,
            observacoes=f'Uso no atendimento #{atendimento.pk}',
        )
        carteira.saldo = saldo - valor
        carteira.save(update_fields=['saldo', 'atualizado_em'])
        logger.info('credito_indicacao_usado', extra={'atendimento_id': atendimento.pk, 'valor': str(valor)})
        return movimento

    @staticmethod
    @transaction.atomic
    def devolver_credito_usado(atendimento: Atendimento) -> int:
        """Atendimento que nao aconteceu (cancelado, faltou, reagendado): o credito abatido volta
        (CREDITO CANCELAMENTO). Idempotente."""
        usado = FidelidadeService.creditos_usados([atendimento.pk]).get(atendimento.pk)
        if not usado:
            return 0
        carteira = Carteira.objects.select_for_update().get(cliente_id=atendimento.cliente_id)
        MovimentoCarteira.objects.create(
            carteira=carteira, tipo=MovimentoCarteira.Tipo.CREDITO, origem=MovimentoCarteira.Origem.CANCELAMENTO,
            valor=usado, saldo_resultante=carteira.saldo + usado, atendimento=atendimento,
            observacoes=f'Devolução do uso no atendimento #{atendimento.pk}',
        )
        carteira.saldo += usado
        carteira.save(update_fields=['saldo', 'atualizado_em'])
        return 1
