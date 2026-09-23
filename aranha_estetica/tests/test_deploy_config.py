"""Regressao dos achados de deploy/infra (P1): cron, healthcheck, system checks,
bootstrap_admin, migrate_atomico, jobs de retencao e coerencia dos settings."""
import json
import os
import subprocess
import sys
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest import mock, skipIf, skipUnless

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
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

    # rev_infra-03: ?celery=1 publico pingava um broker inexistente (prod sem
    # worker) e prendia uma das 4 threads ~6 s por chamada anonima
    def test_celery_ping_ignorado_sem_worker(self):
        with mock.patch('celery.app.control.Control.ping') as ping:
            normal = self.client.get('/health/')
            r = self.client.get('/health/?celery=1')
        ping.assert_not_called()
        self.assertEqual(r.status_code, normal.status_code)
        self.assertNotIn('celery', r.json())

    @override_settings(CELERY_WORKER_ENABLED=True)
    @mock.patch.dict(os.environ, {'CRON_TOKEN': 'segredo-cron'})
    def test_celery_ping_com_worker_exige_token(self):
        from aranha_estetica.views import health

        with mock.patch.object(health, '_ping_celery', return_value=True) as ping:
            anonimo = self.client.get('/health/?celery=1')
            errado = self.client.get('/health/?celery=1', HTTP_X_CRON_TOKEN='x')
            ping.assert_not_called()
            ok = self.client.get('/health/?celery=1', HTTP_X_CRON_TOKEN='segredo-cron')
        ping.assert_called_once()
        self.assertNotIn('celery', anonimo.json())
        self.assertNotIn('celery', errado.json())
        self.assertTrue(ok.json()['celery'])

    def test_ping_celery_limita_a_conexao_ao_broker(self):
        from aranha_estetica.views import health

        with mock.patch('celery.current_app') as app:
            conn = app.connection_for_write.return_value.__enter__.return_value
            app.control.ping.return_value = [{'w1': {'ok': 'pong'}}]
            self.assertTrue(health._ping_celery())
        conn.ensure_connection.assert_called_once_with(
            max_retries=1, interval_start=0, interval_step=0, timeout=1)
        app.control.ping.assert_called_once_with(timeout=1, connection=conn)


