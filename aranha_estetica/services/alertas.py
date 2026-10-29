"""Alertas de saude em lote p/ listas (agenda do portal, agendamentos do painel).

A regra (prontuario + fichas de anamnese respondidas) e o carregamento em lote ficam em
utils.saude (alertas_saude / alertas_em_lote: 3 consultas p/ a lista inteira).
ids_com_ficha_pendente marca quem nao tem a ficha obrigatoria em dia (D14: vale 12 meses).
"""
from __future__ import annotations

from ..utils.saude import alertas_em_lote


def alertas_por_cliente(clientes) -> dict[int, list[dict]]:
    """{cliente_id: [{'label','valor','origem'}]} so p/ quem tem alerta."""
    return alertas_em_lote(getattr(cli, 'pk', None) for cli in clientes if cli is not None)


def ids_com_ficha_pendente(atendimentos) -> set[int]:
    """pk dos atendimentos ativos sem a ficha de anamnese OBRIGATORIA em dia.

    Ficha obrigatoria = FormularioAnamnese ANAMNESE ativo e obrigatorio que vale
    p/ o procedimento (FormularioAnamnese.aplica_a — a mesma regra do wizard). Pendente quando a
    cliente nao respondeu aquele formulario nos VALIDADE_FICHA_MESES antes do atendimento (D14,
    services/anamnese) — o agendamento pela recepcao nao coleta a ficha e, sem ela, os alertas de
    saude ficam vazios. 2 queries p/ a lista inteira.
    """
    from django.db.models import Max

    from ..models import Atendimento, FormularioAnamnese, RespostaAnamnese
    from .anamnese import valida_desde

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

    ultima = {
        (cliente_id, formulario_id): quando
        for cliente_id, formulario_id, quando in RespostaAnamnese.objects.filter(
            cliente_id__in={at.cliente_id for at in ativos},
            formulario_id__in=[f.pk for f in formularios],
            respondida_em__isnull=False,
        ).values('cliente_id', 'formulario_id').annotate(ultima=Max('respondida_em'))
        .values_list('cliente_id', 'formulario_id', 'ultima')
    }

    def em_dia(at, fid) -> bool:
        quando = ultima.get((at.cliente_id, fid))
        return quando is not None and quando >= valida_desde(at.data_hora_inicio)

    pendentes = set()
    for at in ativos:
        if any(not em_dia(at, fid) for fid in aplicaveis(at.procedimento)):
            pendentes.add(at.pk)
    return pendentes
