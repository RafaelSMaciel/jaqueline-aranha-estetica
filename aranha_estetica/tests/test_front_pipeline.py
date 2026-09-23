"""Guardas de regressao do pipeline de assets do front (fundacao).

Os testes de render so checam string HTML e nao pegam config de build/tema
quebrada (4 bugs achados so na verificacao visual). Estas guardas travam os 3
que afetam producao, a custo zero (leitura de arquivo / settings).
"""
import json
import os
import re
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import reverse

_SRC = Path(settings.BASE_DIR) / 'aranha_estetica' / 'static' / 'src' / 'css'


class FrontPipelineGuards(TestCase):
    def test_seletor_dark_casa_valor_do_app(self):
        # O app usa 'escuro' (PT) em context processor/cookie/html.
        # O seletor CSS dark precisa casar isso — nao 'dark' (EN).
        tokens = (_SRC / 'tokens.css').read_text(encoding='utf-8')
        self.assertIn('[data-theme="escuro"]', tokens)
        self.assertNotIn('[data-theme="dark"]', tokens)

    def test_app_css_escaneia_templates_django(self):
        # Tailwind v4 nao auto-detecta templates Django; sem @source nenhum
        # utilitario das classes nos .html e gerado.
        app = (_SRC / 'app.css').read_text(encoding='utf-8')
        self.assertIn('@source', app)
        self.assertIn('templates', app)

    def test_django_vite_prefixo_casa_base_do_vite(self):
        # vite.config base '/static/dist/' exige static_url_prefix='dist',
        # senao as URLs de asset ficam sem /dist/ e dao 404.
        self.assertEqual(
            settings.DJANGO_VITE['default'].get('static_url_prefix'), 'dist',
        )


    def test_htmx_fora_do_bundle(self):
        # HTMX sem uso injetava <style> sem nonce (violacao de CSP em toda pagina)
        app_js = (_SRC.parent / 'js' / 'app.js').read_text(encoding='utf-8')
        self.assertNotIn("import 'htmx.org'", app_js)

    def test_cookie_consent_persistido_em_cookie(self):
        app_js = (_SRC.parent / 'js' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('cookie_consent=', app_js)
        self.assertIn('max-age=31536000', app_js)

    def test_dourado_de_texto_passa_aa_no_tema_claro(self):
        # text-marca no claro: #7A5F1F (5,8:1). #C9A84C como texto dava 2,2:1.
        tokens = (_SRC / 'tokens.css').read_text(encoding='utf-8')
        claro = tokens.split('[data-theme="escuro"]')[0]
        self.assertIn('--cor-marca: var(--ouro-700);', claro)
        self.assertIn('--ouro-700: #7A5F1F;', claro)

    def test_sw_nao_cacheia_html_privado(self):
        sw = (Path(settings.BASE_DIR) / 'aranha_estetica' / 'templates' / 'pwa' / 'sw.js').read_text(encoding='utf-8')
        self.assertIn('PUBLIC_PAGES', sw)
        self.assertNotIn("'/api/dias-disponiveis'", sw)
        self.assertNotIn('logo-sem-fundo', sw)


_WEBPUSH_JS = Path(settings.BASE_DIR) / 'aranha_estetica' / 'static' / 'js' / 'webpush.js'


class WebpushCsrfTests(TestCase):
    """Regressao crawl-1: no portal do profissional o botao 'Ativar avisos' mandava
    X-CSRFToken vazio (sem meta e cookie HttpOnly) -> 403, e o JS dava sucesso."""

    def test_js_le_token_do_form_e_confere_resposta(self):
        js = _WEBPUSH_JS.read_text(encoding='utf-8')
        self.assertIn('input[name="csrfmiddlewaretoken"]', js)
        self.assertIn('if (!resp.ok) throw', js)
        # ja inscrito no navegador nao encerra sem re-registrar no servidor
        self.assertNotIn('if (sub) return true', js)
        self.assertIn('return registrar(sub)', js)

    @override_settings(ADMIN_2FA_OBRIGATORIO=False)
    @patch.dict(os.environ, {'WEBPUSH_VAPID_PUBLIC_KEY': 'chave-publica-teste'})
    def test_token_da_pagina_do_portal_passa_no_csrf_do_subscribe(self):
        from aranha_estetica.models import AssinaturaPush, Profissional, Usuario
        prof = Profissional.objects.create(nome='Dra. Push', ativo=True)
        user = Usuario.objects.create_user(
            email='push@test.com', password='SenhaForte#2026', nome='Dra. Push',
            papel=Usuario.PAPEL_PROFISSIONAL, profissional=prof,
        )
        c = Client(enforce_csrf_checks=True)
        c.force_login(user)
        html = c.get(reverse('aranha:profissional_agenda')).content.decode()
        self.assertIn('id="webpushEnable"', html)
        self.assertIn('js/webpush.js', html)
        m = re.search(r'name="csrf-token" content="([^"]+)"', html) or             re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
        self.assertIsNotNone(m, 'pagina sem token CSRF legivel pelo webpush.js')

        corpo = json.dumps({'endpoint': 'https://push.example.com/abc',
                            'keys': {'p256dh': 'p' * 20, 'auth': 'a' * 10}})
        sem_token = c.post(reverse('aranha:webpush_subscribe'), corpo,
                           content_type='application/json', HTTP_X_CSRFTOKEN='')
        self.assertEqual(sem_token.status_code, 403)
        resp = c.post(reverse('aranha:webpush_subscribe'), corpo,
                      content_type='application/json', HTTP_X_CSRFTOKEN=m.group(1))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(AssinaturaPush.objects.filter(user=user).count(), 1)
