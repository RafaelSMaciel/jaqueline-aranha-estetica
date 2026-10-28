"""Alertas de saude em lote p/ listas (agenda do portal, agendamentos do painel).

A regra (prontuario + fichas de anamnese respondidas) e o carregamento em lote ficam em
utils.saude (alertas_saude / alertas_em_lote: 3 consultas p/ a lista inteira).
ids_com_ficha_pendente marca quem ainda nao respondeu a ficha obrigatoria.
"""
from __future__ import annotations

from ..utils.saude import alertas_em_lote


def alertas_por_cliente(clientes) -> dict[int, list[dict]]:
    """{cliente_id: [{'label','valor','origem'}]} so p/ quem tem alerta."""
    return alertas_em_lote(getattr(cli, 'pk', None) for cli in clientes if cli is not None)


def ids_com_ficha_pendente(atendimentos) -> set[int]:
    """pk dos atendimentos ativos sem a ficha de anamnese OBRIGATORIA respondida.

    Ficha obrigatoria = FormularioAnamnese ANAMNESE ativo e obrigatorio que vale
    p/ o procedimento (FormularioAnamnese.aplica_a — a mesma regra do wizard). Pendente quando a cliente nunca respondeu aquele formulario
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
        FormularioAnamnese.objects.filter(tipo='ANAMNESE', ativo=True, obrigatorio=True)
        .only('pk', 'escopo', 'categoria', 'procedimento_id')
    )
    if not formularios:
        return set()

    def aplicaveis(procedimento):
        return {f.pk for f in formularios if f.aplica_a(procedimento)}

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
