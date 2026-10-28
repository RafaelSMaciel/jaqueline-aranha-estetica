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
