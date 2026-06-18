from django.test import TestCase
from django.template import engines
from django_cotton.compiler_regex import CottonCompiler

_compiler = CottonCompiler()

def render(s, ctx=None):
    compiled = _compiler.process(s)
    return engines['django'].from_string(compiled).render(ctx or {})

class ComponentesTests(TestCase):
    def test_botao_solido_texto(self):
        html = render("<c-botao variante='solido'>Agendar</c-botao>")
        self.assertIn('Agendar', html)
        self.assertIn('bg-marca-forte', html)

    def test_botao_ghost(self):
        html = render("<c-botao variante='ghost'>Ver</c-botao>")
        self.assertIn('border-borda', html)

    def test_botao_loading_desabilita(self):
        html = render("<c-botao loading='true'>Enviar</c-botao>")
        self.assertIn('disabled', html)

    def test_card_envolve_slot(self):
        html = render("<c-card>conteudo-x</c-card>")
        self.assertIn('conteudo-x', html)
        self.assertIn('border-borda', html)

    def test_campo_tem_label_associado(self):
        html = render("<c-campo nome='email' label='E-mail' />")
        self.assertIn('for="email"', html)
        self.assertIn('id="email"', html)

    def test_campo_mostra_erro(self):
        html = render("<c-campo nome='email' label='E-mail' erro='invalido' />")
        self.assertIn('invalido', html)
        self.assertIn('text-erro', html)

    def test_badge_status(self):
        html = render("<c-badge status='REALIZADO'>Realizado</c-badge>")
        self.assertIn('Realizado', html)
        self.assertIn('data-status="REALIZADO"', html)
