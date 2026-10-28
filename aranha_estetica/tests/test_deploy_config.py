"""Regressao dos achados de deploy/infra (P1): cron, healthcheck, system checks,
bootstrap_admin, migrate_atomico, jobs de retencao e coerencia dos settings."""
import json
import os
import re
import subprocess
import sys
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest import mock, skipIf, skipUnless

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from aranha_estetica import checks as aranha_checks
from aranha_estetica.views.infra import cron

BASE_DIR = Path(settings.BASE_DIR)
_CHAMADAS = {'n': 0}


def _job_que_falha():
    _CHAMADAS['n'] += 1
    raise ValueError('db down')


def _job_ok():
    return 3


# ─── Cron ────────────────────────────────────────────────────────────
@override_settings(CRON_TOKEN='segredo-cron')
class CronEndpointTests(TestCase):
    url = '/cron/run/{}/'

    def setUp(self):
        from django.core.cache import cache
        cache.clear()  # contador do rate limit do cron (30/h por IP)

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
        self.assertIn('housekeeping', r.json()['disponiveis'])

    def test_job_ok_200(self):
        with mock.patch.dict(cron.JOB_MAP, {'ok': _job_ok}):
            r = self._post('ok')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'ok': True, 'job': 'ok', 'resultado': '3'})

    def test_job_que_falha_500_e_roda_uma_vez(self):
        _CHAMADAS['n'] = 0
        with mock.patch.dict(cron.JOB_MAP, {'falha': _job_que_falha}):
            r = self._post('falha')
        self.assertEqual(r.status_code, 500)
        self.assertFalse(r.json()['ok'])
        self.assertEqual(_CHAMADAS['n'], 1)  # roda uma vez: nada de retry na hora

    @override_settings(CRON_MONITOR_URL='https://hc-ping.com/chave/')
    def test_monitor_recebe_ok_e_fail_e_nunca_muda_a_resposta(self):
        with mock.patch.object(cron.requests, 'post') as post, mock.patch.dict(cron.JOB_MAP, {'ok': _job_ok}):
            self.assertEqual(self._post('ok').status_code, 200)
        post.assert_called_once_with('https://hc-ping.com/chave/ok', timeout=5)
        _CHAMADAS['n'] = 0
        with mock.patch.object(cron.requests, 'post', side_effect=cron.requests.ConnectionError) as post, \
                mock.patch.dict(cron.JOB_MAP, {'falha': _job_que_falha}):
            self.assertEqual(self._post('falha').status_code, 500)       # monitor fora: resposta igual
        post.assert_called_once_with('https://hc-ping.com/chave/falha/fail', timeout=5)

    def test_sem_monitor_configurado_nao_chama_nada(self):
        with mock.patch.object(cron.requests, 'post') as post, mock.patch.dict(cron.JOB_MAP, {'ok': _job_ok}):
            self._post('ok')
        post.assert_not_called()

    def test_jobs_de_manutencao_registrados(self):
        for nome in ('emails', 'housekeeping', 'feriados', 'lgpd_purgar', 'lembrete_diario'):
            self.assertIn(nome, cron.JOB_MAP)


@override_settings(CRON_TOKEN='')
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
    'WHATSAPP_NUMERO': '5517991234567', 'CRON_TOKEN': 'c',
    'CLINIC_EMAIL': 'contato@clinica.com.br',
    'TURNSTILE_SECRET_KEY': 'ts-secret', 'TURNSTILE_SITE_KEY': 'ts-site',
}


def _config(**env):
    """Variaveis de ambiente do caso -> override_settings: settings/base.py le a env no boot (marca em
    MARCA_ENV, o resto em settings de mesmo nome) e os checks leem os settings."""
    marca = {chave: valor for chave, valor in env.items() if chave in settings.MARCA_ENV}
    outros = {chave: valor for chave, valor in env.items() if chave not in marca}
    return override_settings(MARCA_ENV={**settings.MARCA_ENV, **marca}, **outros)
_PROD_OK = dict(
    DEBUG=False, SITE_URL='https://jaquelinearanha.com.br', SMS_DEV_LOG_ONLY=False,
    EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend', PROFISSIONAL_2FA_OBRIGATORIO=True,
)


