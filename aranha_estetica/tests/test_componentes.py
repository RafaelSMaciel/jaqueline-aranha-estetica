"""Componentes cotton do design system (templates/cotton/) e o contrato com o CSS deles.

O visual mora nas classes de componentes.css (.btn-primario, .cartao, .campo...): os testes de
render conferem a classe emitida e os de CSS conferem que a classe pinta com o token certo.
"""
import re
from pathlib import Path

from django.conf import settings
from django.template import engines
from django.test import SimpleTestCase
from django_cotton.compiler_regex import CottonCompiler

_compiler = CottonCompiler()
_CSS = Path(settings.BASE_DIR) / 'aranha_estetica' / 'static' / 'src' / 'css'


def render(s, ctx=None):
    compiled = _compiler.process(s)
    return engines['django'].from_string(compiled).render(ctx or {})


def _regra(seletor):
    """Corpo da regra CSS de `seletor` em componentes.css (primeira ocorrencia exata)."""
    css = (_CSS / 'componentes.css').read_text(encoding='utf-8')
    m = re.search(r'(?:^|[\s}])' + re.escape(seletor) + r'\s*\{([^}]*)\}', css)
    assert m, f'regra {seletor} ausente'
    return m.group(1)


class BotaoTests(SimpleTestCase):
    def test_botao_solido_e_o_primario_dourado(self):
        html = render("<c-botao variante='solido'>Agendar</c-botao>")
        self.assertIn('Agendar', html)
        self.assertIn('btn-primario', html)
        self.assertIn('background: var(--cor-marca-forte)', _regra('.btn-primario'))
        self.assertIn('color: var(--cor-sobre-marca)', _regra('.btn-primario'))

    def test_botao_ghost_e_o_secundario_com_borda(self):
        html = render("<c-botao variante='ghost'>Ver</c-botao>")
        self.assertIn('btn-secundario', html)
        self.assertIn('border-color: var(--cor-borda-forte)', _regra('.btn-secundario'))

    def test_botao_loading_desabilita(self):
        html = render("<c-botao loading='true'>Enviar</c-botao>")
        self.assertIn('disabled', html)
        self.assertIn('aria-busy="true"', html)

    def test_botao_mescla_class_do_chamador(self):
        # Regressao: class do chamador ia p/ um 2o atributo class (ignorado pelo browser)
        html = render("<c-botao tipo='submit' class='w-full justify-center'>Entrar</c-botao>")
        self.assertEqual(html.count('class="btn '), 1)
        self.assertEqual(html.count(' class='), 1)
        self.assertIn('w-full justify-center', html)
        self.assertIn('btn-primario', html)
        self.assertIn('type="submit"', html)

    def test_botao_com_href_vira_link(self):
        html = render("<c-botao href='/x/' variante='secundario' tamanho='sm'>Ir</c-botao>")
        self.assertRegex(html, r'<a href="/x/"[^>]*class="btn btn-secundario btn-sm"')
        self.assertNotIn('<button', html)

    def test_perigo_preenchido_tem_texto_proprio_nos_dois_temas(self):
        self.assertIn('var(--cor-sobre-perigo)', _regra('.btn-perigo'))
        tokens = (_CSS / 'tokens.css').read_text(encoding='utf-8')
        self.assertEqual(tokens.count('--cor-perigo-forte:'), 3)
        self.assertEqual(tokens.count('--cor-sobre-perigo:'), 3)


class CampoTests(SimpleTestCase):
    def test_campo_tem_label_associado(self):
        html = render("<c-campo nome='email' label='E-mail' />")
        self.assertIn('for="email"', html)
        self.assertIn('id="email"', html)

    def test_campo_mostra_erro_ligado_ao_controle(self):
        html = render("<c-campo nome='email' label='E-mail' erro='invalido' />")
        self.assertIn('invalido', html)
        self.assertIn('class="erro-campo"', html)
        self.assertIn('aria-invalid="true"', html)
        self.assertIn('aria-describedby="email-erro"', html)
        self.assertIn('color: var(--cor-erro)', _regra('.erro-campo'))

    def test_ajuda_e_erro_juntos_no_describedby(self):
        html = render("<c-campo nome='tel' label='Telefone' ajuda='Com DDD' erro='curto' />")
        self.assertIn('aria-describedby="tel-ajuda tel-erro"', html)

    def test_campo_mescla_class_do_chamador(self):
        html = render("<c-campo nome='email' label='E-mail' class='w-full' />")
        self.assertIn('class="campo w-full"', html)
        self.assertIn('border: 1px solid var(--cor-borda-campo)', _regra('.campo'))

    def test_select_e_textarea(self):
        html = render("<c-campo nome='cat' label='Categoria' tipo='select'><option>A</option></c-campo>")
        self.assertRegex(html, r'<select id="cat" name="cat" class="campo"[^>]*><option>A</option></select>')
        html = render("<c-campo nome='obs' label='Obs' tipo='textarea' valor='oi' />")
        self.assertRegex(html, r'<textarea id="obs" name="obs" class="campo"[^>]*>oi</textarea>')

    def test_campo_no_celular_tem_16px(self):
        # abaixo de 16px o Safari do iPhone da zoom ao focar o campo
        self.assertIn('font-size: 1rem', _regra('.campo'))


