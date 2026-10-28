"""Relatorio gerencial em Excel por COMPETENCIA MENSAL — os mesmos numeros do financeiro (D20).

A soma de cada aba fecha com o card do financeiro do mesmo mes (services/financeiro): a aba
Atendimentos diz, linha a linha, a origem (avulso, sessao de pacote, retorno), se fatura e quanto,
e a comissao; a aba Pacotes traz as vendas do mes (+) e os reembolsos do mes (-), pela data de cada
um; a aba Comissoes resume o mes por profissional para o repasse. Dinheiro em Decimal (formato
#,##0.00), nunca float.
"""
from __future__ import annotations

from decimal import Decimal

from ..models import Atendimento, CompraPacote, MovimentoComissao
from ..utils.datas import fmt_local
from . import financeiro
from .comissao import ComissaoService

FORMATO_REAL = '#,##0.00'
COLUNAS_ATENDIMENTOS = ['ID', 'Data', 'Hora', 'Cliente', 'Profissional', 'Procedimento', 'Status', 'Origem',
                        'Valor cobrado (R$)', 'Fatura?', 'Valor faturado (R$)', 'Comissão (R$)',
                        'Status da comissão']
COLUNAS_PACOTES = ['ID da compra', 'Movimento', 'Data', 'Cliente', 'Pacote', 'Status da compra', 'Valor (R$)']
COLUNAS_COMISSOES = ['Profissional', 'Qtd', 'Gerada no mês (R$)', 'Pendente (R$)', 'Paga (R$)',
                     'Saldo a pagar hoje (R$)']


def _texto_seguro(ws) -> None:
    """Impede formula injection na ultima linha: o openpyxl grava como formula toda str iniciada por
    '=' e o nome da cliente vem do agendamento publico (texto livre)."""
    for cell in ws[ws.max_row]:
        if isinstance(cell.value, str) and cell.value.startswith(('=', '+', '-', '@')):
            cell.data_type = 's'


def _formatar_reais(ws, colunas) -> None:
    for coluna in colunas:
        for (cell,) in ws.iter_rows(min_row=2, min_col=coluna, max_col=coluna):
            if isinstance(cell.value, Decimal):
                cell.number_format = FORMATO_REAL


def _origem(at) -> str:
    if at.eh_retorno:
        return 'Retorno'
    if getattr(at, 'sessao_pacote_vinculada', None) is not None:
        return 'Sessão de pacote'
    return 'Avulso'


def _comissao_por_atendimento(atendimento_ids) -> dict:
    """{atendimento_id: (valor, status)} do credito vigente (o nao estornado; senao o ultimo)."""
    mapa: dict[int, tuple[Decimal, str]] = {}
    creditos = (MovimentoComissao.objects.creditos_com_status().filter(atendimento_id__in=atendimento_ids)
                .order_by('atendimento_id', 'pk'))
    for credito in creditos:
        anterior = mapa.get(credito.atendimento_id)
        if anterior is None or anterior[1] == MovimentoComissao.STATUS_ESTORNADA:
            mapa[credito.atendimento_id] = (credito.valor, credito.status)
    return mapa


