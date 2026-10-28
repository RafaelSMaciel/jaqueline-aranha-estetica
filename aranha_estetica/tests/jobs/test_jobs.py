"""Jobs periodicos: registro, agenda, trava diaria, housekeeping e feriados (SQLite)."""
import os
from datetime import date, datetime, timedelta
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from aranha_estetica import jobs, jobs_manutencao
from aranha_estetica.models import AgendaExcecao, ExecucaoJob
from aranha_estetica.services import feriados

from ..factories import criar_cliente

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


def _local(*args):
    return timezone.make_aware(datetime(*args))


class AgendaTests(TestCase):
    def test_todo_job_tem_horario_e_todo_horario_tem_job(self):
        self.assertEqual(set(jobs.JOB_AGENDA), set(jobs.JOB_MAP))
        self.assertLessEqual(jobs.JOBS_UMA_VEZ_POR_DIA, set(jobs.JOB_MAP))

    def test_jobs_devidos_na_janela_dos_15_minutos(self):
        segunda_9h = _local(2026, 11, 2, 9, 0)
        self.assertEqual(jobs.jobs_devidos(segunda_9h - timedelta(minutes=15), segunda_9h),
                         ['emails', 'limpeza_status', 'aniversario'])
        # o cron atrasou 4 min: o das 9:00 ainda cai na janela (8:49, 9:04]
        self.assertIn('aniversario', jobs.jobs_devidos(_local(2026, 11, 2, 8, 49), _local(2026, 11, 2, 9, 4)))
        # janela seguinte (9:04, 9:19] nao repete
        self.assertEqual(jobs.jobs_devidos(_local(2026, 11, 2, 9, 4), _local(2026, 11, 2, 9, 19)), ['emails'])

    def test_lembrete_duas_vezes_por_dia_lgpd_no_domingo_feriados_no_dia_1(self):
        def devidos(*momento):
            fim = _local(*momento)
            return jobs.jobs_devidos(fim - timedelta(minutes=15), fim)
        self.assertIn('lembrete_diario', devidos(2026, 11, 2, 8, 0))
        self.assertIn('sem_desfecho', devidos(2026, 11, 2, 8, 0))
        self.assertIn('lembrete_diario', devidos(2026, 11, 2, 18, 0))
        self.assertIn('lgpd_purgar', devidos(2026, 11, 1, 3, 0))        # domingo
        self.assertNotIn('lgpd_purgar', devidos(2026, 11, 2, 3, 0))     # segunda
        self.assertIn('feriados', devidos(2026, 12, 1, 4, 30))
        self.assertNotIn('feriados', devidos(2026, 12, 2, 4, 30))


@override_settings(EMAIL_BACKEND=LOCMEM)
class TravaDiariaTests(TestCase):
    def setUp(self):
        hoje = timezone.localdate()
        criar_cliente(nome='Aniversariante', email='niver@example.com',
                      data_nascimento=hoje.replace(year=1992), consent_email_marketing=True)

    def test_aniversario_roda_uma_vez_por_dia(self):
        self.assertEqual(jobs.executar('aniversario'), '1 e-mails de aniversario')
        with self.assertRaises(jobs.JobJaExecutado):
            jobs.executar('aniversario')
        self.assertEqual(len(mail.outbox), 1)
        execucao = ExecucaoJob.objects.get(job='aniversario')
        self.assertEqual((execucao.dia, execucao.resultado), (timezone.localdate(), '1 e-mails de aniversario'))
        self.assertIsNotNone(execucao.concluido_em)

    def test_forcar_roda_de_novo(self):
        jobs.executar('aniversario')
        jobs.executar('aniversario', forcar=True)
        self.assertEqual(len(mail.outbox), 2)

    def test_falha_fica_registrada_e_nao_roda_de_novo_sozinha(self):
        with mock.patch.dict(jobs.JOB_MAP, {'aniversario': mock.Mock(side_effect=RuntimeError('smtp'))}):
            with self.assertRaises(RuntimeError):
                jobs.executar('aniversario')
            with self.assertRaises(jobs.JobJaExecutado):
                jobs.executar('aniversario')
        self.assertEqual(ExecucaoJob.objects.get(job='aniversario').resultado, 'erro: RuntimeError')

    @mock.patch.dict(os.environ, {'CRON_TOKEN': 'segredo-cron'})
    def test_cron_http_responde_409_na_segunda_chamada(self):
        primeira = self.client.post('/cron/run/aniversario/', HTTP_X_CRON_TOKEN='segredo-cron')
        segunda = self.client.post('/cron/run/aniversario/', HTTP_X_CRON_TOKEN='segredo-cron')
        self.assertEqual((primeira.status_code, segunda.status_code), (200, 409))
        self.assertEqual(len(mail.outbox), 1)

    def test_rodar_job_avisa_e_nao_reenvia(self):
        call_command('rodar_job', 'aniversario', stdout=StringIO())
        saida = StringIO()
        call_command('rodar_job', 'aniversario', stdout=saida)
        self.assertIn('ja rodou hoje', saida.getvalue())
        self.assertEqual(len(mail.outbox), 1)

    def test_jobs_sem_trava_rodam_quantas_vezes_chamar(self):
        jobs.executar('emails')
        jobs.executar('emails')
        self.assertFalse(ExecucaoJob.objects.filter(job='emails').exists())