# ─── System checks de producao ───────────────────────────────────────
_ENV_PROD_OK = {
    'ZENVIA_API_TOKEN': 'tok', 'ZENVIA_FROM': 'clinica',
    'WHATSAPP_NUMERO': '5517991234567', 'CRON_TOKEN': 'c', 'SMS_DEV_LOG_ONLY': '',
    'CLINIC_EMAIL': 'contato@clinica.com.br',
    'TURNSTILE_SECRET_KEY': 'ts-secret', 'TURNSTILE_SITE_KEY': 'ts-site',
}
_PROD_OK = dict(
    DEBUG=False, SITE_URL='https://jaquelinearanha.com.br', SMS_DEV_LOG_ONLY=False,
    EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
)


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
                        CRON_TOKEN='', SMS_DEV_LOG_ONLY='', CLINIC_EMAIL='')
        self.assertEqual(ids, ['aranha.W001', 'aranha.W002', 'aranha.W003', 'aranha.W004',
                               'aranha.W005', 'aranha.W007'])

    @override_settings(**_PROD_OK)
    def test_email_filebased_ou_locmem_nao_entrega(self):
        # filebased grava no disco efemero do Railway: nada chega (mesma regra
        # do runtime, utils/email). locmem so existe no runner: so aviso de config.
        from aranha_estetica.utils import email as email_utils

        for backend in ('filebased', 'locmem', 'console', 'dummy'):
            with self.subTest(backend=backend), \
                    override_settings(EMAIL_BACKEND=f'django.core.mail.backends.{backend}.EmailBackend'):
                self.assertEqual(self._ids(**_ENV_PROD_OK), ['aranha.W003'])
        with override_settings(EMAIL_BACKEND='django.core.mail.backends.filebased.EmailBackend'):
            self.assertFalse(email_utils.email_configurado())

    @override_settings(**_PROD_OK, ADMIN_2FA_OBRIGATORIO=False)
    def test_2fa_de_admin_desligado_em_prod_avisa(self):
        self.assertEqual(self._ids(**_ENV_PROD_OK), ['aranha.W008'])

    @override_settings(**_PROD_OK, ADMIN_2FA_OBRIGATORIO=True)
    def test_2fa_de_admin_ligado_nao_avisa(self):
        self.assertEqual(self._ids(**_ENV_PROD_OK), [])

    @override_settings(**_PROD_OK)
    def test_sem_banco_liberado_nao_confere_admin(self):
        # `check` puro (build do Docker) nao toca no banco: W006 so com databases
        self.assertNotIn('aranha.W006', self._ids(**_ENV_PROD_OK))

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

    @override_settings(**_PROD_OK)
    def test_whatsapp_que_o_site_descarta_avisa(self):
        # followups-check-w004 / rev_infra-07: '123' ou numero sem DDD tem digito,
        # mas normalizar_whatsapp devolve '' e o site esconde todos os botoes
        for numero in ('123', '991234567', '(17) 3'):
            with self.subTest(numero=numero):
                self.assertEqual(self._ids(**{**_ENV_PROD_OK, 'WHATSAPP_NUMERO': numero}),
                                 ['aranha.W004'])
        # DDD + numero (sem 55) o runtime completa: nao avisa
        self.assertEqual(self._ids(**{**_ENV_PROD_OK, 'WHATSAPP_NUMERO': '(17) 99123-4567'}), [])

    @override_settings(**_PROD_OK)
    def test_sms_real_sem_turnstile_avisa(self):
        # rev_security-05: OTP obrigatorio em todo booking + cota global de SMS
        # -> sem captcha poucos IPs travam o agendamento online
        sem = {**_ENV_PROD_OK, 'TURNSTILE_SECRET_KEY': '', 'TURNSTILE_SITE_KEY': ''}
        self.assertEqual(self._ids(**sem), ['aranha.W009'])
        self.assertEqual(self._ids(**{**sem, 'TURNSTILE_SECRET_KEY': 'ts'}), ['aranha.W009'])
        self.assertEqual(self._ids(**_ENV_PROD_OK), [])
        # sem provedor de SMS o aviso e o W002 (nao duplica)
        self.assertEqual(self._ids(**{**sem, 'ZENVIA_API_TOKEN': ''}), ['aranha.W002'])

    def test_w009_silenciado_no_runner(self):
        self.assertIn('aranha.W009', settings.SILENCED_SYSTEM_CHECKS)


