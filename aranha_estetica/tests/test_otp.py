"""Testes de OTP: model + service + SMS (falha fechada) + endpoints do wizard e do portal.

Django TestCase (roda no `manage.py test`, antes era pytest-only e ficava fora
da suite).
"""
import logging
import os
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import Cliente, CodigoOtp
from aranha_estetica.services import otp as otp_service
from aranha_estetica.utils import sms


class OtpCodeModelTests(TestCase):
    def test_gerar_e_verificar_ok(self):
        codigo, obj = CodigoOtp.gerar('joao@example.com')
        self.assertRegex(codigo, r'^\d{6}$')
        self.assertNotEqual(obj.codigo_hash, codigo)  # guardado hashed
        ok, motivo = CodigoOtp.verificar('joao@example.com', codigo)
        self.assertTrue(ok)
        self.assertEqual(motivo, 'ok')

    def test_codigo_errado_incrementa_tentativas(self):
        codigo, _ = CodigoOtp.gerar('a@x.com')
        ok, motivo = CodigoOtp.verificar('a@x.com', '000000' if codigo != '000000' else '111111')
        self.assertFalse(ok)
        self.assertTrue(motivo.startswith('incorreto'))

    def test_bloqueio_apos_max_tentativas(self):
        CodigoOtp.gerar('z@x.com')
        for _ in range(CodigoOtp.MAX_TENTATIVAS):
            CodigoOtp.verificar('z@x.com', '000000')
        ok, motivo = CodigoOtp.verificar('z@x.com', '000000')
        self.assertFalse(ok)
        self.assertIn(motivo, ('bloqueado', 'expirado'))

    def test_expirado(self):
        codigo, obj = CodigoOtp.gerar('exp@x.com')
        obj.expira_em = timezone.now() - timedelta(seconds=1)
        obj.save()
        ok, motivo = CodigoOtp.verificar('exp@x.com', codigo)
        self.assertFalse(ok)
        self.assertEqual(motivo, 'expirado')

    def test_reenvio_invalida_anteriores(self):
        codigo1, _ = CodigoOtp.gerar('re@x.com')
        CodigoOtp.objects.filter(email='re@x.com').update(
            criado_em=timezone.now() - timedelta(seconds=120)
        )
        codigo2, _ = CodigoOtp.gerar('re@x.com')
        ok1, _ = CodigoOtp.verificar('re@x.com', codigo1)
        self.assertFalse(ok1)
        ok2, _ = CodigoOtp.verificar('re@x.com', codigo2)
        self.assertTrue(ok2)


@override_settings(SMS_DEV_LOG_ONLY=True)
class OtpServiceTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_solicitar_sem_telefone_falha(self):
        """OTP exige telefone — sem ele, retorna falha sem enviar nada."""
        mail.outbox.clear()
        ok, motivo, canal = otp_service.solicitar_otp('foo@example.com')
        self.assertFalse(ok)
        self.assertEqual(motivo, 'telefone_ausente')
        self.assertIsNone(canal)
        self.assertEqual(len(mail.outbox), 0)

    def test_solicitar_sms_com_telefone(self):
        """Com telefone, canal resultante e SMS (modo dev log-only)."""
        ok, motivo, canal = otp_service.solicitar_otp('sms@example.com', telefone='11999999999')
        self.assertTrue(ok)
        self.assertEqual(motivo, 'ok')
        self.assertEqual(canal, CodigoOtp.CANAL_SMS)

    def test_rate_limit_reenvio(self):
        otp_service.solicitar_otp('rl@example.com', telefone='11999999999')
        ok, motivo, _ = otp_service.solicitar_otp('rl@example.com', telefone='11999999999')
        self.assertFalse(ok)
        self.assertEqual(motivo, 'aguarde')

    def test_verificar_email_vazio(self):
        ok, motivo = otp_service.verificar_otp('', '123456')
        self.assertFalse(ok)
        self.assertEqual(motivo, 'dados_ausentes')

    def test_normalizar_telefone_br(self):
        self.assertEqual(otp_service.normalizar_telefone_br('+55 (17) 99999-0001'), '17999990001')
        self.assertEqual(otp_service.normalizar_telefone_br('(17) 3333-4444'), '1733334444')
        self.assertEqual(otp_service.normalizar_telefone_br('99999-0001'), '')
        self.assertEqual(otp_service.normalizar_telefone_br('+1 415 555 0100 99'), '')
        self.assertEqual(otp_service.normalizar_telefone_br(None), '')


