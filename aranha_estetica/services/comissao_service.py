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
from decimal import Decimal
from typing import Optional

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from ..domain.event_bus import EventBus
from ..domain.events import (
    AtendimentoCancelado,
    AtendimentoRealizado,
    ComissaoCalculada,
)
from ..models import Atendimento, MovimentoComissao, RegraComissao

logger = logging.getLogger(__name__)


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
        candidatos = list(RegraComissao.objects.filter(ativo=True).filter(match_filter))

        # Score por especificidade: 3=ambos, 2=so prof, 1=so proc, 0=fallback
        def score(r: RegraComissao) -> int:
            return (1 if r.profissional_id else 0) * 2 + (1 if r.procedimento_id else 0)

        candidatos.sort(key=score, reverse=True)
        return candidatos[0] if candidatos else None

    @staticmethod
    @transaction.atomic
    def calcular_comissao(atendimento: Atendimento) -> Optional[MovimentoComissao]:
        """Calcula comissao para atendimento REALIZADO. Idempotente."""
        if atendimento.status != Atendimento.STATUS_REALIZADO:
            return None
        if atendimento.eh_retorno:
            return None  # retorno gratis nao gera comissao
        if not atendimento.valor_cobrado or atendimento.valor_cobrado <= 0:
            return None

        regra = ComissaoService.resolver_regra(
            atendimento.profissional_id, atendimento.procedimento_id,
        )
        if not regra:
            return None

        if regra.percentual is not None:
            valor = (atendimento.valor_cobrado * regra.percentual / Decimal('100')).quantize(Decimal('0.01'))
        else:
            valor = regra.valor or Decimal('0.00')

        if valor <= 0:
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
