"""Defesas dos webhooks publicos (csrf_exempt): Zenvia (status de SMS) e handshake da Meta.

So a autenticacao/forma do pedido; o tratamento das mensagens recebidas fica em
test_whatsapp_webhook.py."""
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

SEGREDO = 'segredo-zenvia-' + 'k7' * 10
CORPO = '{"messageId": "m1", "messageStatus": {"code": "DELIVERED"}, "to": "5517999990000"}'


@override_settings(DEBUG=False, ZENVIA_WEBHOOK_SECRET='', ZENVIA_ALLOWED_IPS=[])
class ZenviaWebhookTests(TestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse('aranha:zenvia_sms_webhook')

    def _post(self, corpo=CORPO, ip='203.0.113.5', **headers):
        return self.client.post(self.url, data=corpo, content_type='application/json', REMOTE_ADDR=ip, **headers)

    def test_sem_segredo_nem_allowlist_recusa_com_503(self):
        self.assertEqual(self._post().status_code, 503)

    @override_settings(ZENVIA_ALLOWED_IPS=['abc', '10.0.0.0/8'])
    def test_allowlist_de_cidr_e_entrada_invalida_ignorada(self):
        self.assertEqual(self._post(ip='10.1.2.3').status_code, 200)
        self.assertEqual(self._post(ip='192.168.0.1').status_code, 403)

    @override_settings(ZENVIA_WEBHOOK_SECRET=SEGREDO)
    def test_segredo_no_header(self):
        self.assertEqual(self._post().status_code, 403)
        self.assertEqual(self._post(HTTP_X_ZENVIA_SIGNATURE='errado').status_code, 403)
        self.assertEqual(self._post(HTTP_X_ZENVIA_SIGNATURE='çã').status_code, 403)  # nao 500
        self.assertEqual(self._post(HTTP_X_ZENVIA_SIGNATURE=SEGREDO).status_code, 200)

    @override_settings(ZENVIA_WEBHOOK_SECRET=SEGREDO)
    def test_corpo_invalido_e_400(self):
        for corpo in ('x', '[]', '\xff'):
            with self.subTest(corpo=corpo):
                self.assertEqual(self._post(corpo=corpo, HTTP_X_ZENVIA_SIGNATURE=SEGREDO).status_code, 400)
        estranho = '{"messageStatus": "texto"}'
        self.assertEqual(self._post(corpo=estranho, HTTP_X_ZENVIA_SIGNATURE=SEGREDO).status_code, 200)


class HandshakeMetaTests(TestCase):
    VERIFY = 'verify-token-' + 'p4' * 10

    def _get(self, token, modo='subscribe'):
        return self.client.get(reverse('aranha:whatsapp_webhook'),
                               {'hub.mode': modo, 'hub.verify_token': token, 'hub.challenge': '12345'})

    @override_settings(WHATSAPP_VERIFY_TOKEN=VERIFY)
    def test_token_certo_ecoa_o_challenge(self):
        resp = self._get(self.VERIFY)
        self.assertEqual((resp.status_code, resp.content), (200, b'12345'))

    @override_settings(WHATSAPP_VERIFY_TOKEN=VERIFY)
    def test_token_errado_vazio_ou_modo_errado_e_403(self):
        for token, modo in (('errado', 'subscribe'), ('', 'subscribe'), ('çã', 'subscribe'), (self.VERIFY, 'x')):
            with self.subTest(token=token, modo=modo):
                self.assertEqual(self._get(token, modo).status_code, 403)

    @override_settings(WHATSAPP_VERIFY_TOKEN='')
    def test_sem_token_configurado_ninguem_confirma(self):
        self.assertEqual(self._get('').status_code, 403)