@override_settings(**_PROD_OK)
class ChecksComBancoTests(TestCase):
    def _ids(self, databases=('default',), **env):
        with mock.patch.dict(os.environ, {**_ENV_PROD_OK, **env}):
            return sorted(e.id for e in aranha_checks.check_config_producao(
                databases=list(databases) if databases else None))

    def _admin(self, email='dona@clinica.com.br', **kw):
        U = get_user_model()
        dados = {'nome': 'Dona', 'papel': U.PAPEL_ADMIN, 'ativo': True}
        dados.update(kw)
        return U.objects.create_user(email, SENHA_FORTE, **dados)

    def test_whatsapp_da_tela_branding_conta_quando_banco_liberado(self):
        from aranha_estetica.models import Configuracao

        self._admin()
        sem_banco = self._ids(databases=None, WHATSAPP_NUMERO='')
        Configuracao.objects.create(chave='WHATSAPP_NUMERO', valor='5517991234567')
        com_banco = self._ids(WHATSAPP_NUMERO='')
        self.assertEqual(sem_banco, ['aranha.W004'])
        self.assertEqual(com_banco, [])

    def test_tela_branding_vence_a_env_como_no_runtime(self):
        # get_branding: `db.get(chave) or env` — com numero valido na env e '123'
        # no Branding o site esconde os botoes; o check tem de avisar tambem
        from aranha_estetica.models import Configuracao
        from aranha_estetica.utils.branding import get_branding, invalidar_cache

        self._admin()
        self.assertEqual(self._ids(), [])
        Configuracao.objects.create(chave='WHATSAPP_NUMERO', valor='123')
        Configuracao.objects.create(chave='CLINIC_EMAIL', valor='sem-arroba')
        invalidar_cache()
        with mock.patch.dict(os.environ, _ENV_PROD_OK):
            self.assertEqual(get_branding()['WHATSAPP_NUMERO'], '')
        invalidar_cache()
        self.assertEqual(self._ids(), ['aranha.W004', 'aranha.W007'])
        # sem o banco liberado (build/`check` puro) so a env conta
        self.assertEqual(self._ids(databases=None), [])

    def test_clinic_email_da_tela_branding_conta_quando_banco_liberado(self):
        from aranha_estetica.models import Configuracao

        self._admin()
        self.assertEqual(self._ids(CLINIC_EMAIL=''), ['aranha.W007'])
        Configuracao.objects.create(chave='CLINIC_EMAIL', valor='contato@clinica.com.br')
        self.assertEqual(self._ids(CLINIC_EMAIL=''), [])

    def test_painel_sem_admin_utilizavel_avisa(self):
        # conta demo desligada pela 0042: inativa e com senha inutilizavel
        U = get_user_model()
        demo = self._admin('admin@shivazen.com', ativo=False)
        demo.set_unusable_password()
        demo.save()
        U.objects.create_user('ana@clinica.com.br', SENHA_FORTE, nome='Ana',
                              papel=U.PAPEL_PROFISSIONAL)
        self.assertEqual(self._ids(), ['aranha.W006'])

        sem_senha = self._admin('outra@clinica.com.br')
        sem_senha.set_unusable_password()
        sem_senha.save()
        self.assertEqual(self._ids(), ['aranha.W006'])

        self._admin('dona@clinica.com.br')
        self.assertEqual(self._ids(), [])


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

    def test_sem_env_e_sem_admin_utilizavel_avisa_no_stderr(self):
        # prod em 0026 -> a 0042 desliga admin@shivazen.com/admin123 -> sem env o
        # painel ficava sem ninguem e o log do deploy nao dizia nada
        U = get_user_model()
        demo = U.objects.create_user('admin@shivazen.com', 'admin123', nome='Admin demo',
                                     papel=U.PAPEL_ADMIN)
        demo.ativo = False
        demo.set_unusable_password()
        demo.save()
        out, err = self._run()
        self.assertIn('nada a fazer', out)
        self.assertIn('sem administrador', err)

    def test_sem_env_com_admin_ok_nao_avisa(self):
        U = get_user_model()
        U.objects.create_user('dona@clinica.com.br', SENHA_FORTE, nome='Dona', papel=U.PAPEL_ADMIN)
        _, err = self._run()
        self.assertEqual(err, '')

    def test_senha_recusada_sem_outro_admin_tambem_avisa(self):
        _, err = self._run(ADMIN_EMAIL='dona@clinica.com.br', ADMIN_PASSWORD='123')
        self.assertIn('recusada', err)
        self.assertIn('sem administrador', err)

    def test_admin_criado_nao_gera_erro(self):
        _, err = self._run(ADMIN_EMAIL='dona@clinica.com.br', ADMIN_PASSWORD=SENHA_FORTE)
        self.assertEqual(err, '')

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

    # rev_infra-04: desativacao/rebaixamento feito no painel nao e desfeito no deploy
    def _outro_admin(self):
        U = get_user_model()
        return U.objects.create_user('socia@clinica.com.br', SENHA_FORTE, nome='Socia',
                                     papel=U.PAPEL_ADMIN)

    def test_conta_desativada_no_painel_com_outro_admin_fica_desativada(self):
        U = get_user_model()
        self._outro_admin()
        u = U.objects.create_user('dona@clinica.com.br', 'Senha-do-Painel#1', nome='Dona',
                                  papel=U.PAPEL_ADMIN)
        u.ativo = False
        u.save()
        _, err = self._run(ADMIN_EMAIL='dona@clinica.com.br', ADMIN_PASSWORD=SENHA_FORTE)
        u.refresh_from_db()
        self.assertFalse(u.ativo)
        self.assertTrue(u.check_password('Senha-do-Painel#1'))
        self.assertIn('mantido', err)
        self.assertNotIn('sem administrador', err)

    def test_conta_rebaixada_no_painel_so_volta_a_admin_com_reset(self):
        U = get_user_model()
        self._outro_admin()
        u = U.objects.create_user('dona@clinica.com.br', SENHA_FORTE, nome='Dona',
                                  papel=U.PAPEL_RECEPCAO)
        _, err = self._run(ADMIN_EMAIL='dona@clinica.com.br', ADMIN_PASSWORD=SENHA_FORTE)
        u.refresh_from_db()
        self.assertEqual(u.papel, U.PAPEL_RECEPCAO)
        self.assertIn('mantido', err)
        self._run(ADMIN_EMAIL='dona@clinica.com.br', ADMIN_PASSWORD=SENHA_FORTE,
                  ADMIN_PASSWORD_RESET='true')
        u.refresh_from_db()
        self.assertEqual(u.papel, U.PAPEL_ADMIN)

    # pgupgrade-04: login compara o e-mail exato; o painel grava em minusculas
    def test_email_criado_em_minusculas(self):
        U = get_user_model()
        self._run(ADMIN_EMAIL=' Dona@Clinica.COM ', ADMIN_PASSWORD=SENHA_FORTE)
        u = U.objects.get(email__iexact='dona@clinica.com')
        self.assertEqual(u.email, 'dona@clinica.com')
        self.assertEqual(U.objects.get_by_natural_key('dona@clinica.com'), u)

    # pgupgrade-03: 2FA cadastrado com a senha publica da conta demo nao sobrevive
    def _devices_2fa(self, user):
        from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
        from django_otp.plugins.otp_totp.models import TOTPDevice

        TOTPDevice.objects.create(user=user, name='atacante', confirmed=True)
        static = StaticDevice.objects.create(user=user, name='backup-atk', confirmed=True)
        StaticToken.objects.create(device=static, token='atk00001')

    def test_reativacao_remove_2fa_antigo(self):
        from aranha_estetica.models import LogAuditoria
        from aranha_estetica.utils import dois_fatores

        U = get_user_model()
        demo = U.objects.create_user('admin@shivazen.com', 'admin123', nome='Admin demo',
                                     papel=U.PAPEL_ADMIN)
        self._devices_2fa(demo)
        demo.ativo = False  # estado deixado pela 0042
        demo.set_unusable_password()
        demo.save()
        out, _ = self._run(ADMIN_EMAIL='admin@shivazen.com', ADMIN_PASSWORD=SENHA_FORTE)
        demo.refresh_from_db()
        self.assertTrue(demo.ativo)
        self.assertFalse(dois_fatores.tem_2fa(demo))
        self.assertIsNone(dois_fatores.verificar_token(demo, 'atk00001'))
        self.assertIn('2FA antigo', out)
        self.assertTrue(LogAuditoria.objects.filter(
            acao__icontains='2FA removido', registro_id=demo.pk).exists())

    def test_reset_com_conta_ativa_mantem_2fa(self):
        from aranha_estetica.utils import dois_fatores

        U = get_user_model()
        u = U.objects.create_user('dona@clinica.com.br', 'Outra-Senha#2026', nome='Dona',
                                  papel=U.PAPEL_ADMIN)
        self._devices_2fa(u)  # aqui: 2FA legitimo da dona
        self._run(ADMIN_EMAIL='dona@clinica.com.br', ADMIN_PASSWORD=SENHA_FORTE,
                  ADMIN_PASSWORD_RESET='true')
        u.refresh_from_db()
        self.assertTrue(u.check_password(SENHA_FORTE))
        self.assertTrue(dois_fatores.tem_2fa(u))


