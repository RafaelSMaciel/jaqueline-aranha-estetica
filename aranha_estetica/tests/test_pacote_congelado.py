"""Pacote congelado na venda (docs/specs/padrao-sql.md §4.e, migration 0052).

A venda copia nome/itens/valor por sessao do catalogo (CompraPacote.vender ->
CompraPacoteItem); saldo, debito, finalizacao, comissao, expiracao e DSAR leem os
itens congelados — o catalogo fica livre p/ editar. No SQLite o Python cobre o
trigger (ConsumoSessao._validar, CompraPacoteItem imutavel); o PG fica em test_pg_ddl.
"""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from aranha_estetica.models import (
    Atendimento, CompraPacote, CompraPacoteItem, ConsumoSessao, ItemPacote, MovimentoComissao, RegraComissao,
)
from aranha_estetica.models.pacotes import rateio_ponderado, rateio_por_sessao
from aranha_estetica.services.comissao import ComissaoService

from .factories import (
    ja_comecou,
    criar_atendimento,
    criar_cliente,
    criar_compra_pacote,
    criar_pacote,
    criar_procedimento,
    criar_profissional,
)


def _realizar(atendimento):
    ja_comecou(atendimento).marcar_realizado()
    return atendimento


class VendaCongelaItensTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.drenagem = criar_procedimento('Drenagem', profissional=self.prof)
        self.peeling = criar_procedimento('Peeling', profissional=self.prof)
        self.pacote = criar_pacote('Combo', preco=Decimal('1000.00'), procedimento=self.drenagem, sessoes=4)
        ItemPacote.objects.create(pacote=self.pacote, procedimento=self.peeling, quantidade_sessoes=2)
        self.cli = criar_cliente()

    def test_vender_copia_nome_itens_e_rateio(self):
        compra = CompraPacote.vender(self.pacote, self.cli, Decimal('1000.00'))
        self.assertEqual((compra.pacote_nome, compra.status), ('Combo', 'ATIVO'))
        self.assertIsNotNone(compra.data_expiracao)
        itens = list(compra.itens.order_by('pk'))
        self.assertEqual([(i.procedimento_id, i.procedimento_nome, i.quantidade_sessoes) for i in itens],
                         [(self.drenagem.pk, 'Drenagem', 4), (self.peeling.pk, 'Peeling', 2)])
        # 1000 / 6 sessoes = 166,666... -> 166,67 (meio p/ cima), igual p/ todos os itens
        self.assertEqual({i.valor_unitario for i in itens}, {Decimal('166.67')})
        self.assertEqual(rateio_por_sessao(Decimal('100.00'), 0), Decimal('0.00'))
        self.assertEqual(rateio_por_sessao(Decimal('0.05'), 2), Decimal('0.03'))

    def test_catalogo_editado_depois_nao_muda_a_compra(self):
        compra = criar_compra_pacote(self.cli, self.pacote)
        self.pacote.nome = 'Combo Novo'
        self.pacote.save()
        self.pacote.itens.all().delete()
        ItemPacote.objects.create(pacote=self.pacote, procedimento=self.peeling, quantidade_sessoes=10)
        compra = CompraPacote.objects.get(pk=compra.pk)
        self.assertEqual(compra.pacote_nome, 'Combo')
        self.assertEqual([(linha['procedimento_nome'], linha['total']) for linha in compra.saldo_por_procedimento()],
                         [('Drenagem', 4), ('Peeling', 2)])
        self.assertEqual(compra.sessoes_restantes(), 6)
        self.assertIn('Combo', str(compra))

    def test_item_da_compra_e_imutavel_no_python(self):
        item = criar_compra_pacote(self.cli, self.pacote).itens.first()
        item.quantidade_sessoes = 99
        with self.assertRaises(ValidationError):
            item.save()
        with self.assertRaises(ValidationError):
            CompraPacoteItem.objects.filter(pk=item.pk).update(quantidade_sessoes=99)
        item.refresh_from_db()
        self.assertEqual(item.quantidade_sessoes, 4)

    def test_apagar_a_compra_leva_itens_e_consumos(self):
        compra = criar_compra_pacote(self.cli, self.pacote)
        _realizar(criar_atendimento(self.cli, self.prof, self.drenagem))
        self.assertEqual(ConsumoSessao.objects.count(), 1)
        compra.delete()
        self.assertFalse(CompraPacoteItem.objects.exists())
        self.assertFalse(ConsumoSessao.objects.exists())


