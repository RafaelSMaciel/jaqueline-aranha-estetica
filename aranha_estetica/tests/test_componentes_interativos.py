from django.test import TestCase
from django.template import engines
from django_cotton.compiler_regex import CottonCompiler

_compiler = CottonCompiler()

def render(s, ctx=None):
    compiled = _compiler.process(s)
    return engines['django'].from_string(compiled).render(ctx or {})

class InterativosTests(TestCase):
    def test_toast_aria_live_role_e_sem_innerHTML(self):
        html = render("<c-toast>Olá</c-toast>")
        self.assertIn('aria-live="polite"', html)
        self.assertIn('role="status"', html)
        self.assertIn('Olá', html)
        self.assertNotIn('innerHTML', html)
