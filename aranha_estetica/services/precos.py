"""Preco de vitrine ("A partir de") — a mesma conta do agendamento, em lote.

O valor GRAVADO no atendimento sai de ``utils.precos.preco_com_promocao`` (profissional e data do
atendimento). A vitrine — cards do wizard, widget embutido, especialidades, pagina do servico e
promocoes — mostra o preco base do dia (sem base: o menor entre os profissionais) com a promocao
que o agendamento aplicaria sobre ele. Uma funcao so para todas essas telas: antes eram duas
privadas divergentes (_precos_card, _precos_vitrine) e uma consulta de promocao por procedimento.
CDC art. 30/35: a vitrine nunca anuncia um valor que o atendimento nao grava.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Q

from ..models import Promocao
from ..utils.datas import hoje as hoje_local
from ..utils.precos import aplicar_promocao, melhor_promocao, preco_base_map, promocao_aplicavel


@dataclass(frozen=True)
class PrecoExibido:
    """Preco da vitrine: ``final`` = ``cheio`` com a ``promocao`` (None = sem desconto no dia)."""
    final: Decimal
    cheio: Decimal
    promocao: Promocao | None = None

    @property
    def em_promocao(self) -> bool:
        return self.promocao is not None


def promocoes_vigentes(dia: date | None = None, procedimento_ids=None) -> list[Promocao]:
    """Promocoes ativas no dia que o agendamento honra (``promocao_aplicavel``), numa consulta.

    procedimento_ids: so as desses procedimentos + as gerais (None = todas).
    """
    dia = dia or hoje_local()
    qs = Promocao.objects.filter(ativa=True, data_inicio__lte=dia, data_fim__gte=dia)
    if procedimento_ids is not None:
        qs = qs.filter(Q(procedimento_id__in=procedimento_ids) | Q(procedimento__isnull=True))
    return [p for p in qs.select_related('procedimento').order_by('pk') if promocao_aplicavel(p)]


def candidatas_de(procedimento_id: int, vigentes: list[Promocao]) -> list[Promocao]:
    """As promocoes vigentes que valem p/ o procedimento: as dele + as gerais."""
    return [p for p in vigentes if p.procedimento_id in (None, procedimento_id)]


def precos_de_vitrine(procedimentos, dia: date | None = None) -> dict[int, PrecoExibido]:
    """{procedimento_id: PrecoExibido} — 2 consultas para qualquer tamanho de catalogo.

    Procedimento sem preco fica fora (a tela mostra "A consultar"). Promocao que nao reduz o valor
    nao aparece (guarda ``final >= cheio``).
    """
    procedimentos = list(procedimentos)
    if not procedimentos:
        return {}
    dia = dia or hoje_local()
    ids = [p.pk for p in procedimentos]
    cheios = preco_base_map(ids, dia=dia)
    vigentes = promocoes_vigentes(dia, ids)
    precos = {}
    for pk in ids:
        cheio = cheios.get(pk)
        if cheio is None:
            continue
        promocao = melhor_promocao(candidatas_de(pk, vigentes), cheio)
        final = aplicar_promocao(cheio, promocao)
        if promocao is not None and final >= cheio:
            promocao, final = None, cheio
        precos[pk] = PrecoExibido(final=final, cheio=cheio, promocao=promocao)
    return precos
