"""Tests for context processors."""
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from aranha_estetica.context_processors import clinica_globals


class ClinicaGlobalsTests(TestCase):
    """Tests that clinica_globals injects clinic variables."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_retorna_todas_variaveis(self):
        request = self.factory.get('/')
        ctx = clinica_globals(request)
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
        ctx = clinica_globals(request)
        self.assertEqual(ctx['CLINIC_NAME'], 'Minha Clinica')
        self.assertEqual(ctx['CLINIC_EMAIL'], 'test@test.com')
        self.assertEqual(ctx['CLINIC_PHONE'], '11999999999')

    def test_valores_padrao_quando_env_vazia(self):
        request = self.factory.get('/')
        ctx = clinica_globals(request)
        # Should have defaults, not None
        self.assertIsNotNone(ctx['CLINIC_NAME'])
        self.assertIsNotNone(ctx['SITE_URL'])


class BrandingSemPlaceholderTests(TestCase):
    """Regressao public_front-02/deploy-06: nunca contato ficticio; Branding > env; SITE_URL de settings."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.factory = RequestFactory()

    @patch.dict('os.environ', {'WHATSAPP_NUMERO': '', 'CLINIC_PHONE': '', 'CLINIC_EMAIL': ''})
    def test_contatos_ausentes_ficam_vazios(self):
        ctx = clinica_globals(self.factory.get('/'))
        self.assertEqual(ctx['WHATSAPP_NUMERO'], '')
        self.assertEqual(ctx['CLINIC_PHONE'], '')
        self.assertEqual(ctx['CLINIC_EMAIL'], '')
        self.assertEqual(ctx['CLINIC_PHONE_TEL'], '')

    def test_site_url_vem_de_settings(self):
        from django.test import override_settings
        with override_settings(SITE_URL='https://clinica.exemplo'):
            ctx = clinica_globals(self.factory.get('/'))
        self.assertEqual(ctx['SITE_URL'], 'https://clinica.exemplo')

    @patch.dict('os.environ', {'CLINIC_PHONE': '(17) 3222-1111'})
    def test_telefone_vira_href_tel(self):
        ctx = clinica_globals(self.factory.get('/'))
        self.assertEqual(ctx['CLINIC_PHONE_TEL'], '+551732221111')

    @patch.dict('os.environ', {'CLINIC_PHONE': ''})
    def test_tela_branding_tem_precedencia_sobre_env(self):
        from aranha_estetica.models import Configuracao
        Configuracao.objects.create(chave='CLINIC_PHONE', valor='(17) 98888-7777')
        ctx = clinica_globals(self.factory.get('/'))
        self.assertEqual(ctx['CLINIC_PHONE'], '(17) 98888-7777')

    @patch.dict('os.environ', {'INSTAGRAM_URL': 'javascript:alert(1)', 'THEME_COLOR': 'red;x'})
    def test_valores_inseguros_sao_descartados(self):
        ctx = clinica_globals(self.factory.get('/'))
        self.assertEqual(ctx['INSTAGRAM_URL'], '')
        self.assertEqual(ctx['THEME_COLOR'], '#C9A84C')

    def test_nome_curto_do_pwa(self):
        from aranha_estetica.context_processors import _nome_curto
        self.assertEqual(_nome_curto('Jaqueline Aranha Estética'), 'J. Aranha')
        self.assertEqual(_nome_curto('Spa Zen'), 'Spa Zen')


class NormalizarWhatsappTests(TestCase):
    """Regressao gap4-05: numero da env sem DDI virava wa.me de outro pais (+1)."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def test_normaliza_ddi_e_invalidos(self):
        from aranha_estetica.utils.branding import normalizar_whatsapp
        self.assertEqual(normalizar_whatsapp('17991234567'), '5517991234567')
        self.assertEqual(normalizar_whatsapp('(17) 3232-4567'), '551732324567')
        self.assertEqual(normalizar_whatsapp('+55 (17) 99123-4567'), '5517991234567')
        self.assertEqual(normalizar_whatsapp('5517991234567'), '5517991234567')
        self.assertEqual(normalizar_whatsapp('123'), '')
        self.assertEqual(normalizar_whatsapp(''), '')
        self.assertEqual(normalizar_whatsapp(None), '')

    @patch.dict('os.environ', {'WHATSAPP_NUMERO': '(17) 99123-4567'})
    def test_env_sem_ddi_ganha_55_no_branding(self):
        ctx = clinica_globals(RequestFactory().get('/'))
        self.assertEqual(ctx['WHATSAPP_NUMERO'], '5517991234567')

    @patch.dict('os.environ', {'WHATSAPP_NUMERO': '123'})
    def test_env_invalida_esconde_whatsapp(self):
        ctx = clinica_globals(RequestFactory().get('/'))
        self.assertEqual(ctx['WHATSAPP_NUMERO'], '')
