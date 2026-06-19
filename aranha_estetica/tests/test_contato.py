"""Testes do formulario de contato publico (view agenda_contato)."""
from unittest.mock import patch

from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse


@override_settings(RATELIMIT_ENABLE=False)
class ContatoViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = reverse('aranha:agenda_contato')

    # ── helpers ──────────────────────────────────────────────────────────────

    def _post(self, **overrides):
        data = {
            'name': 'Ana Silva',
            'email': 'ana@exemplo.com.br',
            'phone': '17988880000',
            'subject': 'agendamento',
            'message': 'Gostaria de agendar uma avaliacao facial.',
            'privacy': '1',
        }
        data.update(overrides)
        return self.client.post(self.url, data)

    # ── GET ──────────────────────────────────────────────────────────────────

    def test_get_renderiza_formulario(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Contato')
        self.assertContains(resp, 'name="name"')
        self.assertContains(resp, 'name="email"')
        self.assertContains(resp, 'name="subject"')
        self.assertContains(resp, 'name="message"')
        self.assertContains(resp, 'name="privacy"')

    # ── POST vazio / invalido — nao envia email ───────────────────────────────

    def test_post_sem_nome_mostra_erro_sem_enviar_email(self):
        resp = self._post(name='')
        # re-render (nao redireciona)
        self.assertEqual(resp.status_code, 200)
        messages_list = list(resp.context['messages'])
        self.assertTrue(
            any('obrigatorio' in str(m).lower() for m in messages_list),
            'Esperava mensagem de erro de validacao',
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_post_sem_email_mostra_erro(self):
        resp = self._post(email='')
        self.assertEqual(resp.status_code, 200)
        messages_list = list(resp.context['messages'])
        self.assertTrue(any('obrigatorio' in str(m).lower() for m in messages_list))
        self.assertEqual(len(mail.outbox), 0)

    def test_post_sem_subject_mostra_erro(self):
        resp = self._post(subject='')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_post_sem_message_mostra_erro(self):
        resp = self._post(message='')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_post_sem_privacy_mostra_erro(self):
        resp = self._post(privacy='')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    # ── POST valido — redireciona e envia email ───────────────────────────────

    def test_post_valido_redireciona_prg(self):
        resp = self._post()
        self.assertRedirects(resp, self.url)

    def test_post_valido_mensagem_sucesso(self):
        resp = self._post(follow=True)
        # Apos follow=True, as mensagens ficam nos cookies/storage do request
        # Verificamos via get_messages storage ou pelo conteudo renderizado
        messages_list = list(resp.wsgi_request._messages)
        self.assertTrue(
            any('enviada' in str(m).lower() for m in messages_list),
            'Esperava mensagem de sucesso apos envio',
        )

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        CLINIC_EMAIL='clinica@test.com',
        DEFAULT_FROM_EMAIL='noreply@test.com',
    )
    def test_post_valido_envia_email_para_clinica(self):
        self._post(subject='orcamento', name='Carlos', message='Quero um orcamento.')
        self.assertEqual(len(mail.outbox), 1)
        enviado = mail.outbox[0]
        self.assertIn('orcamento', enviado.subject.lower())
        self.assertIn('Carlos', enviado.body)
        self.assertIn('clinica@test.com', enviado.recipients())

    # ── Falha de email nao derruba a request ─────────────────────────────────

    def test_falha_de_email_ainda_redireciona_com_sucesso(self):
        """Se o envio de email falhar, a view NAO deve levantar 500."""
        with patch('django.core.mail.send_mail', side_effect=Exception('SMTP down')):
            resp = self._post()
        # PRG mesmo com erro de email
        self.assertRedirects(resp, self.url)

    # ── Preserva valores no re-render apos erro ───────────────────────────────

    def test_post_invalido_preserva_valores_no_contexto(self):
        resp = self._post(name='')
        self.assertIn('form_data', resp.context)
        self.assertEqual(resp.context['form_data']['email'], 'ana@exemplo.com.br')

    # ── a11y — labels e iframe no template ───────────────────────────────────

    def test_template_tem_label_para_subject(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, 'for="subject"')

    def test_template_tem_label_para_message(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, 'for="message"')

    def test_template_tem_label_para_privacy(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, 'for="privacy"')

    def test_iframe_mapa_tem_title(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, 'title=')
        # garante que e iframe com title, nao so qualquer tag com title
        content = resp.content.decode()
        self.assertIn('<iframe', content)
        import re
        iframe_match = re.search(r'<iframe[^>]*title=[^>]*>', content)
        self.assertIsNotNone(iframe_match, 'iframe do mapa deve ter atributo title')