class RodarJobsDevidosTests(TestCase):
    def test_roda_so_os_da_janela_e_isola_a_falha(self):
        chamados = []

        def job(nome, falha=False):
            def _rodar():
                chamados.append(nome)
                if falha:
                    raise RuntimeError(nome)
                return 'ok'
            return _rodar

        fakes = {nome: job(nome, falha=(nome == 'emails')) for nome in jobs.JOB_MAP}
        saida, erros = StringIO(), StringIO()
        with mock.patch.dict(jobs.JOB_MAP, fakes), self.assertRaisesMessage(CommandError, 'falharam: emails'):
            call_command('rodar_jobs_devidos', '--agora', '2026-11-02T09:00', stdout=saida, stderr=erros)
        self.assertEqual(chamados, ['emails', 'limpeza_status', 'aniversario'])
        self.assertIn('emails: FALHOU', erros.getvalue())
        self.assertIn('aniversario: ok', saida.getvalue())

    def test_somente_filtra_e_job_desconhecido_recusa(self):
        fake = mock.Mock(return_value='ok')
        with mock.patch.dict(jobs.JOB_MAP, {'emails': fake, 'limpeza_status': fake}):
            call_command('rodar_jobs_devidos', '--agora', '2026-11-02T09:00', '--somente', 'emails',
                         stdout=StringIO())
        fake.assert_called_once()
        with self.assertRaises(CommandError):
            call_command('rodar_jobs_devidos', '--somente', 'nao-existe', stdout=StringIO())

    @override_settings(CRON_MONITOR_URL='https://hc-ping.com/chave/')
    def test_avisa_o_monitor_por_job(self):
        with mock.patch.dict(jobs.JOB_MAP, {'emails': mock.Mock(return_value='ok')}), \
                mock.patch.object(jobs.requests, 'post') as post:
            call_command('rodar_jobs_devidos', '--agora', '2026-11-02T09:15', '--somente', 'emails',
                         stdout=StringIO())
        post.assert_called_once_with('https://hc-ping.com/chave/emails', timeout=5)


class HousekeepingIsolamentoTests(TestCase):
    def test_etapa_que_falha_nao_impede_as_outras(self):
        depois = mock.Mock(return_value=3)
        etapas = [('a', mock.Mock(side_effect=OSError('disco'))), ('b', depois)]
        with mock.patch.object(jobs_manutencao, 'ETAPAS_HOUSEKEEPING', etapas), \
                self.assertRaisesMessage(RuntimeError, 'housekeeping falhou em: a'):
            jobs_manutencao.job_housekeeping()
        depois.assert_called_once()

    def test_falha_dentro_da_procedure_aparece(self):
        banco = mock.Mock(return_value=({'otp': 1, 'links': 'erro: links'}, ['links']))
        with mock.patch.object(jobs_manutencao, 'ETAPAS_HOUSEKEEPING', [('banco', banco)]), \
                self.assertRaisesMessage(RuntimeError, 'links'):
            jobs_manutencao.job_housekeeping()

    @mock.patch.dict(os.environ, {'CRON_TOKEN': 'segredo-cron'})
    def test_cron_http_da_500_quando_uma_etapa_falha(self):
        etapas = [('a', mock.Mock(side_effect=OSError('disco')))]
        with mock.patch.object(jobs_manutencao, 'ETAPAS_HOUSEKEEPING', etapas):
            resp = self.client.post('/cron/run/housekeeping/', HTTP_X_CRON_TOKEN='segredo-cron')
        self.assertEqual(resp.status_code, 500)
        self.assertFalse(resp.json()['ok'])


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
