"""Guardas de regressao da migracao do wizard (Onda 2).

Os bugs principais (estado em sessionStorage, re-hidratacao) sao client-side e
foram verificados no browser. Estes testes travam o que e' testavel no server:
a11y de teclado (cards = <button>) e a remocao do Bootstrap (re-skin base_v2).
"""

from django.test import TestCase
from django.urls import reverse

from .factories import criar_procedimento, criar_profissional


class WizardMigracaoTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        criar_procedimento(profissional=self.prof)

    def _html(self):
        resp = self.client.get(reverse('aranha:agendamento_publico'))
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_proc_card_e_button_nao_div(self):
        # WCAG 2.1.1: card de procedimento precisa ser focavel/acionavel por teclado.
        html = self._html()
        self.assertIn('proc-card', html)
        self.assertRegex(html, r'<button[^>]*class="[^"]*proc-card')

    def test_sem_bootstrap_na_pagina(self):
        # Pagina migrada p/ base_v2 (Tailwind) — nao deve carregar Bootstrap.
        html = self._html()
        self.assertNotIn('bootstrap.min.css', html)
        self.assertNotIn('bootstrap.bundle', html)

    def test_js_do_wizard_externalizado(self):
        # JS saiu do template (CSP-safe) e carrega como arquivo estatico.
        html = self._html()
        self.assertIn('js/wizard.js', html)
        self.assertIn('WIZARD_CFG', html)  # config dos URLs Django
