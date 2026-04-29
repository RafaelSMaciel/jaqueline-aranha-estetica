"""Utilitarios de preco para procedimentos."""
from __future__ import annotations

from ..models import Preco


def preco_base_map(procedimentos=None):
    """Retorna dict {procedimento_id: Decimal} do preco base (sem profissional).

    Fallback: se um procedimento so tiver preco com profissional, usa o menor.
    Aceita um queryset/iterable de procedimentos para limitar o escopo.
    """
    qs = Preco.objects.all()
    if procedimentos is not None:
        ids = [getattr(p, 'pk', p) for p in procedimentos]
        qs = qs.filter(procedimento_id__in=ids)

    # Preco base (profissional nulo) tem prioridade.
    mapa = {
        p.procedimento_id: p.valor
        for p in qs.filter(profissional__isnull=True)
    }
    # Preencher faltantes com primeiro preco por profissional encontrado.
    for p in qs.filter(profissional__isnull=False):
        mapa.setdefault(p.procedimento_id, p.valor)
    return mapa


def preco_para(procedimento, profissional=None):
    """Retorna o preco aplicavel para (procedimento, profissional).

    Prioridade: preco especifico do profissional > preco base (sem profissional).
    """
    qs = Preco.objects.filter(procedimento=procedimento)
    if profissional is not None:
        especifico = qs.filter(profissional=profissional).first()
        if especifico is not None:
            return especifico
    return qs.filter(profissional__isnull=True).first()


def mask_telefone(telefone: str) -> str:
    """Mascara telefone para log: mantem apenas os 4 ultimos digitos."""
    if not telefone:
        return '****'
    digitos = ''.join(ch for ch in telefone if ch.isdigit())
    if len(digitos) <= 4:
        return '****'
    return f'***{digitos[-4:]}'
