"""Tests for context processors."""
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from app_shivazen.context_processors import shivazen_globals


class ShivazenGlobalsTests(TestCase):
    """Tests that shivazen_globals injects clinic variables."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_retorna_todas_variaveis(self):
        request = self.factory.get('/')
        ctx = shivazen_globals(request)
        expected_keys = [
            'CLINIC_NAME', 'CLINIC_SUBTITLE', 'CLINIC_EMAIL',
            'CLINIC_PHONE', 'CLINIC_ADDRESS', 'WHATSAPP_NUMERO', 'SITE_URL',
        ]
        for key in expected_keys:
            self.assertIn(key, ctx, f'{key} missing from context')

    @patch.dict('os.environ', {
        'CLINIC_NAME': 'Minha Clinica',
        'CLINIC_EMAIL': 'test@test.com',
        'CLINIC_PHONE': '11999999999',
    })
    def test_le_variaveis_de_ambiente(self):
        request = self.factory.get('/')
        ctx = shivazen_globals(request)
        self.assertEqual(ctx['CLINIC_NAME'], 'Minha Clinica')
        self.assertEqual(ctx['CLINIC_EMAIL'], 'test@test.com')
        self.assertEqual(ctx['CLINIC_PHONE'], '11999999999')

    def test_valores_padrao_quando_env_vazia(self):
        request = self.factory.get('/')
        ctx = shivazen_globals(request)
        # Should have defaults, not None
        self.assertIsNotNone(ctx['CLINIC_NAME'])
        self.assertIsNotNone(ctx['SITE_URL'])
