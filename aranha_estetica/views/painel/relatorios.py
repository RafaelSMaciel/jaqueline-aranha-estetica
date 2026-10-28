"""Relatorios do painel — Comissoes (o NPS tem tela propria: views/painel/nps.py).

Apenas leitura, exceto a baixa de comissao (POST).
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from ...decorators import staff_required
from ...models import MovimentoComissao, Profissional
from ...services.comissao import ComissaoService
from ...utils.auditoria import registrar_log
from ...utils.datas import inicio_periodo, periodo_valido
from ...utils.parse import id_int

logger = logging.getLogger(__name__)


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
    periodo = periodo_valido(request.GET.get('periodo'))
    status_filter = request.GET.get('status', 'all')
    prof_filter = request.GET.get('profissional', 'all')
    inicio = inicio_periodo(periodo)

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
