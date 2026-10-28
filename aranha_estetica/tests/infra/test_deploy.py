"""Caminho do deploy: perfis de settings (prod/staging), system checks de producao e coerencia
entre Dockerfile, railway.json e .env.example."""
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from aranha_estetica import checks as aranha_checks

BASE_DIR = Path(settings.BASE_DIR)
# Valores falsos, so do teste: o check nao conecta no banco nem usa a chave
CHAVE_TESTE = 'k3Y-de-teste-' + 'abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH'
FERNET_TESTE = 'Y2ktc28tcGFyYS1vLWNoZWNrLWRlcGxveS0zMmJ5dGU='


def _manage(*args, **env_extra):
    """Roda manage.py num processo limpo (settings resolvidos do zero pela env)."""
    env = {k: v for k, v in os.environ.items() if k not in ('DEBUG', 'SMS_DEV_LOG_ONLY')}
    env.update({
        'DJANGO_SECRET_KEY': CHAVE_TESTE, 'CHAVES_CIFRAGEM': FERNET_TESTE,
        'DATABASE_URL': 'postgres://u:p@localhost:5432/db',
        'EMAIL_BACKEND': 'anymail.backends.resend.EmailBackend', 'RESEND_API_KEY': 're_teste',
        **env_extra,
    })
    return subprocess.run(
        [sys.executable, '-W', 'ignore', 'manage.py', *args], cwd=BASE_DIR, env=env,
        capture_output=True, text=True, timeout=180,
    )


class CheckDeployTests(SimpleTestCase):
    """O mesmo `check --deploy --fail-level ERROR` do CI, nos dois perfis publicados."""

    def test_staging_e_prod_passam_no_check_de_deploy(self):
        for perfil in ('prod', 'staging'):
            with self.subTest(perfil=perfil):
                proc = _manage('check', '--deploy', '--fail-level', 'ERROR',
                               DJANGO_ENV=perfil, RAILWAY_ENVIRONMENT_NAME='dev')
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertNotIn('ERRORS:', proc.stderr + proc.stdout)


@override_settings(DEBUG=False, SITE_URL='https://jaquelinearanha.com.br', SMS_DEV_LOG_ONLY=False,
                   PROFISSIONAL_2FA_OBRIGATORIO=True)
class EmailDoStagingTests(SimpleTestCase):
    """A trava do staging (BackendAmbienteTeste) entrega pelo EMAIL_BACKEND_REAL: o aviso de
    'nenhum e-mail e entregue' olha o backend real, como utils/email.email_configurado."""

    def _ids(self):
        return [e.id for e in aranha_checks.check_config_producao()]

    @override_settings(EMAIL_BACKEND='aranha_estetica.utils.email.BackendAmbienteTeste',
                       EMAIL_BACKEND_REAL='django.core.mail.backends.dummy.EmailBackend')
    def test_staging_sem_backend_real_avisa_w003(self):
        self.assertIn('aranha.W003', self._ids())

    @override_settings(EMAIL_BACKEND='aranha_estetica.utils.email.BackendAmbienteTeste',
                       EMAIL_BACKEND_REAL='django.core.mail.backends.smtp.EmailBackend')
    def test_staging_com_backend_real_nao_avisa_w003(self):
        self.assertNotIn('aranha.W003', self._ids())
