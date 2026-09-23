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
from ..models import Atendimento, CompraPacote, MovimentoComissao

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

    # Faturamento por periodo (REALIZADO + valor_cobrado > 0, exclui retornos).
    # Sessao de pacote NAO entra: valor_cobrado dela e o preco cheio do
    # procedimento, e a receita real ja entrou na venda do pacote (abaixo).
    base_pago = Atendimento.objects.filter(
        status=Atendimento.STATUS_REALIZADO,
        eh_retorno=False,
        valor_cobrado__gt=0,
        sessao_pacote_vinculada__isnull=True,
    )
    # Venda de pacote = receita no dia da compra (cancelados fora)
    vendas_pacote = CompraPacote.objects.exclude(status='CANCELADO').filter(valor_pago__gt=0)

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
        total=Sum('valor_pago'), count=Count('id'),
    )
    pac_semana = vendas_pacote.filter(criado_em__gte=inicio_semana).aggregate(
        total=Sum('valor_pago'), count=Count('id'),
    )
    pac_mes = vendas_pacote.filter(criado_em__gte=inicio_mes).aggregate(
        total=Sum('valor_pago'), count=Count('id'),
    )

    # No-show no mes
    no_show_mes = Atendimento.objects.filter(
        status=Atendimento.STATUS_FALTOU,
        data_hora_inicio__gte=inicio_mes,
    ).count()

    realizados_mes = fat_mes['count'] or 0
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

    # Comissoes pendentes (todas)
    comissoes_pendentes = MovimentoComissao.objects.filter(
        status=MovimentoComissao.STATUS_PENDENTE,
    ).aggregate(total=Sum('valor'), count=Count('id'))

    # Comissoes por profissional (mes)
    comissoes_por_prof = (
        MovimentoComissao.objects
        .filter(criado_em__gte=inicio_mes, status__in=['PENDENTE', 'PAGA'])
        .values('profissional__nome')
        .annotate(total=Sum('valor'), count=Count('id'))
        .order_by('-total')
    )

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
        'no_show_count': no_show_mes,
        'no_show_pct': round(no_show_pct, 1),
        'top_procedimentos': list(top_procs),
        'top_profissionais': list(top_profs),
        'comissoes_pendentes_total': comissoes_pendentes['total'] or Decimal('0.00'),
        'comissoes_pendentes_count': comissoes_pendentes['count'] or 0,
        'comissoes_por_prof': list(comissoes_por_prof),
    }
