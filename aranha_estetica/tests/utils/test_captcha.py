"""Cloudflare Turnstile (utils/captcha): a barreira que protege a cota de SMS falha fechado."""
from unittest import mock

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from aranha_estetica.models import CodigoOtp
from aranha_estetica.utils import captcha

POST = 'aranha_estetica.utils.captcha.requests.post'


def _resposta(json=None, erro_json=None):
    resp = mock.Mock()
    resp.json.side_effect = erro_json
    if erro_json is None:
        resp.json.return_value = json
    return resp


@override_settings(DEBUG=False, TURNSTILE_SECRET_KEY='segredo-turnstile')
class VerificarTurnstileTests(SimpleTestCase):
    def test_success_true_aceita_e_manda_secret_token_e_ip(self):
        with mock.patch(POST, return_value=_resposta({'success': True})) as post:
            self.assertTrue(captcha.verificar_turnstile('tok', ip='203.0.113.9'))
        dados = post.call_args.kwargs['data']
        self.assertEqual(dados, {'secret': 'segredo-turnstile', 'response': 'tok', 'remoteip': '203.0.113.9'})
        self.assertEqual(post.call_args.kwargs['timeout'], captcha.TIMEOUT_SEGUNDOS)

    def test_success_false_ou_nao_booleano_recusa(self):
        for corpo in ({'success': False, 'error-codes': ['invalid-input-response']}, {'success': 'true'},
                      {}, ['success']):
            with self.subTest(corpo=corpo), mock.patch(POST, return_value=_resposta(corpo)):
                self.assertFalse(captcha.verificar_turnstile('tok'))

    def test_timeout_e_json_invalido_recusam(self):
        with mock.patch(POST, side_effect=requests.Timeout):
            self.assertFalse(captcha.verificar_turnstile('tok'))
        with mock.patch(POST, return_value=_resposta(erro_json=ValueError('nao e json'))):
            self.assertFalse(captcha.verificar_turnstile('tok'))

    def test_token_vazio_recusa_sem_chamar_a_cloudflare(self):
        with mock.patch(POST) as post:
            self.assertFalse(captcha.verificar_turnstile(''))
        post.assert_not_called()

    @override_settings(TURNSTILE_SECRET_KEY='')
    def test_sem_chave_recusa_fora_de_debug_e_passa_em_debug(self):
        with mock.patch(POST) as post:
            self.assertFalse(captcha.verificar_turnstile('tok'))
            with override_settings(DEBUG=True):
                self.assertTrue(captcha.verificar_turnstile('tok'))
        post.assert_not_called()
        self.assertFalse(captcha.turnstile_enabled())


@override_settings(TURNSTILE_SECRET_KEY='segredo-turnstile', TURNSTILE_SITE_KEY='site', RATELIMIT_ENABLE=False)
class CaptchaNoPedidoDeCodigoTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_captcha_recusado_devolve_400_e_nao_gera_codigo(self):
        with mock.patch(POST, return_value=_resposta({'success': False})):
            resp = self.client.post(reverse('aranha:solicitar_otp_agendamento'),
                                    {'telefone': '17999990001', 'cf-turnstile-response': 'tok'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['codigo'], 'captcha')
        self.assertFalse(CodigoOtp.objects.exists())