# Prod liga o 2FA do PROFISSIONAL por padrao; os testes de settings de dev o tem desligado (W012)
@override_settings(PROFISSIONAL_2FA_OBRIGATORIO=True)
class ChecksProducaoTests(SimpleTestCase):
    def _ids(self, **env):
        with _config(**env):
            return sorted(e.id for e in aranha_checks.check_config_producao())

    @override_settings(DEBUG=True)
    def test_mudo_em_debug(self):
        self.assertEqual(self._ids(ZENVIA_API_TOKEN=''), [])

    @override_settings(
        DEBUG=False, SITE_URL='http://127.0.0.1:8000', SMS_DEV_LOG_ONLY=False,
        EMAIL_BACKEND='django.core.mail.backends.dummy.EmailBackend', PROFISSIONAL_2FA_OBRIGATORIO=True,
    )
    def test_config_ausente_gera_warnings(self):
        ids = self._ids(ZENVIA_API_TOKEN='', ZENVIA_FROM='', WHATSAPP_NUMERO='',
                        CRON_TOKEN='', CLINIC_EMAIL='')
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

    def test_retencao_da_trilha_abaixo_do_minimo_do_banco_avisa(self):
        with override_settings(**{**_PROD_OK, 'RETENCAO_LOG_AUDITORIA_DIAS': 90}):
            self.assertEqual(self._ids(**_ENV_PROD_OK), ['aranha.W013'])

    def test_minimo_da_trilha_bate_com_o_trigger(self):
        from aranha_estetica.constants import TRILHA_RETENCAO_MINIMA_DIAS
        from aranha_estetica.migrations.sql import v0059_trilha_append_only as sql

        intervalo = f"interval '{TRILHA_RETENCAO_MINIMA_DIAS} days'"
        self.assertIn(intervalo, sql.FN_LOG_AUDITORIA_IMUTAVEL)
        self.assertIn(intervalo, sql.FN_HISTORICO_ALTERACAO_IMUTAVEL)
        self.assertGreaterEqual(settings.RETENCAO_LOG_AUDITORIA_DIAS, TRILHA_RETENCAO_MINIMA_DIAS)

    def test_2fa_de_profissional_desligado_em_prod_avisa(self):
        with override_settings(**{**_PROD_OK, 'PROFISSIONAL_2FA_OBRIGATORIO': False}):
            self.assertEqual(self._ids(**_ENV_PROD_OK), ['aranha.W012'])

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
        for check_id in ('aranha.W009', 'aranha.W010', 'aranha.W011'):
            self.assertIn(check_id, settings.SILENCED_SYSTEM_CHECKS)

    # ─── e-mail por provedor HTTP (django-anymail; Railway Hobby sem SMTP) ───
    _RESEND = 'anymail.backends.resend.EmailBackend'
    _REMETENTE = 'Jaqueline Aranha <contato@jaquelinearanha.com.br>'

    def _email(self, backend, anymail=None, remetente=_REMETENTE):
        from aranha_estetica.utils import email as email_utils

        with override_settings(EMAIL_BACKEND=backend, ANYMAIL=anymail or {},
                               DEFAULT_FROM_EMAIL=remetente), \
                _config(**_ENV_PROD_OK):
            avisos = [e for e in aranha_checks.check_config_producao()
                      if e.id in ('aranha.W003', 'aranha.W011')]
            return avisos, email_utils.email_configurado()

    @override_settings(**_PROD_OK)
    def test_anymail_com_chave_do_esp_entrega_e_nao_avisa(self):
        from aranha_estetica.utils.email import ANYMAIL_CHAVE_POR_ESP

        for esp, chave in ANYMAIL_CHAVE_POR_ESP.items():
            with self.subTest(esp=esp):
                avisos, configurado = self._email(f'anymail.backends.{esp}.EmailBackend',
                                                  {chave: 'chave-do-esp'})
                self.assertEqual(avisos, [])
                self.assertTrue(configurado)

    @override_settings(**_PROD_OK)
    def test_anymail_sem_a_chave_do_esp_avisa_w011_e_falha_fechado(self):
        from aranha_estetica.utils.email import ANYMAIL_CHAVE_POR_ESP

        for esp, chave in ANYMAIL_CHAVE_POR_ESP.items():
            # chave de OUTRO provedor nao serve
            outra = {'SENDGRID_API_KEY': 'x'} if esp != 'sendgrid' else {'RESEND_API_KEY': 'x'}
            for anymail in ({}, outra, {chave: '  '}):
                with self.subTest(esp=esp, anymail=anymail):
                    avisos, configurado = self._email(f'anymail.backends.{esp}.EmailBackend', anymail)
                    self.assertEqual([a.id for a in avisos], ['aranha.W011'])
                    self.assertIn(chave, avisos[0].msg)
                    self.assertFalse(configurado)

    @override_settings(**_PROD_OK)
    def test_anymail_de_esp_que_os_settings_nao_suportam_avisa(self):
        # os settings so repassam da env as chaves de resend/brevo/sendgrid/mailgun/postmark
        avisos, configurado = self._email('anymail.backends.mailjet.EmailBackend',
                                          {'RESEND_API_KEY': 'x'})
        self.assertEqual([a.id for a in avisos], ['aranha.W011'])
        self.assertIn('nao repassam', avisos[0].msg)
        self.assertFalse(configurado)

    @override_settings(**_PROD_OK)
    def test_anymail_com_remetente_de_exemplo_avisa(self):
        # ESP recusa remetente de dominio nao verificado: nada seria entregue
        for remetente in ('noreply@clinica.com.br', 'Clinica <noreply@clinica.com.br>',
                          'webmaster@localhost', ''):
            with self.subTest(remetente=remetente):
                avisos, _ = self._email(self._RESEND, {'RESEND_API_KEY': 're_x'}, remetente)
                self.assertEqual([a.id for a in avisos], ['aranha.W011'])
                self.assertIn('DEFAULT_FROM_EMAIL', avisos[0].msg)

    @override_settings(**_PROD_OK)
    def test_anymail_console_e_test_nao_entregam(self):
        for backend in ('anymail.backends.console.EmailBackend', 'anymail.backends.test.EmailBackend'):
            with self.subTest(backend=backend):
                avisos, _ = self._email(backend)
                self.assertEqual([a.id for a in avisos], ['aranha.W003'])

    @override_settings(**_PROD_OK)
    def test_w003_sugere_o_anymail(self):
        avisos, configurado = self._email('django.core.mail.backends.dummy.EmailBackend')
        self.assertFalse(configurado)
        self.assertEqual([a.id for a in avisos], ['aranha.W003'])
        self.assertIn('anymail.backends.resend.EmailBackend', avisos[0].hint)
        self.assertIn('RESEND_API_KEY', avisos[0].hint)