# ─── setup_2fa --force (recuperacao) ─────────────────────────────────
class Setup2FAResetTests(TestCase):
    def test_force_remove_todos_os_devices_do_usuario(self):
        # pgupgrade-03: --force so apagava o device de mesmo nome; o TOTP/backup
        # plantado com outro nome seguia valendo apos a "recuperacao"
        from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
        from django_otp.plugins.otp_totp.models import TOTPDevice

        from aranha_estetica.models import LogAuditoria
        from aranha_estetica.utils import dois_fatores

        U = get_user_model()
        u = U.objects.create_user('dona@clinica.com.br', SENHA_FORTE, nome='Dona',
                                  papel=U.PAPEL_ADMIN)
        TOTPDevice.objects.create(user=u, name='atacante', confirmed=True)
        static = StaticDevice.objects.create(user=u, name='backup-atk', confirmed=True)
        StaticToken.objects.create(device=static, token='atk00001')
        outro = U.objects.create_user('socia@clinica.com.br', SENHA_FORTE, nome='Socia',
                                      papel=U.PAPEL_ADMIN)
        TOTPDevice.objects.create(user=outro, name='default', confirmed=True)

        call_command('setup_2fa', 'dona@clinica.com.br', '--force', stdout=StringIO())

        self.assertEqual(list(TOTPDevice.objects.filter(user=u).values_list('name', flat=True)),
                         ['default'])
        self.assertEqual(list(StaticDevice.objects.filter(user=u).values_list('name', flat=True)),
                         ['backup'])
        self.assertIsNone(dois_fatores.verificar_token(u, 'atk00001'))
        self.assertTrue(TOTPDevice.objects.filter(user=outro).exists())  # so o alvo
        self.assertTrue(LogAuditoria.objects.filter(
            acao__icontains='setup_2fa', registro_id=u.pk).exists())


