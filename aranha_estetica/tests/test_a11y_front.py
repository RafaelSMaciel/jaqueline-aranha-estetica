"""Guardas de acessibilidade do front (leitura de arquivo, custo zero).

Os achados da auditoria visual (foco anulado, dourado ilegivel, toast que some, titulos
pulados...) voltavam a cada tela nova copiada de outra. Estas guardas travam o padrao.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

_APP = Path(settings.BASE_DIR) / 'aranha_estetica'
_TEMPLATES = _APP / 'templates'
_CSS = _APP / 'static' / 'src' / 'css'
_JS = _APP / 'static' / 'src' / 'js'


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
