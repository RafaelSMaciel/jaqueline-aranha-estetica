"""Dashboard financeiro — agregados de faturamento, no-show, top procedimentos.

Apenas leitura. Cache leve (60s) p/ proteger DB em refresh frequente.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Count, Sum
from django.shortcuts import render
from django.utils import timezone

from ..decorators import staff_required
from ..models import Atendimento, MovimentoComissao
from ..services import financeiro
from ..services.comissao_service import ComissaoService

logger = logging.getLogger(__name__)

CACHE_TTL_DASHBOARD = 60  # 1 minuto


@staff_required
def dashboard_financeiro(request):
    """Renderiza dashboard com agregados.

    Periodos: hoje, semana (7d), mes (30d).
    Metricas: faturamento (avulsos + venda de pacotes), no-show, top
    procedimento, comissoes pendentes.
    """
    agora = timezone.now()
    hoje = timezone.localdate()

    # Cache compartilhado por instancia de CACHE_TTL_DASHBOARD (60s), nao por dia:
    # a data na chave so evita colisao trivial entre dias diferentes; a janela
    # real de reaproveitamento e o TTL de 60s.
    cache_key = f'dashboard_financeiro:{hoje.isoformat()}'
    ctx = cache.get(cache_key)
    if ctx is None:
        ctx = _calcular_metricas(agora, hoje)
        cache.set(cache_key, ctx, CACHE_TTL_DASHBOARD)

    return render(request, 'painel/dashboard_financeiro.html', ctx)


def _calcular_metricas(agora, hoje) -> dict:
    """Single helper — agregados em batch p/ minimizar queries."""
    inicio_semana = agora - timedelta(days=7)
    inicio_mes = agora - timedelta(days=30)

    # Faturamento por periodo (D20, services/financeiro — mesma regra do
    # dashboard e da ficha): avulsos REALIZADOS com valor > 0, sem retorno e sem
    # sessao de pacote (o valor_cobrado dela e o preco cheio; a receita real ja
    # entrou na venda do pacote, abaixo).
    base_pago = financeiro.atendimentos_faturados()
    # Venda de pacote = receita no dia da compra, liquida do reembolso: o
    # cancelado entra com o que a clinica reteve (sessoes usadas/multa);
    # devolvido integral (liquido 0) nao conta como venda.
    vendas_pacote = financeiro.vendas_pacote()
    receita_pacote = financeiro.RECEITA_PACOTE

    fat_hoje = base_pago.filter(data_hora_inicio__date=hoje).aggregate(
        total=Sum('valor_cobrado'), count=Count('id'),
    )
    fat_semana = base_pago.filter(data_hora_inicio__gte=inicio_semana).aggregate(
        total=Sum('valor_cobrado'), count=Count('id'),
    )
    fat_mes = base_pago.filter(data_hora_inicio__gte=inicio_mes).aggregate(
        total=Sum('valor_cobrado'), count=Count('id'),
    )
    pac_hoje = vendas_pacote.filter(criado_em__date=hoje).aggregate(
        total=receita_pacote, count=Count('id'),
    )
    pac_semana = vendas_pacote.filter(criado_em__gte=inicio_semana).aggregate(
        total=receita_pacote, count=Count('id'),
    )
    pac_mes = vendas_pacote.filter(criado_em__gte=inicio_mes).aggregate(
        total=receita_pacote, count=Count('id'),
    )

    # No-show no mes
    no_show_mes = Atendimento.objects.filter(
        status=Atendimento.STATUS_FALTOU,
        data_hora_inicio__gte=inicio_mes,
    ).count()

    # Denominador = TODOS os realizados (sessao de pacote, retorno e sem valor
    # tambem compareceram). fat_mes['count'] e so faturamento: usa-lo aqui
    # inflava o % de faltas em clinica que vende pacote.
    realizados_mes = Atendimento.objects.filter(
        status=Atendimento.STATUS_REALIZADO,
        data_hora_inicio__gte=inicio_mes,
    ).count()
    total_finalizados_mes = realizados_mes + no_show_mes
    no_show_pct = (
        (no_show_mes * 100 / total_finalizados_mes) if total_finalizados_mes else 0
    )

    # Top 5 procedimentos no mes (por faturamento)
    top_procs = (
        base_pago.filter(data_hora_inicio__gte=inicio_mes)
        .values('procedimento__nome')
        .annotate(total=Sum('valor_cobrado'), count=Count('id'))
        .order_by('-total')[:5]
    )

    # Top 5 profissionais no mes (por faturamento)
    top_profs = (
        base_pago.filter(data_hora_inicio__gte=inicio_mes)
        .values('profissional__nome')
        .annotate(total=Sum('valor_cobrado'), count=Count('id'))
        .order_by('-total')[:5]
    )

    # Comissoes pendentes (todas): creditos sem PAGAMENTO nem ESTORNO (ledger, 0053)
    creditos = MovimentoComissao.objects.creditos_com_status()
    pendentes = list(creditos.filter(status=MovimentoComissao.STATUS_PENDENTE).values_list('valor', flat=True))
    comissoes_pendentes = {'total': sum(pendentes, Decimal('0.00')), 'count': len(pendentes)}

    # Comissoes por profissional (mes): creditos do periodo nao estornados
    por_prof = {}
    for nome, valor in (
        creditos.filter(criado_em__gte=inicio_mes)
        .exclude(status=MovimentoComissao.STATUS_ESTORNADA)
        .values_list('profissional__nome', 'valor')
    ):
        linha = por_prof.setdefault(nome, {'profissional__nome': nome, 'total': Decimal('0.00'), 'count': 0})
        linha['total'] += valor
        linha['count'] += 1
    comissoes_por_prof = sorted(por_prof.values(), key=lambda r: (-r['total'], r['profissional__nome'] or ''))
    # Saldo negativo = comissao paga e depois estornada: desconta do proximo repasse
    saldos = ComissaoService.saldos_por_profissional()
    comissoes_a_descontar = -sum((s for s in saldos.values() if s < 0), Decimal('0.00'))

    zero = Decimal('0.00')
    return {
        'periodo_referencia': hoje,
        # fat_*_total = atendimentos avulsos + pacotes vendidos no periodo
        'fat_hoje_total': (fat_hoje['total'] or zero) + (pac_hoje['total'] or zero),
        'fat_hoje_count': fat_hoje['count'] or 0,
        'fat_hoje_pacotes': pac_hoje['count'] or 0,
        'fat_semana_total': (fat_semana['total'] or zero) + (pac_semana['total'] or zero),
        'fat_semana_count': fat_semana['count'] or 0,
        'fat_semana_pacotes': pac_semana['count'] or 0,
        'fat_mes_total': (fat_mes['total'] or zero) + (pac_mes['total'] or zero),
        'fat_mes_count': fat_mes['count'] or 0,
        'fat_mes_pacotes': pac_mes['count'] or 0,
        'fat_mes_pacotes_total': pac_mes['total'] or zero,
        # ticket medio dos avulsos (mesma funcao do dashboard e da ficha do cliente)
        'ticket_medio_mes': financeiro.ticket_medio(fat_mes['total'], fat_mes['count']),
        'no_show_count': no_show_mes,
        'no_show_pct': round(no_show_pct, 1),
        'top_procedimentos': list(top_procs),
        'top_profissionais': list(top_profs),
        'comissoes_pendentes_total': comissoes_pendentes['total'] or Decimal('0.00'),
        'comissoes_pendentes_count': comissoes_pendentes['count'] or 0,
        'comissoes_por_prof': comissoes_por_prof,
        'comissoes_a_descontar': comissoes_a_descontar,
    }