# ─── migrate_atomico ─────────────────────────────────────────────────
class MigrateAtomicoTests(TestCase):
    @skipIf(connection.vendor == 'postgresql', 'no Postgres o migrate_atomico entra no ramo atomico')
    def test_fora_do_postgres_cai_no_migrate_normal(self):
        out = StringIO()
        call_command('migrate_atomico', '--noinput', verbosity=1, stdout=out)
        self.assertIn('migrate normal', out.getvalue())

    @skipUnless(connection.vendor == 'postgresql', 'ramo atomico so existe no Postgres')
    def test_no_postgres_nao_cai_no_migrate_normal(self):
        out = StringIO()
        call_command('migrate_atomico', '--noinput', verbosity=1, stdout=out)
        self.assertNotIn('migrate normal', out.getvalue())
        # SET LOCAL vale ate o fim da transacao (aqui: a do proprio teste)
        with connection.cursor() as cursor:
            cursor.execute('SHOW lock_timeout')
            self.assertEqual(cursor.fetchone()[0], '5s')

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

        fake_conn = mock.MagicMock(vendor='postgresql')
        with mock.patch.object(mod, 'connections', {'default': fake_conn}), \
                mock.patch.object(mod.transaction, 'atomic') as atomic, \
                mock.patch.object(mod.MigrateCommand, 'handle', return_value=None) as handle:
            mod.Command(stdout=StringIO()).handle(database='default', verbosity=0)
        atomic.assert_called_once_with(using='default')
        handle.assert_called_once()
        # rev_infra-06: lock do deploy antigo nao pode deixar o upgrade esperando
        # sem fim com as tabelas ja alteradas travadas
        cursor = fake_conn.cursor.return_value.__enter__.return_value
        cursor.execute.assert_any_call("SET LOCAL lock_timeout = '5s'")


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

    def _importar_prod(self, **env_extra):
        env = {k: v for k, v in os.environ.items()
               if k not in ('ADMIN_2FA_OBRIGATORIO', 'EMBED_FRAME_ANCESTORS')}
        env.update({'DJANGO_ENV': 'prod', 'DJANGO_SECRET_KEY': 'x' * 50, **env_extra})
        codigo = (
            'import json\n'
            'import clinica.settings.prod as s\n'
            'print(json.dumps({"tem_2fa": hasattr(s, "ADMIN_2FA_OBRIGATORIO"),'
            '"obrig": getattr(s, "ADMIN_2FA_OBRIGATORIO", None),'
            '"frames": s.EMBED_FRAME_ANCESTORS}))\n'
        )
        proc = subprocess.run(
            [sys.executable, '-W', 'ignore', '-c', codigo], cwd=BASE_DIR, env=env,
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_2fa_e_frame_ancestors_por_env(self):
        # sem env: o codigo decide em runtime (2FA ligado fora de DEBUG)
        self.assertEqual(self._importar_prod(), {'tem_2fa': False, 'obrig': None, 'frames': ''})
        s = self._importar_prod(ADMIN_2FA_OBRIGATORIO='false',
                                EMBED_FRAME_ANCESTORS=' https://linktr.ee ')
        self.assertEqual(s, {'tem_2fa': True, 'obrig': False, 'frames': 'https://linktr.ee'})
        self.assertTrue(self._importar_prod(ADMIN_2FA_OBRIGATORIO='True')['obrig'])


class DjangoEnvInvalidoTests(SimpleTestCase):
    """rev_infra-01: DJANGO_ENV fora de dev|prod caia no dev.py (DEBUG=True,
    e-mail no console, SMS/WhatsApp "fingindo" envio) mesmo no Railway."""

    def _importar(self, valor):
        env = {k: v for k, v in os.environ.items() if k != 'DEBUG'}
        env.update({'DJANGO_ENV': valor, 'DJANGO_SECRET_KEY': 'x' * 50,
                    'RAILWAY_ENVIRONMENT_NAME': 'production'})
        return subprocess.run(
            [sys.executable, '-W', 'ignore', '-c', 'import clinica.settings as s; print(s.DEBUG)'],
            cwd=BASE_DIR, env=env, capture_output=True, text=True, timeout=120,
        )

    def test_valor_desconhecido_derruba_o_boot(self):
        for valor in ('production', 'staging'):
            with self.subTest(valor=valor):
                proc = self._importar(valor)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn('ImproperlyConfigured', proc.stderr)
                self.assertIn(valor, proc.stderr)

    def test_dev_e_prod_continuam_valendo(self):
        for valor, debug in (('prod', 'False'), (' PROD ', 'False'), ('dev', 'True')):
            with self.subTest(valor=valor):
                proc = self._importar(valor)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout.strip().splitlines()[-1], debug)


