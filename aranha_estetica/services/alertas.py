"""Alertas de saude em lote p/ listas (agenda do portal, agendamentos do painel).

utils.saude.alertas_saude(cliente) e a regra (prontuario + fichas de
anamnese respondidas); aqui so evitamos 2 queries por linha: um pre-filtro
em lote acha quem TEM algo a alertar e so esses passam pelo helper.
ids_com_ficha_pendente marca quem ainda nao respondeu a ficha obrigatoria.
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
    # questionario configuravel (EAV migrado na 0036 + prontuario_salvar):
    # quem so tem respostas_extras tambem passa pelo helper
    preenchido |= ~Q(respostas_extras={})
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


def ids_com_ficha_pendente(atendimentos) -> set[int]:
    """pk dos atendimentos ativos sem a ficha de anamnese OBRIGATORIA respondida.

    Ficha obrigatoria = FormularioAnamnese ANAMNESE ativo e obrigatorio que vale
    p/ o procedimento (mesmo escopo do booking: GLOBAL, CATEGORIA ou
    PROCEDIMENTO). Pendente quando a cliente nunca respondeu aquele formulario
    — o agendamento pela recepcao nao coleta a ficha e, sem ela, os alertas de
    saude ficam vazios. 2 queries p/ a lista inteira.
    """
    from ..models import Atendimento, FormularioAnamnese, RespostaAnamnese

    ativos = [
        at for at in atendimentos
        if at is not None and at.status in Atendimento.STATUS_ATIVOS and at.procedimento_id
    ]
    if not ativos:
        return set()
    formularios = list(
        FormularioAnamnese.objects.filter(
            tipo='ANAMNESE', ativo=True, obrigatorio=True,
            escopo__in=('GLOBAL', 'CATEGORIA', 'PROCEDIMENTO'),
        ).only('pk', 'escopo', 'categoria', 'procedimento_id')
    )
    if not formularios:
        return set()

    def aplicaveis(procedimento):
        return {
            f.pk for f in formularios
            if f.escopo == 'GLOBAL'
            or (f.escopo == 'CATEGORIA' and f.categoria == procedimento.categoria)
            or (f.escopo == 'PROCEDIMENTO' and f.procedimento_id == procedimento.pk)
        }

    respondidas = set(
        RespostaAnamnese.objects.filter(
            cliente_id__in={at.cliente_id for at in ativos},
            formulario_id__in=[f.pk for f in formularios],
            respondida_em__isnull=False,
        ).values_list('cliente_id', 'formulario_id')
    )
    pendentes = set()
    for at in ativos:
        if any((at.cliente_id, fid) not in respondidas for fid in aplicaveis(at.procedimento)):
            pendentes.add(at.pk)
    return pendentes
