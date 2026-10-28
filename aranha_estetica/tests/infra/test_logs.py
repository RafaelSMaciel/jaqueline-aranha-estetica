"""Log da aplicacao sem token de link magico nem dado pessoal (filtro RedigirLog no handler console)
e nivel/ambiente da observabilidade por env."""
import json
import logging
import os
import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest import mock, skipUnless

from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase

from clinica.logging_filtros import RedigirLog

BASE_DIR = Path(settings.BASE_DIR)
TOKEN = 'TOKENSECRETO0123456789abcdef'

try:
    import sentry_sdk  # noqa: F401
    TEM_SENTRY = True
except ImportError:
    TEM_SENTRY = False


def _registro(msg, *args, exc_info=None, **extra):
    record = logging.LogRecord('django.request', logging.WARNING, __file__, 1, msg, args, exc_info)
    for chave, valor in extra.items():
        setattr(record, chave, valor)
    return record


class RedigirLogTests(SimpleTestCase):
    def test_mensagem_formatada_sai_redigida(self):
        record = _registro('Not Found: %s', f'/confirmar/{TOKEN}/')
        self.assertTrue(RedigirLog().filter(record))
        self.assertEqual(record.getMessage(), 'Not Found: /confirmar/[token]/')

    def test_extra_e_traceback_redigidos(self):
        class Requisicao:
            def __str__(self):
                return f"<WSGIRequest: GET '/termo/{TOKEN}/'>"
        try:
            raise ValueError('falhou para ana.souza@gmail.com')
        except ValueError:
            record = _registro('erro', exc_info=sys.exc_info(), request=Requisicao(),
                               destino='ana.souza@gmail.com', status_code=404, ids=[1, 2])
        RedigirLog().filter(record)
        self.assertIsNone(record.exc_info)
        self.assertNotIn('ana.souza', record.exc_text)
        self.assertIn('ValueError', record.exc_text)
        self.assertEqual(record.request, "<WSGIRequest: GET '/termo/[token]/'>")
        self.assertEqual(record.destino, 'an***@gmail.com')
        self.assertEqual((record.status_code, record.ids), (404, [1, 2]))

    def test_args_que_nao_casam_nao_derrubam_quem_loga(self):
        record = _registro('%s e %s', 'so-um')
        self.assertTrue(RedigirLog().filter(record))
        self.assertEqual(record.getMessage(), '%s e %s')

    def test_handler_console_tem_o_filtro(self):
        filtros = settings.LOGGING['handlers']['console']['filters']
        self.assertEqual(filtros, ['redigir'])
        self.assertEqual(settings.LOGGING['filters']['redigir']['()'], 'clinica.logging_filtros.RedigirLog')
        # formatter JSON no modulo novo (pythonjsonlogger.jsonlogger e legado e emite DeprecationWarning)
        self.assertEqual(settings.LOGGING['formatters']['json']['()'], 'pythonjsonlogger.json.JsonFormatter')


class LogDoDjangoSemTokenTests(TestCase):
    """O caminho com o token sai no log do Django em toda resposta 4xx (404, 403 de CSRF)."""

    def _saida_do_console(self, requisicao):
        handler = next(h for h in logging.getLogger('django').handlers
                       if any(isinstance(f, RedigirLog) for f in h.filters))
        saida = StringIO()
        anterior = handler.setStream(saida)
        try:
            requisicao()
        finally:
            handler.setStream(anterior)
        return saida.getvalue()

    def test_404_de_link_inexistente(self):
        log = self._saida_do_console(lambda: self.client.get(f'/confirmar/{TOKEN}/'))
        self.assertIn('/confirmar/[token]/', log)
        self.assertNotIn(TOKEN, log)

    def test_403_de_csrf_em_rota_com_token(self):
        cliente = Client(enforce_csrf_checks=True)
        log = self._saida_do_console(lambda: cliente.post(f'/termo/{TOKEN}/', {}))
        self.assertIn('CSRF', log)
        self.assertIn('/termo/[token]/', log)
        self.assertNotIn(TOKEN, log)


class NivelDeLogEmProducaoTests(SimpleTestCase):
    def _nivel_django(self, **env_extra):
        env = {k: v for k, v in os.environ.items() if k != 'DJANGO_LOG_LEVEL'}
        env.update({'DJANGO_ENV': 'prod', 'DJANGO_SECRET_KEY': 'k3Y-de-teste-' + 'x1y2z3' * 8, **env_extra})
        codigo = ('import json\nimport clinica.settings.prod as s\n'
                  'print(json.dumps(s.LOGGING["loggers"]["django"]["level"]))\n')
        proc = subprocess.run([sys.executable, '-W', 'ignore', '-c', codigo], cwd=BASE_DIR, env=env,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_django_log_level_vale_em_prod(self):
        self.assertEqual(self._nivel_django(), 'WARNING')
        self.assertEqual(self._nivel_django(DJANGO_LOG_LEVEL='INFO'), 'INFO')


@skipUnless(TEM_SENTRY, 'sentry-sdk ausente')
class AmbienteDoSentryTests(SimpleTestCase):
    """A imagem fixa DJANGO_ENV=prod: o ambiente de testes do Railway reportava como 'prod'."""

    def _ambiente(self, **env):
        from aranha_estetica.utils.pii import opcoes_sentry

        base = {'SENTRY_ENVIRONMENT': '', 'RAILWAY_ENVIRONMENT_NAME': '', 'DJANGO_ENV': ''}
        with mock.patch.dict(os.environ, {**base, **env}):
            return opcoes_sentry('https://chave@o0.ingest.sentry.io/1')['environment']

    def test_precedencia(self):
        self.assertEqual(self._ambiente(), 'prod')
        self.assertEqual(self._ambiente(DJANGO_ENV='prod', RAILWAY_ENVIRONMENT_NAME='dev'), 'dev')
        self.assertEqual(self._ambiente(DJANGO_ENV='prod', RAILWAY_ENVIRONMENT_NAME='dev',
                                        SENTRY_ENVIRONMENT='staging'), 'staging')
