"""Regressao dos achados de deploy/infra (P1): cron, healthcheck, system checks,
bootstrap_admin, migrate_atomico, jobs de retencao e coerencia dos settings."""
import json
import os
import subprocess
import sys
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest import mock

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from aranha_estetica import checks as aranha_checks
from aranha_estetica.views import cron

BASE_DIR = Path(settings.BASE_DIR)
_CHAMADAS = {'n': 0}


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def _job_que_falha(self):
    """Espelha os job_* reais: captura e pede retry."""
    _CHAMADAS['n'] += 1
    try:
        raise ValueError('db down')
    except ValueError as exc:
        raise self.retry(exc=exc)


@shared_task
def _job_ok():
    return 3


# ─── Cron ────────────────────────────────────────────────────────────
@mock.patch.dict(os.environ, {'CRON_TOKEN': 'segredo-cron'})
class CronEndpointTests(TestCase):
    url = '/cron/run/{}/'

    def _post(self, job, token='segredo-cron'):
        headers = {'HTTP_X_CRON_TOKEN': token} if token is not None else {}
        return self.client.post(self.url.format(job), **headers)

    def test_sem_token_403(self):
        self.assertEqual(self._post('housekeeping', token=None).status_code, 403)

    def test_token_errado_403(self):
        self.assertEqual(self._post('housekeeping', token='x').status_code, 403)

    def test_token_nao_ascii_403_nao_500(self):
        # compare_digest(str, str) levantava TypeError -> 500
        self.assertEqual(self._post('housekeeping', token='çãõ-ñ').status_code, 403)

    def test_token_na_query_nao_autentica(self):
        r = self.client.post(self.url.format('housekeeping') + '?token=segredo-cron')
        self.assertEqual(r.status_code, 403)

    def test_get_nao_permitido(self):
        r = self.client.get(self.url.format('housekeeping'), HTTP_X_CRON_TOKEN='segredo-cron')
        self.assertEqual(r.status_code, 405)

    def test_job_desconhecido_404(self):
        r = self._post('nao-existe')
        self.assertEqual(r.status_code, 404)
        self.assertIn('housekeeping', r.json()['available'])

    def test_job_ok_200(self):
        with mock.patch.dict(cron.JOB_MAP, {'ok': _job_ok}):
            r = self._post('ok')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'ok': True, 'job': 'ok', 'result': '3'})

    def test_job_que_falha_500_e_roda_uma_vez(self):
        _CHAMADAS['n'] = 0
        with mock.patch.dict(cron.JOB_MAP, {'falha': _job_que_falha}):
            r = self._post('falha')
        self.assertEqual(r.status_code, 500)
        self.assertFalse(r.json()['ok'])
        self.assertEqual(_CHAMADAS['n'], 1)  # sem reexecutar o lote 4x

    def test_falha_em_eager_sem_propagar_vira_500(self):
        # prod: EAGER_PROPAGATES=False -> apply() devolve FAILURE sem levantar
        resultado = mock.Mock()
        resultado.failed.return_value = True
        resultado.result = RuntimeError('db down')
        job = mock.Mock(max_retries=3)
        job.apply.return_value = resultado
        with mock.patch.dict(cron.JOB_MAP, {'falha': job}):
            r = self._post('falha')
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.json(), {'ok': False, 'job': 'falha', 'error': 'db down'})
        job.apply.assert_called_once_with(retries=3)

    def test_jobs_de_manutencao_registrados(self):
        for nome in ('housekeeping', 'feriados', 'lgpd_purgar', 'lembrete_diario'):
            self.assertIn(nome, cron.JOB_MAP)


@mock.patch.dict(os.environ, {'CRON_TOKEN': ''})
class CronSemTokenConfiguradoTests(TestCase):
    def test_sem_cron_token_no_ambiente_recusa(self):
        r = self.client.post('/cron/run/housekeeping/', HTTP_X_CRON_TOKEN='')
        self.assertEqual(r.status_code, 403)