@override_settings(**_PROD_OK)
class ChecksComBancoTests(TestCase):
    def setUp(self):
        # W010 fora dos outros casos: LGPD e SAUDE ativos (0045/0047) mesmo se
        # um TransactionTestCase anterior limpou o banco
        from aranha_estetica.models import VersaoTermo

        for tipo in ('LGPD', 'SAUDE'):
            if not VersaoTermo.objects.filter(tipo=tipo, procedimento__isnull=True, ativa=True).exists():
                VersaoTermo.objects.create(tipo=tipo, titulo=tipo, conteudo=f'Texto {tipo}',
                                           versao='1.0', vigente_desde=timezone.localdate())

    def _ids(self, databases=('default',), **env):
        with _config(**{**_ENV_PROD_OK, **env}):
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
        with _config(**_ENV_PROD_OK):
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

    # contrato 3: sem termo LGPD ativo o booking recusa; sem SAUDE o aceite do
    # dado de saude (art. 11) fica sem prova
    def _w010(self):
        with _config(**_ENV_PROD_OK):
            return [e for e in aranha_checks.check_config_producao(databases=['default'])
                    if e.id == 'aranha.W010']

    def test_sem_termo_lgpd_ou_saude_ativo_avisa_w010(self):
        from aranha_estetica.models import VersaoTermo
        from aranha_estetica.tests.factories import criar_procedimento

        self._admin()
        self.assertEqual(self._ids(), [])
        VersaoTermo.objects.filter(tipo='SAUDE').update(ativa=False)
        self.assertEqual(self._ids(), ['aranha.W010'])
        self.assertIn('termo SAUDE:', self._w010()[0].msg)
        VersaoTermo.objects.filter(tipo='LGPD').update(ativa=False)
        self.assertIn('termo LGPD e SAUDE:', self._w010()[0].msg)
        # termo ativo de PROCEDIMENTO nao substitui o global
        VersaoTermo.objects.create(tipo='LGPD', procedimento=criar_procedimento(), titulo='x',
                                   conteudo='y', versao='1', vigente_desde=timezone.localdate())
        self.assertEqual(self._ids(), ['aranha.W010'])
        # `check` puro (build do Docker) nao toca no banco
        self.assertEqual(self._ids(databases=None), [])

    def test_so_lgpd_inativo_avisa_so_lgpd(self):
        from aranha_estetica.models import VersaoTermo

        self._admin()
        VersaoTermo.objects.filter(tipo='LGPD').update(ativa=False)
        self.assertIn('termo LGPD:', self._w010()[0].msg)

    def test_w010_mudo_com_tabela_de_termos_ausente(self):
        # pre-deploy de banco antigo/novo: os checks rodam antes do migrate
        from django.db import ProgrammingError

        self._admin()
        with mock.patch.object(aranha_checks, 'termos_sem_versao_ativa',
                               side_effect=ProgrammingError('relation "versao_termo" does not exist')):
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


