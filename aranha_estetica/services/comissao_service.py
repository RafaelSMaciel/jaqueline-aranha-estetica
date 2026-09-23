"""Comissao Profissional — calcula comissao em AtendimentoRealizado.

Suporta percentual ou valor. Resolve regra mais especifica:
  1. (profissional, procedimento) — mais especifica
  2. (profissional, qualquer procedimento)
  3. (qualquer profissional, procedimento)
  4. (qualquer, qualquer) — fallback global

Idempotente via UNIQUE(atendimento) WHERE status in (PENDENTE, PAGA).
Estornado em AtendimentoCancelado.
"""
from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from ..domain.event_bus import EventBus
from ..domain.events import (
    AtendimentoCancelado,
    AtendimentoRealizado,
    ComissaoCalculada,
)
from ..models import Atendimento, ConsumoSessao, MovimentoComissao, RegraComissao

logger = logging.getLogger(__name__)

CENTAVO = Decimal('0.01')
CEM = Decimal('100')


def _centavos(valor: Decimal) -> Decimal:
    """Arredonda em centavos meio-para-cima — mesmo criterio de utils/precos."""
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)


class ComissaoService:
    """Calcula e estorna comissoes."""

    @staticmethod
    def resolver_regra(profissional_id: int, procedimento_id: int) -> Optional[RegraComissao]:
        """Retorna regra mais especifica ativa, None se nada bater."""
        match_filter = (
            Q(profissional_id=profissional_id, procedimento_id=procedimento_id)
            | Q(profissional_id=profissional_id, procedimento_id__isnull=True)
            | Q(profissional_id__isnull=True, procedimento_id=procedimento_id)
            | Q(profissional_id__isnull=True, procedimento_id__isnull=True)
        )
        # order_by explicito: com 2 regras de mesma especificidade vence a
        # editada por ultimo (desempate por pk). Sem isso o sort estavel
        # herdava a ordem do banco, que nao e garantida.
        candidatos = list(
            RegraComissao.objects.filter(ativo=True).filter(match_filter)
            .order_by('-atualizado_em', '-pk')
        )

        # Score por especificidade: 3=ambos, 2=so prof, 1=so proc, 0=fallback
        def score(r: RegraComissao) -> int:
            return (1 if r.profissional_id else 0) * 2 + (1 if r.procedimento_id else 0)

        candidatos.sort(key=score, reverse=True)
        return candidatos[0] if candidatos else None

    @staticmethod
    def base_calculo(atendimento: Atendimento) -> Decimal:
        """Valor sobre o qual incide a comissao.

        Sessao debitada de pacote: valor pago no pacote / total de sessoes do
        pacote (valor_cobrado da sessao e o preco cheio do procedimento e
        inflaria a comissao). Avulso: valor_cobrado.
        """
        consumo = (
            ConsumoSessao.objects.select_related('compra_pacote__pacote')
            .filter(atendimento_id=atendimento.pk).first()
        )
        if consumo is None:
            return atendimento.valor_cobrado or Decimal('0.00')
        compra = consumo.compra_pacote
        total_sessoes = compra.pacote.itens.aggregate(t=Sum('quantidade_sessoes'))['t'] or 0
        if total_sessoes <= 0:
            return Decimal('0.00')
        return _centavos(compra.valor_pago / Decimal(total_sessoes))

    @staticmethod
    @transaction.atomic
    def calcular_comissao(atendimento: Atendimento) -> Optional[MovimentoComissao]:
        """Calcula comissao para atendimento REALIZADO. Idempotente."""
        if atendimento.status != Atendimento.STATUS_REALIZADO:
            return None
        if atendimento.eh_retorno:
            return None  # retorno gratis nao gera comissao
        base = ComissaoService.base_calculo(atendimento)
        if base <= 0:
            return None

        regra = ComissaoService.resolver_regra(
            atendimento.profissional_id, atendimento.procedimento_id,
        )
        if not regra:
            return None

        if regra.percentual is not None:
            percentual = regra.percentual
            if percentual > CEM:
                # Regra antiga/invalida (>100%) nunca paga mais que o atendimento
                # rendeu; o CHECK/validator do model barra novas.
                logger.warning('regra_comissao_percentual_acima_de_100', extra={'regra_id': regra.pk})
                percentual = CEM
            valor = _centavos(base * percentual / CEM)
        else:
            valor = regra.valor or Decimal('0.00')

        if valor <= 0:
            return None

        # Idempotencia total por atendimento: nunca cria 2a comissao (mesmo apos
        # ESTORNADA) — previne double-pay quando um atendimento volta a Realizado.
        # A UNIQUE constraint so cobre PENDENTE/PAGA; este guard cobre o resto.
        if MovimentoComissao.objects.filter(atendimento=atendimento).exists():
            logger.info('comissao_ja_existe_ignorada', extra={'atendimento_id': atendimento.pk})
            return None

        try:
            movimento = MovimentoComissao.objects.create(
                profissional=atendimento.profissional,
                atendimento=atendimento,
                regra=regra,
                valor=valor,
                status=MovimentoComissao.STATUS_PENDENTE,
            )
        except IntegrityError:
            logger.info(
                'comissao_duplicada_ignorada',
                extra={'atendimento_id': atendimento.pk},
            )
            return None

        transaction.on_commit(lambda: EventBus.publish(ComissaoCalculada(
            occurred_at=timezone.now(),
            movimento_comissao_id=movimento.pk,
            profissional_id=atendimento.profissional_id,
            atendimento_id=atendimento.pk,
            valor=str(valor),
        )))
        logger.info(
            'comissao_calculada',
            extra={
                'movimento_id': movimento.pk,
                'profissional_id': atendimento.profissional_id,
                'valor': str(valor),
            },
        )
        return movimento

    @staticmethod
    @transaction.atomic
    def marcar_paga(movimento_id: int) -> bool:
        """Marca uma comissao PENDENTE como PAGA (idempotente, sob lock).

        Retorna True se efetuou a baixa; False se nao existe ou ja nao estava
        pendente (ex.: ja paga ou estornada).
        """
        mov = (
            MovimentoComissao.objects.select_for_update()
            .filter(pk=movimento_id)
            .first()
        )
        if not mov or mov.status != MovimentoComissao.STATUS_PENDENTE:
            return False
        mov.status = MovimentoComissao.STATUS_PAGA
        mov.pago_em = timezone.now()
        mov.save(update_fields=['status', 'pago_em'])
        logger.info(
            'comissao_paga',
            extra={'movimento_id': mov.pk, 'valor': str(mov.valor)},
        )
        return True

    @staticmethod
    @transaction.atomic
    def estornar_comissao(atendimento: Atendimento) -> int:
        """Marca comissoes ativas do atendimento como ESTORNADA."""
        ativas = MovimentoComissao.objects.select_for_update().filter(
            atendimento=atendimento,
            status__in=[MovimentoComissao.STATUS_PENDENTE, MovimentoComissao.STATUS_PAGA],
        )
        count = ativas.update(status=MovimentoComissao.STATUS_ESTORNADA)
        if count:
            logger.info(
                'comissoes_estornadas',
                extra={'atendimento_id': atendimento.pk, 'count': count},
            )
        return count


@EventBus.subscribe(AtendimentoRealizado)
def _on_realizado_calcula_comissao(event: AtendimentoRealizado) -> None:
    try:
        atendimento = Atendimento.objects.select_related('profissional', 'procedimento').get(
            pk=event.atendimento_id,
        )
    except Atendimento.DoesNotExist:
        return
    ComissaoService.calcular_comissao(atendimento)


@EventBus.subscribe(AtendimentoCancelado)
def _on_cancelado_estorna_comissao(event: AtendimentoCancelado) -> None:
    try:
        atendimento = Atendimento.objects.get(pk=event.atendimento_id)
    except Atendimento.DoesNotExist:
        return
    ComissaoService.estornar_comissao(atendimento)
