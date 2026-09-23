"""Guardas de regressao do pipeline de assets do front (fundacao).

Os testes de render so checam string HTML e nao pegam config de build/tema
quebrada (4 bugs achados so na verificacao visual). Estas guardas travam os 3
que afetam producao, a custo zero (leitura de arquivo / settings).
"""
from pathlib import Path

from django.conf import settings
from django.test import TestCase

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