# ─── Conexao Postgres (settings/base.opcoes_postgres) ────────────────
class OpcoesPostgresTests(SimpleTestCase):
    """Nome da conexao (pg_stat_activity, log do Postgres) e teto por statement."""
    databases = {'default'}   # so SHOW (leitura), sem isolamento de transacao

    def _opcoes(self, db, **env):
        from clinica.settings.base import opcoes_postgres

        with mock.patch.dict(os.environ, env):
            for chave in ('DB_APPLICATION_NAME', 'DB_STATEMENT_TIMEOUT_MS'):
                if chave not in env:
                    os.environ.pop(chave, None)
            return opcoes_postgres(db).get('OPTIONS')

    def test_padrao_e_por_env(self):
        pg = 'django.db.backends.postgresql'
        self.assertEqual(self._opcoes({'ENGINE': pg, 'OPTIONS': {'sslmode': 'require'}}), {
            'sslmode': 'require', 'application_name': 'shivazen', 'options': '-c statement_timeout=30000'})
        self.assertEqual(self._opcoes({'ENGINE': pg}, DB_APPLICATION_NAME='shivazen-cron',
                                      DB_STATEMENT_TIMEOUT_MS='0'), {'application_name': 'shivazen-cron'})
        self.assertIsNone(self._opcoes({'ENGINE': 'django.db.backends.sqlite3'}))

    @skipUnless(connection.vendor == 'postgresql', 'so no Postgres')
    def test_conexao_da_suite_usa_as_opcoes(self):
        with connection.cursor() as cursor:
            cursor.execute('SHOW statement_timeout')
            self.assertEqual(cursor.fetchone()[0], '30s')
            cursor.execute('SHOW application_name')
            self.assertEqual(cursor.fetchone()[0], 'shivazen')


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
            # sem o teto da conexao (opcoes_postgres): data migration longa nao cai no meio
            cursor.execute('SHOW statement_timeout')
            self.assertEqual(cursor.fetchone()[0], '0')
            # trilha historico_alteracao: o que uma migration mudar sai com origem 'migracao'
            cursor.execute("SELECT current_setting('app.origem', true)")
            self.assertEqual(cursor.fetchone()[0], 'migracao')

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
        cursor.execute.assert_any_call('SET LOCAL statement_timeout = 0')
        cursor.execute.assert_any_call("SET LOCAL app.origem = 'migracao'")

    # log do pre-deploy tem de dizer que nada ficou pela metade e onde o banco parou
    def test_falha_escreve_estado_no_stderr_e_relevanta(self):
        from aranha_estetica.management.commands import migrate_atomico as mod

        fake_conn = mock.MagicMock(vendor='postgresql')
        err = StringIO()
        with mock.patch.object(mod, 'connections', {'default': fake_conn}), \
                mock.patch.object(mod.transaction, 'atomic'), \
                mock.patch.object(mod.MigrateCommand, 'handle', side_effect=RuntimeError('0047 quebrou')), \
                mock.patch.object(mod.Command, '_ultima_migration_aplicada',
                                  return_value='aranha_estetica.0026_otpcode_canal_default_sms') as ultima, \
                self.assertRaisesMessage(RuntimeError, '0047 quebrou'):
            mod.Command(stdout=StringIO(), stderr=err).handle(database='default', verbosity=0)
        ultima.assert_called_once_with(None)
        self.assertIn(
            'migrate_atomico: FALHOU - transacao desfeita; banco continua em '
            'aranha_estetica.0026_otpcode_canal_default_sms', err.getvalue())

    def test_sucesso_nao_escreve_falha(self):
        from aranha_estetica.management.commands import migrate_atomico as mod

        err = StringIO()
        with mock.patch.object(mod, 'connections', {'default': mock.MagicMock(vendor='postgresql')}), \
                mock.patch.object(mod.transaction, 'atomic'), \
                mock.patch.object(mod.MigrateCommand, 'handle', return_value=None):
            mod.Command(stdout=StringIO(), stderr=err).handle(database='default', verbosity=0)
        self.assertNotIn('FALHOU', err.getvalue())

    def test_ultima_migration_aplicada_le_django_migrations(self):
        from django.db.migrations.loader import MigrationLoader

        from aranha_estetica.management.commands import migrate_atomico as mod

        folha = MigrationLoader(connection).graph.leaf_nodes('aranha_estetica')[0][1]
        cmd = mod.Command(stdout=StringIO(), stderr=StringIO())
        self.assertEqual(cmd._ultima_migration_aplicada(), f'aranha_estetica.{folha}')
        self.assertEqual(cmd._ultima_migration_aplicada('sessions'), 'sessions.0001_initial')
        self.assertEqual(cmd._ultima_migration_aplicada('nao_existe'),
                         'nao_existe sem nenhuma migration aplicada')

    def test_ultima_migration_com_banco_fora_nao_mascara_o_erro(self):
        from django.db import OperationalError

        from aranha_estetica.management.commands import migrate_atomico as mod

        cmd = mod.Command(stdout=StringIO(), stderr=StringIO())
        with mock.patch.object(mod.MigrationRecorder, 'applied_migrations',
                               side_effect=OperationalError('server closed the connection')):
            self.assertEqual(cmd._ultima_migration_aplicada(),
                             '<desconhecida: OperationalError ao ler django_migrations>')

    @skipUnless(connection.vendor == 'postgresql', 'ramo atomico so existe no Postgres')
    def test_no_postgres_falha_desfaz_tudo_e_informa_a_migration_atual(self):
        from django.db.migrations.loader import MigrationLoader

        from aranha_estetica.management.commands import migrate_atomico as mod

        def _migra_e_quebra(cmd, *args, **options):
            with connection.cursor() as cursor:
                cursor.execute('CREATE TABLE probe_migrate_atomico (id int)')
            raise RuntimeError('migration quebrou no meio')

        folha = MigrationLoader(connection).graph.leaf_nodes('aranha_estetica')[0][1]
        err = StringIO()
        with mock.patch.object(mod.MigrateCommand, 'handle', _migra_e_quebra), \
                self.assertRaisesMessage(RuntimeError, 'migration quebrou no meio'):
            call_command('migrate_atomico', '--noinput', verbosity=0, stdout=StringIO(), stderr=err)
        self.assertIn('migrate_atomico: FALHOU - transacao desfeita; banco continua em '
                      f'aranha_estetica.{folha}', err.getvalue())
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('probe_migrate_atomico')")
            self.assertIsNone(cursor.fetchone()[0])


