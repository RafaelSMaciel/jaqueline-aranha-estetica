"""Utilitarios de preco para procedimentos.

Preco e versionado por `vigente_desde`: vale a vigencia mais recente com
vigente_desde <= hoje (fuso local). Se so houver vigencia futura, usa a mais
proxima (evita procedimento "sem preco" por diferenca de fuso no default).
"""
from __future__ import annotations

from collections import defaultdict

from ..models import Preco
from .datas import hoje as hoje_local


def _escolher_vigente(precos, hoje):
    """Da lista de vigencias de um mesmo escopo, a que vale hoje."""
    atuais = [p for p in precos if p.vigente_desde <= hoje]
    if atuais:
        return max(atuais, key=lambda p: (p.vigente_desde, p.pk))
    return min(precos, key=lambda p: (p.vigente_desde, p.pk))


def _vigente_qs(qs, hoje):
    atual = qs.filter(vigente_desde__lte=hoje).order_by('-vigente_desde', '-pk').first()
    if atual is None:
        atual = qs.order_by('vigente_desde', 'pk').first()
    return atual


def preco_base_map(procedimentos=None):
    """Retorna dict {procedimento_id: Decimal} do preco base vigente (sem profissional).

    Fallback: se um procedimento so tiver preco com profissional, usa o menor
    preco vigente entre os profissionais.
    Aceita um queryset/iterable de procedimentos para limitar o escopo.
    """
    qs = Preco.objects.all()
    if procedimentos is not None:
        ids = [getattr(p, 'pk', p) for p in procedimentos]
        qs = qs.filter(procedimento_id__in=ids)

    hoje = hoje_local()
    base = defaultdict(list)
    por_prof = defaultdict(list)
    for p in qs.only('pk', 'procedimento_id', 'profissional_id', 'valor', 'vigente_desde'):
        if p.profissional_id is None:
            base[p.procedimento_id].append(p)
        else:
            por_prof[(p.procedimento_id, p.profissional_id)].append(p)

    # Preco base (profissional nulo) tem prioridade.
    mapa = {pid: _escolher_vigente(lista, hoje).valor for pid, lista in base.items()}
    for (pid, _prof_id), lista in por_prof.items():
        if pid in base:
            continue
        valor = _escolher_vigente(lista, hoje).valor
        if pid not in mapa or valor < mapa[pid]:
            mapa[pid] = valor
    return mapa


def _dia(data=None):
    """date local p/ comparar vigencias (aceita date, datetime aware ou None=hoje)."""
    from datetime import datetime

    if data is None:
        return hoje_local()
    if isinstance(data, datetime):
        from .datas import data_local
        return data_local(data)
    return data


def preco_para(procedimento, profissional=None, data=None):
    """Retorna o Preco vigente aplicavel para (procedimento, profissional) na data.

    Prioridade: preco especifico do profissional > preco base (sem profissional).
    data: dia do atendimento (default hoje local).
    """
    hoje = _dia(data)
    qs = Preco.objects.filter(procedimento=procedimento)
    if profissional is not None:
        especifico = _vigente_qs(qs.filter(profissional=profissional), hoje)
        if especifico is not None:
            return especifico
    return _vigente_qs(qs.filter(profissional__isnull=True), hoje)


def _promocao_aplicavel(promocao) -> bool:
    """Promocao geral (sem procedimento) so vale por percentual: preco fixo
    geral viraria teto para todo o catalogo."""
    if promocao.procedimento_id:
        return promocao.preco_promocional is not None or bool(promocao.desconto_percentual)
    return promocao.preco_promocional is None and bool(promocao.desconto_percentual)


def promocao_vigente(procedimento, data=None):
    """Promocao ativa que reduz o preco do procedimento na data (fuso local), ou None.

    Considera promocoes do proprio procedimento (preco fixo ou percentual) e
    gerais (procedimento nulo, so percentual). Havendo varias, vence a de
    menor preco final (calculado sobre o preco base da data).
    """
    from django.db.models import Q

    from ..models import Promocao

    dia = _dia(data)
    candidatas = [
        p for p in Promocao.objects.filter(ativa=True, data_inicio__lte=dia, data_fim__gte=dia)
        .filter(Q(procedimento=procedimento) | Q(procedimento__isnull=True))
        .order_by('pk')
        if _promocao_aplicavel(p)
    ]
    if not candidatas:
        return None
    preco = preco_para(procedimento, data=dia)
    base = preco.valor if preco is not None else None
    if base is None:
        # sem preco base: so promocao de preco fixo do proprio procedimento faz sentido
        fixas = [p for p in candidatas if p.preco_promocional is not None and p.procedimento_id]
        return fixas[0] if fixas else None
    melhor = min(candidatas, key=lambda p: (aplicar_promocao(base, p), p.pk))
    return melhor if aplicar_promocao(base, melhor) < base else None


def aplicar_promocao(valor, promocao):
    """Valor final (Decimal, 2 casas) apos a promocao; nunca negativo nem maior que o cheio."""
    from decimal import ROUND_HALF_UP, Decimal

    if valor is None or promocao is None:
        return valor
    valor = Decimal(valor)
    if not _promocao_aplicavel(promocao):
        final = valor
    elif promocao.preco_promocional is not None:
        final = min(Decimal(promocao.preco_promocional), valor)
    elif promocao.desconto_percentual:
        final = valor * (Decimal('100') - Decimal(promocao.desconto_percentual)) / Decimal('100')
    else:
        final = valor
    return max(final, Decimal('0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def preco_com_promocao(procedimento, profissional=None, data=None):
    """(valor_final, promocao|None, valor_cheio) p/ exibir e gravar no agendamento.

    valor_cheio = preco vigente na data (profissional > base). valor_final
    aplica a promocao vigente na data do atendimento; promocao so volta
    quando de fato reduz o valor. Sem preco -> (None, None, None).
    """
    preco = preco_para(procedimento, profissional, data)
    if preco is None:
        return None, None, None
    cheio = preco.valor
    promo = promocao_vigente(procedimento, data)
    final = aplicar_promocao(cheio, promo)
    if promo is not None and final >= cheio:
        return cheio, None, cheio
    return final, promo, cheio
