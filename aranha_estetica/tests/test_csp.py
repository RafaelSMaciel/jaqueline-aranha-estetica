"""Garante que CSP nao volta a permitir 'unsafe-inline' em script-src/style-src.

Regressao prevention: Lote 3 removeu unsafe-inline desses contextos.
Se alguem reverter por engano, este teste pega.
"""
import re
from pathlib import Path

from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from aranha_estetica.middleware import ContentSecurityPolicyMiddleware

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / 'templates'


def _diretiva(csp, nome):
    return next(
        (part.strip() for part in csp.split(';') if part.strip().startswith(f'{nome} ')),
        '',
    )


class CspHeaderTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _get_csp(self, url=None):
        resp = self.client.get(url or reverse('aranha:inicio'))
        self.assertEqual(resp.status_code, 200)
        return resp.headers.get('Content-Security-Policy', '')

    def test_script_src_sem_unsafe_inline(self):
        script_src = _diretiva(self._get_csp(), 'script-src')
        self.assertNotIn("'unsafe-inline'", script_src, msg=f'script-src ainda tem unsafe-inline: {script_src}')

    def test_style_src_sem_unsafe_inline(self):
        style_src = _diretiva(self._get_csp(), 'style-src')
        self.assertNotIn("'unsafe-inline'", style_src, msg=f'style-src ainda tem unsafe-inline: {style_src}')

    def test_csp_inclui_nonce(self):
        csp = self._get_csp()
        self.assertIn("'nonce-", csp)

    def test_frame_ancestors_none(self):
        csp = self._get_csp()
        self.assertIn("frame-ancestors 'none'", csp)

    def test_sem_script_src_attr_unsafe_inline(self):
        # Handlers inline (onclick=...) nao sao mais usados: com a diretiva, um
        # <img onerror=...> injetado executaria apesar do nonce.
        csp = self._get_csp()
        self.assertNotIn("script-src-attr 'unsafe-inline'", csp)

    def test_sem_hosts_externos_sem_uso(self):
        csp = self._get_csp()
        for host in ('unpkg.com', 'code.jquery.com', 'google-analytics.com'):
            with self.subTest(host=host):
                self.assertNotIn(host, csp)

    def test_embed_pode_ser_embutido(self):
        try:
            url = reverse('aranha:embed_agendar')
        except NoReverseMatch:
            self.skipTest('widget /embed/agendar/ removido')
        csp = self._get_csp(url)
        self.assertNotIn("frame-ancestors 'none'", csp)
        self.assertIn('frame-ancestors https:', csp)


class FrameAncestorsMiddlewareTests(TestCase):
    """@xframe_options_exempt libera o iframe tambem na CSP (senao prevalece 'none')."""

    def _csp(self, exempt):
        def view(_request):
            resp = HttpResponse('ok')
            if exempt:
                resp.xframe_options_exempt = True
            return resp
        mw = ContentSecurityPolicyMiddleware(view)
        return mw(RequestFactory().get('/'))['Content-Security-Policy']

    def test_resposta_comum_nao_embutivel(self):
        self.assertIn("frame-ancestors 'none'", self._csp(exempt=False))

    def test_resposta_exempt_embutivel_em_https(self):
        self.assertIn('frame-ancestors https:', self._csp(exempt=True))

    @override_settings(EMBED_FRAME_ANCESTORS='https://linktr.ee https://www.instagram.com')
    def test_exempt_respeita_allowlist(self):
        self.assertIn(
            'frame-ancestors https://linktr.ee https://www.instagram.com', self._csp(exempt=True),
        )


class SemHandlersInlineTests(TestCase):
    """Sem script-src-attr 'unsafe-inline', on*= nos templates quebraria em prod."""

    def test_templates_sem_on_handlers(self):
        padrao = re.compile(r'\son[a-z]+\s*=\s*["\']', re.IGNORECASE)
        achados = []
        for arq in TEMPLATES_DIR.rglob('*.html'):
            for n, linha in enumerate(arq.read_text(encoding='utf-8').splitlines(), 1):
                if padrao.search(linha):
                    achados.append(f'{arq.relative_to(TEMPLATES_DIR)}:{n}')
        self.assertEqual(achados, [], 'handlers inline encontrados (use data-* + listener)')
