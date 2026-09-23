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

    def test_jsdelivr_so_por_caminho_da_lib(self):
        # Host inteiro liberado = qualquer pacote npm/GitHub roda apesar do nonce
        csp = self._get_csp()
        for nome in ('script-src', 'style-src', 'font-src'):
            with self.subTest(diretiva=nome):
                self.assertNotIn('https://cdn.jsdelivr.net ', _diretiva(csp, nome) + ' ')
        script_src = _diretiva(csp, 'script-src')
        self.assertFalse(permitido_pela_csp('https://cdn.jsdelivr.net/gh/evil/x.js', script_src))
        self.assertFalse(permitido_pela_csp('https://cdn.jsdelivr.net/npm/angular@1.8.3/angular.js', script_src))

    def test_libs_de_cdn_dos_templates_liberadas_na_csp(self):
        # Trocou a versao no template sem atualizar JSDELIVR_PATHS -> quebra em prod
        csp = self._get_csp()
        script_src, style_src = _diretiva(csp, 'script-src'), _diretiva(csp, 'style-src')
        padrao = re.compile(r'<(script|link)\b[^>]*?(?:src|href)="(https://cdn\.jsdelivr\.net/[^"]+)"')
        urls = []
        for arq in TEMPLATES_DIR.rglob('*.html'):
            for tag, url in padrao.findall(arq.read_text(encoding='utf-8')):
                urls.append(url)
                diretiva = script_src if tag == 'script' else style_src
                with self.subTest(template=str(arq.relative_to(TEMPLATES_DIR)), url=url):
                    self.assertTrue(permitido_pela_csp(url, diretiva), f'{url} bloqueada pela CSP')
        self.assertTrue(urls, 'nenhuma lib de CDN encontrada: revise o teste')

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


class CdnComSriTests(TestCase):
    """Lib de CDN sem integrity = arquivo trocado no jsDelivr roda com o nonce da pagina."""

    def test_scripts_e_css_de_cdn_com_sri(self):
        padrao = re.compile(r'<(?:script|link)\b[^>]*?(?:src|href)="(https://cdn\.jsdelivr\.net/[^"]+)"[^>]*>')
        urls = []
        for arq in TEMPLATES_DIR.rglob('*.html'):
            for m in padrao.finditer(arq.read_text(encoding='utf-8')):
                tag, url = m.group(0), m.group(1)
                urls.append(url)
                with self.subTest(template=str(arq.relative_to(TEMPLATES_DIR)), url=url):
                    self.assertRegex(tag, r'\bintegrity="sha384-[A-Za-z0-9+/]{64}"')
                    self.assertIn('crossorigin="anonymous"', tag)
                    # .min.js que o pacote nao publica e gerado pelo jsDelivr na
                    # hora (o proprio arquivo avisa: nao usar SRI) — hash quebraria
                    self.assertFalse(url.endswith('chart.umd.min.js'), url)
        self.assertTrue(urls, 'nenhuma lib de CDN encontrada: revise o teste')


@override_settings(ADMIN_2FA_OBRIGATORIO=False)
class SwaggerStyleComNonceTests(TestCase):
    def test_style_do_swagger_usa_o_nonce_da_csp(self):
        from aranha_estetica.models import Usuario
        admin_user = Usuario.objects.create_user(
            email='swagger@test.com', password='Senha-Forte-2026!', nome='Swagger',
            papel=Usuario.PAPEL_ADMIN,
        )
        c = Client()
        c.force_login(admin_user)
        resp = c.get(reverse('aranha:swagger-ui'))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        m = re.search(r'<style nonce="([^"]+)"', html)
        self.assertIsNotNone(m, 'swagger sem <style nonce=...>')
        self.assertIn(f"'nonce-{m.group(1)}'", resp['Content-Security-Policy'])
        self.assertEqual(re.findall(r'<style(?![^>]*nonce=)', html), [])
