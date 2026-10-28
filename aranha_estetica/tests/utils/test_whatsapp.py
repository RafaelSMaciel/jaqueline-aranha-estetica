"""utils/whatsapp: o cliente HTTP da Cloud API exercitado de verdade (so o requests.post e falso)."""
from unittest import mock

import requests
from django.test import SimpleTestCase, override_settings

from aranha_estetica.utils import whatsapp

POST = 'aranha_estetica.utils.whatsapp.requests.post'
CREDENCIAIS = {'WHATSAPP_TOKEN': 'tok-meta', 'WHATSAPP_PHONE_ID': '123456'}


@override_settings(DEBUG=False)
@mock.patch.dict('os.environ', CREDENCIAIS)
class EnviarTemplateTests(SimpleTestCase):
    def test_payload_url_e_cabecalhos(self):
        components = [{'type': 'body', 'parameters': [{'type': 'text', 'text': 'Bia'}]}]
        with mock.patch(POST, return_value=mock.Mock(status_code=200, text='{}')) as post:
            self.assertTrue(whatsapp.enviar_template('(17) 99999-0000', 'confirmacao_d1', components))
        url = post.call_args.args[0]
        self.assertEqual(url, 'https://graph.facebook.com/v23.0/123456/messages')
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Bearer tok-meta')
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['to'], '5517999990000')
        self.assertEqual(payload['type'], 'template')
        self.assertEqual(payload['template']['name'], 'confirmacao_d1')
        self.assertEqual(payload['template']['language'], {'code': 'pt_BR'})
        self.assertEqual(payload['template']['components'], components)
        self.assertEqual(post.call_args.kwargs['timeout'], whatsapp.TIMEOUT_SEGUNDOS)

    def test_erro_http_e_timeout_devolvem_false(self):
        with mock.patch(POST, return_value=mock.Mock(status_code=400, text='{"error": "x"}')):
            self.assertFalse(whatsapp.enviar_template('17999990000', 'x'))
        with mock.patch(POST, side_effect=requests.Timeout):
            self.assertFalse(whatsapp.enviar_template('17999990000', 'x'))

    def test_texto_livre(self):
        with mock.patch(POST, return_value=mock.Mock(status_code=201, text='{}')) as post:
            self.assertTrue(whatsapp.enviar_texto('17999990000', 'Pronto'))
        payload = post.call_args.kwargs['json']
        self.assertEqual((payload['type'], payload['text']['body']), ('text', 'Pronto'))

    def test_telefone_vazio_nao_chama_a_api(self):
        with mock.patch(POST) as post:
            self.assertFalse(whatsapp.enviar_template('', 'x'))
        post.assert_not_called()

    @mock.patch.dict('os.environ', {'WHATSAPP_TOKEN': '', 'WHATSAPP_PHONE_ID': ''})
    def test_sem_credencial_falha_fechado(self):
        with mock.patch(POST) as post:
            self.assertFalse(whatsapp.enviar_template('17999990000', 'x'))
        post.assert_not_called()
        self.assertFalse(whatsapp.pode_enviar_whatsapp())

    @override_settings(AMBIENTE_TESTE=True, DESTINATARIOS_TESTE=['17911112222'])
    def test_ambiente_de_testes_so_envia_a_lista(self):
        with mock.patch(POST, return_value=mock.Mock(status_code=200, text='{}')) as post:
            self.assertFalse(whatsapp.enviar_template('17999990000', 'x'))
            post.assert_not_called()
            self.assertTrue(whatsapp.enviar_template('17911112222', 'x'))
        post.assert_called_once()

    @override_settings(DEBUG=True)
    def test_debug_so_loga(self):
        with mock.patch(POST) as post:
            self.assertTrue(whatsapp.enviar_template('17999990000', 'x'))
        post.assert_not_called()