class Settings2FAEApiTests(TestCase):
    def test_admin_do_django_otp_esconde_semente_e_qr(self):
        # rev_security-09: config/qrcode do TOTPDeviceAdmin davam 200 p/ qualquer ADMIN
        from django_otp.conf import settings as otp_settings
        self.assertIs(settings.OTP_ADMIN_HIDE_SENSITIVE_DATA, True)
        self.assertIs(otp_settings.OTP_ADMIN_HIDE_SENSITIVE_DATA, True)

    def test_2fa_de_profissional_opt_in_por_env(self):
        # rev_security-14: setting lido por utils/dois_fatores (default desligado)
        self.assertIs(settings.PROFISSIONAL_2FA_OBRIGATORIO, False)

    def test_api_fechada_por_padrao_e_raiz_do_router_so_p_staff(self):
        # crawl-7: /api/v1/ (DefaultRouter) herdava IsAuthenticated e listava os
        # endpoints p/ PROFISSIONAL
        from aranha_estetica.models import Profissional

        self.assertEqual(settings.REST_FRAMEWORK['DEFAULT_PERMISSION_CLASSES'],
                         ['rest_framework.permissions.IsAdminUser'])
        U = get_user_model()
        prof = Profissional.objects.create(nome='Dra. Portal')
        user = U.objects.create_user('prof@clinica.com.br', SENHA_FORTE, nome='Dra. Portal',
                                     papel=U.PAPEL_PROFISSIONAL, profissional=prof)
        self.client.force_login(user)
        for url in ('/api/v1/', '/api/v1/.json'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)


class SettingsSwaggerTests(SimpleTestCase):
    def test_swagger_ui_em_versao_fixa(self):
        # @latest mudaria a UI (e o JS carregado com a CSP) sem deploy
        for chave in ('SWAGGER_UI_DIST', 'SWAGGER_UI_FAVICON_HREF'):
            url = settings.SPECTACULAR_SETTINGS[chave]
            self.assertRegex(url, r'^https://cdn\.jsdelivr\.net/npm/swagger-ui-dist@\d+\.\d+\.\d+(/|$)')


# ─── Bloqueio do django-axes ─────────────────────────────────────────
@override_settings(RATELIMIT_ENABLE=False, AXES_FAILURE_LIMIT=2)
class AxesBloqueioTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def test_bloqueio_mostra_pagina_pt_br_com_429(self):
        U = get_user_model()
        U.objects.create_user('dona@clinica.com.br', SENHA_FORTE, nome='Dona', papel=U.PAPEL_ADMIN)
        dados = {'username': 'dona@clinica.com.br', 'password': 'senha-errada-123'}
        r = None
        for _ in range(2):
            r = self.client.post('/admin-login/', dados)
        self.assertContains(r, 'Acesso bloqueado', status_code=429)
        self.assertNotContains(r, 'Account locked', status_code=429)


