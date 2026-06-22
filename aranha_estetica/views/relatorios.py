"""Relatorios do painel — NPS e Comissoes.

Telas dedicadas (antes NPS vivia como 1 card no dashboard e Comissoes como
tabela no financeiro). Apenas leitura, exceto a baixa de comissao (POST).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Q, Sum
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..decorators import staff_required
from ..models import AvaliacaoNPS, MovimentoComissao, Profissional
from ..services.comissao_service import ComissaoService

logger = logging.getLogger(__name__)

# Janelas de periodo aceitas (dias). 'tudo' => sem corte.
PERIODOS = {'30': 30, '90': 90, 'tudo': None}


def _inicio_periodo(periodo: str):
    dias = PERIODOS.get(periodo, 90)
    if dias is None:
        return None
    return timezone.now() - timedelta(days=dias)


# ════════════════════════════════════════════════════════════════════
# NPS
# ════════════════════════════════════════════════════════════════════
@staff_required
def painel_nps(request):
    """Distribuicao de NPS, score, promotores/detratores e comentarios."""
    periodo = request.GET.get('periodo', '90')
    if periodo not in PERIODOS:
        periodo = '90'
    inicio = _inicio_periodo(periodo)

    qs = AvaliacaoNPS.objects.select_related(
        'atendimento__cliente', 'atendimento__profissional',
    )
    if inicio is not None:
        qs = qs.filter(criado_em__gte=inicio)

    agg = qs.aggregate(
        total=Count('id'),
        media=Avg('nota'),
        promotores=Count('id', filter=Q(nota__gte=9)),
        neutros=Count('id', filter=Q(nota__gte=7, nota__lte=8)),
        detratores=Count('id', filter=Q(nota__lte=6)),
    )
    total = agg['total'] or 0
    promotores = agg['promotores'] or 0
    neutros = agg['neutros'] or 0
    detratores = agg['detratores'] or 0

    nps_score = round((promotores - detratores) * 100 / total) if total else None
    pct = lambda n: round(n * 100 / total) if total else 0  # noqa: E731

    # Distribuicao por nota (0..10) p/ as barras.
    por_nota = {row['nota']: row['c'] for row in qs.values('nota').annotate(c=Count('id'))}
    max_count = max(por_nota.values()) if por_nota else 0
    distribuicao = [
        {
            'nota': n,
            'count': por_nota.get(n, 0),
            'altura': round((por_nota.get(n, 0) * 100 / max_count)) if max_count else 0,
            'classe': 'detrator' if n <= 6 else ('neutro' if n <= 8 else 'promotor'),
        }
        for n in range(10, -1, -1)
    ]

    comentarios = [
        a for a in qs.exclude(comentario__isnull=True).exclude(comentario='')
        .order_by('-criado_em')[:20]
    ]

    ctx = {
        'periodo': periodo,
        'total': total,
        'media': round(agg['media'], 1) if agg['media'] is not None else None,
        'promotores': promotores, 'promotores_pct': pct(promotores),
        'neutros': neutros, 'neutros_pct': pct(neutros),
        'detratores': detratores, 'detratores_pct': pct(detratores),
        'nps_score': nps_score,
        'distribuicao': distribuicao,
        'comentarios': comentarios,
    }
    return render(request, 'painel/nps.html', ctx)


# ════════════════════════════════════════════════════════════════════
# Comissoes
# ════════════════════════════════════════════════════════════════════
@staff_required
def painel_comissoes(request):
    """Lista comissoes com filtros (status/profissional/periodo) + baixa."""
    periodo = request.GET.get('periodo', '90')
    if periodo not in PERIODOS:
        periodo = '90'
    status_filter = request.GET.get('status', 'all')
    prof_filter = request.GET.get('profissional', 'all')
    inicio = _inicio_periodo(periodo)

    base = MovimentoComissao.objects.select_related(
        'profissional', 'atendimento__cliente', 'atendimento__procedimento',
    )
    if inicio is not None:
        base = base.filter(criado_em__gte=inicio)
    if prof_filter != 'all' and prof_filter.isdigit():
        base = base.filter(profissional_id=int(prof_filter))

    # Totais por status (respeitando periodo+profissional, ignorando o filtro de status).
    totais = base.aggregate(
        pendente_total=Sum('valor', filter=Q(status=MovimentoComissao.STATUS_PENDENTE)),
        pendente_count=Count('id', filter=Q(status=MovimentoComissao.STATUS_PENDENTE)),
        paga_total=Sum('valor', filter=Q(status=MovimentoComissao.STATUS_PAGA)),
        paga_count=Count('id', filter=Q(status=MovimentoComissao.STATUS_PAGA)),
        estornada_count=Count('id', filter=Q(status=MovimentoComissao.STATUS_ESTORNADA)),
    )

    # Resumo por profissional (pendente + paga).
    por_prof = (
        base.exclude(status=MovimentoComissao.STATUS_ESTORNADA)
        .values('profissional__nome')
        .annotate(
            pendente=Sum('valor', filter=Q(status=MovimentoComissao.STATUS_PENDENTE)),
            paga=Sum('valor', filter=Q(status=MovimentoComissao.STATUS_PAGA)),
            qtd=Count('id'),
        )
        .order_by('-pendente')
    )

    lista = base.order_by('-criado_em')
    if status_filter in (MovimentoComissao.STATUS_PENDENTE, MovimentoComissao.STATUS_PAGA, MovimentoComissao.STATUS_ESTORNADA):
        lista = lista.filter(status=status_filter)

    paginator = Paginator(lista, 50)
    page = paginator.get_page(request.GET.get('page', 1))

    ctx = {
        'periodo': periodo,
        'status_filter': status_filter,
        'prof_filter': prof_filter,
        'profissionais': Profissional.objects.filter(ativo=True).order_by('nome'),
        'pendente_total': totais['pendente_total'] or Decimal('0.00'),
        'pendente_count': totais['pendente_count'] or 0,
        'paga_total': totais['paga_total'] or Decimal('0.00'),
        'paga_count': totais['paga_count'] or 0,
        'estornada_count': totais['estornada_count'] or 0,
        'por_profissional': list(por_prof),
        'comissoes': page,
    }
    return render(request, 'painel/comissoes.html', ctx)


@staff_required
@require_POST
def admin_comissao_pagar(request, pk):
    """Marca uma comissao PENDENTE como PAGA."""
    ok = ComissaoService.marcar_paga(pk)
    if ok:
        messages.success(request, 'Comissão marcada como paga.')
    else:
        messages.error(request, 'Comissão não encontrada ou já não estava pendente.')
    destino = request.POST.get('next') or 'aranha:painel_comissoes'
    if destino.startswith('aranha:'):
        return redirect(destino)
    return redirect('aranha:painel_comissoes')
