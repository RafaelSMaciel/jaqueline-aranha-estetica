"""Matriz de especificidade do ComissaoService.resolver_regra.

Cobre 4 niveis hierarquicos da resolucao:
  1. (prof, proc) — mais especifica
  2. (prof, qualquer)
  3. (qualquer, proc)
  4. (qualquer, qualquer) — fallback
"""
from decimal import Decimal

from django.test import TestCase

from aranha_estetica.models import RegraComissao
from aranha_estetica.services.comissao_service import ComissaoService

from .factories import criar_procedimento, criar_profissional


class ResolverRegraMatrixTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.outro_prof = criar_profissional(nome='Dra. Bia')
        self.proc = criar_procedimento(profissional=self.prof)
        self.outro_proc = criar_procedimento(nome='Massagem', profissional=self.prof)

    def _criar_regra(self, profissional=None, procedimento=None, percentual='20'):
        return RegraComissao.objects.create(
            profissional=profissional,
            procedimento=procedimento,
            percentual=Decimal(percentual),
            ativo=True,
        )

    def test_nenhuma_regra_retorna_none(self):
        self.assertIsNone(
            ComissaoService.resolver_regra(self.prof.pk, self.proc.pk)
        )

    def test_fallback_global_quando_so_existe_regra_geral(self):
        regra = self._criar_regra(percentual='10')
        result = ComissaoService.resolver_regra(self.prof.pk, self.proc.pk)
        self.assertEqual(result, regra)

    def test_so_procedimento_vence_fallback(self):
        self._criar_regra(percentual='10')
        regra_proc = self._criar_regra(procedimento=self.proc, percentual='15')
        result = ComissaoService.resolver_regra(self.prof.pk, self.proc.pk)
        self.assertEqual(result, regra_proc)

    def test_so_profissional_vence_so_procedimento(self):
        self._criar_regra(percentual='10')
        self._criar_regra(procedimento=self.proc, percentual='15')
        regra_prof = self._criar_regra(profissional=self.prof, percentual='25')
        result = ComissaoService.resolver_regra(self.prof.pk, self.proc.pk)
        self.assertEqual(result, regra_prof)

    def test_prof_e_proc_vence_todas(self):
        self._criar_regra(percentual='10')
        self._criar_regra(procedimento=self.proc, percentual='15')
        self._criar_regra(profissional=self.prof, percentual='25')
        regra_especifica = self._criar_regra(
            profissional=self.prof, procedimento=self.proc, percentual='35',
        )
        result = ComissaoService.resolver_regra(self.prof.pk, self.proc.pk)
        self.assertEqual(result, regra_especifica)

    def test_regra_inativa_ignorada(self):
        regra = self._criar_regra(
            profissional=self.prof, procedimento=self.proc, percentual='35',
        )
        regra.ativo = False
        regra.save()
        fallback = self._criar_regra(percentual='10')
        result = ComissaoService.resolver_regra(self.prof.pk, self.proc.pk)
        self.assertEqual(result, fallback)

    def test_regra_de_outro_profissional_nao_aplica(self):
        self._criar_regra(profissional=self.outro_prof, percentual='50')
        fallback = self._criar_regra(percentual='10')
        result = ComissaoService.resolver_regra(self.prof.pk, self.proc.pk)
        self.assertEqual(result, fallback)

    def test_regra_de_outro_procedimento_nao_aplica(self):
        self._criar_regra(procedimento=self.outro_proc, percentual='50')
        fallback = self._criar_regra(percentual='10')
        result = ComissaoService.resolver_regra(self.prof.pk, self.proc.pk)
        self.assertEqual(result, fallback)


