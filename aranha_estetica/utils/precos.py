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


def preco_para(procedimento, profissional=None):
    """Retorna o Preco vigente aplicavel para (procedimento, profissional).

    Prioridade: preco especifico do profissional > preco base (sem profissional).
    """
    hoje = hoje_local()
    qs = Preco.objects.filter(procedimento=procedimento)
    if profissional is not None:
        especifico = _vigente_qs(qs.filter(profissional=profissional), hoje)
        if especifico is not None:
            return especifico
    return _vigente_qs(qs.filter(profissional__isnull=True), hoje)
