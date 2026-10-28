"""Sessao de pacote PREVISTA: que compra vai cobrir um atendimento ainda nao realizado.

O debito so acontece no Realizado (services.atendimento.debitar_sessao_de_pacote); antes disso a
recepcao e a cliente viam o preco cheio e podiam cobrar duas vezes. Aqui vale a mesma ordem do
debito — a compra que vence primeiro, depois a mais antiga; validade conferida na data da sessao;
retorno nunca usa pacote — descontando as sessoes que os atendimentos ativos anteriores da cliente
ja vao consumir. So leitura: nada e reservado no pacote.
"""
from __future__ import annotations

from collections import defaultdict

from django.db.models import F
from django.utils import timezone


def sessoes_previstas(atendimentos) -> dict[int, dict]:
    """{atendimento_id: {'pacote': nome, 'restam': sessoes do item depois desta}} dos ativos que um
    pacote da cliente vai cobrir. 2 queries p/ a lista inteira (+ prefetch dos itens)."""
    from ..models import Atendimento, CompraPacote
    from ..models.agendamentos import STATUS_ATIVOS

    alvo = {at.pk for at in atendimentos if at.status in STATUS_ATIVOS and not at.eh_retorno}
    if not alvo:
        return {}
    clientes = {at.cliente_id for at in atendimentos if at.pk in alvo}
    compras = list(
        CompraPacote.objects.filter(
            cliente_id__in=clientes, status__in=(CompraPacote.STATUS_ATIVO, CompraPacote.STATUS_EXPIRADO),
        ).prefetch_related('itens', 'sessoes_realizadas')
        .order_by(F('data_expiracao').asc(nulls_last=True), 'criado_em')
    )
    if not compras:
        return {}

    saldo: dict[int, int] = {}
    por_cliente = defaultdict(list)
    for compra in compras:
        usadas = compra.consumos_por_item()
        for item in compra.itens.all():
            saldo[item.pk] = item.quantidade_sessoes - usadas.get(item.pk, 0)
        por_cliente[compra.cliente_id].append(compra)

    fila = (
        Atendimento.objects.filter(cliente_id__in=por_cliente, status__in=STATUS_ATIVOS, eh_retorno=False)
        .order_by('data_hora_inicio', 'pk').only('pk', 'cliente_id', 'procedimento_id', 'data_hora_inicio')
    )
    previstas = {}
    for at in fila:
        dia = timezone.localdate(at.data_hora_inicio)
        for compra in por_cliente[at.cliente_id]:
            if compra.data_expiracao is not None and compra.data_expiracao < dia:
                continue
            item = next((i for i in compra.itens.all()
                         if i.procedimento_id == at.procedimento_id and saldo[i.pk] > 0), None)
            if item is None:
                continue
            saldo[item.pk] -= 1
            if at.pk in alvo:
                previstas[at.pk] = {'pacote': compra.pacote_nome, 'restam': saldo[item.pk]}
            break
    return previstas
