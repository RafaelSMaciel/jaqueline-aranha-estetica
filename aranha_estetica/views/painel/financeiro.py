"""Dashboard financeiro por COMPETENCIA MENSAL (?mes=AAAA-MM; padrao = mes local corrente).

Tudo do mes escolhido, com as funcoes de services/financeiro (as mesmas da visao geral e do
Excel): faturamento, no-show, rankings de producao e comissoes do mes (pela data do
atendimento), mais a tabela dos ultimos 12 meses. Apenas leitura. Cache leve (60 s) por mes.
"""
from __future__ import annotations

import logging

from django.core.cache import cache
from django.db.models import Count, Sum
from django.shortcuts import render

from ...decorators import staff_required
from ...models import Atendimento, MovimentoComissao
from ...services import financeiro
from ...services.comissao import ComissaoService

logger = logging.getLogger(__name__)

CACHE_TTL_DASHBOARD = 60  # 1 minuto


@staff_required
def dashboard_financeiro(request):
    periodo = financeiro.competencia_do_param(request.GET.get('mes'))
    # Chave por mes: ?mes=2026-09 nao pode servir o cache de outubro (TTL de 60 s)
    cache_key = f'dashboard_financeiro:{periodo.param}'
    ctx = cache.get(cache_key)
    if ctx is None:
        ctx = _calcular_metricas(periodo)
        cache.set(cache_key, ctx, CACHE_TTL_DASHBOARD)
    return render(request, 'painel/dashboard_financeiro.html', ctx)


def _calcular_metricas(periodo: financeiro.Periodo) -> dict:
    atual = financeiro.competencia()
    receita = financeiro.resumo_receita(periodo)

    # Top 5 por PRODUCAO (sessao de pacote pelo valor unitario da venda) — o faturamento poe o
    # pacote no mes da venda e zeraria quem so atende pacote ao lado da comissao dela
    produzido = financeiro.producao(Atendimento.objects.filter(**periodo.filtro('data_hora_inicio')))
    top_procs = (produzido.values('procedimento__nome')
                 .annotate(total=Sum('valor_produzido'), count=Count('id')).order_by('-total')[:5])
    top_profs = (produzido.values('profissional__nome')
                 .annotate(total=Sum('valor_produzido'), count=Count('id')).order_by('-total')[:5])

    # Comissoes: pendentes de todo o ledger (o que falta repassar) + as do mes pela data do
    # ATENDIMENTO (a mesma do faturamento; o clique em Realizado pode ser noutro dia)
    creditos = MovimentoComissao.objects.creditos_com_status()
    pendentes = ComissaoService.totais_por_status(creditos)[MovimentoComissao.STATUS_PENDENTE]
    do_mes = creditos.filter(**periodo.filtro('atendimento__data_hora_inicio'))
    comissoes_por_prof = [linha for linha in ComissaoService.resumo_por_profissional(do_mes) if linha['qtd']]
    # Saldo negativo (legado: comissao paga e depois estornada) — o desfazer recusa comissao paga
    saldos = ComissaoService.saldos_por_profissional()
    comissoes_a_descontar = -sum((s for s in saldos.values() if s < 0), financeiro.ZERO)

    return {
        'periodo': periodo,
        'periodo_anterior': periodo.anterior(),
        'periodo_seguinte': periodo.seguinte() if periodo.inicio < atual.inicio else None,
        'eh_mes_corrente': periodo == atual,
        'receita': receita,
        'no_show': financeiro.no_show(periodo),
        'top_procedimentos': list(top_procs),
        'top_profissionais': list(top_profs),
        'comissoes_pendentes_total': pendentes['total'],
        'comissoes_pendentes_count': pendentes['qtd'],
        'comissoes_por_prof': comissoes_por_prof,
        'comissoes_a_descontar': comissoes_a_descontar,
        'sem_regra_comissao': not ComissaoService.ha_regra_ativa(),
        'creditos_indicacao_mes': financeiro.creditos_indicacao_usados(periodo),
        'ultimos_12_meses': financeiro.ultimos_12_meses(atual),
    }