# ─── Healthcheck ─────────────────────────────────────────────────────
class HealthcheckTests(TestCase):
    def test_liveness_ok(self):
        r = self.client.get('/healthz/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['status'], 'alive')

    def test_manifest_ausente_reprova_liveness_e_readiness(self):
        vite = {'default': {
            'dev_mode': False,
            'static_url_prefix': 'dist',
            'manifest_path': BASE_DIR / 'nao-existe' / 'manifest.json',
        }}
        with override_settings(DJANGO_VITE=vite):
            self.assertEqual(self.client.get('/healthz/').status_code, 503)
            r = self.client.get('/health/')
        self.assertEqual(r.status_code, 503)
        self.assertFalse(r.json()['front'])


# ─── System checks de producao ───────────────────────────────────────
_ENV_PROD_OK = {
    'ZENVIA_API_TOKEN': 'tok', 'ZENVIA_FROM': 'clinica',
    'WHATSAPP_NUMERO': '5517991234567', 'CRON_TOKEN': 'c', 'SMS_DEV_LOG_ONLY': '',
}


class ChecksProducaoTests(SimpleTestCase):
    def _ids(self, **env):
        with mock.patch.dict(os.environ, env):
            return sorted(e.id for e in aranha_checks.check_config_producao())

    @override_settings(DEBUG=True)
    def test_mudo_em_debug(self):
        self.assertEqual(self._ids(ZENVIA_API_TOKEN=''), [])

    @override_settings(
        DEBUG=False, SITE_URL='http://127.0.0.1:8000', SMS_DEV_LOG_ONLY=False,
        EMAIL_BACKEND='django.core.mail.backends.dummy.EmailBackend',
    )
    def test_config_ausente_gera_warnings(self):
        ids = self._ids(ZENVIA_API_TOKEN='', ZENVIA_FROM='', WHATSAPP_NUMERO='',
                        CRON_TOKEN='', SMS_DEV_LOG_ONLY='')
        self.assertEqual(ids, ['aranha.W001', 'aranha.W002', 'aranha.W003', 'aranha.W004', 'aranha.W005'])

    @override_settings(
        DEBUG=False, SITE_URL='https://jaquelinearanha.com.br', SMS_DEV_LOG_ONLY=False,
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
    )
    def test_config_completa_sem_warnings(self):
        self.assertEqual(self._ids(**_ENV_PROD_OK), [])

    @override_settings(
        DEBUG=False, SITE_URL='https://jaquelinearanha.com.br', SMS_DEV_LOG_ONLY=True,
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
    )
    def test_sms_dev_log_only_em_prod_avisa(self):
        self.assertEqual(self._ids(**_ENV_PROD_OK), ['aranha.W002'])

    def test_check_registrado(self):
        from django.core.checks import registry
        self.assertIn(aranha_checks.check_config_producao, registry.registry.get_checks())


class ChecksWhatsappBrandingTests(TestCase):
    @override_settings(
        DEBUG=False, SITE_URL='https://jaquelinearanha.com.br', SMS_DEV_LOG_ONLY=False,
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
    )
    def test_whatsapp_da_tela_branding_conta_quando_banco_liberado(self):
        from aranha_estetica.models import Configuracao

        env = {**_ENV_PROD_OK, 'WHATSAPP_NUMERO': ''}
        with mock.patch.dict(os.environ, env):
            sem_banco = [e.id for e in aranha_checks.check_config_producao()]
            Configuracao.objects.create(chave='WHATSAPP_NUMERO', valor='5517991234567')
            com_banco = [e.id for e in aranha_checks.check_config_producao(databases=['default'])]
        self.assertEqual(sem_banco, ['aranha.W004'])
        self.assertEqual(com_banco, [])


# ─── bootstrap_admin ─────────────────────────────────────────────────
SENHA_FORTE = 'Spa-Zen#2026-forte'


class BootstrapAdminTests(TestCase):
    def _run(self, reset=False, **env):
        base = {'ADMIN_EMAIL': '', 'ADMIN_PASSWORD': '', 'ADMIN_NOME': '', 'ADMIN_PASSWORD_RESET': ''}
        base.update(env)
        out, err = StringIO(), StringIO()
        args = ['--reset-senha'] if reset else []
        with mock.patch.dict(os.environ, base):
            call_command('bootstrap_admin', *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_sem_env_nao_faz_nada(self):
        U = get_user_model()
        antes = U.objects.count()
        out, _ = self._run()
        self.assertIn('nada a fazer', out)
        self.assertEqual(U.objects.count(), antes)

    def test_cria_admin_e_e_idempotente(self):
        U = get_user_model()
        self._run(ADMIN_EMAIL='dona@clinica.com.br', ADMIN_PASSWORD=SENHA_FORTE)
        u = U.objects.get(email='dona@clinica.com.br')
        self.assertEqual(u.papel, U.PAPEL_ADMIN)
        self.assertTrue(u.is_staff and u.ativo)
        self.assertTrue(u.check_password(SENHA_FORTE))

        # senha trocada no painel nao e desfeita no proximo deploy
        u.set_password('Outra-Senha#2026')
        u.save()
        out, _ = self._run(ADMIN_EMAIL='DONA@clinica.com.br', ADMIN_PASSWORD=SENHA_FORTE)
        self.assertIn('senha mantida', out)
        self.assertEqual(U.objects.filter(email__iexact='dona@clinica.com.br').count(), 1)
        u.refresh_from_db()
        self.assertTrue(u.check_password('Outra-Senha#2026'))

        self._run(reset=True, ADMIN_EMAIL='dona@clinica.com.br', ADMIN_PASSWORD=SENHA_FORTE)
        u.refresh_from_db()
        self.assertTrue(u.check_password(SENHA_FORTE))

    def test_reativa_conta_desativada_trocando_a_senha(self):
        U = get_user_model()
        u = U.objects.create_user('admin@shivazen.com', 'admin123', nome='Admin demo',
                                  papel=U.PAPEL_ADMIN, ativo=False)
        self._run(ADMIN_EMAIL='admin@shivazen.com', ADMIN_PASSWORD=SENHA_FORTE)
        u.refresh_from_db()
        self.assertTrue(u.ativo)
        self.assertFalse(u.check_password('admin123'))
        self.assertTrue(u.check_password(SENHA_FORTE))

    def test_promove_usuario_existente_a_admin(self):
        U = get_user_model()
        U.objects.create_user('ana@clinica.com.br', SENHA_FORTE, nome='Ana',
                              papel=U.PAPEL_PROFISSIONAL)
        self._run(ADMIN_EMAIL='ana@clinica.com.br', ADMIN_PASSWORD=SENHA_FORTE)
        self.assertEqual(U.objects.get(email='ana@clinica.com.br').papel, U.PAPEL_ADMIN)

    def test_senha_fraca_nao_cria_e_nao_quebra_o_predeploy(self):
        U = get_user_model()
        _, err = self._run(ADMIN_EMAIL='dona@clinica.com.br', ADMIN_PASSWORD='123')
        self.assertIn('recusada', err)
        self.assertFalse(U.objects.filter(email='dona@clinica.com.br').exists())


# ─── migrate_atomico ─────────────────────────────────────────────────
class MigrateAtomicoTests(TestCase):
    def test_fora_do_postgres_cai_no_migrate_normal(self):
        out = StringIO()
        call_command('migrate_atomico', '--noinput', verbosity=1, stdout=out)
        self.assertIn('migrate normal', out.getvalue())

    def test_postgres_checa_constraints_apos_cada_migration(self):
        from aranha_estetica.management.commands import migrate_atomico as mod

        cmd = mod.Command(stdout=StringIO())
        cmd.verbosity = 0
        cmd._atomico = True
        with mock.patch.object(mod, 'connections') as conns:
            cursor = conns.__getitem__.return_value.cursor.return_value.__enter__.return_value
            cmd.migration_progress_callback('apply_success', migration=mock.Mock(), fake=False)
        executados = [c.args[0] for c in cursor.execute.call_args_list]
        self.assertEqual(executados, ['SET CONSTRAINTS ALL IMMEDIATE', 'SET CONSTRAINTS ALL DEFERRED'])

    def test_postgres_roda_migrate_numa_transacao(self):
        from aranha_estetica.management.commands import migrate_atomico as mod

        fake_conn = mock.Mock(vendor='postgresql')
        with mock.patch.object(mod, 'connections', {'default': fake_conn}), \
                mock.patch.object(mod.transaction, 'atomic') as atomic, \
                mock.patch.object(mod.MigrateCommand, 'handle', return_value=None) as handle:
            mod.Command(stdout=StringIO()).handle(database='default', verbosity=0)
        atomic.assert_called_once_with(using='default')
        handle.assert_called_once()


# ─── Retencao / housekeeping ─────────────────────────────────────────
class HousekeepingTests(TestCase):
    def test_purga_otp_notificacao_e_auditoria_antigos(self):
        from aranha_estetica.models import CodigoOtp, LogAuditoria, Notificacao
        from aranha_estetica.tasks_manutencao import job_housekeeping
        from aranha_estetica.tests.factories import (
            criar_atendimento, criar_cliente, criar_procedimento, criar_profissional,
        )

        agora = timezone.now()
        _, velho = CodigoOtp.gerar('a@x.com')
        _, novo = CodigoOtp.gerar('b@x.com')
        CodigoOtp.objects.filter(pk=velho.pk).update(criado_em=agora - timedelta(hours=25))

        at = criar_atendimento(criar_cliente(), criar_profissional(), criar_procedimento())
        n_velha = Notificacao.objects.create(atendimento=at, mensagem='Oi Maria', token='t-velha')
        n_nova = Notificacao.objects.create(atendimento=at, mensagem='Oi Maria', token='t-nova')
        Notificacao.objects.filter(pk=n_velha.pk).update(criado_em=agora - timedelta(days=400))

        log_velho = LogAuditoria.objects.create(acao='x')
        log_novo = LogAuditoria.objects.create(acao='y')
        LogAuditoria.objects.filter(pk=log_velho.pk).update(criado_em=agora - timedelta(days=365 * 5 + 2))

        resumo = job_housekeeping.apply().get()

        self.assertFalse(CodigoOtp.objects.filter(pk=velho.pk).exists())
        self.assertTrue(CodigoOtp.objects.filter(pk=novo.pk).exists())
        n_velha.refresh_from_db()
        n_nova.refresh_from_db()
        self.assertEqual(n_velha.mensagem, '')
        self.assertEqual(n_nova.mensagem, 'Oi Maria')
        self.assertFalse(LogAuditoria.objects.filter(pk=log_velho.pk).exists())
        self.assertTrue(LogAuditoria.objects.filter(pk=log_novo.pk).exists())
        self.assertEqual(resumo['otp'], 1)

    def test_carregar_feriados(self):
        from aranha_estetica.models import Feriado
        from aranha_estetica.tasks_manutencao import job_carregar_feriados

        self.assertIn('Feriados', job_carregar_feriados.apply().get())
        ano = timezone.now().year
        self.assertTrue(Feriado.objects.filter(data__year=ano + 1).exists())


# ─── Rate-limit por visitante atras do proxy ─────────────────────────
@override_settings(CLIENT_IP_HEADER='HTTP_X_REAL_IP', RATELIMIT_ENABLE=True)
class RateLimitPorVisitanteTests(TestCase):
    """Railway: todos chegam com o REMOTE_ADDR do proxy. O limite tem de ser por
    X-Real-IP, senao um visitante esgota o balde e bloqueia o site inteiro."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def test_limite_estourado_so_para_o_visitante_e_responde_429(self):
        from django.urls import reverse

        url = reverse('aranha:api_horarios_disponiveis')
        proxy = {'REMOTE_ADDR': '100.64.0.2'}
        status = None
        for _ in range(200):
            status = self.client.get(url, HTTP_X_REAL_IP='200.1.1.1', **proxy).status_code
            if status == 429:
                break
        self.assertEqual(status, 429)  # RatelimitMiddleware -> RATELIMIT_VIEW
        outro = self.client.get(url, HTTP_X_REAL_IP='200.2.2.2', **proxy)
        self.assertNotEqual(outro.status_code, 429)


# ─── Settings ────────────────────────────────────────────────────────
class SettingsBaseTests(TestCase):
    def test_site_so_pt_br(self):
        self.assertNotIn('django.middleware.locale.LocaleMiddleware', settings.MIDDLEWARE)
        self.assertEqual([c for c, _ in settings.LANGUAGES], ['pt-br'])
        r = self.client.get('/healthz/', HTTP_ACCEPT_LANGUAGE='en-US,en;q=0.9')
        self.assertNotEqual(r.headers.get('Content-Language'), 'en')

    def test_ratelimit_e_axes_usam_client_ip(self):
        self.assertEqual(settings.RATELIMIT_IP_META_KEY, 'aranha_estetica.utils.security.client_ip')
        self.assertEqual(settings.AXES_CLIENT_IP_CALLABLE, 'aranha_estetica.utils.security.client_ip')

    def test_sem_staticfiles_dirs_duplicado_e_src_fora_da_coleta(self):
        from django.apps import apps
        self.assertFalse(getattr(settings, 'STATICFILES_DIRS', []))
        self.assertIn('src', apps.get_app_config('staticfiles').ignore_patterns)

    def test_email_timeout(self):
        self.assertTrue(settings.EMAIL_TIMEOUT)


class SettingsProdTests(SimpleTestCase):
    """Importa clinica.settings.prod num processo limpo (DEBUG=True na env)."""

    def test_prod_coerente_mesmo_com_debug_na_env(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ('REDIS_URL', 'CELERY_WORKER_ENABLED', 'CLIENT_IP_HEADER', 'SMS_DEV_LOG_ONLY')}
        env.update({'DJANGO_ENV': 'prod', 'DEBUG': 'True', 'DJANGO_SECRET_KEY': 'x' * 50,
                    'STATIC_ROOT': '/tmp/legado', 'ALLOWED_HOSTS': 'exemplo.com.br'})
        codigo = (
            'import json, os\n'
            'import clinica.settings.prod as s\n'
            'print(json.dumps({'
            '"debug": s.DEBUG, "vite_dev": s.DJANGO_VITE["default"]["dev_mode"],'
            '"cookie_2fa": s.TWO_FACTOR_REMEMBER_COOKIE_SECURE, "axes_verbose": s.AXES_VERBOSE,'
            '"loader_cached": "cached" in str(s.TEMPLATES[0]["OPTIONS"]["loaders"]),'
            '"static": s.STORAGES["staticfiles"]["BACKEND"], "static_root": str(s.STATIC_ROOT),'
            '"hosts": s.ALLOWED_HOSTS, "ip_header": s.CLIENT_IP_HEADER,'
            '"email": s.EMAIL_BACKEND, "email_env": os.environ.get("EMAIL_BACKEND"),'
            '"eager": s.CELERY_TASK_ALWAYS_EAGER, "annot": s.CELERY_TASK_ANNOTATIONS,'
            '"sess_save": s.SESSION_SAVE_EVERY_REQUEST, "sess_age": s.SESSION_COOKIE_AGE,'
            '"sms_exigir": s.SMS_EXIGIR_PROVEDOR, "langs": [c for c, _ in s.LANGUAGES]}))\n'
        )
        proc = subprocess.run(
            [sys.executable, '-W', 'ignore', '-c', codigo], cwd=BASE_DIR, env=env,
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        s = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertFalse(s['debug'])
        self.assertFalse(s['vite_dev'])
        self.assertTrue(s['cookie_2fa'])
        self.assertFalse(s['axes_verbose'])
        self.assertTrue(s['loader_cached'])
        self.assertEqual(s['static'], 'whitenoise.storage.CompressedManifestStaticFilesStorage')
        self.assertEqual(Path(s['static_root']), BASE_DIR / 'staticfiles')
        self.assertIn('healthcheck.railway.app', s['hosts'])
        self.assertEqual(s['ip_header'], 'HTTP_X_REAL_IP')
        self.assertEqual(s['email'], s['email_env'] or 'django.core.mail.backends.dummy.EmailBackend')
        self.assertTrue(s['eager'])
        self.assertEqual(s['annot'], {'*': {'max_retries': 0}})
        self.assertTrue(s['sess_save'])
        self.assertEqual(s['sess_age'], 8 * 3600)
        self.assertTrue(s['sms_exigir'])
        self.assertEqual(s['langs'], ['pt-br'])
