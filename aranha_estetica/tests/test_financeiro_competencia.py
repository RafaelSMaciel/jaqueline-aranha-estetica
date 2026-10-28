"""Financeiro por competencia mensal (services/financeiro): periodo unico p/ visao geral, financeiro
e Excel; venda de pacote no mes da venda e reembolso no mes do cancelamento (mes fechado nao muda).
A paridade com vw_faturamento_mensal fica em test_pg_paridade (so Postgres)."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import Atendimento, CompraPacote, Usuario
from aranha_estetica.services import financeiro

from .factories import criar_atendimento, criar_cliente, criar_compra_pacote, criar_pacote, criar_procedimento, \
    criar_profissional


def _local(dia, hora=10, minuto=0):
    return timezone.make_aware(datetime.combine(dia, time(hora, minuto)))


class PeriodoTests(TestCase):
    def test_competencia_e_mes_civil_local(self):
        set_ = financeiro.competencia(date(2026, 9, 17))
        self.assertEqual((set_.inicio, set_.fim, set_.ultimo_dia), (date(2026, 9, 1), date(2026, 10, 1),
                                                                     date(2026, 9, 30)))
        self.assertEqual((set_.param, set_.rotulo), ('2026-09', 'setembro de 2026'))
        self.assertEqual(financeiro.competencia(date(2026, 12, 31)).fim, date(2027, 1, 1))
        self.assertEqual(set_.anterior().param, '2026-08')
        self.assertEqual(financeiro.competencia(date(2026, 1, 5)).anterior().param, '2025-12')
        self.assertEqual(set_.seguinte().param, '2026-10')
        # 23:30 local do dia 30 (UTC ja em outubro) e setembro; 00:00 local do dia 1 e outubro
        self.assertEqual(set_.filtro('x'), {'x__gte': _local(date(2026, 9, 1), 0), 'x__lt': _local(date(2026, 10, 1), 0)})

    def test_parametro_invalido_cai_no_mes_corrente(self):
        atual = financeiro.competencia()
        for valor in ('', None, '2026-13', 'abc', '9999-12', '0001-01', '2026-09-01'):
            with self.subTest(valor=valor):
                self.assertEqual(financeiro.competencia_do_param(valor), atual)
        self.assertEqual(financeiro.competencia_do_param('2026-09').param, '2026-09')


class ReceitaPorCompetenciaTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()
        self.jan, self.mar = financeiro.competencia(date(2026, 1, 10)), financeiro.competencia(date(2026, 3, 10))
        self.pacote = criar_pacote('Glow', preco=Decimal('1000.00'), procedimento=self.proc)

    def _venda(self, quando, valor='1000.00'):
        compra = criar_compra_pacote(self.cli, self.pacote, valor_pago=Decimal(valor))
        CompraPacote.objects.filter(pk=compra.pk).update(criado_em=quando)
        return compra

    def _cancelar(self, compra, reembolso, quando):
        CompraPacote.objects.filter(pk=compra.pk).update(
            status='CANCELADO', valor_reembolsado=Decimal(reembolso), cancelado_em=quando)

    def test_reembolso_de_marco_nao_altera_janeiro_e_marco_pode_ficar_negativo(self):
        compra = self._venda(_local(date(2026, 1, 15)))
        self.assertEqual(financeiro.resumo_pacotes(self.jan)['total'], Decimal('1000.00'))
        self._cancelar(compra, '600.00', _local(date(2026, 3, 2)))
        # janeiro (fechado) nao muda; marco so tem o reembolso: negativo
        self.assertEqual(financeiro.resumo_pacotes(self.jan), {
            'vendas': Decimal('1000.00'), 'qtd_vendas': 1, 'reembolsos': Decimal('0.00'), 'qtd_reembolsos': 0,
            'total': Decimal('1000.00')})
        self.assertEqual(financeiro.resumo_pacotes(self.mar), {
            'vendas': Decimal('0.00'), 'qtd_vendas': 0, 'reembolsos': Decimal('600.00'), 'qtd_reembolsos': 1,
            'total': Decimal('-600.00')})
        self.assertEqual(financeiro.resumo_receita(self.mar)['total'], Decimal('-600.00'))
        # a ficha da cliente (historico inteiro) segue liquida
        self.assertEqual(financeiro.valor_gasto_cliente(self.cli)['pacotes'], Decimal('400.00'))

    def test_fronteira_do_mes_no_fuso_local(self):
        # 31/01 23:30 local = 01/02 em UTC: e janeiro
        self._venda(_local(date(2026, 1, 31), 23, 30), valor='300.00')
        self._venda(_local(date(2026, 2, 1), 0, 0), valor='200.00')
        self.assertEqual(financeiro.resumo_pacotes(self.jan)['vendas'], Decimal('300.00'))
        self.assertEqual(financeiro.resumo_pacotes(financeiro.competencia(date(2026, 2, 1)))['vendas'],
                         Decimal('200.00'))

    def test_venda_de_valor_zero_nao_conta_como_venda(self):
        self._venda(_local(date(2026, 1, 5)), valor='0.00')
        self.assertEqual(financeiro.resumo_pacotes(self.jan)['qtd_vendas'], 0)

    def test_ultimos_12_meses_bate_com_resumo_receita(self):
        atual = financeiro.competencia()
        anterior = atual.anterior()
        compra = criar_compra_pacote(self.cli, self.pacote)          # vendido agora (mes atual)
        CompraPacote.objects.filter(pk=compra.pk).update(criado_em=anterior.inicio_dt + timedelta(hours=12))
        self._cancelar(compra, '250.00', timezone.now())
        at = criar_atendimento(self.cli, self.prof, self.proc, data_hora=anterior.inicio_dt + timedelta(days=1, hours=9))
        Atendimento.objects.filter(pk=at.pk).update(status='REALIZADO', valor_cobrado=Decimal('180.00'))
        tabela = financeiro.ultimos_12_meses()
        self.assertEqual(len(tabela), 12)
        self.assertEqual([linha['periodo'] for linha in tabela[:2]], [atual, anterior])
        indice = atual.inicio.year * 12 + atual.inicio.month - 1 - 11      # 11 meses antes do atual
        self.assertEqual(tabela[-1]['periodo'].inicio, date(indice // 12, indice % 12 + 1, 1))
        for linha in tabela:
            receita = financeiro.resumo_receita(linha['periodo'])
            self.assertEqual((linha['total'], linha['avulsos'], linha['pacotes'], linha['ticket_medio']),
                             (receita['total'], receita['avulsos']['total'], receita['pacotes']['total'],
                              receita['ticket_medio']), linha['periodo'].param)
        self.assertEqual((tabela[1]['avulsos'], tabela[1]['pacotes'], tabela[0]['pacotes']),
                         (Decimal('180.00'), Decimal('1000.00'), Decimal('-250.00')))

    def test_credito_de_indicacao_abatido_no_mes(self):
        from aranha_estetica.models import Carteira
        from aranha_estetica.services.fidelidade import FidelidadeService

        Carteira.objects.create(cliente=self.cli, saldo=Decimal('50.00'))
        at = criar_atendimento(self.cli, self.prof, self.proc)
        Atendimento.objects.filter(pk=at.pk).update(valor_cobrado=Decimal('200.00'))
        at.refresh_from_db()
        FidelidadeService.usar_credito(at, Decimal('30.00'))
        atual = financeiro.competencia()
        self.assertEqual(financeiro.creditos_indicacao_usados(atual), Decimal('30.00'))
        self.assertEqual(financeiro.creditos_indicacao_usados(atual.anterior()), Decimal('0.00'))

    def test_no_show_do_mes(self):
        for dia, status in ((5, 'REALIZADO'), (6, 'REALIZADO'), (7, 'REALIZADO'), (8, 'FALTOU')):
            criar_atendimento(self.cli, self.prof, self.proc, data_hora=_local(date(2026, 1, dia)), status=status)
        criar_atendimento(self.cli, self.prof, self.proc, data_hora=_local(date(2026, 2, 1)), status='FALTOU')
        self.assertEqual(financeiro.no_show(self.jan), {'faltas': 1, 'finalizados': 4, 'percentual': 25.0})


class TelasPorCompetenciaTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = Usuario.objects.create_user(email='adm@competencia.test', password='x',
                                                 papel=Usuario.PAPEL_ADMIN)
        self.client.force_login(self.admin)
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()

    def _realizado(self, quando, valor):
        at = criar_atendimento(self.cli, self.prof, self.proc, data_hora=quando)
        Atendimento.objects.filter(pk=at.pk).update(status='REALIZADO', valor_cobrado=Decimal(valor))

    def test_mes_escolhido_mostra_aquele_mes(self):
        self._realizado(_local(date(2026, 9, 10)), '300.00')
        self._realizado(_local(date(2026, 10, 10)), '50.00')
        resp = self.client.get(reverse('aranha:dashboard_financeiro'), {'mes': '2026-09'})
        self.assertEqual(resp.context['periodo'].param, '2026-09')
        self.assertEqual(resp.context['receita']['total'], Decimal('300.00'))
        self.assertContains(resp, 'setembro de 2026')
        self.assertContains(resp, 'Últimos 12 meses')
        # cache por mes: outubro nao recebe o cache de setembro
        resp = self.client.get(reverse('aranha:dashboard_financeiro'), {'mes': '2026-10'})
        self.assertEqual(resp.context['receita']['total'], Decimal('50.00'))

    def test_receita_da_visao_geral_igual_a_do_financeiro(self):
        atual = financeiro.competencia()
        self._realizado(atual.inicio_dt + timedelta(hours=9), '210.00')
        criar_compra_pacote(self.cli, criar_pacote('Glow', preco=Decimal('500.00'), procedimento=self.proc))
        overview = self.client.get(reverse('aranha:painel_overview'))
        fin = self.client.get(reverse('aranha:dashboard_financeiro')).context
        self.assertEqual(overview.context['receita_mes'], fin['receita']['total'])
        self.assertEqual(overview.context['receita_mes'], Decimal('710.00'))
        self.assertContains(overview, f'Receita de {atual.rotulo}')

    def test_aviso_sem_regra_de_comissao(self):
        resp = self.client.get(reverse('aranha:dashboard_financeiro'))
        self.assertContains(resp, 'Nenhuma regra de comissão ativa')
        from aranha_estetica.models import RegraComissao
        RegraComissao.objects.create(percentual=Decimal('30'), ativo=True)
        cache.clear()
        self.assertNotContains(self.client.get(reverse('aranha:dashboard_financeiro')),
                               'Nenhuma regra de comissão ativa')
