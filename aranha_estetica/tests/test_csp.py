"""Garante que CSP nao volta a permitir 'unsafe-inline' em script-src/style-src.

Regressao prevention: Lote 3 removeu unsafe-inline desses contextos.
Se alguem reverter por engano, este teste pega.
"""
from django.test import Client, TestCase
from django.urls import reverse


class CspHeaderTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _get_csp(self):
        resp = self.client.get(reverse('aranha:inicio'))
        self.assertEqual(resp.status_code, 200)
        return resp.headers.get('Content-Security-Policy', '')

    def test_script_src_sem_unsafe_inline(self):
        csp = self._get_csp()
        script_src = next(
            (part for part in csp.split(';') if part.strip().startswith('script-src ')),
            '',
        )
        self.assertNotIn("'unsafe-inline'", script_src, msg=f'script-src ainda tem unsafe-inline: {script_src}')

    def test_style_src_sem_unsafe_inline(self):
        csp = self._get_csp()
        style_src = next(
            (part for part in csp.split(';') if part.strip().startswith('style-src ')),
            '',
        )
        self.assertNotIn("'unsafe-inline'", style_src, msg=f'style-src ainda tem unsafe-inline: {style_src}')

    def test_csp_inclui_nonce(self):
        csp = self._get_csp()
        self.assertIn("'nonce-", csp)

    def test_frame_ancestors_none(self):
        csp = self._get_csp()
        self.assertIn("frame-ancestors 'none'", csp)
