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


def permitido_pela_csp(url, diretiva):
    """URL casa com alguma fonte da diretiva (fonte com '/' final = prefixo)."""
    for fonte in diretiva.split()[1:]:
        if fonte.count('/') == 2:  # so o host ('https://x.com'): qualquer caminho
            fonte += '/'
        if url == fonte or (fonte.endswith('/') and url.startswith(fonte)):
            return True
    return False


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
        for host in ('unpkg.com', 'code.jquery.com', 'google-analytics.com', 'cdnjs.cloudflare.com'):
            with self.subTest(host=host):
                self.assertNotIn(host, csp)

    def test_sem_cdn_de_pacotes_na_csp(self):
        # FullCalendar e Chart.js vem do npm via Vite: o jsdelivr (que serve QUALQUER pacote
        # npm/GitHub) nao fica liberado em diretiva nenhuma, nem por caminho
        csp = self._get_csp()
        self.assertNotIn('jsdelivr', csp)
        for nome in ('script-src', 'style-src', 'font-src', 'connect-src'):
            with self.subTest(diretiva=nome):
                self.assertFalse(permitido_pela_csp('https://cdn.jsdelivr.net/npm/fullcalendar@6.1.11/index.global.min.js',
                                                    _diretiva(csp, nome)))
        script_src = _diretiva(csp, 'script-src')
        self.assertFalse(permitido_pela_csp('https://cdn.jsdelivr.net/gh/evil/x.js', script_src))
        # unico host externo de script: o Turnstile (captcha dos formularios publicos)
        externos = [f for f in script_src.split()[1:] if f.startswith('http') and 'localhost' not in f]
        self.assertEqual(externos, ['https://challenges.cloudflare.com'])

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


class SemScriptExternoTests(TestCase):
    """Lib de terceiro entra pelo package.json (bundle do Vite, arquivo com hash), nunca por
    <script>/<link> de CDN: arquivo trocado no CDN rodaria com o nonce da pagina."""

    PERMITIDOS = ('https://challenges.cloudflare.com/',)   # Turnstile (partials/_antiabuso.html)

    def test_templates_sem_script_ou_css_de_outro_host(self):
        padrao = re.compile(r'<(?:script|link)\b[^>]*?(?:src|href)="(https?://[^"]+)"[^>]*>')
        achados = []
        for arq in TEMPLATES_DIR.rglob('*.html'):
            if 'email' in arq.relative_to(TEMPLATES_DIR).parts:
                continue
            for m in padrao.finditer(arq.read_text(encoding='utf-8')):
                tag, url = m.group(0), m.group(1)
                if tag.startswith('<link') and 'rel="stylesheet"' not in tag and 'rel="preload"' not in tag:
                    continue   # canonical/alternate/manifest nao carregam codigo
                if not url.startswith(self.PERMITIDOS):
                    achados.append(f'{arq.relative_to(TEMPLATES_DIR)}: {url}')
        self.assertEqual(achados, [])

    def test_libs_do_painel_sao_dependencias_npm_fixas(self):
        import json
        pacote = json.loads((TEMPLATES_DIR.parents[1] / 'package.json').read_text(encoding='utf-8'))
        deps = pacote['dependencies']
        for lib in ('@fullcalendar/core', '@fullcalendar/timegrid', '@fullcalendar/interaction', 'chart.js'):
            with self.subTest(lib=lib):
                self.assertRegex(deps.get(lib, ''), r'^\d+\.\d+\.\d+$')   # versao exata, sem ^/~

    def test_style_src_so_do_proprio_site(self):
        from aranha_estetica.middleware import ContentSecurityPolicyMiddleware as Csp
        self.assertEqual(Csp.ALLOWED_STYLE_SRCS, ["'self'"])
