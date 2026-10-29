"""F-RET — Retorno obrigatorio: marcado COM a cliente, nunca pelo sistema.

No REALIZADO de um procedimento com ``exige_retorno``, o retorno fica DEVIDO na janela
[fim + minimo, fim + maximo] dias (sem custo). Quem marca:
- a recepcao, pela tela "Agendar retorno" do painel (horarios livres so dentro da janela);
- a cliente ve "Seu retorno sem custo: agende ate dd/mm" em Meus agendamentos e fala com a clinica.

Antes o sistema reservava sozinho um horario do meio da janela, PENDENTE: a cliente nao escolhia e,
sem aprovacao, recebia um e-mail de "cancelado". No banco: no maximo 1 retorno vivo (ou realizado)
por origem (uq_atendimento_retorno_por_origem).
"""
from __future__ import annotations

import copy
import logging
from datetime import date, timedelta
from typing import NamedTuple

from django.db import transaction
from django.utils import timezone

from ..excecoes import RegraDeNegocio
from ..models import Atendimento
from ..models.agendamentos import STATUS_ATIVOS, StatusAtendimento
from ..utils.datas import hoje
from .agenda import reservar, validar_horario
from .disponibilidade import SlotService

logger = logging.getLogger(__name__)

# Sem minimo configurado o retorno comeca no dia seguinte (nunca no mesmo dia da origem).
RETORNO_MINIMO_DIAS_PADRAO = 1
# Quanto tempo para tras procurar retornos devidos (a janela maxima e configurada em dias).
HORIZONTE_BUSCA = timedelta(days=400)

PRECO_RETORNO = {
    'valor_cobrado': 0, 'valor_original': 0, 'promocao': None,
    'descricao_preco': 'Retorno obrigatório (sem cobrança)',
}


class RetornoDevido(NamedTuple):
    origem: Atendimento
    de: date
    ate: date


def janela(origem: Atendimento) -> tuple[date, date] | None:
    """(primeiro dia, ultimo dia) do retorno sem custo, ou None se a origem nao gera retorno."""
    proc = origem.procedimento
    if not proc.exige_retorno or origem.eh_retorno:
        return None
    minimo = proc.retorno_minimo_dias or RETORNO_MINIMO_DIAS_PADRAO
    maximo = max(proc.retorno_maximo_dias or minimo, minimo)
    base = timezone.localdate(origem.data_hora_fim)
    return base + timedelta(days=minimo), base + timedelta(days=maximo)


def devidos(cliente_id: int | None = None) -> list[RetornoDevido]:
    """Realizados que exigem retorno, ainda sem retorno marcado e com a janela aberta (ate hoje),
    do que vence primeiro."""
    origens = (
        Atendimento.objects.select_related('cliente', 'procedimento', 'profissional')
        .filter(status=StatusAtendimento.REALIZADO, eh_retorno=False, procedimento__exige_retorno=True,
                data_hora_fim__gte=timezone.now() - HORIZONTE_BUSCA)
        .exclude(retornos__eh_retorno=True,
                 retornos__status__in=(*STATUS_ATIVOS, StatusAtendimento.REALIZADO))
    )
    if cliente_id is not None:
        origens = origens.filter(cliente_id=cliente_id)
    dia = hoje()
    lista = []
    for origem in origens:
        periodo = janela(origem)
        if periodo is not None and periodo[1] >= dia:
            lista.append(RetornoDevido(origem, *periodo))
    return sorted(lista, key=lambda r: (r.ate, r.origem.pk))


def devidos_na_lista(atendimentos) -> dict[int, date]:
    """{pk da origem: ultimo dia} dos REALIZADOS da lista com retorno devido (janela aberta e sem
    retorno vivo ou realizado) — o botao 'Agendar retorno (ate dd/mm)' da agenda. 1 consulta."""
    dia = hoje()
    abertas = {}
    for at in atendimentos:
        periodo = janela(at) if at.status == StatusAtendimento.REALIZADO else None
        if periodo is not None and periodo[1] >= dia:
            abertas[at.pk] = periodo[1]
    if not abertas:
        return {}
    marcados = set(
        Atendimento.objects.filter(atendimento_origem_id__in=abertas, eh_retorno=True,
                                   status__in=(*STATUS_ATIVOS, StatusAtendimento.REALIZADO))
        .values_list('atendimento_origem_id', flat=True)
    )
    return {pk: ate for pk, ate in abertas.items() if pk not in marcados}


def _no_horizonte(profissional, ate: date):
    """O retorno e marcado pela clinica: o horizonte do site (max_advance_dias) e o aviso minimo
    nao se aplicam — vale a janela do retorno."""
    prof = copy.copy(profissional)
    prof.max_advance_dias = max((ate - hoje()).days + 1, prof.max_advance_dias)
    prof.min_notice_horas = 0
    return prof


def horarios(origem: Atendimento, dia: date, profissional=None) -> list[str]:
    """Horarios livres ('HH:MM') do retorno no dia — vazio fora da janela."""
    periodo = janela(origem)
    if periodo is None or not periodo[0] <= dia <= periodo[1]:
        return []
    prof = _no_horizonte(profissional or origem.profissional, periodo[1])
    return SlotService.slots_livres(prof, dia, origem.procedimento)


@transaction.atomic
def agendar(origem: Atendimento, inicio, *, profissional=None) -> Atendimento:
    """Marca o retorno sem custo (AGENDADO: combinado com a cliente) dentro da janela.

    Mesma validacao de horario das outras portas (services.agenda.reservar). Recusa com
    RegraDeNegocio: origem sem retorno, ainda nao realizada, retorno ja marcado ou fora da janela.
    """
    origem = Atendimento.objects.select_for_update().select_related('procedimento').get(pk=origem.pk)
    periodo = janela(origem)
    if periodo is None:
        raise RegraDeNegocio('Este atendimento não tem retorno.', codigo='sem_retorno')
    if origem.status != StatusAtendimento.REALIZADO:
        raise RegraDeNegocio('O retorno é marcado depois do atendimento realizado.', codigo='origem_nao_realizada')
    if Atendimento.objects.filter(
            atendimento_origem=origem, eh_retorno=True,
            status__in=(*STATUS_ATIVOS, StatusAtendimento.REALIZADO)).exists():
        raise RegraDeNegocio('Este atendimento já tem retorno marcado.', codigo='retorno_existente')
    de, ate = periodo
    if not de <= timezone.localdate(inicio) <= ate:
        raise RegraDeNegocio(
            f'O retorno sem custo vale de {de:%d/%m/%Y} a {ate:%d/%m/%Y}.', codigo='retorno_fora_da_janela')
    profissional = profissional or origem.profissional
    validar_horario(_no_horizonte(profissional, ate), origem.procedimento, inicio, detalhado=True)
    retorno = reservar(
        cliente=origem.cliente, profissional=profissional,
        procedimento=origem.procedimento, inicio=inicio, status=StatusAtendimento.AGENDADO,
        eh_retorno=True, atendimento_origem=origem, preco=PRECO_RETORNO,
    )
    logger.info('retorno_agendado', extra={'origem_id': origem.pk, 'retorno_id': retorno.pk})
    return retorno
