"""Regras de faturamento (D20) — fonte UNICA p/ dashboard, financeiro e ficha do cliente.

Faturamento = atendimentos avulsos + vendas de pacote:
  - avulso faturado: REALIZADO, nao retorno, valor_cobrado > 0 e SEM consumo de
    pacote (a sessao de pacote tem valor_cobrado = preco cheio do procedimento, mas a
    receita ja entrou na venda do pacote — contar as duas dobraria a receita);
  - pacote: valor_pago - valor_reembolsado na data da VENDA (cancelado entra com o
    que a clinica reteve; devolvido integral = 0).
Ticket medio = Σ avulsos faturados / quantidade deles (centavos, meio p/ cima).

Espelho SQL (fase L2, com paridade): vw_faturamento_mensal / vw_cliente_resumo
(docs/specs/padrao-sql.md §5.4). Antes desta unificacao o painel calculava o ticket
sem o filtro de valor > 0 e a ficha somava a sessao de pacote a preco cheio no "LTV"
(spec §7.3 itens 2 e 3).
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, F, Sum

from ..models import Atendimento, CompraPacote

ZERO = Decimal('0.00')
CENTAVO = Decimal('0.01')


def atendimentos_faturados(qs=None):
    """Avulsos que contam como receita (D20)."""
    qs = Atendimento.objects.all() if qs is None else qs
    return qs.filter(
        status=Atendimento.STATUS_REALIZADO,
        eh_retorno=False,
        valor_cobrado__gt=0,
        sessao_pacote_vinculada__isnull=True,
    )


def vendas_pacote(qs=None):
    """Compras de pacote com receita liquida (> 0)."""
    qs = CompraPacote.objects.all() if qs is None else qs
    return qs.filter(valor_pago__gt=F('valor_reembolsado'))


RECEITA_PACOTE = Sum(F('valor_pago') - F('valor_reembolsado'))


def ticket_medio(total, qtd) -> Decimal:
    """Σ valor / quantidade, em centavos (meio p/ cima); 0 sem atendimento."""
    if not qtd:
        return ZERO
    return (Decimal(total or 0) / Decimal(qtd)).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def resumo_avulsos(qs) -> dict:
    """{'total', 'qtd', 'ticket_medio'} dos atendimentos faturados do queryset."""
    agg = atendimentos_faturados(qs).aggregate(total=Sum('valor_cobrado'), qtd=Count('pk'))
    total, qtd = agg['total'] or ZERO, agg['qtd'] or 0
    return {'total': total, 'qtd': qtd, 'ticket_medio': ticket_medio(total, qtd)}


def resumo_pacotes(qs) -> dict:
    """{'total', 'qtd'} da receita liquida das vendas de pacote do queryset."""
    agg = vendas_pacote(qs).aggregate(total=RECEITA_PACOTE, qtd=Count('pk'))
    return {'total': agg['total'] or ZERO, 'qtd': agg['qtd'] or 0}


def valor_gasto_cliente(cliente) -> dict:
    """Quanto a cliente rendeu (o "LTV" da ficha): avulsos faturados + pacotes
    liquidos — a sessao de pacote nao soma de novo (= vw_cliente_resumo.valor_total_gasto)."""
    avulsos = resumo_avulsos(Atendimento.objects.filter(cliente=cliente))
    pacotes = resumo_pacotes(CompraPacote.objects.filter(cliente=cliente))
    return {
        'avulsos': avulsos['total'],
        'pacotes': pacotes['total'],
        'total': avulsos['total'] + pacotes['total'],
        'ticket_medio': avulsos['ticket_medio'],
        'qtd_avulsos': avulsos['qtd'],
    }
