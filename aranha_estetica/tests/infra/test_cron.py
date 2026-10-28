"""Cron HTTP (/cron/run/<job>/): resposta sem dado pessoal e freio contra adivinhar o token."""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from aranha_estetica.views.infra import cron

TOKEN = 'token-do-cron-' + 'x9' * 12


def _job_que_vaza():
    raise ValueError('falha ao enviar para ana.souza@gmail.com')


@override_settings(CRON_TOKEN=TOKEN)
class CronRespostaTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_erro_sai_so_com_o_tipo_e_a_referencia_do_log(self):
        with mock.patch.dict(cron.JOB_MAP, {'vaza': _job_que_vaza}), \
                self.assertLogs('aranha_estetica.views.infra.cron', 'ERROR') as logs:
            resp = self.client.post('/cron/run/vaza/', HTTP_X_CRON_TOKEN=TOKEN)
        self.assertEqual(resp.status_code, 500)
        corpo = resp.json()
        self.assertNotIn('ana.souza', resp.content.decode())
        self.assertEqual({k: corpo[k] for k in ('ok', 'codigo', 'mensagem', 'job')},
                         {'ok': False, 'codigo': 'job_falhou', 'mensagem': 'ValueError', 'job': 'vaza'})
        self.assertRegex(corpo['ref'], r'^[0-9a-f]{8}$')
        self.assertEqual(logs.records[0].ref, corpo['ref'])  # correlaciona com o traceback no log

    def test_token_com_caractere_nao_ascii_e_403(self):
        self.assertEqual(self.client.post('/cron/run/housekeeping/', HTTP_X_CRON_TOKEN='çã').status_code, 403)

    def test_31a_chamada_na_hora_e_429(self):
        for _ in range(30):
            self.assertEqual(self.client.post('/cron/run/housekeeping/', HTTP_X_CRON_TOKEN='errado').status_code, 403)
        self.assertEqual(self.client.post('/cron/run/housekeeping/', HTTP_X_CRON_TOKEN=TOKEN).status_code, 429)