class SmsFalhaFechadaTests(TestCase):
    """booking-01 / security-07: sem provedor fora de DEBUG, nada de 'sucesso' so logando."""

    def setUp(self):
        cache.clear()

    @override_settings(DEBUG=False, SMS_DEV_LOG_ONLY=False)
    @patch.dict(os.environ, {'SMS_DEV_LOG_ONLY': '', 'ZENVIA_API_TOKEN': '', 'ZENVIA_FROM': ''})
    def test_sem_provedor_em_prod_retorna_false(self):
        self.assertFalse(sms.sms_disponivel())
        self.assertFalse(sms.enviar_sms('17999990000', 'teste'))
        ok, motivo, _ = otp_service.solicitar_otp_telefone('17999990000')
        self.assertFalse(ok)
        self.assertEqual(motivo, 'sms_falha')
        # Canal fora: nao gera codigo nem consome o cooldown
        self.assertFalse(CodigoOtp.objects.exists())

    @override_settings(DEBUG=False, SMS_DEV_LOG_ONLY=True)
    def test_modo_dev_nunca_loga_o_codigo_fora_de_debug(self):
        with self.assertLogs('aranha_estetica.utils.sms', level=logging.INFO) as cm:
            self.assertTrue(sms.enviar_otp_sms('17999990000', '483920'))
        for rec in cm.records:
            self.assertNotIn('483920', str(getattr(rec, 'preview', '')))
            self.assertFalse(hasattr(rec, 'preview'))

    @override_settings(DEBUG=False, SMS_DEV_LOG_ONLY=False)
    @patch.dict(os.environ, {'SMS_DEV_LOG_ONLY': '', 'ZENVIA_API_TOKEN': 'tok', 'ZENVIA_FROM': 'clinica'})
    @patch('aranha_estetica.utils.sms.requests.post')
    def test_erro_5xx_nao_faz_retry_nem_sleep(self, post):
        post.return_value.status_code = 503
        post.return_value.text = 'indisponivel'
        with patch('time.sleep') as dormir:
            self.assertFalse(sms.enviar_sms('17999990000', 'oi'))
        self.assertEqual(post.call_count, 1)
        dormir.assert_not_called()
        self.assertEqual(post.call_args.kwargs['timeout'], sms.TIMEOUT)

    def test_formatar_telefone_so_brasil(self):
        self.assertEqual(sms.formatar_telefone('17999990000'), '5517999990000')
        self.assertEqual(sms.formatar_telefone('5517999990000'), '5517999990000')
        self.assertEqual(sms.formatar_telefone('14155550100123'), '')
        self.assertEqual(sms.formatar_telefone('12345'), '')
        self.assertFalse(sms.enviar_otp_sms('12345', '000000'))


@override_settings(RATELIMIT_ENABLE=False, SMS_DEV_LOG_ONLY=True)
class OtpAgendamentoEndpointTests(TestCase):
    """booking-02/03 + security-03: desafio preso ao telefone, sem PII por e-mail."""

    def setUp(self):
        cache.clear()
        self.url_sol = reverse('aranha:solicitar_otp_agendamento')
        self.url_ver = reverse('aranha:verificar_otp_agendamento')
        self.codigos = {}
        patcher = patch(
            'aranha_estetica.services.otp.enviar_otp_sms',
            side_effect=lambda tel, codigo, ip=None: self.codigos.__setitem__(tel, codigo) or True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_email_da_vitima_nao_vira_chave_nem_prefill(self):
        Cliente.objects.create(
            nome='Vitima Silva', telefone='17912345678', email='vitima@example.com',
        )
        resp = self.client.post(self.url_sol, {'telefone': '17900000001', 'email': 'vitima@example.com'})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('cliente_existente', resp.json())
        self.assertIn('17900000001', self.codigos)
        codigo = self.codigos['17900000001']
        # Chave do desafio e o telefone do atacante (nunca o e-mail da vitima)
        self.assertFalse(CodigoOtp.objects.filter(email='vitima@example.com').exists())

        resp = self.client.post(self.url_ver, {
            'telefone': '17900000001', 'email': 'vitima@example.com', 'codigo': codigo,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()['prefill'])
        self.assertEqual(self.client.session['otp_agendamento_telefone'], '17900000001')

    def test_cliente_recorrente_com_email_verifica_pelo_telefone(self):
        Cliente.objects.create(
            nome='Joana', telefone='17999990000', email='joana@example.com',
        )
        self.client.post(self.url_sol, {'telefone': '(17) 99999-0000'})
        codigo = self.codigos['17999990000']
        resp = self.client.post(self.url_ver, {'telefone': '17999990000', 'codigo': codigo})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['prefill']['nome'], 'Joana')
        self.assertEqual(resp.json()['prefill']['email'], 'joana@example.com')

    def test_prefill_traz_estado_dos_opt_ins(self):
        # gap1-03: o wizard mostra os checkboxes como estao no cadastro
        Cliente.objects.create(
            nome='Rita', telefone='17999990001', consent_whatsapp_confirmacao=True,
        )
        self.client.post(self.url_sol, {'telefone': '17999990001'})
        codigo = self.codigos['17999990001']
        resp = self.client.post(self.url_ver, {'telefone': '17999990001', 'codigo': codigo})
        self.assertEqual(resp.json()['prefill']['consents'], {
            'consent_email_marketing': False,
            'consent_whatsapp_confirmacao': True,
            'consent_whatsapp_nps': False,
        })

    def test_codigo_de_um_telefone_nao_vale_para_outro(self):
        self.client.post(self.url_sol, {'telefone': '17900000001'})
        codigo = self.codigos['17900000001']
        resp = self.client.post(self.url_ver, {'telefone': '17912345678', 'codigo': codigo})
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn('otp_agendamento_telefone', self.client.session)

    def test_telefone_invalido_nao_envia_sms(self):
        for tel in ('999', '+1 415 555 0100 99', '(00) 99999-0000'):
            with self.subTest(tel=tel):
                resp = self.client.post(self.url_sol, {'telefone': tel})
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.json()['erro'], 'telefone_invalido')
        self.assertEqual(self.codigos, {})

    @override_settings(SMS_DEV_LOG_ONLY=False, DEBUG=False)
    @patch.dict(os.environ, {'SMS_DEV_LOG_ONLY': '', 'ZENVIA_API_TOKEN': '', 'ZENVIA_FROM': ''})
    def test_sms_indisponivel_responde_sms_falha(self):
        resp = self.client.post(self.url_sol, {'telefone': '17900000001'})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()['erro'], 'sms_falha')


