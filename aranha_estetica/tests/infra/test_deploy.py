"""Caminho do deploy: perfis de settings (prod/staging), system checks de producao e coerencia
entre Dockerfile, railway.json e .env.example."""
import json
import os
import re
import shlex
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


class SegredosTests(SimpleTestCase):
    """aranha.E003: SECRET_KEY de exemplo/curta e token de header curto barram o pre-deploy."""

    CHAVE_BOA = 'Zq8' + 'k3Y-de-teste-abcdefghijklmnopqrstuvwxyz0123456789'

    def _ids(self, **config):
        base = {'CIFRAGEM_EXIGE_CHAVE': True, 'SECRET_KEY': self.CHAVE_BOA,
                'CRON_TOKEN': '', 'WHATSAPP_VERIFY_TOKEN': ''}
        with override_settings(**{**base, **config}):
            return [e.id for e in aranha_checks.check_segredos()]

    def test_chave_forte_passa(self):
        self.assertEqual(self._ids(), [])

    def test_chave_fraca_ou_de_exemplo_e_erro(self):
        for chave in ('troque-essa-chave-em-producao', 'curta', 'a' * 60,
                      'django-insecure-' + 'x1y2z3w4' * 6, 'build-only'):
            with self.subTest(chave=chave):
                self.assertEqual(self._ids(SECRET_KEY=chave), ['aranha.E003'])

    def test_token_de_header_curto_e_erro_e_vazio_nao(self):
        self.assertEqual(self._ids(CRON_TOKEN='c' * 31), ['aranha.E003'])
        self.assertEqual(self._ids(WHATSAPP_VERIFY_TOKEN='curto'), ['aranha.E003'])
        self.assertEqual(self._ids(CRON_TOKEN='t0k3n-' * 6, WHATSAPP_VERIFY_TOKEN='v3r1f-' * 6), [])

    def test_so_nos_perfis_publicados(self):
        # dev/testes (e o runner, que roda os checks com DEBUG=False) usam a chave de dev
        self.assertEqual(self._ids(CIFRAGEM_EXIGE_CHAVE=False, SECRET_KEY='curta'), [])


class EnvExampleTests(SimpleTestCase):
    """O .env.example e o roteiro de quem monta um ambiente: lista toda variavel que o codigo le e
    nenhuma que ele ignora (antes faltavam CRON_MONITOR_URL, dados do encarregado LGPD...)."""

    _LEITURA = re.compile(
        r"(?:os\.environ\.get|os\.environ\.setdefault|_env_texto|_env_int|_env_lista)\(\s*'([A-Z][A-Z0-9_]*)'"
        r"|os\.environ\[\s*'([A-Z][A-Z0-9_]*)'\]"
    )
    _DOCUMENTADA = re.compile(r'^#?[ \t]*([A-Z][A-Z0-9_]*)=', re.M)
    # Lidas pelo codigo e fora do exemplo de proposito (alem de RAILWAY_*, que o Railway injeta)
    LIDAS_SEM_DOCUMENTAR = {
        'TESTE_SQLITE_MEMORIA', 'TESTE_VITE_BUILD',  # so a suite (settings/test.py)
        'STATIC_ROOT',  # legado: prod.py ignora (estaticos do build em BASE_DIR/staticfiles)
        'PORT',  # o Railway injeta (startCommand do gunicorn)
    }
    # Documentadas sem leitura: o Celery saiu do projeto (o bloco do exemplo sai junto com as
    # outras configuracoes mortas)
    DOCUMENTADAS_SEM_LEITURA = {'CELERY_WORKER_ENABLED', 'CELERY_BROKER_URL', 'CELERY_RESULT_BACKEND'}

    def _lidas(self):
        arquivos = [*(BASE_DIR / 'clinica' / 'settings').glob('*.py'), *(BASE_DIR / 'aranha_estetica').rglob('*.py')]
        lidas = set()
        for arquivo in arquivos:
            partes = arquivo.relative_to(BASE_DIR).parts
            if 'tests' in partes or 'migrations' in partes:
                continue
            for direto, indexado in self._LEITURA.findall(arquivo.read_text(encoding='utf-8')):
                lidas.add(direto or indexado)
        # gunicorn: ${WEB_THREADS:-4} no CMD do Dockerfile e no startCommand do railway.json
        for arquivo in ('Dockerfile', 'railway.json'):
            lidas |= set(re.findall(r'\$\{([A-Z][A-Z0-9_]*)', (BASE_DIR / arquivo).read_text(encoding='utf-8')))
        # lidas em laco: marca (MARCA_ENV) e chaves dos provedores de e-mail (ANYMAIL)
        return lidas | set(settings.MARCA_ENV) | set(settings.ANYMAIL_CHAVES_ENV)

    def test_exemplo_lista_o_que_o_codigo_le(self):
        documentadas = set(self._DOCUMENTADA.findall((BASE_DIR / '.env.example').read_text(encoding='utf-8')))
        lidas = {nome for nome in self._lidas() if not nome.startswith('RAILWAY_')}
        self.assertEqual(lidas - documentadas - self.LIDAS_SEM_DOCUMENTAR, set(), 'faltam no .env.example')
        self.assertEqual(documentadas - lidas - self.DOCUMENTADAS_SEM_LEITURA, set(), 'sem uso no codigo')

    def test_exemplo_nao_traz_segredo_preenchido(self):
        texto = (BASE_DIR / '.env.example').read_text(encoding='utf-8')
        self.assertRegex(texto, r'(?m)^DJANGO_SECRET_KEY=$')
        self.assertRegex(texto, r'(?m)^# DJANGO_ENV=dev$')  # dev nunca ativo: o exemplo vai p/ o Railway


