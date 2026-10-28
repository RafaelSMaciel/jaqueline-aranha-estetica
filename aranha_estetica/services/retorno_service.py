"""F-RET — Retorno Obrigatorio.

Reage a AtendimentoRealizado. Se procedimento.exige_retorno, cria
Atendimento filho com eh_retorno=True, valor_cobrado=0, status PENDENTE,
no primeiro horario LIVRE do profissional (expediente, folgas, feriados e
bloqueios via SlotService) dentro da janela [min, max] dias — comecando
pelo meio da janela. Sem horario livre: nao cria (recepcao agenda).
Recepcao confirma data efetiva via painel.

Idempotente: 1 retorno por atendimento_origem.
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime, timedelta
from typing import Optional

from django.db import transaction
from django.utils import timezone

from ..excecoes import HorarioIndisponivel
from ..models import Atendimento
from .agenda import reservar

logger = logging.getLogger(__name__)

# Minimo de dias ate o retorno sugerido quando retorno_minimo_dias nao
# esta configurado — evita sugerir retorno no mesmo instante do fim do
# atendimento de origem (janela colapsada).
RETORNO_MINIMO_DIAS_PADRAO = 1


def _primeiro_horario_livre(origem: Atendimento, min_dias: int, max_dias: int):
    """Primeiro slot livre (aware) na janela, do meio para as pontas."""
    from .disponibilidade import SlotService
    from ..utils.datas import data_local

    prof = origem.profissional
    if prof is None:
        return None
    # Retorno e agendado pela clinica: o horizonte do booking publico
    # (max_advance_dias) e o aviso minimo nao se aplicam.
    prof_retorno = copy.copy(prof)
    prof_retorno.max_advance_dias = max_dias + 30
    prof_retorno.min_notice_horas = 0

    fim_origem = origem.data_hora_fim
    limite_inicio = fim_origem + timedelta(days=min_dias)
    base = data_local(fim_origem)
    if base is None:
        return None
    alvo = (min_dias + max_dias) // 2
    for dias in sorted(range(min_dias, max_dias + 1), key=lambda d: (abs(d - alvo), d)):
        dia = base + timedelta(days=dias)
        for hhmm in SlotService.slots_livres(prof_retorno, dia, origem.procedimento):
            hora = datetime.strptime(hhmm, '%H:%M').time()
            inicio = timezone.make_aware(datetime.combine(dia, hora))
            if inicio >= limite_inicio:
                return inicio
    return None


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

        # Sem minimo configurado, garante +1 dia para nao sugerir retorno no
        # mesmo instante do fim do atendimento de origem (janela colapsada).
        min_dias = proc.retorno_minimo_dias or RETORNO_MINIMO_DIAS_PADRAO
        max_dias = max(proc.retorno_maximo_dias or min_dias, min_dias)
        sugerida = _primeiro_horario_livre(atendimento_origem, min_dias, max_dias)
        if sugerida is None:
            logger.info(
                'retorno_sem_slot',
                extra={'atendimento_origem_id': atendimento_origem.pk},
            )
            return None

        from django.db import IntegrityError
        try:
            # duracao de retorno; INSERT em savepoint: slot ocupado nao aborta a transicao
            retorno = reservar(
                cliente=atendimento_origem.cliente, profissional=atendimento_origem.profissional,
                procedimento=proc, inicio=sugerida, status=Atendimento.STATUS_PENDENTE,
                eh_retorno=True, atendimento_origem=atendimento_origem,
                preco={
                    'valor_cobrado': 0, 'valor_original': 0, 'promocao': None,
                    'descricao_preco': 'Retorno obrigatorio (sem cobranca)',
                },
            )
        except (HorarioIndisponivel, IntegrityError):
            # Slot sugerido ja ocupado. Best-effort — recepcao agenda o retorno manualmente.
            logger.warning(
                'retorno_slot_sugerido_ocupado',
                extra={'atendimento_origem_id': atendimento_origem.pk},
            )
            return None

        logger.info(
            'retorno_sugerido',
            extra={
                'origem_id': atendimento_origem.pk,
                'retorno_id': retorno.pk,
                'cliente_id': atendimento_origem.cliente_id,
            },
        )
        return retorno

