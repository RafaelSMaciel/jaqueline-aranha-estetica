"""Versao texto dos e-mails (multipart/alternative): sem CSS e com os links.

Era strip_tags(html): comecava com ~13 linhas do <style> (filtro anti-spam, preview e relogio
leem esta parte) e perdia o href de todo botao.
"""
from pathlib import Path

from django.conf import settings
from django.core import mail
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings

from aranha_estetica.utils import email as email_utils

CONTEXTO = {
    'clinic_name': 'Clinica X', 'site_url': 'https://clinica.test', 'preheader': 'Resumo',
    'codigo': '123456', 'unsub_url': 'https://clinica.test/lgpd/unsubscribe/tok/',
    'dados': {'nome': 'Maria', 'procedimento': 'Limpeza', 'profissional': 'Dra. Ana',
              'data_hora': '01/11/2026 às 10:00', 'link': 'https://clinica.test/termo/tok/'},
}


class HtmlParaTextoTests(SimpleTestCase):
    def test_todos_os_templates_saem_sem_css_nem_tags(self):
        pasta = Path(settings.BASE_DIR) / 'aranha_estetica' / 'templates' / 'email'
        templates = sorted(p.name for p in pasta.glob('*.html') if p.name != 'base_email.html')
        self.assertGreaterEqual(len(templates), 9)
        for nome in templates:
            with self.subTest(template=nome):
                texto = email_utils.html_para_texto(render_to_string(f'email/{nome}', CONTEXTO))
                for lixo in ('{', '}', '@media', '<', 'font-family'):
                    self.assertNotIn(lixo, texto)
                self.assertIn('Clinica X', texto)

    def test_link_vira_texto_e_url(self):
        texto = email_utils.html_para_texto(
            '<style>p{color:red}</style><p>Oi</p><a href="https://x.test/a/">Abrir</a>'
            '<!--[if mso]><v:rect>x</v:rect><![endif]--><a href="https://x.test/b/">https://x.test/b/</a>')
        self.assertEqual(texto, 'Oi\nAbrir: https://x.test/a/https://x.test/b/\n')


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EnvioTests(TestCase):
    def test_corpo_texto_do_envio_real(self):
        email_utils.enviar_cancelamento_email('cliente@exemplo.com', CONTEXTO['dados'])
        corpo = mail.outbox[0].body
        self.assertNotIn('@media', corpo)
        self.assertIn('Agendar Novamente: http', corpo)   # o botao vira texto + URL
