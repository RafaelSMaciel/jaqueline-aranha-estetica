"""NPS no painel: score, distribuicao, comentarios e a moderacao dos depoimentos do site."""
from __future__ import annotations

from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ...decorators import staff_required
from ...excecoes import RegraDeNegocio
from ...models import AvaliacaoNPS
from ...services.nps import NOTA_MAXIMA_DETRATOR, resumo_nps
from ...utils.auditoria import registrar_log
from ...utils.datas import PERIODOS, inicio_periodo, periodo_valido


def _pct(parte: int, total: int) -> int:
    return round(parte * 100 / total) if total else 0


@staff_required
def tela(request):
    """Distribuicao de NPS, score, promotores/detratores e comentarios."""
    periodo = periodo_valido(request.GET.get('periodo'))
    inicio = inicio_periodo(periodo)

    qs = AvaliacaoNPS.objects.select_related('atendimento__cliente', 'atendimento__profissional')
    if inicio is not None:
        qs = qs.filter(criado_em__gte=inicio)

    # services/nps: score e media "meio p/ cima" (= fn_nps_periodo do Postgres, paridade testada)
    resumo = resumo_nps(qs)
    total = resumo['qtd_respostas']

    # Distribuicao por nota (0..10) p/ as barras.
    por_nota = {row['nota']: row['c'] for row in qs.values('nota').annotate(c=Count('id'))}
    max_count = max(por_nota.values()) if por_nota else 0
    distribuicao = [
        {
            'nota': n,
            'count': por_nota.get(n, 0),
            'altura': round((por_nota.get(n, 0) * 100 / max_count)) if max_count else 0,
            'classe': 'detrator' if n <= NOTA_MAXIMA_DETRATOR else ('neutro' if n <= 8 else 'promotor'),
        }
        for n in range(10, -1, -1)
    ]

    comentarios = list(qs.exclude(comentario='').order_by('-criado_em')[:20])

    # Depoimentos que a cliente autorizou publicar e ainda aguardam a equipe
    # (independe do periodo: nao pode sumir da fila por ser antigo).
    aguardando_moderacao = list(
        AvaliacaoNPS.objects.select_related('atendimento__cliente', 'atendimento__profissional')
        .filter(autoriza_publicacao=True, aprovado_publicacao=False)
        .exclude(comentario='')
        .order_by('-criado_em')[:20]
    )

    return render(request, 'painel/nps.html', {
        'periodo': periodo,
        'total': total,
        'media': resumo['nota_media'],
        'promotores': resumo['qtd_promotores'], 'promotores_pct': _pct(resumo['qtd_promotores'], total),
        'neutros': resumo['qtd_neutros'], 'neutros_pct': _pct(resumo['qtd_neutros'], total),
        'detratores': resumo['qtd_detratores'], 'detratores_pct': _pct(resumo['qtd_detratores'], total),
        'nps_score': resumo['nps'],
        'distribuicao': distribuicao,
        'comentarios': comentarios,
        'aguardando_moderacao': aguardando_moderacao,
    })


@staff_required
@require_POST
def publicacao(request, pk):
    """Aprova/retira um depoimento do site (/depoimentos/). A regra (opt-in da cliente — LGPD — e
    comentario presente) mora no model: AvaliacaoNPS.aprovar_publicacao / retirar_publicacao."""
    avaliacao = get_object_or_404(AvaliacaoNPS, pk=pk)
    acao = request.POST.get('acao')
    try:
        if acao == 'aprovar':
            avaliacao.aprovar_publicacao()
            registrar_log(request.user, 'Aprovou depoimento p/ o site', 'avaliacao_nps', avaliacao.pk, request=request)
            messages.success(request, 'Depoimento aprovado: já aparece no site.')
        elif acao == 'reprovar':
            avaliacao.retirar_publicacao()
            registrar_log(request.user, 'Retirou depoimento do site', 'avaliacao_nps', avaliacao.pk, request=request)
            messages.success(request, 'Depoimento retirado do site.')
        else:
            messages.error(request, 'Ação inválida.')
    except RegraDeNegocio as exc:
        messages.error(request, exc.mensagem)
    periodo = request.POST.get('periodo', '')
    destino = redirect('aranha:painel_nps')
    if periodo in PERIODOS:
        destino['Location'] += f'?periodo={periodo}'
    return destino
