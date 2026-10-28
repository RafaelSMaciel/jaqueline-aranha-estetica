"""Jobs periodicos: registro, agenda, trava diaria, housekeeping e feriados (SQLite)."""
from datetime import date
from unittest import mock

from django.test import TestCase

from aranha_estetica.models import AgendaExcecao
from aranha_estetica.services import feriados


class FeriadosTests(TestCase):
    def setUp(self):
        AgendaExcecao.objects.filter(data_inicio__year__in=(2035, 2036)).delete()

    def test_pascoa_em_anos_conhecidos(self):
        self.assertEqual(feriados.pascoa(2000), date(2000, 4, 23))
        self.assertEqual(feriados.pascoa(2024), date(2024, 3, 31))
        self.assertEqual(feriados.pascoa(2026), date(2026, 4, 5))
        self.assertEqual(feriados.pascoa(2038), date(2038, 4, 25))

    def test_moveis_derivam_da_pascoa(self):
        self.assertEqual(feriados.feriados_moveis(2026), [
            (date(2026, 2, 16), 'Carnaval (segunda)'),
            (date(2026, 2, 17), 'Carnaval (terça)'),
            (date(2026, 4, 3), 'Sexta-feira Santa'),
            (date(2026, 6, 4), 'Corpus Christi'),
        ])

    def test_carregar_cria_folga_da_clinica_e_e_idempotente(self):
        self.assertEqual(feriados.carregar([2035]), 13)   # 9 fixos + 4 moveis
        natal = AgendaExcecao.objects.get(origem='FERIADO_NACIONAL', data_inicio=date(2035, 12, 25))
        self.assertEqual((natal.tipo, natal.profissional_id, natal.data_fim, natal.ativo),
                         ('FOLGA', None, date(2035, 12, 25), True))
        self.assertEqual(feriados.carregar([2035]), 0)

    def test_carregar_nao_reativa_feriado_em_que_a_clinica_abre(self):
        feriados.carregar([2035])
        AgendaExcecao.objects.filter(data_inicio=date(2035, 12, 25)).update(ativo=False)
        self.assertEqual(feriados.carregar([2035, 2036]), 13)
        self.assertFalse(AgendaExcecao.objects.get(data_inicio=date(2035, 12, 25)).ativo)

    def test_job_devolve_quantos_criou(self):
        from aranha_estetica.jobs_manutencao import job_carregar_feriados

        with mock.patch.object(feriados, 'anos_padrao', return_value=[2035]):
            self.assertEqual(job_carregar_feriados(), '13 feriados criados')
            self.assertEqual(job_carregar_feriados(), '0 feriados criados')
