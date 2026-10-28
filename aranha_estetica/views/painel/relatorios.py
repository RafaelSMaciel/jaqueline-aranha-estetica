"""Relatorio de NPS do painel (comissoes: views/painel/comissoes).

Tela dedicada (antes NPS vivia como 1 card no dashboard). Apenas leitura, exceto a moderacao
dos depoimentos (POST).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ...decorators import staff_required
from ...models import AvaliacaoNPS
from ...utils.auditoria import registrar_log
from ...utils.nps import resumo_nps

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

    # utils/nps: score e media "meio p/ cima" (= fn_nps_periodo do Postgres, paridade testada)
    resumo = resumo_nps(qs)
    total = resumo['qtd_respostas']
    promotores = resumo['qtd_promotores']
    neutros = resumo['qtd_neutros']
    detratores = resumo['qtd_detratores']

    nps_score = resumo['nps']
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

    # Depoimentos que o cliente autorizou publicar e ainda aguardam a equipe
    # (independe do periodo: nao pode sumir da fila por ser antigo).
    aguardando_moderacao = list(
        AvaliacaoNPS.objects.select_related('atendimento__cliente', 'atendimento__profissional')
        .filter(autoriza_publicacao=True, aprovado_publicacao=False)
        .exclude(comentario__isnull=True).exclude(comentario='')
        .order_by('-criado_em')[:20]
    )

    ctx = {
        'periodo': periodo,
        'total': total,
        'media': resumo['nota_media'],
        'promotores': promotores, 'promotores_pct': pct(promotores),
        'neutros': neutros, 'neutros_pct': pct(neutros),
        'detratores': detratores, 'detratores_pct': pct(detratores),
        'nps_score': nps_score,
        'distribuicao': distribuicao,
        'comentarios': comentarios,
        'aguardando_moderacao': aguardando_moderacao,
    }
    return render(request, 'painel/nps.html', ctx)


@staff_required
@require_POST
def admin_nps_publicacao(request, pk):
    """Aprova/retira um depoimento do site (/depoimentos/).

    So publica com opt-in do cliente (autoriza_publicacao) — LGPD.
    """
    avaliacao = get_object_or_404(AvaliacaoNPS, pk=pk)
    acao = request.POST.get('acao')
    if acao == 'aprovar':
        if not avaliacao.autoriza_publicacao:
            messages.error(request, 'O cliente não autorizou publicar este depoimento.')
        elif not (avaliacao.comentario or '').strip():
            messages.error(request, 'Avaliação sem comentário não vira depoimento.')
        else:
            avaliacao.aprovado_publicacao = True
            avaliacao.save(update_fields=['aprovado_publicacao'])
            registrar_log(request.user, 'Aprovou depoimento p/ o site', 'avaliacao_nps', avaliacao.pk, request=request)
            messages.success(request, 'Depoimento aprovado: já aparece no site.')
    elif acao == 'reprovar':
        avaliacao.aprovado_publicacao = False
        avaliacao.save(update_fields=['aprovado_publicacao'])
        registrar_log(request.user, 'Retirou depoimento do site', 'avaliacao_nps', avaliacao.pk, request=request)
        messages.success(request, 'Depoimento retirado do site.')
    else:
        messages.error(request, 'Ação inválida.')
    periodo = request.POST.get('periodo', '')
    destino = redirect('aranha:painel_nps')
    if periodo in PERIODOS:
        destino['Location'] += f'?periodo={periodo}'
    return destino
