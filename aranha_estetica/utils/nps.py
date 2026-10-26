"""NPS — fonte unica do calculo exibido no painel (visao geral e relatorio de NPS).

NPS = %promotores (9-10) - %detratores (0-6), inteiro arredondado "meio p/ cima"
(Decimal ROUND_HALF_UP, ou seja, empate se afasta do zero) — o mesmo do ``round(numeric)``
do Postgres. O ``round()`` do Python e bancario (``round(12.5) == 12``) e divergia do
espelho SQL em 1 ponto nos casos .5 (decisao D7, docs/specs/padrao-sql.md §5.4 item 3 / §7.1).

Espelho SQL (paridade em tests/test_pg_paridade.py): ``fn_nps_periodo`` / ``vw_nps_mensal``.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, Q, Sum

UM = Decimal('1')
UM_DECIMO = Decimal('0.1')


def calcular_nps(promotores, detratores, total) -> int | None:
    """%promotores - %detratores (inteiro, meio p/ cima); None sem respostas."""
    if not total:
        return None
    valor = Decimal((promotores or 0) - (detratores or 0)) * 100 / Decimal(total)
    return int(valor.quantize(UM, rounding=ROUND_HALF_UP))


def media_nota(soma, total) -> Decimal | None:
    """Media das notas com 1 casa (meio p/ cima) = round(avg(nota), 1) do Postgres."""
    if not total:
        return None
    return (Decimal(soma or 0) / Decimal(total)).quantize(UM_DECIMO, rounding=ROUND_HALF_UP)


def resumo_nps(qs) -> dict:
    """Agrega um queryset de AvaliacaoNPS (1 query) = colunas do fn_nps_periodo.

    {'qtd_respostas', 'qtd_promotores', 'qtd_neutros', 'qtd_detratores', 'nps', 'nota_media'}
    """
    agg = qs.aggregate(
        total=Count('id'),
        soma=Sum('nota'),
        promotores=Count('id', filter=Q(nota__gte=9)),
        neutros=Count('id', filter=Q(nota__gte=7, nota__lte=8)),
        detratores=Count('id', filter=Q(nota__lte=6)),
    )
    total = agg['total'] or 0
    return {
        'qtd_respostas': total,
        'qtd_promotores': agg['promotores'] or 0,
        'qtd_neutros': agg['neutros'] or 0,
        'qtd_detratores': agg['detratores'] or 0,
        'nps': calcular_nps(agg['promotores'], agg['detratores'], total),
        'nota_media': media_nota(agg['soma'], total),
    }
