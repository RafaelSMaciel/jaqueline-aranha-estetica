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


    def test_renderiza_messages_uma_vez(self):
        """Regressao public_front-21: base nao renderizava messages e elas vazavam depois."""
        from django.contrib.messages.storage.base import Message
        html = engines['django'].from_string(
            "{% extends 'estrutura/base_v2.html' %}{% block conteudo %}X{% endblock %}"
        ).render({'tema': 'claro', 'messages': [Message(40, 'Falhou-XYZ')]})
        self.assertEqual(html.count('Falhou-XYZ'), 1)
        self.assertIn('role="alert"', html)

    def test_sem_whatsapp_nao_renderiza_botao_flutuante(self):
        self.assertNotIn('wa.me/', self._render())
