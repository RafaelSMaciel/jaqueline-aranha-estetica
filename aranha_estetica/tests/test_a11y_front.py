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