class CartaoTests(SimpleTestCase):
    def test_card_envolve_slot(self):
        html = render("<c-card>conteudo-x</c-card>")
        self.assertIn('conteudo-x', html)
        self.assertIn('class="cartao cartao-corpo"', html)
        self.assertIn('border: 1px solid var(--cor-borda)', _regra('.cartao'))

    def test_card_mescla_class_do_chamador(self):
        html = render("<c-card class='mt-4'>x</c-card>")
        self.assertEqual(html.count('class='), 1)
        self.assertIn('cartao', html)
        self.assertIn('mt-4', html)

    def test_card_com_titulo_e_acoes(self):
        html = render("<c-card titulo='Pacotes' id='pac'><c-slot name='acoes'><b>A</b></c-slot>corpo</c-card>")
        self.assertIn('<section id="pac" aria-labelledby="pac-titulo" class="cartao"', html)
        self.assertIn('<h2 id="pac-titulo" class="cartao-titulo">', html)
        self.assertIn('<div class="cartao-acoes"><b>A</b></div>', html)


class ComponentesDeTelaTests(SimpleTestCase):
    def test_dialogo_nomeado_pelo_titulo_e_com_fechar(self):
        html = render("<c-dialogo id='d1' titulo='Editar' x-ref='dialogo'>x</c-dialogo>")
        self.assertIn('<dialog id="d1" class="dialogo" aria-labelledby="d1-titulo" x-ref="dialogo">', html)
        self.assertIn('<h2 id="d1-titulo" class="dialogo-titulo">Editar</h2>', html)
        self.assertIn('data-fechar-dialogo aria-label="Fechar"', html)

    def test_abas_sao_links_para_paineis(self):
        html = render(
            "<c-abas rotulo='Ficha'><c-slot name='lista'><c-abas.aba alvo='dados'>Dados</c-abas.aba></c-slot>"
            "<c-abas.painel id='dados'>D</c-abas.painel></c-abas>")
        self.assertIn('<nav class="abas-lista" aria-label="Ficha" data-abas-lista>', html)
        self.assertIn('<a href="#dados" id="aba-dados" class="aba" data-aba="dados"', html)
        self.assertIn('<section id="dados" class="abas-painel" aria-labelledby="aba-dados" data-abas-painel', html)

    def test_menu_de_acoes_tem_nome(self):
        html = render("<c-menu-acoes rotulo='Mais ações para Maria'><a class='menu-item' href='#'>X</a></c-menu-acoes>")
        self.assertIn('<details class="menu-acoes" data-menu-acoes', html)
        self.assertIn('aria-label="Mais ações para Maria"', html)

    def test_vazio_sem_acao_nao_deixa_bloco_vazio(self):
        self.assertNotIn('vazio-acoes', render("<c-vazio titulo='Nada'></c-vazio>"))
        self.assertIn('vazio-acoes', render("<c-vazio titulo='Nada'><a href='#'>Criar</a></c-vazio>"))

    def test_kpi_mostra_variacao_com_seta_e_texto(self):
        html = render("<c-kpi rotulo='Hoje' valor='3' variacao='+2' tendencia='sobe' comparacao='vs. ontem' />")
        self.assertIn('kpi-variacao-sobe', html)
        self.assertIn('+2 <span class="font-normal text-texto-suave">vs. ontem</span>', html)

    def test_selo_e_nota(self):
        self.assertIn('class="selo selo-info"', render("<c-selo tom='info'>Pacote</c-selo>"))
        html = render("<c-nota tom='aviso' titulo='Atenção'>x</c-nota>")
        self.assertIn('role="note" class="nota nota-aviso"', html)

    def test_icone_e_decorativo(self):
        html = render("<c-icone nome='agenda' />")
        self.assertIn('aria-hidden="true"', html)
        self.assertIn('class="w-4 h-4"', html)

    def test_iniciais_do_avatar(self):
        from aranha_estetica.templatetags.painel_tags import iniciais
        self.assertEqual(iniciais('Mariana da Costa'), 'MC')   # antes: 1a e ultima LETRA ("MA")
        self.assertEqual(iniciais('ana'), 'A')
        self.assertEqual(iniciais(None), '')

    def test_tabela_responsiva_tem_legenda(self):
        html = render("<c-tabela legenda='Agenda' responsiva='1'><tbody></tbody></c-tabela>")
        self.assertIn('class="tabela tabela-responsiva"', html)
        self.assertIn('<caption class="sr-only">Agenda</caption>', html)
