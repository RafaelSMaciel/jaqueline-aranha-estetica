"""Varredura visual de TODAS as paginas no navegador (Playwright + Chromium headless + axe-core).

Cada rota PUBLICA/STAFF/PROFISSIONAL de tests/test_rotas.ROTAS e aberta no papel certo em duas
larguras — desktop (1366px, tema claro) e celular (390px, tema escuro: cobre os dois temas sem
uma 3a passada) — e reprova em:
  - resposta HTTP de erro (a pagina ou qualquer recurso do proprio site);
  - erro de JavaScript (pageerror) ou erro no console (inclui violacao de CSP);
  - rolagem lateral no celular (a pagina mais larga que a tela);
  - sintaxe de template vazando no texto ({# #}, {% %}, {{ }});
  - violacao serious/critical do axe-core (WCAG 2.0/2.1 A e AA).
Excecao conhecida de terceiro so pela lista PERMITIDOS, com o motivo.

O axe vem do node_modules (devDependency, `npm ci`), injetado por add_init_script (fora da CSP da
pagina, que segue valendo). Pulado sem Playwright/Chromium/bundle do Vite, como o teste do wizard;
no CI (job navegador, EXIGE_NAVEGADOR=1) reprova em vez de pular.
Local: `npm ci && npm run build`, `playwright install chromium`, TESTE_VITE_BUILD=1.
"""
import os
import re
from pathlib import Path
from unittest import SkipTest

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings

from . import test_rotas as rotas

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depende do ambiente
    sync_playwright = None

AXE = Path(settings.BASE_DIR) / 'node_modules' / 'axe-core' / 'axe.min.js'
LARGURAS = {
    'desktop': dict(viewport={'width': 1366, 'height': 900}, color_scheme='light'),
    'celular': dict(viewport={'width': 390, 'height': 844}, color_scheme='dark', is_mobile=True, has_touch=True),
}
PAPEL_DO_PERFIL = {rotas.PUBLICA: None, rotas.STAFF: 'admin', rotas.PROFISSIONAL: 'profissional'}
VAZAMENTO = re.compile(r'\{#|#\}|\{%|%\}|\{\{|\}\}')
# (rota, regra do axe ou 'console') -> motivo. So problema de terceiro que nao da p/ corrigir aqui.
PERMITIDOS: dict[tuple[str, str], str] = {}


def _pular(motivo):
    if os.environ.get('EXIGE_NAVEGADOR', '').strip() == '1':
        raise RuntimeError(f'EXIGE_NAVEGADOR=1: {motivo}')
    raise SkipTest(motivo)


