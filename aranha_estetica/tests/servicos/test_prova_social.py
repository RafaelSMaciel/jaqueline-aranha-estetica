"""Numeros do site (services/prova_social): so o que o banco ou a dona sustentam."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from aranha_estetica.models import AvaliacaoNPS, Configuracao
from aranha_estetica.models.configuracao import CHAVES
from aranha_estetica.services import prova_social

from ..factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self._dia = 0

    def _realizado(self, cliente=None):
        self._dia += 1
        quando = timezone.now() - timedelta(days=self._dia)
        return criar_atendimento(cliente or criar_cliente(), self.prof, self.proc,
                                 data_hora=quando, status='REALIZADO')

    def _notas(self, *notas):
        for nota in notas:
            AvaliacaoNPS.objects.create(atendimento=self._realizado(), nota=nota)


class NotaMediaTests(_Base):
    def test_sem_respostas_suficientes_nao_ha_nota(self):
        self._notas(*[10] * (prova_social.MIN_AVALIACOES - 1))
        numeros = prova_social.calcular()
        self.assertIsNone(numeros['nota_media'])
        self.assertEqual(numeros['estrelas'], [])
        self.assertEqual(numeros['total_avaliacoes'], prova_social.MIN_AVALIACOES - 1)

    def test_media_em_cinco_estrelas_arredondada_meio_para_cima(self):
        # media 9,5 -> 4,75 -> "4,8"
        self._notas(*([10] * 5 + [9] * 5))
        numeros = prova_social.calcular()
        self.assertEqual(numeros['nota_media'], Decimal('4.8'))
        self.assertEqual(numeros['total_avaliacoes'], 10)
        self.assertEqual(numeros['estrelas'], ['cheia'] * 5)

    def test_estrelas_nunca_arredondam_para_cima(self):
        self.assertEqual(prova_social.estrelas(Decimal('4.7')), ['cheia'] * 4 + ['meia'])
        self.assertEqual(prova_social.estrelas(Decimal('4.2')), ['cheia'] * 4 + ['vazia'])
        self.assertEqual(prova_social.estrelas(Decimal('3.5')), ['cheia'] * 3 + ['meia', 'vazia'])
        self.assertEqual(prova_social.estrelas(None), [])


class ClientesAtendidasTests(_Base):
    def test_conta_clientes_distintas_so_com_realizado(self):
        repetida = criar_cliente(nome='Volta Sempre')
        for _ in range(3):
            self._realizado(repetida)
        self._realizado()
        criar_atendimento(criar_cliente(), self.prof, self.proc)   # AGENDADO: nao conta
        sem_degrau = patch.object(prova_social, 'arredondar_para_baixo', side_effect=lambda n: n)
        with patch.object(prova_social, 'MIN_CLIENTES', 2), sem_degrau:
            self.assertEqual(prova_social.calcular()['clientes_atendidas'], 2)
        with patch.object(prova_social, 'MIN_CLIENTES', 3):
            self.assertIsNone(prova_social.calcular()['clientes_atendidas'])

    def test_abaixo_do_minimo_nao_aparece(self):
        self._realizado()
        self.assertIsNone(prova_social.calcular()['clientes_atendidas'])

    def test_arredonda_para_baixo(self):
        self.assertEqual(prova_social.arredondar_para_baixo(57), 50)
        self.assertEqual(prova_social.arredondar_para_baixo(127), 120)
        self.assertEqual(prova_social.arredondar_para_baixo(1234), 1200)


class TratamentosTests(_Base):
    def test_so_procedimentos_agendaveis_por_categoria(self):
        criar_procedimento(nome='Drenagem', categoria='CORPORAL', profissional=self.prof)
        criar_procedimento(nome='Sem profissional', categoria='CORPORAL')        # nao agendavel
        inativo = criar_procedimento(nome='Inativo', profissional=self.prof)
        inativo.ativo = False
        inativo.save()
        numeros = prova_social.calcular()
        self.assertEqual(numeros['tratamentos'], 2)
        self.assertEqual(numeros['tratamentos_por_categoria'], {'FACIAL': 1, 'CORPORAL': 1})


class AnosDeExperienciaTests(_Base):
    def test_em_branco_nao_aparece(self):
        self.assertEqual(Configuracao.ler('anos_experiencia'), '')
        self.assertIsNone(prova_social.calcular()['anos_experiencia'])

    def test_valor_da_dona(self):
        Configuracao.objects.create(chave='anos_experiencia', valor='7')
        self.assertEqual(prova_social.calcular()['anos_experiencia'], 7)

    def test_registro_tipado_opcional(self):
        chave = CHAVES['anos_experiencia']
        self.assertEqual(chave.validar(''), '')
        self.assertEqual(chave.validar(' 12 '), 12)
        for invalido in ('0', '61', 'cinco'):
            with self.subTest(valor=invalido), self.assertRaises(Exception):
                chave.validar(invalido)


class CacheTests(_Base):
    def test_uma_conta_por_janela_e_sem_consulta_por_item(self):
        self._notas(*[10] * 12)
        with self.assertNumQueries(4):
            prova_social.numeros_do_site()
        with self.assertNumQueries(0):
            prova_social.numeros_do_site()

    def test_mudar_a_configuracao_invalida_o_cache(self):
        self.assertIsNone(prova_social.numeros_do_site()['anos_experiencia'])
        Configuracao.objects.create(chave='anos_experiencia', valor='9')
        self.assertEqual(prova_social.numeros_do_site()['anos_experiencia'], 9)
