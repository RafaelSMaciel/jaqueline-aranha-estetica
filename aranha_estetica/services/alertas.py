"""Alertas de saude em lote p/ listas (agenda do portal, agendamentos do painel).

utils.saude.alertas_saude(cliente) e a regra (prontuario + fichas de
anamnese respondidas); aqui so evitamos 2 queries por linha: um pre-filtro
em lote acha quem TEM algo a alertar e so esses passam pelo helper.
"""
from __future__ import annotations

from django.db.models import Q

from ..utils.saude import alertas_saude


def alertas_por_cliente(clientes) -> dict[int, list[dict]]:
    """{cliente_id: [{'label','valor','origem'}]} so p/ quem tem alerta."""
    from ..models import Prontuario, RespostaAnamnese

    por_id = {}
    for cli in clientes:
        if cli is not None and getattr(cli, 'pk', None):
            por_id.setdefault(cli.pk, cli)
    if not por_id:
        return {}

    preenchido = Q()
    for campo in ('alergias', 'contraindicacoes', 'medicamentos_uso'):
        preenchido |= Q(**{f'{campo}__isnull': False}) & ~Q(**{campo: ''})
    candidatos = set(
        Prontuario.objects.filter(preenchido, cliente_id__in=por_id)
        .values_list('cliente_id', flat=True)
    )
    candidatos |= set(
        RespostaAnamnese.objects.filter(
            cliente_id__in=por_id, formulario__tipo='ANAMNESE', respondida_em__isnull=False,
        ).values_list('cliente_id', flat=True)
    )

    mapa = {}
    for cliente_id in candidatos:
        alertas = alertas_saude(por_id[cliente_id])
        if alertas:
            mapa[cliente_id] = alertas
    return mapa