@override_settings(RATELIMIT_ENABLE=False, ADMIN_2FA_OBRIGATORIO=False)
class VarreduraVisualTests(StaticLiveServerTestCase):
    serialized_rollback = True  # preserva dados das data migrations p/ os proximos testes

    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            _pular('Playwright nao instalado')
        if settings.DJANGO_VITE['default']['dev_mode']:
            _pular('sem bundle do Vite: npm run build e TESTE_VITE_BUILD=1')
        if not AXE.exists():
            _pular('axe-core ausente: npm ci (devDependency)')
        cls._async_unsafe_antes = os.environ.get('DJANGO_ALLOW_ASYNC_UNSAFE')
        os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'   # ORM do teste com o event loop do Playwright
        cls._pw = sync_playwright().start()
        try:
            cls._browser = cls._pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - navegador ausente = pular
            cls._pw.stop()
            cls._restaurar_env()
            _pular(f'Chromium indisponivel: {exc}')
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._browser.close()
        cls._pw.stop()
        cls._restaurar_env()

    @classmethod
    def _restaurar_env(cls):
        if cls._async_unsafe_antes is None:
            os.environ.pop('DJANGO_ALLOW_ASYNC_UNSAFE', None)
        else:
            os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = cls._async_unsafe_antes

    def _cookie_de(self, usuario):
        c = Client()
        c.force_login(usuario)
        return {'name': settings.SESSION_COOKIE_NAME, 'value': c.cookies[settings.SESSION_COOKIE_NAME].value,
                'url': self.live_server_url}

    def _visitar(self, page, nome, caminho, largura):
        """Abre a pagina e devolve a lista de problemas (texto) encontrados nela."""
        eventos = []
        base = self.live_server_url
        page.on('pageerror', lambda exc: eventos.append(('js', f'erro de JS: {exc}')))
        page.on('console', lambda m: eventos.append(('console', f'console: {m.text}')) if m.type == 'error' else None)
        page.on('response', lambda r: eventos.append(('http', f'HTTP {r.status} {r.url[len(base):]}'))
                if r.status >= 400 and r.url.startswith(base) else None)
        page.on('requestfailed', lambda r: eventos.append(('http', f'falhou {r.url} ({r.failure})'))
                if r.url.startswith(base) else None)

        resp = page.goto(base + caminho, wait_until='networkidle', timeout=30000)
        problemas = []
        if resp is None or resp.status != 200:
            problemas.append(f'status {resp.status if resp else "sem resposta"}')
        if 'text/html' not in ((resp and resp.headers.get('content-type')) or ''):
            return problemas   # JSON/texto (ex.: liveness): so o status importa
        if largura == 'celular' and page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 1'):
            mais_largo = page.evaluate("""() => {
                const w = window.innerWidth; let pior = null;
                for (const el of document.querySelectorAll('body *')) {
                  const r = el.getBoundingClientRect();
                  if (r.right > w + 1 && (!pior || r.right > pior.r)) pior = {r: r.right, t: el.tagName + '.' + el.className};
                }
                return pior ? pior.t.slice(0, 80) + ' (' + Math.round(pior.r) + 'px)' : '?';
            }""")
            problemas.append(f'rolagem lateral no celular: {mais_largo}')
        texto = page.evaluate('document.body.innerText')
        for m in VAZAMENTO.finditer(texto):
            problemas.append(f'template vazando no texto: ...{texto[max(0, m.start() - 30):m.end() + 30]!r}...')
            break
        violacoes = page.evaluate("""async () => {
            const r = await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']},
                                               resultTypes: ['violations']});
            return r.violations.filter(v => v.impact === 'serious' || v.impact === 'critical')
              .map(v => ({id: v.id, impact: v.impact, alvos: v.nodes.slice(0, 3).map(n => n.target.join(' '))}));
        }""")
        for v in violacoes:
            if (nome, v['id']) not in PERMITIDOS:
                problemas.append(f"axe {v['id']} [{v['impact']}]: {'; '.join(v['alvos'])[:200]}")
        for tipo, texto_evento in eventos:
            if tipo == 'console' and (nome, 'console') in PERMITIDOS:
                continue
            problemas.append(texto_evento[:300])
        return problemas

    def test_paginas_sem_erro_rolagem_vazamento_nem_violacao_grave(self):
        dados, admin, usuario_prof = rotas.montar_dados()
        cookies = {'admin': self._cookie_de(admin), 'profissional': self._cookie_de(usuario_prof)}
        helper = rotas.RotasTests()
        helper.dados = dados
        alvo = os.environ.get('VARREDURA_ROTAS', '')   # filtro opcional p/ depurar uma tela
        achados = []
        for nome, (_, _, perfil) in sorted(rotas.ROTAS.items()):
            if perfil not in PAPEL_DO_PERFIL or (alvo and alvo not in nome):
                continue
            papel = PAPEL_DO_PERFIL[perfil]
            caminho = helper._url(nome)
            for largura, opcoes in LARGURAS.items():
                ctx = self._browser.new_context(locale='pt-BR', timezone_id='America/Sao_Paulo',
                                                reduced_motion='reduce', service_workers='block', **opcoes)
                ctx.add_init_script(path=str(AXE))
                extras = [{'name': 'cookie_consent', 'value': 'essenciais', 'url': self.live_server_url}]
                if papel:
                    extras.append(cookies[papel])
                ctx.add_cookies(extras)
                page = ctx.new_page()
                try:
                    problemas = self._visitar(page, nome, caminho, largura)
                except Exception as exc:  # noqa: BLE001 - navegacao que estoura vira achado, nao aborta a varredura
                    problemas = [f'{exc.__class__.__name__}: {str(exc)[:200]}']
                finally:
                    ctx.close()
                achados += [f'{nome} [{largura}] {caminho}: {p}' for p in problemas]
        self.assertEqual(achados, [], '\n' + '\n'.join(achados))
