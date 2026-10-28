"""Contrato 2 (CompraPacote.valor_reembolsado) e promocao geral so percentual (0047)."""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from aranha_estetica.models import CompraPacote, Promocao

from .factories import criar_cliente, criar_compra_pacote, criar_pacote, criar_procedimento


class ValorReembolsadoTests(TestCase):
    def setUp(self):
        self.compra = criar_compra_pacote(criar_cliente(), criar_pacote(), valor_pago=Decimal('600.00'))

    def _gravar(self, valor):
        CompraPacote.objects.filter(pk=self.compra.pk).update(
            valor_reembolsado=valor, status='CANCELADO', cancelado_em=timezone.now())

    def test_default_zero(self):
        self.compra.refresh_from_db()
        self.assertEqual(self.compra.valor_reembolsado, Decimal('0.00'))
        self.assertIsNone(self.compra.cancelado_em)

    def test_reembolso_e_cancelado_em_so_com_compra_cancelada(self):
        """ck_compra_pacote_reembolso_so_cancelado e ck_compra_pacote_cancelado_em_coerente (0065)."""
        casos = (
            {'valor_reembolsado': Decimal('10.00')},                       # reembolso sem cancelar
            {'cancelado_em': timezone.now()},                               # instante sem cancelar
            {'status': 'CANCELADO'},                                        # cancelada sem instante
        )
        for campos in casos:
            with self.subTest(campos=campos), self.assertRaises(IntegrityError), transaction.atomic():
                CompraPacote.objects.filter(pk=self.compra.pk).update(**campos)

    def test_save_da_compra_cancelada_grava_o_instante(self):
        compra = criar_compra_pacote(criar_cliente(), criar_pacote('Outro'), status='CANCELADO')
        self.assertIsNotNone(compra.cancelado_em)

    def test_reembolso_ate_o_valor_pago(self):
        self._gravar(Decimal('600.00'))
        self.compra.refresh_from_db()
        self.assertEqual(self.compra.valor_reembolsado, Decimal('600.00'))

    def test_check_recusa_negativo_e_acima_do_pago(self):
        for valor in (Decimal('-0.01'), Decimal('600.01')):
            with self.subTest(valor=valor), self.assertRaises(IntegrityError), transaction.atomic():
                self._gravar(valor)

    def test_full_clean_recusa_acima_do_pago(self):
        self.compra.valor_reembolsado = Decimal('700.00')
        with self.assertRaises(ValidationError):
            self.compra.full_clean()


class PromocaoGeralSoPercentualTests(TestCase):
    def setUp(self):
        hoje = timezone.localdate()
        self.datas = {'data_inicio': hoje, 'data_fim': hoje + timedelta(days=10)}

    def test_clean_recusa_geral_com_preco_fixo(self):
        promo = Promocao(nome='Tudo por 49', preco_promocional=Decimal('49.00'), **self.datas)
        with self.assertRaises(ValidationError) as ctx:
            promo.full_clean()
        self.assertEqual(list(ctx.exception.message_dict), ['preco_promocional'])
        with self.assertRaises(IntegrityError), transaction.atomic():
            promo.save()

    def test_geral_percentual_e_preco_fixo_do_procedimento_passam(self):
        geral = Promocao(nome='Semana', desconto_percentual=Decimal('10'), **self.datas)
        geral.full_clean()
        geral.save()
        fixa = Promocao(nome='Limpeza por 99', procedimento=criar_procedimento(),
                        preco_promocional=Decimal('99.00'), **self.datas)
        fixa.full_clean()
        fixa.save()
        self.assertEqual(Promocao.objects.count(), 2)

    def test_tirar_o_procedimento_de_promo_de_preco_fixo_e_recusado(self):
        """Painel: editar e esvaziar o procedimento de uma promo de preco fixo."""
        promo = Promocao.objects.create(nome='Fixa', procedimento=criar_procedimento(),
                                        preco_promocional=Decimal('99.00'), **self.datas)
        promo.procedimento = None
        with self.assertRaises(IntegrityError), transaction.atomic():
            promo.save()
