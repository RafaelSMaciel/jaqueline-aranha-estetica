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
