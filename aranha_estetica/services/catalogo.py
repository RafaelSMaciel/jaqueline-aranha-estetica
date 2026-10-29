"""Catalogo de procedimentos: preco base versionado e quem realiza (Habilitacao).

Regras que moravam na view do painel (views/painel/procedimentos):

- o preco base muda abrindo uma vigencia nova a partir de hoje (o historico fica; o atendimento ja
  marcado guarda o proprio valor), e so quando o valor muda;
- quem realiza = os profissionais marcados, mas tirar a habilitacao de quem ainda tem atendimento
  futuro do procedimento e recusado (reagendar/cancelar antes).
"""
from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from django.utils import timezone

from ..excecoes import RegraDeNegocio
from ..models import Atendimento, Habilitacao, Preco, Procedimento, Profissional
from ..utils.datas import hoje
from ..utils.precos import preco_base_map


def definir_preco_base(procedimento: Procedimento, valor: Decimal | None) -> bool:
    """Preco base (sem profissional) vigente a partir de hoje, so se o valor mudou. True = gravou.

    Compara com o preco que o site mostra (utils/precos.preco_base_map, a mesma regra da lista do
    painel): salvar o formulario sem mexer no preco nao cria vigencia. Ja havendo vigencia de hoje,
    ela e ajustada. valor None = preco nao informado (fica o que esta)."""
    if valor is None or preco_base_map([procedimento]).get(procedimento.pk) == valor:
        return False
    Preco.objects.update_or_create(
        procedimento=procedimento, profissional=None, vigente_desde=hoje(), defaults={'valor': valor},
    )
    return True


def sincronizar_habilitacoes(procedimento: Procedimento, profissionais: Iterable[Profissional]) -> None:
    """Habilitacoes do procedimento = os profissionais marcados. Tirar quem ainda tem atendimento
    futuro deste procedimento e recusado (RegraDeNegocio habilitacao_em_uso): reagende/cancele antes."""
    novos = {p.pk for p in profissionais}
    atuais = set(Habilitacao.objects.filter(procedimento=procedimento).values_list('profissional_id', flat=True))
    sair = atuais - novos
    if sair:
        presos = (Atendimento.objects.filter(
            procedimento=procedimento, profissional_id__in=sair, status__in=Atendimento.STATUS_ATIVOS,
            data_hora_inicio__gte=timezone.now(),
        ).select_related('profissional').order_by('data_hora_inicio'))
        if presos.exists():
            nomes = sorted({a.profissional.nome for a in presos})
            raise RegraDeNegocio(
                f'{", ".join(nomes)} ainda tem {presos.count()} atendimento(s) futuro(s) de {procedimento.nome}: '
                'reagende ou cancele antes de tirar a habilitação.', codigo='habilitacao_em_uso')
        Habilitacao.objects.filter(procedimento=procedimento, profissional_id__in=sair).delete()
    for pk in novos - atuais:
        Habilitacao.objects.create(procedimento=procedimento, profissional_id=pk)