# ─── Retencao / housekeeping ─────────────────────────────────────────
@skipUnless(connection.vendor == 'postgresql', 'sp_purgar_dados_expirados so no Postgres')
class HousekeepingTests(TestCase):
    def test_purga_otp_notificacao_e_auditoria_antigos(self):
        from aranha_estetica.models import CodigoOtp, LogAuditoria, Notificacao
        from aranha_estetica.jobs_manutencao import job_housekeeping
        from aranha_estetica.tests.factories import (
            criar_atendimento, criar_cliente, criar_procedimento, criar_profissional, envelhecer_trilha,
        )

        agora = timezone.now()
        _, velho = CodigoOtp.gerar('a@x.com')
        _, novo = CodigoOtp.gerar('b@x.com')
        CodigoOtp.objects.filter(pk=velho.pk).update(criado_em=agora - timedelta(hours=25))

        at = criar_atendimento(criar_cliente(), criar_profissional(), criar_procedimento())
        n_velha = Notificacao.objects.create(atendimento=at, mensagem='Oi Maria')
        n_nova = Notificacao.objects.create(atendimento=at, mensagem='Oi Maria')
        Notificacao.objects.filter(pk=n_velha.pk).update(criado_em=agora - timedelta(days=400))

        log_velho = LogAuditoria.objects.create(acao='x')
        log_novo = LogAuditoria.objects.create(acao='y')
        envelhecer_trilha(LogAuditoria, log_velho.pk, agora - timedelta(days=365 * 5 + 2))

        resumo = job_housekeeping()

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
        from aranha_estetica.models import AgendaExcecao
        from aranha_estetica.jobs_manutencao import job_carregar_feriados

        self.assertIn('Feriados', job_carregar_feriados())
        ano = timezone.localdate().year  # o comando usa o ano local (31/12 21h+ ja e outro ano em UTC)
        self.assertTrue(AgendaExcecao.objects.filter(
            origem='FERIADO_NACIONAL', profissional__isnull=True, tipo='FOLGA', data_inicio__year=ano + 1,
        ).exists())


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
        self.assertEqual(settings.RATELIMIT_IP_META_KEY, 'aranha_estetica.utils.seguranca.client_ip')
        self.assertEqual(settings.AXES_CLIENT_IP_CALLABLE, 'aranha_estetica.utils.seguranca.client_ip')

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
               if k not in ('REDIS_URL', 'CLIENT_IP_HEADER', 'SMS_DEV_LOG_ONLY')}
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
        self.assertTrue(s['sess_save'])
        self.assertEqual(s['sess_age'], 8 * 3600)
        self.assertTrue(s['sms_exigir'])
        self.assertEqual(s['langs'], ['pt-br'])

    def _importar_prod(self, **env_extra):
        env = {k: v for k, v in os.environ.items()
               if k not in ('ADMIN_2FA_OBRIGATORIO', 'PROFISSIONAL_2FA_OBRIGATORIO', 'EMBED_FRAME_ANCESTORS')}
        env.update({'DJANGO_ENV': 'prod', 'DJANGO_SECRET_KEY': 'x' * 50, **env_extra})
        codigo = (
            'import json\n'
            'import clinica.settings.prod as s\n'
            'print(json.dumps({"tem_2fa": hasattr(s, "ADMIN_2FA_OBRIGATORIO"),'
            '"obrig": getattr(s, "ADMIN_2FA_OBRIGATORIO", None),'
            '"prof": s.PROFISSIONAL_2FA_OBRIGATORIO,'
            '"frames": s.EMBED_FRAME_ANCESTORS}))\n'
        )
        proc = subprocess.run(
            [sys.executable, '-W', 'ignore', '-c', codigo], cwd=BASE_DIR, env=env,
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_2fa_de_profissional_ligado_por_padrao_em_prod(self):
        self.assertIs(self._importar_prod()['prof'], True)
        self.assertIs(self._importar_prod(PROFISSIONAL_2FA_OBRIGATORIO='false')['prof'], False)

    _CHAVES_ESP = ('RESEND_API_KEY', 'BREVO_API_KEY', 'SENDGRID_API_KEY', 'MAILGUN_API_KEY',
                   'MAILGUN_SENDER_DOMAIN', 'POSTMARK_SERVER_TOKEN')

    def _importar_prod_email(self, **env_extra):
        env = dict(os.environ)
        env.update({'DJANGO_ENV': 'prod', 'DJANGO_SECRET_KEY': 'x' * 50, 'EMAIL_TIMEOUT': '',
                    **{chave: '' for chave in self._CHAVES_ESP}, **env_extra})
        codigo = (
            'import json\n'
            'import clinica.settings.prod as s\n'
            'print(json.dumps({"backend": s.EMAIL_BACKEND, "app": "anymail" in s.INSTALLED_APPS,'
            '"anymail": getattr(s, "ANYMAIL", None), "chaves": list(s.ANYMAIL_CHAVES_ENV)}))\n'
        )
        proc = subprocess.run(
            [sys.executable, '-W', 'ignore', '-c', codigo], cwd=BASE_DIR, env=env,
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_anymail_montado_da_env_so_com_backend_anymail(self):
        # Railway Hobby bloqueia SMTP: backend HTTP + chave do ESP vinda da env
        s = self._importar_prod_email(
            EMAIL_BACKEND='anymail.backends.resend.EmailBackend', RESEND_API_KEY=' re_123 ',
            MAILGUN_SENDER_DOMAIN='mg.clinica.com.br', EMAIL_TIMEOUT='7',
        )
        self.assertEqual(s['backend'], 'anymail.backends.resend.EmailBackend')
        self.assertTrue(s['app'])
        # so as chaves presentes (vazia fica de fora) + timeout do SMTP
        self.assertEqual(s['anymail'], {'RESEND_API_KEY': 're_123',
                                        'MAILGUN_SENDER_DOMAIN': 'mg.clinica.com.br',
                                        'REQUESTS_TIMEOUT': 7})
        # sem backend anymail: nem app nem ANYMAIL (dummy continua o default de prod)
        s = self._importar_prod_email(EMAIL_BACKEND='', RESEND_API_KEY='re_123')
        self.assertEqual(s['backend'], 'django.core.mail.backends.dummy.EmailBackend')
        self.assertFalse(s['app'])
        self.assertIsNone(s['anymail'])

    def test_settings_leem_a_chave_de_todo_esp_suportado(self):
        from aranha_estetica.utils.email import ANYMAIL_CHAVE_POR_ESP

        self.assertTrue(set(ANYMAIL_CHAVE_POR_ESP.values()) <= set(settings.ANYMAIL_CHAVES_ENV))
        for esp in ANYMAIL_CHAVE_POR_ESP:  # backend existe na versao instalada
            with self.subTest(esp=esp):
                __import__(f'anymail.backends.{esp}')

    def test_2fa_e_frame_ancestors_por_env(self):
        # sem env: o codigo decide em runtime (2FA ligado fora de DEBUG)
        self.assertEqual(self._importar_prod(), {'tem_2fa': False, 'obrig': None, 'prof': True, 'frames': ''})
        s = self._importar_prod(ADMIN_2FA_OBRIGATORIO='false',
                                EMBED_FRAME_ANCESTORS=' https://linktr.ee ')
        self.assertEqual(s, {'tem_2fa': True, 'obrig': False, 'prof': True, 'frames': 'https://linktr.ee'})
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
        for valor in ('production', 'homolog'):
            with self.subTest(valor=valor):
                proc = self._importar(valor)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn('ImproperlyConfigured', proc.stderr)
                self.assertIn(valor, proc.stderr)

    def test_prod_e_staging_valem_no_railway_e_dev_nao(self):
        for valor, debug in (('prod', 'False'), (' PROD ', 'False'), ('staging', 'False')):
            with self.subTest(valor=valor):
                proc = self._importar(valor)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout.strip().splitlines()[-1], debug)
        # o .env de dev colado no Railway publicaria DEBUG=True: o boot cai
        proc = self._importar('dev')
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('DJANGO_ENV=dev no Railway', proc.stderr)


class AmbienteTesteTests(TestCase):
    """Ambiente de testes publicado (settings/staging.py): producao + travas de envio."""

    def test_staging_importa_com_as_travas(self):
        env = {k: v for k, v in os.environ.items() if k not in ('DEBUG', 'SMS_DEV_LOG_ONLY')}
        env.update({'DJANGO_ENV': 'staging', 'DJANGO_SECRET_KEY': 'x' * 50, 'RAILWAY_ENVIRONMENT_NAME': 'dev',
                    'EMAIL_BACKEND': 'django.core.mail.backends.locmem.EmailBackend'})
        codigo = ('import json\nimport clinica.settings as s\n'
                  'print(json.dumps({"teste": s.AMBIENTE_TESTE, "debug": s.DEBUG, "sms": s.SMS_DEV_LOG_ONLY,'
                  '"email": s.EMAIL_BACKEND, "real": s.EMAIL_BACKEND_REAL}))\n')
        proc = subprocess.run([sys.executable, '-W', 'ignore', '-c', codigo], cwd=BASE_DIR, env=env,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        s = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(s, {'teste': True, 'debug': False, 'sms': True,
                             'email': 'aranha_estetica.utils.email.BackendAmbienteTeste',
                             'real': 'django.core.mail.backends.locmem.EmailBackend'})

    @override_settings(AMBIENTE_TESTE=True, DESTINATARIOS_TESTE=['dona@clinica.test', '(17) 99999-0000'],
                       EMAIL_BACKEND='aranha_estetica.utils.email.BackendAmbienteTeste',
                       EMAIL_BACKEND_REAL='django.core.mail.backends.locmem.EmailBackend')
    def test_so_destinatarios_de_teste_recebem(self):
        from django.core import mail

        from aranha_estetica.utils.seguranca import destino_permitido
        self.assertTrue(destino_permitido('Dona@Clinica.test'))
        self.assertTrue(destino_permitido('5517999990000'))
        self.assertFalse(destino_permitido('cliente@gmail.com'))
        self.assertFalse(destino_permitido('17988887777'))
        mail.send_mail('Oi', 'texto', 'clinica@clinica.test', ['cliente@gmail.com', 'dona@clinica.test'])
        mail.send_mail('Oi', 'texto', 'clinica@clinica.test', ['outra@gmail.com'])
        self.assertEqual([m.to for m in mail.outbox], [['dona@clinica.test']])

    def test_fora_do_ambiente_de_teste_nada_e_bloqueado(self):
        from aranha_estetica.utils.seguranca import destino_permitido
        self.assertTrue(destino_permitido('qualquer@gmail.com'))

    @override_settings(AMBIENTE_TESTE=True)
    def test_robots_fechado_noindex_e_faixa(self):
        robots = self.client.get('/robots.txt').content.decode()
        self.assertIn('Disallow: /\n', robots)
        self.assertNotIn('Sitemap', robots)
        home = self.client.get('/')
        self.assertEqual(home['X-Robots-Tag'], 'noindex, nofollow, noarchive')
        self.assertContains(home, 'Ambiente de testes')

    def test_producao_sem_postgres_e_erro(self):
        from aranha_estetica.checks import check_postgres_obrigatorio
        with override_settings(CIFRAGEM_EXIGE_CHAVE=True,
                               DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}):
            self.assertEqual([e.id for e in check_postgres_obrigatorio()], ['aranha.E002'])
        self.assertEqual(check_postgres_obrigatorio(), [])   # dev/testes nao exigem


class Settings2FAEApiTests(TestCase):
    def test_admin_do_django_otp_esconde_semente_e_qr(self):
        # rev_security-09: config/qrcode do TOTPDeviceAdmin davam 200 p/ qualquer ADMIN
        from django_otp.conf import settings as otp_settings
        self.assertIs(settings.OTP_ADMIN_HIDE_SENSITIVE_DATA, True)
        self.assertIs(otp_settings.OTP_ADMIN_HIDE_SENSITIVE_DATA, True)

    def test_2fa_de_profissional_desligado_em_dev(self):
        # rev_security-14: setting lido por utils/dois_fatores; em prod o padrao e ligado
        # (SettingsProdTests.test_2fa_de_profissional_ligado_por_padrao_em_prod)
        self.assertIs(settings.PROFISSIONAL_2FA_OBRIGATORIO, False)


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
class CiFixadoTests(SimpleTestCase):
    """CI e imagem nao mudam em silencio: action por SHA, imagem por digest, token so leitura."""

    def _ler(self, *partes):
        return BASE_DIR.joinpath(*partes).read_text(encoding='utf-8')

    def test_actions_por_sha_e_imagens_por_digest(self):
        ci = self._ler('.github', 'workflows', 'ci.yml')
        actions = re.findall(r'uses:\s*(\S+)', ci)
        self.assertTrue(actions)
        for action in actions:
            self.assertRegex(action, r'^[\w.-]+/[\w.-]+@[0-9a-f]{40}$')
        imagens = (re.findall(r'image:\s*(\S+)', ci)
                   + re.findall(r'image:\s*(\S+)', self._ler('docker-compose.yml'))
                   + re.findall(r'^FROM\s+(\S+)', self._ler('Dockerfile'), re.M))
        self.assertEqual(len(imagens), 6)   # 2 servicos do CI, compose, 3 stages do Dockerfile
        for imagem in imagens:
            self.assertRegex(imagem, r'@sha256:[0-9a-f]{64}$')

    def test_token_so_leitura_e_todo_job_com_timeout(self):
        ci = self._ler('.github', 'workflows', 'ci.yml')
        self.assertIn('\npermissions:\n  contents: read\n', ci)
        jobs = re.findall(r'^  ([\w-]+):\n    name:', ci, re.M)
        self.assertGreaterEqual(len(jobs), 8)
        self.assertEqual(ci.count('    timeout-minutes:'), len(jobs))
        self.assertNotIn('continue-on-error', ci)   # pip-audit bloqueia
        self.assertTrue((BASE_DIR / '.github' / 'dependabot.yml').exists())


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

    def test_backend_http_de_email_instalado(self):
        # sem ele nenhum e-mail sai no Railway Hobby (SMTP bloqueado)
        self.assertIn('django-anymail', self._pins('requirements.txt'))

    def test_lock_espelha_as_versoes_diretas(self):
        diretas = self._pins('requirements.txt')
        lock = self._pins('requirements.lock')
        self.assertGreater(len(lock), len(diretas))  # transitivas inclusas
        for nome, versao in diretas.items():
            with self.subTest(pacote=nome):
                self.assertEqual(lock.get(nome), versao,
                                 f'{nome}: requirements.txt e requirements.lock divergem')
        for transitiva in ('py-vapid', 'cryptography', 'django-otp', 'qrcode', 'phonenumbers'):
            self.assertIn(transitiva, lock)

    def test_build_e_ci_instalam_pelo_lock(self):
        dockerfile = (BASE_DIR / 'Dockerfile').read_text(encoding='utf-8')
        self.assertIn('-r requirements.txt -c requirements.lock', dockerfile)
        self.assertIn('pip install --no-index', dockerfile)  # runtime so usa os wheels
        ci = (BASE_DIR / '.github' / 'workflows' / 'ci.yml').read_text(encoding='utf-8')
        instalacoes = [linha.strip() for linha in ci.splitlines() if 'pip install -r requirements' in linha]
        # testes, migracoes-sqlite, postgres, postgres-migracoes (tag migracoes em jobs paralelos)
        # e navegador (Playwright)
        self.assertEqual(len(instalacoes), 5)
        self.assertTrue(all(linha.endswith('-r requirements-dev.txt -c requirements.lock') for linha in instalacoes))
