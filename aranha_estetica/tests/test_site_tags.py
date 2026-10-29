"""site_tags: link "Agendar" unico (wizard ou WhatsApp sem SMS) e menu publico de uma lista so."""
from unittest.mock import patch

from django.template import engines
from django.test import RequestFactory, TestCase
from django.urls import resolve


def _render(texto, **ctx):
    return engines['django'].from_string(texto).render(ctx)


class AgendarLinkTests(TestCase):
    def test_wizard_com_pre_selecao(self):
        self.assertEqual(_render('<a {% agendar_link %}>'), '<a href="/agendamento/">')
        html = _render('<a {% agendar_link profissional=7 procedimento=3 %}>')
        self.assertEqual(html, '<a href="/agendamento/?profissional=7&amp;procedimento=3">')

    def test_sem_sms_vai_para_o_whatsapp(self):
        html = _render('<a {% agendar_link procedimento=3 %}>', AGENDAR_WHATSAPP='https://wa.me/5517999990000?text=Oi')
        self.assertIn('href="https://wa.me/5517999990000?text=Oi" target="_blank" rel="noopener"', html)

    def test_paginas_publicas_seguem_o_whatsapp_sem_sms(self):
        with patch('aranha_estetica.utils.sms.sms_disponivel', return_value=False), \
                patch('aranha_estetica.context_processors.get_branding',
                      return_value={'WHATSAPP_NUMERO': '5517999990000', 'CLINIC_NAME': 'Clinica'}):
            html = self.client.get('/servicos/faciais/').content.decode()
        self.assertNotIn('href="/agendamento/"', html)
        self.assertIn('https://wa.me/5517999990000', html)


class MenuPublicoTests(TestCase):
    def test_item_atual_com_aria_current(self):
        request = RequestFactory().get('/equipe/')
        request.resolver_match = resolve('/equipe/')
        html = _render('{% menu_publico as m %}{% for i in m %}{{ i.rotulo }}:{{ i.atual }};{% endfor %}',
                       request=request)
        self.assertIn('Equipe:True;', html)
        self.assertIn('Início:False;', html)
        pagina = self.client.get('/equipe/').content.decode()
        self.assertEqual(pagina.count('aria-current="page"'), 2)   # nav desktop + celular


class FotoResponsivaTests(TestCase):
    """{% foto %}: <picture> WebP + JPEG das variantes do scripts/imagens.py, sem CLS."""

    def test_variantes_geradas_e_em_dia(self):
        import importlib.util
        from pathlib import Path

        from django.conf import settings
        caminho = Path(settings.BASE_DIR) / 'scripts' / 'imagens.py'
        spec = importlib.util.spec_from_file_location('imagens', caminho)
        try:
            modulo = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(modulo)
        except ImportError:   # Pillow so no requirements-dev: confere so o manifest
            from aranha_estetica.templatetags.site_tags import manifest_fotos
            self.assertTrue(manifest_fotos())
            return
        self.assertEqual(modulo.verificar(), [])

    def test_picture_com_webp_fallback_jpeg_e_dimensoes(self):
        html = _render("{% foto 'facial-2' alt='Cuidado facial' sizes='50vw' classe='w-full' %}")
        self.assertIn('<picture class="foto"><source type="image/webp" srcset="', html)
        for w in (480, 768, 1024):
            self.assertIn(f'assets/clinica/variantes/facial-2-{w}.webp {w}w', html)
            self.assertIn(f'assets/clinica/variantes/facial-2-{w}.jpg {w}w', html)
        self.assertIn('src="/static/assets/clinica/variantes/facial-2-768.jpg"', html)
        self.assertIn('width="1024" height="683"', html)
        self.assertIn('sizes="50vw"', html)
        self.assertIn('alt="Cuidado facial"', html)
        self.assertIn('loading="lazy"', html)
        self.assertNotIn('fetchpriority', html)

    def test_foto_do_topo_tem_prioridade_e_nao_e_lazy(self):
        html = _render("{% foto 'facial-3' alt='x' prioridade=True %}")
        self.assertIn('fetchpriority="high"', html)
        self.assertNotIn('loading="lazy"', html)

    def test_alt_escapado_e_foto_sem_variante_cai_na_original(self):
        html = _render("{% foto 'nova' alt=nome %}", nome='<b>"x"</b>')
        self.assertIn('src="/static/assets/clinica/nova.webp"', html)
        self.assertIn('alt="&lt;b&gt;&quot;x&quot;&lt;/b&gt;"', html)


class NomeDaProfissionalTests(TestCase):
    def test_iniciais_primeiro_nome_e_itens(self):
        self.assertEqual(_render('{{ n|iniciais }}', n='Jaqueline Aranha'), 'JA')
        self.assertEqual(_render('{{ n|iniciais }}', n='Dra. Ana de Souza'), 'AS')
        self.assertEqual(_render('{{ n|iniciais }}', n='Ana'), 'A')
        self.assertEqual(_render('{{ n|primeiro_nome }}', n='Dra. Ana de Souza'), 'Ana')
        self.assertEqual(_render('{% for i in t|itens_especialidade %}[{{ i }}]{% endfor %}',
                                 t='Biomédica · Estética facial ; Injetáveis'),
                         '[Biomédica][Estética facial][Injetáveis]')
