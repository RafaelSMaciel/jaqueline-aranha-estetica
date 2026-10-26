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
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..decorators import staff_required
from ..models import AvaliacaoNPS, MovimentoComissao, Profissional
from ..services.comissao_service import ComissaoService
from ..utils.audit import registrar_log
from ..utils.nps import resumo_nps
from ..utils.parse import id_int

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


# ════════════════════════════════════════════════════════════════════
# Comissoes
# ════════════════════════════════════════════════════════════════════
@staff_required
def painel_comissoes(request):
    """Comissoes (livro-razao, 0053): creditos com status derivado + baixa + saldo.

    Status vem do ledger (PAGAMENTO/ESTORNO do credito). O saldo por profissional
    e do ledger inteiro (Σ credito − pagamento − estorno) e pode ficar NEGATIVO:
    comissao paga e depois estornada = a descontar do proximo repasse.
    """
    periodo = request.GET.get('periodo', '90')
    if periodo not in PERIODOS:
        periodo = '90'
    status_filter = request.GET.get('status', 'all')
    prof_filter = request.GET.get('profissional', 'all')
    inicio = _inicio_periodo(periodo)

    base = MovimentoComissao.objects.creditos_com_status().select_related(
        'profissional', 'atendimento__cliente', 'atendimento__procedimento',
    )
    if inicio is not None:
        base = base.filter(criado_em__gte=inicio)
    # id_int: isdigit() aceitava '²' e o int() dava 500
    prof_id = id_int(prof_filter)
    if prof_id is not None:
        base = base.filter(profissional_id=prof_id)

    # Totais por status (respeitando periodo+profissional, ignorando o filtro de status).
    totais = {
        MovimentoComissao.STATUS_PENDENTE: [Decimal('0.00'), 0],
        MovimentoComissao.STATUS_PAGA: [Decimal('0.00'), 0],
        MovimentoComissao.STATUS_ESTORNADA: [Decimal('0.00'), 0],
    }
    for status, valor in base.order_by().values_list('status', 'valor'):
        totais[status][0] += valor
        totais[status][1] += 1

    # Resumo por profissional (pendente + paga no periodo; saldo do ledger inteiro).
    por_prof = ComissaoService.resumo_por_profissional(base, prof_id)

    lista = base.order_by('-criado_em', '-pk')
    if status_filter in (MovimentoComissao.STATUS_PENDENTE, MovimentoComissao.STATUS_PAGA, MovimentoComissao.STATUS_ESTORNADA):
        lista = lista.filter(status=status_filter)

    paginator = Paginator(lista, 50)
    page = paginator.get_page(request.GET.get('page', 1))

    ctx = {
        'periodo': periodo,
        'status_filter': status_filter,
        'prof_filter': prof_filter,
        'profissionais': Profissional.objects.filter(ativo=True).order_by('nome'),
        'pendente_total': totais[MovimentoComissao.STATUS_PENDENTE][0],
        'pendente_count': totais[MovimentoComissao.STATUS_PENDENTE][1],
        'paga_total': totais[MovimentoComissao.STATUS_PAGA][0],
        'paga_count': totais[MovimentoComissao.STATUS_PAGA][1],
        'estornada_count': totais[MovimentoComissao.STATUS_ESTORNADA][1],
        'por_profissional': por_prof,
        'tem_saldo_negativo': any(r['saldo_negativo'] for r in por_prof),
        'comissoes': page,
    }
    return render(request, 'painel/comissoes.html', ctx)


@staff_required
@require_POST
def admin_comissao_pagar(request, pk):
    """Baixa de uma comissao PENDENTE: lanca o PAGAMENTO (autor = quem clicou)."""
    pagamento = ComissaoService.marcar_paga(pk, usuario=request.user)
    if pagamento:
        registrar_log(
            request.user, 'Marcou comissão como paga', 'movimento_comissao', pk,
            {'pagamento': pagamento.pk, 'valor': str(pagamento.valor)}, request=request,
        )
        messages.success(request, 'Comissão marcada como paga.')
    else:
        messages.error(request, 'Comissão não encontrada ou já não estava pendente.')
    # Allowlist: nome de rota arbitrario vindo do POST daria NoReverseMatch (500)
    destino = request.POST.get('next')
    if destino in ('aranha:painel_comissoes', 'aranha:dashboard_financeiro'):
        return redirect(destino)
    return redirect('aranha:painel_comissoes')
