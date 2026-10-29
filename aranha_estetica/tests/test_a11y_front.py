"""Guardas de acessibilidade do front (leitura de arquivo, custo zero).

Os achados da auditoria visual (foco anulado, dourado ilegivel, toast que some, titulos
pulados...) voltavam a cada tela nova copiada de outra. Estas guardas travam o padrao.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

_APP = Path(settings.BASE_DIR) / 'aranha_estetica'
_TEMPLATES = _APP / 'templates'
_CSS = _APP / 'static' / 'src' / 'css'
_JS = _APP / 'static' / 'src' / 'js'


def _sem_comentarios(css):
    return re.sub(r'/\*.*?\*/', '', css, flags=re.S)


def _templates_html():
    return sorted(_TEMPLATES.rglob('*.html'))


def _sem_camadas(css):
    """CSS sem comentarios e sem os blocos `@layer ... { ... }` (casamento de chaves)."""
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    saida, i = [], 0
    for m in re.finditer(r'@layer[^{;]*\{', css):
        if m.start() < i:
            continue
        saida.append(css[i:m.start()])
        prof, j = 1, m.end()
        while prof and j < len(css):
            prof += {'{': 1, '}': -1}.get(css[j], 0)
            j += 1
        i = j
    saida.append(css[i:])
    return ''.join(saida)


class FocoVisivelTests(SimpleTestCase):
    """Achado 165: `outline-none` (camada utilities) vencia o :focus-visible global (camada base)."""

    def test_regra_de_foco_fica_fora_de_layer(self):
        css = (_CSS / 'app.css').read_text(encoding='utf-8')
        fora = _sem_camadas(css)
        self.assertRegex(fora, r':focus-visible\s*\{\s*outline:\s*2px solid var\(--cor-foco\)')

    def test_templates_nao_tiram_o_contorno_de_foco(self):
        proibido = re.compile(r'(?<![\w-])(?:focus:)?outline-none(?![\w-])|focus:ring-\[#')
        achados = [f'{arq.relative_to(_TEMPLATES)}:{n}'
                   for arq in _templates_html()
                   for n, linha in enumerate(arq.read_text(encoding='utf-8').splitlines(), 1)
                   if proibido.search(linha)]
        self.assertEqual(achados, [])


def _classes(html):
    """Conjuntos de classes de cada atributo class="..." (sem variante: hover:x conta a parte)."""
    return [set(m.group(1).split()) for m in re.finditer(r'class="([^"]*)"', html)]


class ContrasteMarcaTests(SimpleTestCase):
    """Achado 164: branco sobre o dourado da 2,3:1; o texto sobre marca-forte e --cor-sobre-marca."""

    def test_sem_texto_branco_sobre_dourado(self):
        achados = []
        for arq in _templates_html():
            if 'email' in arq.parts:   # e-mail tem paleta propria (sem CSS var)
                continue
            html = arq.read_text(encoding='utf-8')
            if re.search(r'from-\[#C9A84C\]|bg-\[#C9A84C\] text-white', html):
                achados.append(f'{arq.name}: gradiente/fundo dourado cru')
            for cls in _classes(html):
                if 'bg-marca-forte' in cls and 'text-white' in cls:
                    achados.append(f'{arq.name}: {" ".join(sorted(cls))[:80]}')
        self.assertEqual(achados, [])

    def test_tokens_de_acao_de_sucesso_nos_dois_temas(self):
        tokens = (_CSS / 'tokens.css').read_text(encoding='utf-8')
        # :root (claro), [data-theme="escuro"] e o bloco do sistema (prefers-color-scheme)
        self.assertEqual(tokens.count('--cor-sucesso-forte:'), 3)
        self.assertEqual(tokens.count('--cor-sobre-sucesso:'), 3)
        app = (_CSS / 'app.css').read_text(encoding='utf-8')
        self.assertIn('--color-sucesso-forte: var(--cor-sucesso-forte);', app)


class TemaEscuroTests(SimpleTestCase):
    """Achado 163: cor fixa de tema claro (hex) no wizard e no painel ignorava o tokens.css."""

    def test_wizard_js_sem_cor_fixa(self):
        js = (_JS / 'wizard.js').read_text(encoding='utf-8')
        codigo = re.sub(r'//[^\n]*|/\*.*?\*/', '', js, flags=re.S)
        self.assertEqual(re.findall(r'#[0-9a-fA-F]{3,6}\b', codigo), [])
        self.assertNotIn('cssText', codigo)

    def test_wizard_e_visao_geral_sem_cor_de_texto_ou_fundo_fixos(self):
        for nome in ('agendamento/agendamento_publico.html', 'painel/overview.html'):
            html = (_TEMPLATES / nome).read_text(encoding='utf-8')
            # cor de texto/fundo inline em hex e gradiente de tema claro (fallback de var() pode)
            fixos = re.findall(r'(?<![\w-])(?:color|background):\s*#[0-9a-fA-F]{3,6}\b'
                               r'|(?:from|to|bg|text)-\[#[0-9a-fA-F]{3,6}\]'
                               r'|linear-gradient\([^)]*#[0-9a-fA-F]{3,6}', html)
            self.assertEqual(fixos, [], nome)
        # o CSS do agendamento e de Meus agendamentos mora no site.css: so tokens, nenhum hex
        site = _sem_comentarios((_CSS / 'site.css').read_text(encoding='utf-8'))
        self.assertEqual(re.findall(r'#[0-9a-fA-F]{3,6}\b', site), [])


class SitePublicoSemEstiloInlineTests(SimpleTestCase):
    """O CSS das paginas do site mora no static/src/css/site.css (tokens): nada de style="..." nem
    <style> por pagina. Fora: o widget de iframe (CSS proprio, sem o bundle do Vite) e o
    especialidades, cujas regras das abas sao geradas por categoria (precisam do laco do template)."""

    PASTAS = ('publico', 'agendamento', 'estrutura', 'cotton/chrome', 'partials')
    SEM_BUNDLE = {'agendamento/embed.html'}
    STYLE_GERADO = {'publico/especialidades.html'}

    def _arquivos(self):
        for pasta in self.PASTAS:
            for arq in sorted((_TEMPLATES / pasta).rglob('*.html')):
                rel = arq.relative_to(_TEMPLATES).as_posix()
                if rel not in self.SEM_BUNDLE:
                    yield rel, arq.read_text(encoding='utf-8')

    def test_sem_atributo_style(self):
        achados = [rel for rel, html in self._arquivos() if re.search(r'\sstyle="', html)]
        self.assertEqual(achados, [])

    def test_sem_bloco_style_por_pagina(self):
        achados = [rel for rel, html in self._arquivos()
                   if rel not in self.STYLE_GERADO
                   and re.search(r'(?<!<noscript>)<style[\s>]', re.sub(r'\{#.*?#\}', '', html))]
        self.assertEqual(achados, [])

    def test_sem_cor_crua_nas_classes(self):
        achados = [rel for rel, html in self._arquivos() if re.search(r'(?:bg|text|border|from|to)-\[#', html)]
        self.assertEqual(achados, [])


class PaletaDeStatusTests(SimpleTestCase):
    """Achado 167: status do atendimento pintado com 4 paletas (Material) fora dos tokens --st-*."""

    def test_status_so_pelos_tokens(self):
        agenda = (_TEMPLATES / 'profissional' / 'agenda.html').read_text(encoding='utf-8')
        self.assertNotIn('.badge-', agenda)
        self.assertIn("partials/_status_badge.html' with status=atd.status", agenda)
        self.assertEqual(re.findall(r'\.atn-card-\w+\s*\{[^}]*#[0-9a-fA-F]{3,6}', agenda), [])
        lista = (_TEMPLATES / 'painel' / 'agendamentos.html').read_text(encoding='utf-8')
        self.assertIn('status-{{ agend.status }}', lista)
        ficha = (_TEMPLATES / 'painel' / 'cliente_detalhe.html').read_text(encoding='utf-8')
        self.assertEqual(re.findall(r'\.cli-tl-item[^{]*\{[^}]*#[0-9a-fA-F]{3,6}', ficha), [])
        visao = (_TEMPLATES / 'painel' / 'overview.html').read_text(encoding='utf-8')
        self.assertIn("'--st-' + s.toLowerCase() + '-texto'", visao)


class CascaDoPainelTests(TestCase):
    """Achado 169: sem link de pulo, ativo so pela cor, menu fora da tela ainda focavel."""

    def setUp(self):
        from aranha_estetica.models import Usuario
        self.client.force_login(Usuario.objects.create_superuser(
            email='casca@test.com', password='senha-forte-123', nome='Casca'))

    def _html(self, rota):
        resp = self.client.get(reverse(rota))
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_link_de_pulo_e_o_primeiro_link(self):
        html = self._html('aranha:painel_agendamentos')
        corpo = html[html.index('<body'):]
        primeiro = re.search(r'<a\s[^>]*href="([^"]+)"', corpo)
        self.assertEqual(primeiro.group(1), '#conteudo')
        self.assertIn('id="conteudo"', html)

    def test_item_da_tela_aberta_tem_aria_current(self):
        html = self._html('aranha:painel_agendamentos')
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertRegex(html, r'href="%s"[^>]*aria-current="page"' % re.escape(reverse('aranha:painel_agendamentos')))
        # 2FA nunca ficava ativo
        html = self._html('aranha:admin_2fa_setup')
        self.assertRegex(html, r'href="%s"[^>]*aria-current="page"' % re.escape(reverse('aranha:admin_2fa_setup')))

    def test_calendario_na_operacao_logo_apos_a_agenda_e_menu_inerte_no_celular(self):
        html = self._html('aranha:painel_overview')
        menu = html[html.index('id="sidenavMain"'):html.index('</nav>')]
        rotulos = re.findall(r'<span>([^<]+)</span>\s*</a>', menu)
        self.assertEqual(rotulos[:3], ['Visão geral', 'Agenda', 'Calendário'])
        self.assertIn('x-bind:inert="navInerte()"', html)
        js = (_JS / 'app.js').read_text(encoding='utf-8')
        self.assertIn('navInerte()', js)


class ViolacoesDoAxeTests(SimpleTestCase):
    """Apontadas pelo axe-core no crawl de todas as rotas (desktop)."""

    def test_aria_label_so_em_elemento_com_papel(self):
        # aria-label em div/span/p sem role e proibido (aria-prohibited-attr): o leitor ignora
        achados = [f'{arq.relative_to(_TEMPLATES)}: {m.group(0)[:60]}'
                   for arq in _templates_html()
                   for m in re.finditer(r'<(?:div|span|p)\b[^>]*\baria-label=[^>]*>', arq.read_text(encoding='utf-8'))
                   if 'role=' not in m.group(0)]
        self.assertEqual(achados, [])

    def test_campos_sem_rotulo_visivel_tem_nome(self):
        config = (_TEMPLATES / 'painel' / 'configuracoes.html').read_text(encoding='utf-8')
        for campo in re.findall(r'<input type="text"[^>]*>', config):
            self.assertIn('aria-label=', campo)
        compliance = (_TEMPLATES / 'painel' / 'termos_compliance.html').read_text(encoding='utf-8')
        self.assertIn('<select name="tipo" aria-label=', compliance)

    def test_setas_do_calendario_com_nome(self):
        # o JS do calendario saiu do template p/ o modulo do Vite (painel-calendario.js)
        cal = (_TEMPLATES / 'painel' / 'calendar.html').read_text(encoding='utf-8')
        self.assertIn("vite_asset 'aranha_estetica/static/src/js/painel-calendario.js'", cal)
        js = (_JS / 'painel-calendario.js').read_text(encoding='utf-8')
        self.assertIn('buttonHints', js)
        self.assertIn("setAttribute('aria-hidden', 'true')", js)

    def test_rodape_sem_cor_crua(self):
        rodape = (_TEMPLATES / 'cotton' / 'chrome' / 'rodape.html').read_text(encoding='utf-8')
        self.assertIsNone(re.search(r'\[#[0-9a-fA-F]{3,6}\]', rodape))


class TitulosEAbasTests(SimpleTestCase):
    """Achado 170: h1 -> h5/h6 no painel, h3 sem h2 no rodape e tablist sem tabs."""

    def test_titulos_nao_pulam_nivel(self):
        """Pagina (extends) nao pula nivel: o h1 vem da casca (painel) ou da propria pagina."""
        pulos = []
        for arq in _templates_html():
            rel = arq.relative_to(_TEMPLATES).as_posix()
            html = arq.read_text(encoding='utf-8')
            if rel.startswith(('email/', 'cotton/', 'partials/')) or '{% extends' not in html:
                continue
            anterior = 1
            for nivel in (int(n) for n in re.findall(r'<h([1-6])[\s>]', html)):
                if nivel > anterior + 1:
                    pulos.append(f'{rel}: h{anterior} -> h{nivel}')
                    break
                anterior = nivel
        self.assertEqual(pulos, [])

    def test_rodape_comeca_em_h2(self):
        rodape = (_TEMPLATES / 'cotton' / 'chrome' / 'rodape.html').read_text(encoding='utf-8')
        self.assertNotRegex(rodape, r'<h[3-6][\s>]')

    def test_sem_papel_de_aba_sem_aba_de_verdade(self):
        for nome in ('agendamento/agendamento_publico.html', 'publico/especialidades.html'):
            html = (_TEMPLATES / nome).read_text(encoding='utf-8')
            self.assertNotRegex(html, r'role="(tablist|tab|tabpanel)"', nome)
        wizard = (_TEMPLATES / 'agendamento' / 'agendamento_publico.html').read_text(encoding='utf-8')
        self.assertIn('class="cat-tab" data-cat="" aria-pressed="true"', wizard)
        self.assertIn("setAttribute('aria-pressed'", (_JS / 'wizard.js').read_text(encoding='utf-8'))
        esp = (_TEMPLATES / 'publico' / 'especialidades.html').read_text(encoding='utf-8')
        self.assertIn(':focus-visible ~ * label[for=', esp)


class ComentarioDeTemplateTests(SimpleTestCase):
    """{# #} do Django e de UMA linha: quebrado em duas, o texto vaza para a pagina."""

    def test_nenhum_comentario_curto_em_varias_linhas(self):
        import pathlib
        import re
        quebrados = []
        for caminho in pathlib.Path(__file__).resolve().parents[1].joinpath('templates').rglob('*.html'):
            texto = caminho.read_text(encoding='utf-8')
            for m in re.finditer(r'\{#(?:(?!#\}).)*$', texto, re.M):
                quebrados.append(f'{caminho.name}:{texto[:m.start()].count(chr(10)) + 1}')
        self.assertEqual(quebrados, [], 'use {% comment %} para comentario de varias linhas')
