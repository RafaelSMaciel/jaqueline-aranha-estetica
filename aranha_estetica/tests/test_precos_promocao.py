"""utils/precos: promocao vigente na data do atendimento (contrato 2)."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from aranha_estetica.models import Preco, Promocao
from aranha_estetica.utils.precos import (
    aplicar_promocao, preco_com_promocao, preco_para, promocao_vigente,
)

from .factories import criar_procedimento, criar_profissional


class PrecoComPromocaoTests(TestCase):
    def setUp(self):
        self.hoje = timezone.localdate()
        self.proc = criar_procedimento(nome='Limpeza de Pele', preco=Decimal('150.00'))

    def _promo(self, procedimento=None, inicio=0, fim=10, **kwargs):
        return Promocao.objects.create(
            nome=kwargs.pop('nome', 'Setembro Glow'), procedimento=procedimento,
            data_inicio=self.hoje + timedelta(days=inicio),
            data_fim=self.hoje + timedelta(days=fim), **kwargs,
        )

    def test_percentual_do_procedimento(self):
        promo = self._promo(self.proc, desconto_percentual=Decimal('20'))
        self.assertEqual(preco_com_promocao(self.proc), (Decimal('120.00'), promo, Decimal('150.00')))

    def test_preco_fixo_do_procedimento(self):
        promo = self._promo(self.proc, preco_promocional=Decimal('99.00'))
        final, vigente, cheio = preco_com_promocao(self.proc)
        self.assertEqual((final, vigente, cheio), (Decimal('99.00'), promo, Decimal('150.00')))

    def test_promo_expirada_ou_inativa_nao_vale(self):
        self._promo(self.proc, inicio=-10, fim=-1, desconto_percentual=Decimal('20'))
        self._promo(self.proc, ativa=False, desconto_percentual=Decimal('30'))
        self.assertEqual(preco_com_promocao(self.proc), (Decimal('150.00'), None, Decimal('150.00')))

    def test_promo_geral_de_preco_fixo_nao_vira_teto_do_catalogo(self):
        promo = self._promo(None, preco_promocional=Decimal('10.00'))
        self.assertIsNone(promocao_vigente(self.proc))
        self.assertEqual(aplicar_promocao(Decimal('150.00'), promo), Decimal('150.00'))
        self.assertEqual(preco_com_promocao(self.proc)[0], Decimal('150.00'))

    def test_promo_geral_percentual_vale(self):
        promo = self._promo(None, desconto_percentual=Decimal('10'))
        self.assertEqual(preco_com_promocao(self.proc), (Decimal('135.00'), promo, Decimal('150.00')))

    def test_promo_que_nao_reduz_o_valor_nao_e_exibida(self):
        self._promo(self.proc, preco_promocional=Decimal('200.00'))
        self.assertEqual(preco_com_promocao(self.proc), (Decimal('150.00'), None, Decimal('150.00')))

    def test_vale_a_promocao_da_data_do_atendimento(self):
        promo = self._promo(self.proc, inicio=5, fim=8, desconto_percentual=Decimal('20'))
        self.assertIsNone(preco_com_promocao(self.proc)[1])
        quando = timezone.now() + timedelta(days=6)
        self.assertEqual(preco_com_promocao(self.proc, data=quando)[1], promo)
        self.assertEqual(preco_com_promocao(self.proc, data=quando.date())[0], Decimal('120.00'))

    def test_preco_vigente_na_data_do_atendimento(self):
        Preco.objects.create(
            procedimento=self.proc, valor=Decimal('180.00'),
            vigente_desde=self.hoje + timedelta(days=30),
        )
        self.assertEqual(preco_para(self.proc).valor, Decimal('150.00'))
        self.assertEqual(preco_para(self.proc, data=self.hoje + timedelta(days=31)).valor, Decimal('180.00'))

    def test_preco_do_profissional_com_promocao(self):
        prof = criar_profissional()
        Preco.objects.create(procedimento=self.proc, profissional=prof, valor=Decimal('200.00'))
        self._promo(self.proc, desconto_percentual=Decimal('10'))
        final, _promo, cheio = preco_com_promocao(self.proc, prof)
        self.assertEqual((final, cheio), (Decimal('180.00'), Decimal('200.00')))


class VigenciaPrecoProfissionalTests(TestCase):
    """rev_booking-06: tabela futura do profissional nao vale antes da vigencia."""

    def setUp(self):
        self.hoje = timezone.localdate()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(nome='Peeling', preco=None)
        Preco.objects.create(
            procedimento=self.proc, valor=Decimal('150.00'),
            vigente_desde=self.hoje - timedelta(days=30),
        )
        Preco.objects.create(
            procedimento=self.proc, profissional=self.prof, valor=Decimal('400.00'),
            vigente_desde=self.hoje + timedelta(days=30),
        )

    def test_base_vigente_ate_a_tabela_do_profissional_valer(self):
        self.assertEqual(preco_com_promocao(self.proc, self.prof, self.hoje + timedelta(days=2))[0],
                         Decimal('150.00'))
        self.assertEqual(preco_com_promocao(self.proc, self.prof, self.hoje + timedelta(days=31))[0],
                         Decimal('400.00'))

    def test_sem_preco_base_usa_a_vigencia_futura_do_profissional(self):
        Preco.objects.filter(procedimento=self.proc, profissional__isnull=True).delete()
        self.assertEqual(preco_para(self.proc, self.prof).valor, Decimal('400.00'))

    def test_a_partir_de_prefere_profissional_ja_vigente(self):
        from aranha_estetica.utils.precos import preco_base_map
        Preco.objects.filter(procedimento=self.proc, profissional__isnull=True).delete()
        outra = criar_profissional(nome='Dra. Vigente')
        Preco.objects.create(procedimento=self.proc, profissional=outra, valor=Decimal('500.00'),
                             vigente_desde=self.hoje - timedelta(days=1))
        # 400 so vale daqui a 30 dias: hoje o menor preco que o booking cobra e 500
        self.assertEqual(preco_base_map([self.proc])[self.proc.pk], Decimal('500.00'))


class PromocaoSobrePrecoDoProfissionalTests(TestCase):
    """followups-promocoes-vitrine-diverge-agendamento: % sobre o preco cobrado."""

    def setUp(self):
        self.hoje = timezone.localdate()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(nome='Botox', preco=None)
        Preco.objects.create(procedimento=self.proc, profissional=self.prof, valor=Decimal('1000.00'))
        self.promo = Promocao.objects.create(
            nome='Setembro', procedimento=self.proc, desconto_percentual=Decimal('20'),
            data_inicio=self.hoje, data_fim=self.hoje + timedelta(days=5),
        )

    def test_percentual_sem_preco_base_vale_sobre_o_preco_do_profissional(self):
        self.assertEqual(preco_com_promocao(self.proc, self.prof),
                         (Decimal('800.00'), self.promo, Decimal('1000.00')))

    def test_valor_base_explicito(self):
        self.assertIsNone(promocao_vigente(self.proc))  # sem base e sem valor: so preco fixo
        self.assertEqual(promocao_vigente(self.proc, self.hoje, valor_base=Decimal('1000.00')), self.promo)