@override_settings(RATELIMIT_ENABLE=False, SMS_DEV_LOG_ONLY=True)
class MeusAgendamentosOtpTests(TestCase):
    """booking-13/14: login por celular OU e-mail, sem enumeracao de clientes."""

    def setUp(self):
        cache.clear()
        self.url_env = reverse('aranha:meus_agendamentos_enviar_otp')
        self.url_ver = reverse('aranha:meus_agendamentos_verificar_otp')
        self.codigos = {}
        patcher = patch(
            'aranha_estetica.services.otp.enviar_otp_sms',
            side_effect=lambda tel, codigo, ip=None: self.codigos.__setitem__(tel, codigo) or True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cliente = Cliente.objects.create(
            nome='Cliente Portal', telefone='17988880000', email='portal@example.com',
        )

    def test_resposta_identica_com_e_sem_cadastro(self):
        r1 = self.client.post(self.url_env, {'identificador': 'portal@example.com'})
        cache.clear()
        r2 = self.client.post(self.url_env, {'identificador': 'naoexiste@example.com'})
        r3 = self.client.post(self.url_env, {'identificador': '17900001111'})
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json(), r2.json())
        self.assertEqual(r1.json(), r3.json())
        # So o telefone do cadastro recebeu codigo
        self.assertEqual(list(self.codigos), ['17988880000'])

    def test_login_por_telefone_lista_agendamentos(self):
        self.client.post(self.url_env, {'identificador': '(17) 98888-0000'})
        codigo = self.codigos['17988880000']
        resp = self.client.post(self.url_ver, {'identificador': '17988880000', 'codigo': codigo})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.session['meus_agendamentos_telefone'], '17988880000')
        pagina = self.client.get(reverse('aranha:meus_agendamentos'))
        self.assertEqual(pagina.context['step'], '3')

    def test_login_por_email_usa_telefone_do_cadastro(self):
        self.client.post(self.url_env, {'identificador': 'PORTAL@example.com'})
        codigo = self.codigos['17988880000']
        resp = self.client.post(self.url_ver, {'identificador': 'portal@example.com', 'codigo': codigo})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.session['meus_agendamentos_telefone'], '17988880000')

    def test_identificador_desconhecido_nao_loga(self):
        resp = self.client.post(self.url_ver, {'identificador': 'x@example.com', 'codigo': '123456'})
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn('meus_agendamentos_telefone', self.client.session)


