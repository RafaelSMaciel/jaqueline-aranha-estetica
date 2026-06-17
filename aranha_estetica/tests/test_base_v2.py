from django.test import TestCase
from django.template import engines


class BaseV2Tests(TestCase):
    def _render(self, tema='claro'):
        tmpl = engines['django'].from_string(
            "{% extends 'estrutura/base_v2.html' %}{% block conteudo %}OI-CONTEUDO{% endblock %}"
        )
        return tmpl.render({'tema': tema})

    def test_aplica_tema_claro(self):
        html = self._render('claro')
        self.assertIn('data-theme="claro"', html)
        self.assertIn('OI-CONTEUDO', html)

    def test_aplica_tema_escuro(self):
        self.assertIn('data-theme="escuro"', self._render('escuro'))
