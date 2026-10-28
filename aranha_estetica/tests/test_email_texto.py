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
        self.assertGreaterEqual(len(templates), 7)   # os 7 que utils/email envia (otp/nps sairam)
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


class EmailNoOutlookTests(SimpleTestCase):
    """Outlook (motor do Word) ignora gradiente e padding em <a>: toda faixa com gradiente tem cor
    solida de reserva e todo botao fica num <td bgcolor> (senao: texto branco no branco, link miudo)."""

    def _templates(self):
        pasta = Path(settings.BASE_DIR) / 'aranha_estetica' / 'templates' / 'email'
        return {p.name: p.read_text(encoding='utf-8') for p in pasta.glob('*.html')}

    def test_gradiente_tem_cor_de_reserva(self):
        import re
        sem = []
        for nome, html in self._templates().items():
            for estilo in re.findall(r'style="([^"]*linear-gradient[^"]*)"', html):
                if 'background-color:' not in estilo.split('linear-gradient')[0]:
                    sem.append(f'{nome}: {estilo[:60]}')
        self.assertEqual(sem, [])

    def test_botao_dentro_de_td_com_bgcolor(self):
        import re
        soltos = []
        for nome, html in self._templates().items():
            for m in re.finditer(r'<a href="[^"]+" style="display:inline-block;background-color:', html):
                antes = html[max(0, m.start() - 250):m.start()]
                if '<td bgcolor=' not in antes:
                    soltos.append(nome)
        self.assertEqual(soltos, [])