class ImagemTests(SimpleTestCase):
    """Dockerfile x railway.json: o Railway sobrescreve o CMD com o startCommand (os dois tem de ser
    o mesmo comando) e a imagem roda sem poder reescrever o proprio codigo."""

    def _dockerfile(self):
        return (BASE_DIR / 'Dockerfile').read_text(encoding='utf-8')

    def test_start_command_igual_ao_cmd(self):
        cmd = [linha for linha in self._dockerfile().splitlines() if linha.startswith('CMD ')]
        self.assertEqual(len(cmd), 1)
        railway = json.loads((BASE_DIR / 'railway.json').read_text(encoding='utf-8'))
        self.assertEqual(shlex.split(railway['deploy']['startCommand']), json.loads(cmd[0][len('CMD '):]))

    def test_codigo_do_root_e_runtime_sem_shell_nem_curl(self):
        dockerfile = self._dockerfile()
        self.assertNotIn('--chown=app:app . .', dockerfile)
        self.assertNotRegex(dockerfile, r'COPY --from=front --chown')
        self.assertIn('--shell /usr/sbin/nologin app', dockerfile)
        self.assertNotRegex(dockerfile, r'(?m)^\s+curl \\$')
        self.assertIn('USER app', dockerfile)

    def test_contexto_do_build_filtra_em_qualquer_pasta(self):
        ignorados = (BASE_DIR / '.dockerignore').read_text(encoding='utf-8').splitlines()
        for padrao in ('**/__pycache__', '**/*.sqlite3*', '**/*.dump*', '**/*.log', 'aranha_estetica/tests/'):
            with self.subTest(padrao=padrao):
                self.assertIn(padrao, ignorados)


class CsrfEmProducaoTests(SimpleTestCase):
    def test_so_origens_https_sao_confiaveis(self):
        env = {k: v for k, v in os.environ.items() if k not in ('DEBUG', 'RAILWAY_PUBLIC_DOMAIN')}
        env.update({'DJANGO_ENV': 'prod', 'DJANGO_SECRET_KEY': CHAVE_TESTE,
                    'CSRF_TRUSTED_ORIGINS': 'http://localhost:8000,https://clinica.test',
                    'RAILWAY_PUBLIC_DOMAIN': 'app.up.railway.app'})
        codigo = 'import json\nimport clinica.settings as s\nprint(json.dumps(s.CSRF_TRUSTED_ORIGINS))\n'
        proc = subprocess.run([sys.executable, '-W', 'ignore', '-c', codigo], cwd=BASE_DIR, env=env,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout.strip().splitlines()[-1]),
                         ['https://clinica.test', 'https://app.up.railway.app'])


class TentativasOtpTests(SimpleTestCase):
    """aranha.E004: OTP_MAX_TENTATIVAS acima do CHECK do banco (10) virava 500 no 11o erro."""

    def test_fora_de_1_a_10_e_erro(self):
        for valor, esperado in ((5, []), (10, []), (1, []), (11, ['aranha.E004']), (0, ['aranha.E004'])):
            with self.subTest(valor=valor), override_settings(OTP_MAX_TENTATIVAS=valor):
                self.assertEqual([e.id for e in aranha_checks.check_tentativas_otp()], esperado)

    def test_valor_nao_numerico_derruba_o_boot_com_o_nome_da_variavel(self):
        env = {k: v for k, v in os.environ.items() if k != 'DEBUG'}
        env.update({'DEBUG': 'True', 'OTP_MAX_TENTATIVAS': 'cinco'})
        proc = subprocess.run([sys.executable, '-W', 'ignore', '-c', 'import clinica.settings.dev'],
                              cwd=BASE_DIR, env=env, capture_output=True, text=True, timeout=120)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("OTP_MAX_TENTATIVAS='cinco'", proc.stderr)