@override_settings(RATELIMIT_ENABLE=False, SMS_DEV_LOG_ONLY=True)
class OtpQuotaNaoQueimaCodigoTests(TestCase):
    """rev_security-05 / rev_booking-05: quota esgotada nao gera (nem invalida) codigo."""

    TEL = '17991234567'

    def setUp(self):
        cache.clear()
        self.codigos = []
        patcher = patch(
            'aranha_estetica.services.otp.enviar_otp_sms',
            side_effect=lambda tel, codigo, ip=None: self.codigos.append(codigo) or True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _pular_cooldown(self):
        CodigoOtp.objects.update(criado_em=timezone.now() - timedelta(minutes=5))

    def _esgotar_quota_do_telefone(self):
        cache.set(f'sms_rl:tel:55{self.TEL}', sms.SMS_MAX_POR_HORA, 3600)

    def test_quota_esgotada_mantem_o_codigo_da_vitima(self):
        ok, _motivo, _canal = otp_service.solicitar_otp_telefone(self.TEL)
        self.assertTrue(ok)
        codigo_vitima = self.codigos[-1]
        self._pular_cooldown()
        self._esgotar_quota_do_telefone()

        ok, motivo, canal = otp_service.solicitar_otp_telefone(self.TEL)
        self.assertEqual((ok, motivo, canal), (False, 'limite_sms', None))
        self.assertEqual(len(self.codigos), 1)  # nada enviado
        self.assertEqual(CodigoOtp.objects.count(), 1)  # nada gerado

        self.assertEqual(otp_service.verificar_otp_telefone(self.TEL, codigo_vitima), (True, 'ok'))

    def test_endpoint_do_wizard_responde_429_limite_sms(self):
        self._esgotar_quota_do_telefone()
        resp = self.client.post(reverse('aranha:solicitar_otp_agendamento'), {'telefone': self.TEL})
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()['erro'], 'limite_sms')
        self.assertFalse(CodigoOtp.objects.exists())

    def test_portal_continua_neutro_com_quota_esgotada(self):
        # Anti-enumeracao: quota so esgota em telefone cadastrado -> resposta neutra
        Cliente.objects.create(nome='Portal', telefone=self.TEL)
        self._esgotar_quota_do_telefone()
        resp = self.client.post(reverse('aranha:meus_agendamentos_enviar_otp'), {'identificador': self.TEL})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        self.assertFalse(CodigoOtp.objects.exists())

    def test_quota_por_ip_agrupa_ipv6_por_prefixo_64(self):
        # Trocar o sufixo do IPv6 (mesmo /64) nao renova a quota por IP
        for i in range(sms.SMS_MAX_POR_IP_HORA):
            sms.registrar_envio(f'1799000{i:04d}', ip=f'2001:db8:1:2::{i + 1:x}')
        self.assertFalse(sms.pode_enviar('17991112222', ip='2001:db8:1:2:aaaa:bbbb:cccc:dddd'))
        self.assertTrue(sms.pode_enviar('17991112222', ip='2001:db8:1:3::1'))
        self.assertTrue(sms.pode_enviar('17991112222', ip='203.0.113.9'))


@override_settings(RATELIMIT_ENABLE=False, SMS_DEV_LOG_ONLY=True)
class OtpSoParaCelularTests(TestCase):
    """rev_booking-12: SMS de OTP so vai p/ celular (fixo nunca recebe e gasta quota)."""

    def setUp(self):
        cache.clear()
        self.codigos = {}
        patcher = patch(
            'aranha_estetica.services.otp.enviar_otp_sms',
            side_effect=lambda tel, codigo, ip=None: self.codigos.__setitem__(tel, codigo) or True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_servico_recusa_fixo_e_aceita_celular(self):
        self.assertFalse(otp_service.eh_celular_br('1733221100'))
        self.assertTrue(otp_service.eh_celular_br('17991234567'))
        self.assertEqual(otp_service.solicitar_otp_telefone('1733221100'), (False, 'telefone_invalido', None))
        self.assertEqual(self.codigos, {})
        ok, motivo, _ = otp_service.solicitar_otp_telefone('17991234567')
        self.assertEqual((ok, motivo), (True, 'ok'))

    def test_wizard_recusa_fixo(self):
        resp = self.client.post(reverse('aranha:solicitar_otp_agendamento'), {'telefone': '(17) 3322-1100'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['erro'], 'telefone_invalido')
        self.assertEqual(self.codigos, {})
        self.assertFalse(CodigoOtp.objects.exists())

    def test_portal_fixo_digitado_e_erro_de_formato(self):
        resp = self.client.post(reverse('aranha:meus_agendamentos_enviar_otp'), {'identificador': '1733221100'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['erro'], 'identificador_invalido')

    def test_portal_cadastro_com_fixo_responde_neutro_sem_sms(self):
        # Cadastro da recepcao com fixo: login por e-mail nao manda SMS p/ o fixo
        Cliente.objects.create(nome='Fixo', telefone='1733221100', email='fixo@example.com')
        resp = self.client.post(reverse('aranha:meus_agendamentos_enviar_otp'), {'identificador': 'fixo@example.com'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        self.assertEqual(self.codigos, {})
