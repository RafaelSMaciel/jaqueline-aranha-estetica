"""Alertas de saude e marcadores em lote p/ listas (agenda do portal, agendamentos do painel).

A regra (prontuario + fichas de anamnese respondidas) e o carregamento em lote ficam em
utils.saude (alertas_saude / alertas_em_lote: 3 consultas p/ a lista inteira).
ids_com_ficha_pendente marca quem nao tem a ficha obrigatoria em dia (D14: vale 12 meses).
marcar_atendimentos pendura todos os marcadores da linha (alerta, termo, ficha, pacote, credito) em
cada atendimento — a view chama, o template so le atributo (antes: template tag com consultas).
registrar_leitura_saude deixa na trilha a tela que MOSTROU alerta de saude (LGPD, RIPD).
"""
from __future__ import annotations

from ..utils.auditoria import registrar_log
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

    from ..models import Atendimento, Cliente, FormularioAnamnese, RespostaAnamnese
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

    # revogou o consentimento de saude: ficha anterior a revogacao nao vale mais
    revogadas = dict(
        Cliente.all_objects.filter(pk__in={at.cliente_id for at in ativos}, saude_revogada_em__isnull=False)
        .values_list('pk', 'saude_revogada_em')
    )

    def em_dia(at, fid) -> bool:
        quando = ultima.get((at.cliente_id, fid))
        return quando is not None and quando >= valida_desde(at.data_hora_inicio, revogadas.get(at.cliente_id))

    pendentes = set()
    for at in ativos:
        if any(not em_dia(at, fid) for fid in aplicaveis(at.procedimento)):
            pendentes.add(at.pk)
    return pendentes


def marcar_atendimentos(atendimentos, *, valores: bool = False) -> list:
    """Pendura os marcadores da linha em cada atendimento da lista (quantidade fixa de consultas).

    Sempre: ``alertas_saude`` (lista), ``termo_pendente`` (termo do procedimento nao aceito),
    ``termo_link`` (dos pendentes, os que ainda aceitam assinatura) e ``ficha_pendente``.
    valores=True (agenda do painel): ``de_pacote`` (sessao ja debitada de pacote), ``pacote_previsto``
    ({pacote, restam} de ativo que um pacote vai cobrir: nao cobrar), ``saldo_credito`` (credito de
    indicacao da cliente) e ``credito_usado`` (valor ja abatido neste atendimento).
    Devolve a propria lista (materializada)."""
    from .termos import aceita_assinatura, ids_com_termo_procedimento_pendente

    lista = list(atendimentos or [])
    if not lista:
        return lista
    alertas = alertas_por_cliente(at.cliente for at in lista)
    termo = ids_com_termo_procedimento_pendente(lista)
    ficha = ids_com_ficha_pendente(lista)
    for at in lista:
        at.alertas_saude = alertas.get(at.cliente_id, [])
        at.termo_pendente = at.pk in termo
        at.termo_link = at.termo_pendente and aceita_assinatura(at)
        at.ficha_pendente = at.pk in ficha
    if valores:
        _marcar_valores(lista)
    return lista


def _marcar_valores(lista) -> None:
    from ..models import ConsumoSessao
    from .fidelidade import FidelidadeService
    from .pacotes import sessoes_previstas

    debitados = set(
        ConsumoSessao.objects.filter(atendimento_id__in=[at.pk for at in lista]).values_list('atendimento_id', flat=True)
    )
    previstas = sessoes_previstas(lista)
    saldos = FidelidadeService.saldos(at.cliente_id for at in lista)
    usados = FidelidadeService.creditos_usados(at.pk for at in lista)
    for at in lista:
        at.de_pacote = at.pk in debitados
        at.pacote_previsto = previstas.get(at.pk)
        at.saldo_credito = saldos.get(at.cliente_id)
        at.credito_usado = usados.get(at.pk)


def registrar_leitura_saude(request, cliente_ids, tela: str) -> None:
    """A tela mostrou alerta de saude dessas clientes: 1 linha na trilha por exibicao (LGPD art. 37;
    o RIPD promete que cada leitura de dado de saude vai para a log_auditoria). Sem nome no texto
    (a trilha sobrevive a anonimizacao); nada a registrar se ninguem tinha alerta."""
    ids = sorted({pk for pk in cliente_ids if pk is not None})
    if ids:
        registrar_log(request.user, 'Leu alertas de saúde', 'prontuario', None,
                      {'clientes': ids, 'tela': tela}, request=request)