class ResolverRegraDeterminismoTests(TestCase):
    """Duas regras ativas com a mesma especificidade: vence a editada por ultimo."""

    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)

    def test_empate_resolve_pela_mais_recente(self):
        from datetime import timedelta
        from django.utils import timezone

        antiga = RegraComissao.objects.create(
            profissional=self.prof, procedimento=self.proc, percentual=Decimal('10'), ativo=True,
        )
        nova = RegraComissao.objects.create(
            profissional=self.prof, procedimento=self.proc, percentual=Decimal('30'), ativo=True,
        )
        agora = timezone.now()
        RegraComissao.objects.filter(pk=antiga.pk).update(atualizado_em=agora - timedelta(days=1))
        RegraComissao.objects.filter(pk=nova.pk).update(atualizado_em=agora)
        self.assertEqual(ComissaoService.resolver_regra(self.prof.pk, self.proc.pk), nova)

        RegraComissao.objects.filter(pk=antiga.pk).update(atualizado_em=agora + timedelta(days=1))
        self.assertEqual(ComissaoService.resolver_regra(self.prof.pk, self.proc.pk), antiga)


class ComissaoSessaoPacoteTests(TestCase):
    """Sessao de pacote comissiona sobre (valor pago / sessoes), nao o preco cheio."""

    def test_base_e_rateio_do_pacote(self):
        from aranha_estetica.models import Atendimento, ConsumoSessao
        from .factories import criar_atendimento, criar_cliente, criar_compra_pacote, criar_pacote

        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        cli = criar_cliente()
        RegraComissao.objects.create(percentual=Decimal('10'), ativo=True)
        at = criar_atendimento(cli, prof, proc, status='AGENDADO')
        pacote = criar_pacote(preco=Decimal('700.00'), procedimento=proc, sessoes=5)
        compra = criar_compra_pacote(cli, pacote)
        ConsumoSessao.objects.create(compra_pacote=compra, atendimento=at)
        Atendimento.objects.filter(pk=at.pk).update(status='REALIZADO', valor_cobrado=Decimal('180.00'))
        at.refresh_from_db()

        mov = ComissaoService.calcular_comissao(at)
        # 700 / 5 = 140 -> 10% = 14,00 (e nao 18,00 sobre o preco cheio)
        self.assertEqual(mov.valor, Decimal('14.00'))

    def test_avulso_continua_sobre_valor_cobrado(self):
        from aranha_estetica.models import Atendimento
        from .factories import criar_atendimento, criar_cliente

        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        RegraComissao.objects.create(percentual=Decimal('10'), ativo=True)
        at = criar_atendimento(criar_cliente(), prof, proc, status='AGENDADO')
        Atendimento.objects.filter(pk=at.pk).update(status='REALIZADO', valor_cobrado=Decimal('180.00'))
        at.refresh_from_db()
        self.assertEqual(ComissaoService.calcular_comissao(at).valor, Decimal('18.00'))


class ComissaoArredondamentoTests(TestCase):
    """Centavos meio-para-cima (mesmo criterio de utils/precos) e teto de 100%."""

    def _realizado(self, valor):
        from aranha_estetica.models import Atendimento
        from .factories import criar_atendimento, criar_cliente

        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        at = criar_atendimento(criar_cliente(), prof, proc, status='AGENDADO')
        Atendimento.objects.filter(pk=at.pk).update(status='REALIZADO', valor_cobrado=Decimal(valor))
        at.refresh_from_db()
        return at

    def test_meio_centavo_arredonda_para_cima(self):
        RegraComissao.objects.create(percentual=Decimal('5'), ativo=True)
        at = self._realizado('10.50')
        # 5% de 10,50 = 0,525 -> 0,53 (ROUND_HALF_EVEN dava 0,52)
        self.assertEqual(ComissaoService.calcular_comissao(at).valor, Decimal('0.53'))

    def test_percentual_acima_de_100_nunca_paga_mais_que_o_atendimento(self):
        from unittest import mock

        regra = RegraComissao.objects.create(percentual=Decimal('30'), ativo=True)
        regra.percentual = Decimal('150')  # regra legada (antes do CHECK 0-100)
        at = self._realizado('200.00')
        with mock.patch.object(ComissaoService, 'resolver_regra', return_value=regra):
            mov = ComissaoService.calcular_comissao(at)
        self.assertEqual(mov.valor, Decimal('200.00'))
