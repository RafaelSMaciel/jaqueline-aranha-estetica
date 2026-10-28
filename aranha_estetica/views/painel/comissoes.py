"""Comissoes do painel: livro-razao (0053) com status derivado, baixa, repasse em lote e regras.

- Lista: creditos do periodo (pela data do ATENDIMENTO, a mesma da competencia do financeiro),
  totais por status e resumo por profissional com o saldo do ledger inteiro.
- Repasse: "Pagar pendentes ate dd/mm" de uma profissional lanca os PAGAMENTOs numa transacao
  (ComissaoService.pagar_pendentes), audita e abre o comprovante para imprimir.
- Regras: criar (percentual OU valor fixo), desativar/reativar e excluir a nunca usada. Regra usada
  nao muda (RegraComissao.save + tg_regra_comissao_validar): percentual novo = regra nova.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, ProtectedError, Subquery
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ...decorators import staff_required
from ...forms.financeiro import RegraComissaoForm, RepasseForm
from ...forms.painel import avisar_erros
from ...models import MovimentoComissao, RegraComissao
from ...services.comissao import ComissaoService
from ...utils.auditoria import registrar_log
from ...utils.datas import hoje
from ...utils.parse import id_int

logger = logging.getLogger(__name__)

# Janelas de periodo aceitas (dias, pela data do atendimento). 'tudo' => sem corte.
PERIODOS = {'30': 30, '90': 90, 'tudo': None}


@staff_required
def painel_comissoes(request):
    """Creditos com status derivado + baixa + repasse + saldo por profissional.

    O saldo por profissional e do ledger inteiro (Σ credito − pagamento − estorno), nao so do periodo.
    """
    periodo = request.GET.get('periodo', '90')
    if periodo not in PERIODOS:
        periodo = '90'
    status_filter = request.GET.get('status', 'all')
    prof_filter = request.GET.get('profissional', 'all')

    base = MovimentoComissao.objects.creditos_com_status().select_related(
        'profissional', 'regra', 'atendimento__cliente', 'atendimento__procedimento',
    )
    dias = PERIODOS[periodo]
    if dias is not None:
        base = base.filter(atendimento__data_hora_inicio__gte=timezone.now() - timedelta(days=dias))
    prof_id = id_int(prof_filter)
    if prof_id is not None:
        base = base.filter(profissional_id=prof_id)

    # Totais por status (respeitando periodo+profissional, ignorando o filtro de status).
    totais = ComissaoService.totais_por_status(base)
    por_prof = ComissaoService.resumo_por_profissional(base, prof_id)

    # PAGAMENTO do credito: link p/ o comprovante do repasse em que ele saiu
    pagamento = MovimentoComissao.objects.filter(movimento_origem=OuterRef('pk'), tipo=MovimentoComissao.TIPO_PAGAMENTO)
    lista = base.annotate(pagamento_pk=Subquery(pagamento.values('pk')[:1])).order_by(
        '-atendimento__data_hora_inicio', '-pk')
    if status_filter in MovimentoComissao.Status.values:
        lista = lista.filter(status=status_filter)
    page = Paginator(lista, 50).get_page(request.GET.get('page', 1))

    ctx = {
        'periodo': periodo,
        'status_filter': status_filter,
        'prof_filter': prof_filter,
        # ativas + inativas com saldo != 0 (quem saiu com comissao pendente tambem recebe)
        'profissionais': ComissaoService.profissionais_do_filtro(),
        'pendente_total': totais[MovimentoComissao.STATUS_PENDENTE]['total'],
        'pendente_count': totais[MovimentoComissao.STATUS_PENDENTE]['qtd'],
        'paga_total': totais[MovimentoComissao.STATUS_PAGA]['total'],
        'paga_count': totais[MovimentoComissao.STATUS_PAGA]['qtd'],
        'estornada_count': totais[MovimentoComissao.STATUS_ESTORNADA]['qtd'],
        'por_profissional': por_prof,
        'tem_saldo_negativo': any(r['saldo_negativo'] for r in por_prof),
        'sem_regra_comissao': not ComissaoService.ha_regra_ativa(),
        'hoje': hoje(),
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


@staff_required
@require_POST
def admin_comissao_repasse(request):
    """'Pagar pendentes ate <data>' de uma profissional: PAGAMENTOs numa transacao + auditoria +
    comprovante (GET painel_comissao_repasse, imprimivel e reabrivel)."""
    form = RepasseForm(request.POST)
    if not form.is_valid():
        avisar_erros(request, form)
        return redirect('aranha:painel_comissoes')
    prof, ate = form.cleaned_data['profissional'], form.cleaned_data['ate']
    pagamentos = ComissaoService.pagar_pendentes(prof.pk, ate, usuario=request.user)
    if not pagamentos:
        messages.info(request, f'{prof.nome} não tem comissão pendente de atendimento até {ate:%d/%m/%Y}.')
        return redirect('aranha:painel_comissoes')
    total = sum((p.valor for p in pagamentos), Decimal('0.00'))
    registrar_log(
        request.user, 'Pagou comissões em lote (repasse)', 'movimento_comissao', pagamentos[0].pk,
        {'profissional': prof.pk, 'ate': ate.isoformat(), 'qtd': len(pagamentos), 'valor': str(total),
         'creditos': [p.movimento_origem_id for p in pagamentos]},
        request=request,
    )
    messages.success(request, f'Repasse de {prof.nome}: {len(pagamentos)} comissão(ões) paga(s).')
    return redirect('aranha:painel_comissao_repasse', pk=pagamentos[0].pk)


@staff_required
def painel_comissao_repasse(request, pk):
    """Comprovante do repasse (o lote do PAGAMENTO pk) para imprimir."""
    repasse = ComissaoService.repasse(pk)
    if repasse is None:
        raise Http404('Pagamento de comissão não encontrado.')
    return render(request, 'painel/comissao_repasse.html', {'repasse': repasse})


# ─── regras ─────────────────────────────────────────────────────────────────
@staff_required
def painel_comissao_regras(request):
    """Regras com especificidade e numero de usos; formulario de regra nova."""
    regras = list(
        RegraComissao.objects.select_related('profissional', 'procedimento')
        .annotate(usos=Count('movimentocomissao'))
        .order_by('-ativo', 'profissional__nome', 'procedimento__nome', '-pk')
    )
    regras.sort(key=lambda r: (not r.ativo, -r.especificidade))
    return render(request, 'painel/comissao_regras.html', {
        'regras': regras,
        'form': RegraComissaoForm(),
        'sem_regra_comissao': not any(r.ativo for r in regras),
    })


@staff_required
@require_POST
def admin_comissao_regra_criar(request):
    form = RegraComissaoForm(request.POST)
    if not form.is_valid():
        avisar_erros(request, form)
        return redirect('aranha:painel_comissao_regras')
    regra = form.salvar()
    registrar_log(request.user, 'Criou regra de comissão', 'regra_comissao', regra.pk,
                  {'profissional': regra.profissional_id, 'procedimento': regra.procedimento_id,
                   'percentual': str(regra.percentual) if regra.percentual is not None else None,
                   'valor_fixo': str(regra.valor) if regra.valor is not None else None}, request=request)
    messages.success(request, f'Regra criada: {regra}.')
    return redirect('aranha:painel_comissao_regras')


@staff_required
@require_POST
def admin_comissao_regra_ativo(request, pk):
    """Desativa/reativa (o unico jeito de "mudar" uma regra ja usada)."""
    regra = get_object_or_404(RegraComissao, pk=pk)
    regra.ativo = not regra.ativo
    regra.save(update_fields=['ativo', 'atualizado_em'])
    registrar_log(request.user, 'Reativou regra de comissão' if regra.ativo else 'Desativou regra de comissão',
                  'regra_comissao', regra.pk, request=request)
    messages.success(request, f'Regra {"reativada" if regra.ativo else "desativada"}: {regra}.')
    return redirect('aranha:painel_comissao_regras')


@staff_required
@require_POST
def admin_comissao_regra_excluir(request, pk):
    """Exclui regra NUNCA usada (cadastro errado). Usada: so desativar (o credito cita a regra)."""
    regra = get_object_or_404(RegraComissao, pk=pk)
    if regra.foi_usada():
        messages.error(request, 'Regra já usada em comissão não é excluída: desative-a.')
        return redirect('aranha:painel_comissao_regras')
    descricao = str(regra)
    try:
        regra.delete()
    except ProtectedError:   # corrida com um credito novo (FK PROTECT)
        messages.error(request, 'Regra já usada em comissão não é excluída: desative-a.')
        return redirect('aranha:painel_comissao_regras')
    registrar_log(request.user, 'Excluiu regra de comissão', 'regra_comissao', pk, {'regra': descricao},
                  request=request)
    messages.success(request, f'Regra excluída: {descricao}.')
    return redirect('aranha:painel_comissao_regras')
