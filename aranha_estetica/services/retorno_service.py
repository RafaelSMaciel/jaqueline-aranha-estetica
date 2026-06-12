"""F-RET — Retorno Obrigatorio.

Reage a AtendimentoRealizado. Se procedimento.exige_retorno, cria
Atendimento filho com eh_retorno=True, valor_cobrado=0, status PENDENTE.
Recepcao confirma data efetiva via painel.

Idempotente: 1 retorno por atendimento_origem.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from django.db import transaction
from django.utils import timezone

from ..domain.event_bus import EventBus
from ..domain.events import AtendimentoRealizado, RetornoSugerido
from ..models import Atendimento

logger = logging.getLogger(__name__)


class RetornoService:
    """Cria atendimentos de retorno automaticos."""

    @staticmethod
    @transaction.atomic
    def sugerir_retorno(atendimento_origem: Atendimento) -> Optional[Atendimento]:
        """Cria atendimento PENDENTE de retorno.

        Pre-condicoes (qualquer falha -> None):
          - procedimento.exige_retorno == True
          - atendimento_origem.eh_retorno == False
          - nao existe retorno previo para este origem (idempotente)
        """
        proc = atendimento_origem.procedimento
        if not proc.exige_retorno:
            return None
        if atendimento_origem.eh_retorno:
            return None
        if Atendimento.objects.filter(
            atendimento_origem=atendimento_origem, eh_retorno=True,
        ).exists():
            logger.info(
                'retorno_ja_sugerido',
                extra={'atendimento_origem_id': atendimento_origem.pk},
            )
            return None

        base = atendimento_origem.data_hora_fim
        min_dias = proc.retorno_minimo_dias or 0
        max_dias = proc.retorno_maximo_dias or min_dias
        janela_inicio = base + timedelta(days=min_dias)
        janela_fim = base + timedelta(days=max_dias)
        sugerida = janela_inicio + (janela_fim - janela_inicio) / 2
        duracao = proc.duracao_retorno_minutos or 30

        retorno = Atendimento.objects.create(
            cliente=atendimento_origem.cliente,
            profissional=atendimento_origem.profissional,
            procedimento=proc,
            data_hora_inicio=sugerida,
            data_hora_fim=sugerida + timedelta(minutes=duracao),
            valor_cobrado=0,
            valor_original=0,
            descricao_preco='Retorno obrigatorio (sem cobranca)',
            status=Atendimento.STATUS_PENDENTE,
            eh_retorno=True,
            atendimento_origem=atendimento_origem,
        )

        transaction.on_commit(lambda: EventBus.publish(RetornoSugerido(
            occurred_at=timezone.now(),
            atendimento_origem_id=atendimento_origem.pk,
            atendimento_retorno_id=retorno.pk,
            cliente_id=atendimento_origem.cliente_id,
            procedimento_id=proc.pk,
        )))
        logger.info(
            'retorno_sugerido',
            extra={
                'origem_id': atendimento_origem.pk,
                'retorno_id': retorno.pk,
                'cliente_id': atendimento_origem.cliente_id,
            },
        )
        return retorno


@EventBus.subscribe(AtendimentoRealizado)
def _on_realizado_sugere_retorno(event: AtendimentoRealizado) -> None:
    """Handler: AtendimentoRealizado -> RetornoService."""
    try:
        atendimento = (
            Atendimento.objects
            .select_related('procedimento', 'cliente', 'profissional')
            .get(pk=event.atendimento_id)
        )
    except Atendimento.DoesNotExist:
        return
    RetornoService.sugerir_retorno(atendimento)