def planilha_gerencial(periodo: financeiro.Periodo):
    """Workbook (openpyxl) com Resumo, Atendimentos, Pacotes e Comissoes do mes."""
    import openpyxl
    from openpyxl.styles import Font

    receita = financeiro.resumo_receita(periodo)
    atendimentos = list(
        Atendimento.objects.filter(**periodo.filtro('data_hora_inicio'))
        .select_related('cliente', 'profissional', 'procedimento', 'sessao_pacote_vinculada')
        .order_by('data_hora_inicio')
    )
    ids = [at.pk for at in atendimentos]
    faturados = set(financeiro.atendimentos_faturados(Atendimento.objects.filter(pk__in=ids))
                    .values_list('pk', flat=True))
    comissoes = _comissao_por_atendimento(ids)
    rotulos_comissao = dict(MovimentoComissao.STATUS_CHOICES)
    vendas = list(CompraPacote.objects.filter(valor_pago__gt=0, **periodo.filtro('criado_em'))
                  .select_related('cliente').order_by('criado_em'))
    reembolsos = list(CompraPacote.objects.filter(valor_reembolsado__gt=0, **periodo.filtro('cancelado_em'))
                      .select_related('cliente').order_by('cancelado_em'))
    creditos_mes = MovimentoComissao.objects.creditos_com_status().filter(
        **periodo.filtro('atendimento__data_hora_inicio'))
    por_profissional = [linha for linha in ComissaoService.resumo_por_profissional(creditos_mes) if linha['qtd']]
    totais_comissao = ComissaoService.totais_por_status(creditos_mes)

    wb = openpyxl.Workbook()
    resumo = wb.active
    resumo.title = 'Resumo'
    avulsos, pacotes = receita['avulsos'], receita['pacotes']
    linhas = [
        ('Competência', f'{periodo.rotulo} ({periodo.inicio:%d/%m/%Y} a {periodo.ultimo_dia:%d/%m/%Y})'),
        ('Atendimentos avulsos faturados', avulsos['qtd']),
        ('Receita de atendimentos avulsos (R$)', avulsos['total']),
        ('Ticket médio dos avulsos (R$)', avulsos['ticket_medio']),
        ('Pacotes vendidos no mês', pacotes['qtd_vendas']),
        ('Vendas de pacotes (R$)', pacotes['vendas']),
        ('Reembolsos de pacotes no mês', pacotes['qtd_reembolsos']),
        ('Reembolsos de pacotes (R$)', -pacotes['reembolsos']),
        ('Receita de pacotes no mês (R$)', pacotes['total']),
        ('Faturamento do mês (R$)', receita['total']),
        ('Comissões do mês, geradas (R$)', totais_comissao[MovimentoComissao.STATUS_PENDENTE]['total']
         + totais_comissao[MovimentoComissao.STATUS_PAGA]['total']),
        ('Comissões do mês ainda pendentes (R$)', totais_comissao[MovimentoComissao.STATUS_PENDENTE]['total']),
    ]
    for rotulo, valor in linhas:
        resumo.append([rotulo, valor])
    resumo.append([])
    resumo.append(['Faturamento = atendimentos avulsos realizados no mês (sem retorno, sem sessão de pacote, '
                   'valor > 0) + pacotes vendidos no mês − reembolsos de pacote feitos no mês: a mesma regra do '
                   'financeiro. Comissões pela data do atendimento.'])
    _formatar_reais(resumo, [2])
    resumo.column_dimensions['A'].width = 48
    resumo.column_dimensions['B'].width = 30

    ws = wb.create_sheet('Atendimentos')
    ws.append(COLUNAS_ATENDIMENTOS)
    total_faturado = Decimal('0.00')
    for at in atendimentos:
        conta = at.pk in faturados
        valor_faturado = (at.valor_cobrado or Decimal('0.00')) if conta else Decimal('0.00')
        total_faturado += valor_faturado
        comissao = comissoes.get(at.pk)
        ws.append([
            at.pk,
            fmt_local(at.data_hora_inicio, '%d/%m/%Y'),
            fmt_local(at.data_hora_inicio, '%H:%M'),
            at.cliente.nome,
            at.profissional.nome,
            at.procedimento.nome,
            at.get_status_display(),
            _origem(at),
            at.valor_cobrado if at.valor_cobrado is not None else Decimal('0.00'),
            'Sim' if conta else 'Não',
            valor_faturado,
            comissao[0] if comissao else None,
            str(rotulos_comissao.get(comissao[1], comissao[1])) if comissao else '',
        ])
        _texto_seguro(ws)
    ws.append(['Total faturado', *[''] * 9, total_faturado])
    _formatar_reais(ws, [9, 11, 12])

    wp = wb.create_sheet('Pacotes')
    wp.append(COLUNAS_PACOTES)
    for compra in vendas:
        wp.append([compra.pk, 'Venda', fmt_local(compra.criado_em, '%d/%m/%Y'), compra.cliente.nome,
                   compra.pacote_nome, compra.get_status_display(), compra.valor_pago])
        _texto_seguro(wp)
    for compra in reembolsos:
        wp.append([compra.pk, 'Reembolso (cancelamento)', fmt_local(compra.cancelado_em, '%d/%m/%Y'),
                   compra.cliente.nome, compra.pacote_nome, compra.get_status_display(), -compra.valor_reembolsado])
        _texto_seguro(wp)
    wp.append(['Receita de pacotes no mês', '', '', '', '', '', pacotes['total']])
    _formatar_reais(wp, [7])

    wc = wb.create_sheet('Comissões')
    wc.append(COLUNAS_COMISSOES)
    for linha in por_profissional:
        wc.append([linha['profissional__nome'], linha['qtd'], linha['gerada'], linha['pendente'], linha['paga'],
                   linha['saldo']])
        _texto_seguro(wc)
    _formatar_reais(wc, [3, 4, 5, 6])

    for planilha in (resumo, ws, wp, wc):
        for cell in planilha[1]:
            cell.font = Font(bold=True)
    return wb, {'atendimentos': len(atendimentos), 'pacotes': len(vendas) + len(reembolsos),
                'profissionais': len(por_profissional)}
