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
