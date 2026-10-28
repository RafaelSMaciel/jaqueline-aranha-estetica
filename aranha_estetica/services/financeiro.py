"""Regras de faturamento (D20) por COMPETENCIA MENSAL — fonte unica p/ visao geral, financeiro, Excel
e ficha da cliente.

Periodo = mes civil no fuso da clinica: [1o dia, 1o dia do mes seguinte) (``competencia``). A visao
geral e o financeiro usam a mesma funcao e escrevem o periodo na tela — antes um era o mes civil e o
outro "ultimos 30 dias" e os dois chamavam de "mes".

Faturamento do mes = atendimentos avulsos + pacotes:
  - avulso faturado: REALIZADO, nao retorno, valor_cobrado > 0 e SEM consumo de pacote, no mes do
    atendimento (a sessao de pacote tem valor_cobrado = preco cheio, mas a receita ja entrou na
    venda do pacote — contar as duas dobraria a receita);
  - pacote: valor_pago no mes da VENDA (criado_em) menos valor_reembolsado no mes do CANCELAMENTO
    (cancelado_em). Mes fechado nao muda depois: o reembolso de marco nao mexe em janeiro. Um mes
    pode fechar com receita de pacotes NEGATIVA (reembolso maior que as vendas do mes).
Ticket medio = Σ avulsos faturados / quantidade deles (centavos, meio p/ cima).
Comissao do mes conta pela data do ATENDIMENTO (a mesma do faturamento), nao pelo clique.

Espelho SQL (paridade em tests/test_pg_paridade): vw_faturamento_mensal (0065) e vw_cliente_resumo.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, DecimalField, F, QuerySet, Sum
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from ..models import Atendimento, CompraPacote
from ..utils.datas import hoje, inicio_do_dia

ZERO = Decimal('0.00')
CENTAVO = Decimal('0.01')
MESES = ('janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro',
         'outubro', 'novembro', 'dezembro')


# ─── periodo ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Periodo:
    """Intervalo de datas locais [inicio, fim) — fim exclusivo."""
    inicio: date
    fim: date

    @property
    def inicio_dt(self) -> datetime:
        return inicio_do_dia(self.inicio)

    @property
    def fim_dt(self) -> datetime:
        return inicio_do_dia(self.fim)

    @property
    def ultimo_dia(self) -> date:
        return self.fim - timedelta(days=1)

    def filtro(self, campo: str) -> dict:
        """``.filter(**periodo.filtro('data_hora_inicio'))`` — intervalo que usa o indice da coluna."""
        return {f'{campo}__gte': self.inicio_dt, f'{campo}__lt': self.fim_dt}

    # competencia mensal (inicio = dia 1)
    @property
    def param(self) -> str:
        """'AAAA-MM' (?mes= do financeiro e do Excel)."""
        return f'{self.inicio:%Y-%m}'

    @property
    def rotulo(self) -> str:
        """'setembro de 2026'."""
        return f'{MESES[self.inicio.month - 1]} de {self.inicio.year}'

    def anterior(self) -> Periodo:
        return competencia(self.inicio - timedelta(days=1))

    def seguinte(self) -> Periodo:
        return competencia(self.fim)


def competencia(dia: date | None = None) -> Periodo:
    """Mes civil (fuso local) que contem ``dia`` (padrao: hoje)."""
    dia = dia or hoje()
    inicio = dia.replace(day=1)
    fim = date(inicio.year + inicio.month // 12, inicio.month % 12 + 1, 1)
    return Periodo(inicio, fim)


def competencia_do_param(valor: str | None) -> Periodo:
    """?mes=AAAA-MM -> Periodo; vazio, invalido ou fora de 2000-2100 = mes corrente (URL manipulada
    nao vira 500 — o ano 9999 estouraria o "mes seguinte" e a tabela de 12 meses)."""
    from ..utils.datas import ler_mes

    try:
        dia = ler_mes(valor) if valor else None
    except ValueError:
        dia = None
    if dia is None or not 2000 <= dia.year <= 2100:
        return competencia()
    return competencia(dia)


# ─── receita ────────────────────────────────────────────────────────────────
def atendimentos_faturados(qs=None):
    """Avulsos que contam como receita (D20)."""
    qs = Atendimento.objects.all() if qs is None else qs
    return qs.filter(
        status=Atendimento.STATUS_REALIZADO,
        eh_retorno=False,
        valor_cobrado__gt=0,
        sessao_pacote_vinculada__isnull=True,
    )


# Producao: o valor de cada sessao REALIZADA — avulso pelo valor cobrado, sessao de pacote pelo valor
# unitario congelado na venda. Para rankings por profissional/procedimento: o faturamento poe a
# receita do pacote no mes da venda e zera a sessao, e quem so atende pacote aparecia com "gerou R$ 0"
# ao lado da comissao dela.
VALOR_PRODUZIDO = Coalesce(
    F('sessao_pacote_vinculada__compra_pacote_item__valor_unitario'), F('valor_cobrado'),
    output_field=DecimalField(max_digits=10, decimal_places=2),
)


def producao(qs=None):
    """Atendimentos realizados (sem retorno) anotados com `valor_produzido`."""
    qs = Atendimento.objects.all() if qs is None else qs
    return qs.filter(status=Atendimento.STATUS_REALIZADO, eh_retorno=False).annotate(
        valor_produzido=VALOR_PRODUZIDO)


def creditos_indicacao_usados(periodo: Periodo) -> Decimal:
    """Credito de indicacao abatido em atendimentos no periodo, menos o devolvido (custo de
    marketing da clinica: o valor_cobrado segue cheio, base da comissao)."""
    from ..models import MovimentoCarteira

    def soma(**filtro):
        return MovimentoCarteira.objects.filter(**periodo.filtro('criado_em'), **filtro).aggregate(
            t=Sum('valor'))['t'] or ZERO
    return soma(origem='USO_AGENDAMENTO', tipo='DEBITO') - soma(origem='CANCELAMENTO', tipo='CREDITO')


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


def resumo_pacotes(periodo: Periodo) -> dict:
    """Receita de pacotes por competencia: vendas do mes (valor_pago, pela data da venda) menos os
    reembolsos do mes (valor_reembolsado, pela data do cancelamento). ``total`` pode ser negativo."""
    vendas = CompraPacote.objects.filter(valor_pago__gt=0, **periodo.filtro('criado_em')).aggregate(
        total=Sum('valor_pago'), qtd=Count('pk'))
    reembolsos = CompraPacote.objects.filter(valor_reembolsado__gt=0, **periodo.filtro('cancelado_em')).aggregate(
        total=Sum('valor_reembolsado'), qtd=Count('pk'))
    valor_vendas, valor_reembolsos = vendas['total'] or ZERO, reembolsos['total'] or ZERO
    return {
        'vendas': valor_vendas, 'qtd_vendas': vendas['qtd'] or 0,
        'reembolsos': valor_reembolsos, 'qtd_reembolsos': reembolsos['qtd'] or 0,
        'total': valor_vendas - valor_reembolsos,
    }


def resumo_receita(periodo: Periodo) -> dict:
    """Faturamento do periodo (os cards da visao geral, do financeiro e o total do Excel)."""
    avulsos = resumo_avulsos(Atendimento.objects.filter(**periodo.filtro('data_hora_inicio')))
    pacotes = resumo_pacotes(periodo)
    return {
        'periodo': periodo,
        'avulsos': avulsos,
        'pacotes': pacotes,
        'total': avulsos['total'] + pacotes['total'],
        'ticket_medio': avulsos['ticket_medio'],
    }


def no_show(periodo: Periodo) -> dict:
    """Faltas do periodo ÷ (faltas + realizados) — sessao de pacote, retorno e atendimento sem valor
    tambem compareceram (so o faturado no denominador inflava o % em clinica que vende pacote)."""
    contagem = dict(
        Atendimento.objects.filter(
            **periodo.filtro('data_hora_inicio'),
            status__in=(Atendimento.STATUS_FALTOU, Atendimento.STATUS_REALIZADO),
        ).order_by().values('status').annotate(n=Count('pk')).values_list('status', 'n')
    )
    faltas = contagem.get(Atendimento.STATUS_FALTOU, 0)
    finalizados = faltas + contagem.get(Atendimento.STATUS_REALIZADO, 0)
    return {'faltas': faltas, 'finalizados': finalizados,
            'percentual': round(faltas * 100 / finalizados, 1) if finalizados else 0}


def _por_mes(qs: QuerySet, campo: str, soma: str) -> dict:
    """{1o dia do mes local: (Σ soma, quantidade)} — TruncMonth no fuso da clinica."""
    linhas = (qs.annotate(mes=TruncMonth(campo, tzinfo=timezone.get_current_timezone()))
              .order_by().values('mes').annotate(valor=Sum(soma), qtd=Count('pk')))
    return {timezone.localtime(linha['mes']).date(): (linha['valor'] or ZERO, linha['qtd']) for linha in linhas}


def ultimos_12_meses(ate: Periodo | None = None) -> list[dict]:
    """Faturamento mes a mes (do mais recente ao mais antigo) — 3 consultas p/ os 12 meses.

    Mesmas regras de resumo_receita; espelho de vw_faturamento_mensal (paridade no Postgres)."""
    ate = ate or competencia()
    meses = [ate]
    while len(meses) < 12:
        meses.append(meses[-1].anterior())
    janela = Periodo(meses[-1].inicio, ate.fim)
    avulsos = _por_mes(atendimentos_faturados(Atendimento.objects.filter(**janela.filtro('data_hora_inicio'))),
                       'data_hora_inicio', 'valor_cobrado')
    vendas = _por_mes(CompraPacote.objects.filter(valor_pago__gt=0, **janela.filtro('criado_em')),
                      'criado_em', 'valor_pago')
    reembolsos = _por_mes(CompraPacote.objects.filter(valor_reembolsado__gt=0, **janela.filtro('cancelado_em')),
                          'cancelado_em', 'valor_reembolsado')
    tabela = []
    for mes in meses:
        valor_avulsos, qtd_avulsos = avulsos.get(mes.inicio, (ZERO, 0))
        valor_vendas, qtd_vendas = vendas.get(mes.inicio, (ZERO, 0))
        valor_reembolsos, qtd_reembolsos = reembolsos.get(mes.inicio, (ZERO, 0))
        pacotes = valor_vendas - valor_reembolsos
        tabela.append({
            'periodo': mes,
            'avulsos': valor_avulsos, 'qtd_avulsos': qtd_avulsos,
            'vendas_pacotes': valor_vendas, 'qtd_vendas_pacotes': qtd_vendas,
            'reembolsos_pacotes': valor_reembolsos, 'qtd_reembolsos_pacotes': qtd_reembolsos,
            'pacotes': pacotes,
            'total': valor_avulsos + pacotes,
            'ticket_medio': ticket_medio(valor_avulsos, qtd_avulsos),
        })
    return tabela


# ─── cliente (historico inteiro, sem competencia) ───────────────────────────
def valor_gasto_cliente(cliente) -> dict:
    """Quanto a cliente rendeu (o "LTV" da ficha): avulsos faturados + pacotes liquidos do
    reembolso — a sessao de pacote nao soma de novo (= vw_cliente_resumo.valor_total_gasto)."""
    avulsos = resumo_avulsos(Atendimento.objects.filter(cliente=cliente))
    pacotes = CompraPacote.objects.filter(cliente=cliente).aggregate(
        t=Sum(F('valor_pago') - F('valor_reembolsado')))['t'] or ZERO
    return {
        'avulsos': avulsos['total'],
        'pacotes': pacotes,
        'total': avulsos['total'] + pacotes,
        'ticket_medio': avulsos['ticket_medio'],
        'qtd_avulsos': avulsos['qtd'],
    }
