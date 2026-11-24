from django.test import TestCase, RequestFactory
from aranha_estetica.context_processors import tema_atual


class TemaContextTests(TestCase):
    def test_sem_cookie_vale_o_padrao_claro(self):
        req = RequestFactory().get('/')
        self.assertEqual(tema_atual(req)['tema'], '')

    def test_le_cookie(self):
        req = RequestFactory().get('/')
        req.COOKIES['tema'] = 'escuro'
        self.assertEqual(tema_atual(req)['tema'], 'escuro')

    def test_cookie_invalido_vale_o_padrao_claro(self):
        req = RequestFactory().get('/')
        req.COOKIES['tema'] = 'xpto'
        self.assertEqual(tema_atual(req)['tema'], '')
