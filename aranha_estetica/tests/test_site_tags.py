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