# ─── Access log do gunicorn sem tokens ───────────────────────────────
class GunicornLoggerTests(SimpleTestCase):
    """rev_security-13 / rev_infra-02: o access log padrao gravava /reagendar/<token>/,
    reset de senha, ?token= do feed ICS e o Referer em claro nos logs do Railway."""

    def test_start_do_docker_e_do_railway_usam_o_logger(self):
        classe = '--logger-class clinica.gunicorn_logger.LoggerSemTokens'
        dockerfile = (BASE_DIR / 'Dockerfile').read_text(encoding='utf-8')
        cmd = [linha for linha in dockerfile.splitlines() if linha.startswith('CMD ')]
        self.assertEqual(len(cmd), 1)
        self.assertIn(classe, cmd[0])
        railway = json.loads((BASE_DIR / 'railway.json').read_text(encoding='utf-8'))
        self.assertIn(classe, railway['deploy']['startCommand'])  # sobrescreve o CMD

    @skipIf(sys.platform == 'win32', 'gunicorn importa fcntl/pwd (so Linux/macOS)')
    def test_access_log_redige_tokens(self):
        import datetime

        from gunicorn.config import Config

        from clinica.gunicorn_logger import LoggerSemTokens

        cfg = Config()
        cfg.set('accesslog', '-')
        logger = LoggerSemTokens(cfg)
        saida = StringIO()
        for handler in logger.access_log.handlers:
            handler.stream = saida
        resp = mock.Mock(status='200 OK', sent=10, headers=[])
        req = mock.Mock(headers=[('REFERER', 'https://x.com/termo/zzz999/')])

        def environ(path, qs=''):
            return {
                'REQUEST_METHOD': 'GET', 'RAW_URI': path + (f'?{qs}' if qs else ''),
                'SERVER_PROTOCOL': 'HTTP/1.1', 'PATH_INFO': path, 'QUERY_STRING': qs,
                'HTTP_REFERER': 'https://x.com/termo/zzz999/', 'REMOTE_ADDR': '1.2.3.4',
            }

        dt = datetime.timedelta(milliseconds=1)
        logger.access(resp, req, environ('/reagendar/abc123/'), dt)
        logger.access(resp, req, environ('/agenda/dra-x/feed.ics', 'token=FEED777&x=1'), dt)
        logger.access(resp, req, environ('/admin-login/recuperar/MQ/cabc12-deadbeef/'), dt)
        logger.access(resp, req, environ('/anamnese/obrigado/'), dt)
        log = saida.getvalue()
        for segredo in ('abc123', 'zzz999', 'FEED777', 'deadbeef'):
            self.assertNotIn(segredo, log)
        self.assertIn('/reagendar/[token]/', log)
        self.assertIn('token=[token]&x=1', log)
        self.assertIn('/anamnese/obrigado/', log)


# ─── Lock das dependencias ───────────────────────────────────────────
class LockDependenciasTests(SimpleTestCase):
    """rev_infra-05: transitivas sem pin -> imagem de prod nao reprodutivel."""

    @staticmethod
    def _pins(arquivo):
        import re

        pins = {}
        for linha in (BASE_DIR / arquivo).read_text(encoding='utf-8').splitlines():
            linha = linha.split('#', 1)[0].strip()
            m = re.match(r'^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?==([^\s;]+)$', linha)
            if m:
                pins[re.sub(r'[-_.]+', '-', m.group(1)).lower()] = m.group(3)
        return pins

    def test_lock_espelha_as_versoes_diretas(self):
        diretas = self._pins('requirements.txt')
        lock = self._pins('requirements.lock')
        self.assertGreater(len(lock), len(diretas))  # transitivas inclusas
        for nome, versao in diretas.items():
            with self.subTest(pacote=nome):
                self.assertEqual(lock.get(nome), versao,
                                 f'{nome}: requirements.txt e requirements.lock divergem')
        for transitiva in ('kombu', 'cryptography', 'django-otp', 'qrcode', 'phonenumbers'):
            self.assertIn(transitiva, lock)

    def test_build_e_ci_instalam_pelo_lock(self):
        dockerfile = (BASE_DIR / 'Dockerfile').read_text(encoding='utf-8')
        self.assertIn('-r requirements.txt -c requirements.lock', dockerfile)
        self.assertIn('pip install --no-index', dockerfile)  # runtime so usa os wheels
        ci = (BASE_DIR / '.github' / 'workflows' / 'ci.yml').read_text(encoding='utf-8')
        self.assertEqual(ci.count('pip install -r requirements-dev.txt -c requirements.lock'), 2)