class RateioPonderadoTests(TestCase):
    """Rateio do valor pago pelo preco de tabela de cada procedimento no dia da venda."""

    def test_funcao_pura(self):
        # 1200 + 5 x 150 = 1950 de tabela; 1800 pagos -> 1107,69 / 138,46 (Σ 1799,99): o centavo
        # que falta vai p/ o item de maior peso, que tem 1 sessao
        self.assertEqual(rateio_ponderado(Decimal('1800.00'), [(Decimal('1200'), 1), (Decimal('150'), 5)]),
                         [Decimal('1107.70'), Decimal('138.46')])
        # sem preco (None/0) em algum item: uniforme
        self.assertEqual(rateio_ponderado(Decimal('100.00'), [(None, 1), (Decimal('50'), 3)]), [Decimal('25.00')] * 2)
        self.assertEqual(rateio_ponderado(Decimal('100.00'), [(Decimal('0'), 1), (Decimal('50'), 3)]),
                         [Decimal('25.00')] * 2)
        # um procedimento so: identico ao rateio uniforme (residuo < qtd centavos fica)
        self.assertEqual(rateio_ponderado(Decimal('1000.00'), [(Decimal('150'), 3)]),
                         [rateio_por_sessao(Decimal('1000.00'), 3)])
        # residuo divisivel pela qtd do item de maior peso (1o no empate): entra inteiro.
        # 20 / 6 = 3,33 -> Σ 19,98; os 2 centavos viram +0,01 nas 2 sessoes do 1o item
        self.assertEqual(rateio_ponderado(Decimal('20.00'), [(Decimal('1'), 2), (Decimal('1'), 4)]),
                         [Decimal('3.34'), Decimal('3.33')])
        # nao divisivel: sobra menos de qtd centavos (10 / 3 = 3,33 -> Σ 9,99; 1 centavo < 2 sessoes)
        self.assertEqual(rateio_ponderado(Decimal('10.00'), [(Decimal('1'), 2), (Decimal('1'), 1)]),
                         [Decimal('3.33'), Decimal('3.33')])
        self.assertEqual(rateio_ponderado(Decimal('0.00'), [(Decimal('10'), 1)]), [Decimal('0.00')])

    def test_venda_heterogenea_e_comissao_da_drenagem(self):
        prof = criar_profissional()
        toxina = criar_procedimento('Toxina', preco=Decimal('1200.00'), profissional=prof)
        drenagem = criar_procedimento('Drenagem', preco=Decimal('150.00'), profissional=prof)
        pacote = criar_pacote('Harmonia', preco=Decimal('1800.00'), procedimento=toxina, sessoes=1)
        ItemPacote.objects.create(pacote=pacote, procedimento=drenagem, quantidade_sessoes=5)
        cli = criar_cliente()
        compra = CompraPacote.vender(pacote, cli, Decimal('1800.00'))
        unitarios = {i.procedimento_id: i.valor_unitario for i in compra.itens.all()}
        self.assertEqual(unitarios, {toxina.pk: Decimal('1107.70'), drenagem.pk: Decimal('138.46')})
        self.assertLessEqual(abs(sum(i.valor_unitario * i.quantidade_sessoes for i in compra.itens.all())
                                 - compra.valor_pago), Decimal('0.05'))

        RegraComissao.objects.create(percentual=Decimal('10'), ativo=True)
        at = _realizar(criar_atendimento(cli, prof, drenagem))
        self.assertEqual(ComissaoService.base_calculo(at), Decimal('138.46'))
        self.assertEqual(MovimentoComissao.objects.get(atendimento=at, tipo='CREDITO').valor, Decimal('13.85'))

    def test_procedimento_sem_preco_cai_no_uniforme(self):
        com_preco = criar_procedimento('Peeling', preco=Decimal('400.00'))
        sem_preco = criar_procedimento('Avaliacao', preco=None)
        pacote = criar_pacote('Misto', preco=Decimal('600.00'), procedimento=com_preco, sessoes=2)
        ItemPacote.objects.create(pacote=pacote, procedimento=sem_preco, quantidade_sessoes=1)
        compra = CompraPacote.vender(pacote, criar_cliente(), Decimal('600.00'))
        self.assertEqual({i.valor_unitario for i in compra.itens.all()}, {Decimal('200.00')})

    def test_preco_mudado_depois_nao_altera_a_compra(self):
        from aranha_estetica.models import Preco

        toxina = criar_procedimento('Toxina', preco=Decimal('1000.00'))
        drenagem = criar_procedimento('Drenagem', preco=Decimal('100.00'))
        pacote = criar_pacote('Duo', preco=Decimal('1100.00'), procedimento=toxina, sessoes=1)
        ItemPacote.objects.create(pacote=pacote, procedimento=drenagem, quantidade_sessoes=1)
        compra = CompraPacote.vender(pacote, criar_cliente(), Decimal('1100.00'))
        Preco.objects.filter(procedimento=drenagem).update(valor=Decimal('900.00'))
        self.assertEqual(sorted(i.valor_unitario for i in compra.itens.all()),
                         [Decimal('100.00'), Decimal('1000.00')])


class ConsumoPeloItemCongeladoTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento('Drenagem', profissional=self.prof)
        self.outro = criar_procedimento('Peeling', profissional=self.prof)
        self.cli = criar_cliente()
        self.pacote = criar_pacote('Drenagem 2x', preco=Decimal('300.00'), procedimento=self.proc, sessoes=2)
        self.compra = criar_compra_pacote(self.cli, self.pacote)
        self.item = self.compra.itens.get()

    def test_debito_usa_o_item_congelado_mesmo_com_catalogo_mudado(self):
        # catalogo passa a ter 10 sessoes; a compra continua com 2
        ItemPacote.objects.filter(pacote=self.pacote).delete()
        ItemPacote.objects.create(pacote=self.pacote, procedimento=self.proc, quantidade_sessoes=10)
        a1 = _realizar(criar_atendimento(self.cli, self.prof, self.proc))
        a2 = _realizar(criar_atendimento(self.cli, self.prof, self.proc))
        a3 = _realizar(criar_atendimento(self.cli, self.prof, self.proc))
        consumos = ConsumoSessao.objects.filter(compra_pacote=self.compra)
        self.assertEqual(set(consumos.values_list('atendimento_id', flat=True)), {a1.pk, a2.pk})
        self.assertEqual(set(consumos.values_list('compra_pacote_item_id', flat=True)), {self.item.pk})
        self.assertFalse(ConsumoSessao.objects.filter(atendimento=a3).exists())  # sem saldo: avulso
        self.compra.refresh_from_db()
        self.assertEqual(self.compra.status, 'FINALIZADO')
        self.assertEqual(self.compra.saldo_por_procedimento()[0]['restantes'], 0)

    def test_procedimento_fora_da_compra_nao_debita_mesmo_se_o_catalogo_ganhou_o_item(self):
        ItemPacote.objects.create(pacote=self.pacote, procedimento=self.outro, quantidade_sessoes=5)
        at = _realizar(criar_atendimento(self.cli, self.prof, self.outro))
        self.assertFalse(ConsumoSessao.objects.filter(atendimento=at).exists())

    def test_validacao_python_espelha_o_trigger(self):
        at = criar_atendimento(self.cli, self.prof, self.proc)
        outra = criar_compra_pacote(self.cli, criar_pacote('Outro', procedimento=self.proc, sessoes=1))
        with self.assertRaisesMessage(ValidationError, 'não pertence'):
            ConsumoSessao.objects.create(compra_pacote=outra, compra_pacote_item=self.item, atendimento=at)
        de_outro_proc = criar_atendimento(self.cli, self.prof, self.outro)
        with self.assertRaisesMessage(ValidationError, 'procedimento'):
            ConsumoSessao.objects.create(compra_pacote=self.compra, compra_pacote_item=self.item,
                                         atendimento=de_outro_proc)
        with self.assertRaisesMessage(ValidationError, 'não tem item'):
            ConsumoSessao.objects.create(compra_pacote=self.compra, atendimento=de_outro_proc)
        retorno = criar_atendimento(self.cli, self.prof, self.proc)
        Atendimento.objects.filter(pk=retorno.pk).update(eh_retorno=True, valor_cobrado=0, atendimento_origem=at)
        with self.assertRaisesMessage(ValidationError, 'Retorno'):
            ConsumoSessao.objects.create(compra_pacote=self.compra, atendimento=retorno)
        # sem item explicito: resolve pelo procedimento; o 3o estoura o saldo (2)
        for _ in range(2):
            ConsumoSessao.objects.create(compra_pacote=self.compra,
                                         atendimento=criar_atendimento(self.cli, self.prof, self.proc))
        with self.assertRaisesMessage(ValidationError, 'não tem mais sessões'):
            ConsumoSessao.objects.create(compra_pacote=self.compra, atendimento=at)

    def test_saldo_com_prefetch_nao_faz_query_por_compra(self):
        _realizar(criar_atendimento(self.cli, self.prof, self.proc))
        compras = list(CompraPacote.objects.filter(cliente=self.cli)
                       .prefetch_related('itens__procedimento', 'sessoes_realizadas'))
        with self.assertNumQueries(0):
            saldo = compras[0].saldo_por_procedimento()
        self.assertEqual((saldo[0]['usadas'], saldo[0]['restantes'], saldo[0]['procedimento']), (1, 1, self.proc))

    def test_comissao_da_sessao_usa_o_valor_unitario_congelado(self):
        RegraComissao.objects.create(percentual=Decimal('10'), ativo=True)
        self.pacote.preco_total = Decimal('9999.00')  # catalogo mudou depois da venda
        self.pacote.save()
        at = criar_atendimento(self.cli, self.prof, self.proc)
        Atendimento.objects.filter(pk=at.pk).update(valor_cobrado=Decimal('180.00'))
        at.refresh_from_db()
        _realizar(at)   # a FSM lanca a comissao pela base congelada
        self.assertEqual(ComissaoService.base_calculo(at), Decimal('150.00'))  # 300 / 2
        self.assertEqual(MovimentoComissao.objects.get(atendimento=at, tipo='CREDITO').valor, Decimal('15.00'))


class ExpiracaoEDsarComItensCongeladosTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento('Drenagem', profissional=self.prof)
        self.cli = criar_cliente(email='paula@example.com')
        self.pacote = criar_pacote('Drenagem 3x', preco=Decimal('300.00'), procedimento=self.proc, sessoes=3)
        self.compra = criar_compra_pacote(self.cli, self.pacote)
        _realizar(criar_atendimento(self.cli, self.prof, self.proc))
        # catalogo renomeado/esvaziado depois da venda
        self.pacote.nome = 'Outro nome'
        self.pacote.save()
        self.pacote.itens.all().delete()

    def test_email_de_expiracao_usa_nome_e_saldo_congelados(self):
        from aranha_estetica.jobs import job_pacote_expirando
        from aranha_estetica.utils.datas import hoje

        CompraPacote.objects.filter(pk=self.compra.pk).update(data_expiracao=hoje() + timedelta(days=7))
        with mock.patch('aranha_estetica.utils.email.enviar_pacote_expirando_email', return_value=True) as envio:
            job_pacote_expirando()
        dados = envio.call_args.args[1]
        self.assertEqual((dados['pacote'], dados['sessoes_restantes']), ('Drenagem 3x', 2))

    def test_dsar_exporta_o_que_foi_vendido(self):
        from aranha_estetica.services.lgpd import LgpdService

        pacote = LgpdService.exportar_dados_cliente(self.cli)['pacotes'][0]
        self.assertEqual(pacote['pacote'], 'Drenagem 3x')
        self.assertEqual(pacote['sessoes_realizadas'], 1)
        self.assertEqual(pacote['itens'], [{
            'procedimento': 'Drenagem', 'sessoes': 3, 'sessoes_usadas': 1, 'sessoes_restantes': 2,
            'valor_por_sessao': '100.00',
        }])

    def test_cancelamento_audita_saldo_congelado(self):
        from django.urls import reverse

        from aranha_estetica.models import LogAuditoria, Usuario

        admin = Usuario.objects.create_user(email='adm@pac.com', password='x', papel=Usuario.PAPEL_ADMIN)
        self.client.force_login(admin)
        self.client.post(reverse('aranha:admin_cancelar_compra_pacote', args=[self.compra.pk]),
                         {'observacao': 'Desistiu', 'valor_reembolsado': '0'})
        log = LogAuditoria.objects.get(tabela='compra_pacote', acao__startswith='Cancelou pacote')
        self.assertEqual(log.acao, 'Cancelou pacote "Drenagem 3x"')
        self.assertEqual(log.detalhes['sessoes_restantes'], 2)

    def test_expirado_ainda_debita_sessao_feita_na_validade(self):
        ontem = timezone.localdate() - timedelta(days=1)
        CompraPacote.objects.filter(pk=self.compra.pk).update(status='EXPIRADO', data_expiracao=ontem)
        quando = timezone.now() - timedelta(days=2)
        at = _realizar(criar_atendimento(self.cli, self.prof, self.proc, data_hora=quando, status='AGENDADO'))
        self.assertTrue(ConsumoSessao.objects.filter(atendimento=at, compra_pacote=self.compra).exists())


class PacoteSoComItensEAdminSoLeituraTests(TestCase):
    """Pacote sem procedimento nao e vendido; compra e consumo nao mudam pelo Django admin."""

    def test_pacote_sem_itens_nao_e_vendido(self):
        from aranha_estetica.excecoes import RegraDeNegocio
        pacote = criar_pacote(procedimento=None)
        with self.assertRaises(RegraDeNegocio):
            CompraPacote.vender(pacote, criar_cliente(), Decimal('100.00'))

    def test_django_admin_de_compra_consumo_e_profissional(self):
        from django.contrib import admin
        from django.test import RequestFactory

        from aranha_estetica.models import ConsumoSessao, Profissional, Usuario
        request = RequestFactory().get('/')
        request.user = Usuario.objects.create_superuser(email='adm@pacote.test', password='x')
        for model in (CompraPacote, ConsumoSessao):
            modeladmin = admin.site._registry[model]
            with self.subTest(model=model.__name__):
                self.assertFalse(modeladmin.has_change_permission(request))
                self.assertFalse(modeladmin.has_delete_permission(request))
                self.assertFalse(modeladmin.has_add_permission(request))
        form = admin.site._registry[Profissional].get_form(request)
        self.assertNotIn('gcal_refresh_token', form.base_fields)
        self.assertNotIn('gcal_sync_token', form.base_fields)
