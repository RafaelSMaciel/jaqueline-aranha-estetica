"""Testes do formulario de contato publico (view agenda_contato)."""
from unittest.mock import patch

from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse


@override_settings(
    RATELIMIT_ENABLE=False,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    CLINIC_EMAIL='clinica@test.com',
    DEFAULT_FROM_EMAIL='noreply@test.com',
)
class ContatoViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = reverse('aranha:agenda_contato')
        # Destino vem de settings.CLINIC_EMAIL (Branding/env isolados do ambiente local)
        p = patch('aranha_estetica.views.public.get_branding', return_value={'CLINIC_EMAIL': ''})
        p.start()
        self.addCleanup(p.stop)

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
            any('obrigat' in str(m).lower() for m in messages_list),
            'Esperava mensagem de erro de validacao',
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_post_sem_email_mostra_erro(self):
        resp = self._post(email='')
        self.assertEqual(resp.status_code, 200)
        messages_list = list(resp.context['messages'])
        self.assertTrue(any('obrigat' in str(m).lower() for m in messages_list))
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

    def test_post_valido_envia_email_para_clinica(self):
        self._post(subject='orcamento', name='Carlos', message='Quero um orcamento.')
        self.assertEqual(len(mail.outbox), 1)
        enviado = mail.outbox[0]
        self.assertIn('orçamento', enviado.subject.lower())
        self.assertIn('Carlos', enviado.body)
        self.assertIn('clinica@test.com', enviado.recipients())
        # Resposta vai direto p/ quem escreveu (o from e o noreply)
        self.assertEqual(enviado.reply_to, ['ana@exemplo.com.br'])

    # ── Falha de email: nao derruba a request e NAO finge sucesso ───────────

    def test_falha_de_email_nao_finge_sucesso_e_preserva_mensagem(self):
        """Regressao: SMTP falhava, a mensagem se perdia e a tela dizia 'enviada'."""
        with patch('django.core.mail.EmailMessage.send', side_effect=Exception('SMTP down')):
            resp = self._post(message='Minha duvida importante.')
        self.assertEqual(resp.status_code, 200)
        msgs = [str(m).lower() for m in resp.context['messages']]
        self.assertFalse(any('enviada' in m for m in msgs))
        self.assertTrue(any('não conseguimos enviar' in m for m in msgs))
        self.assertEqual(resp.context['form_data']['message'], 'Minha duvida importante.')
        self.assertContains(resp, 'Minha duvida importante.')

    @override_settings(DEBUG=False, EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend')
    def test_backend_console_em_prod_nao_finge_sucesso(self):
        """Regressao: em prod sem SMTP o console 'enviava' (retorna 1) e a mensagem sumia no log."""
        resp = self._post()
        self.assertEqual(resp.status_code, 200)
        msgs = [str(m).lower() for m in resp.context['messages']]
        self.assertFalse(any('enviada' in m for m in msgs))
        self.assertTrue(resp.context['envio_falhou'])

    @override_settings(CLINIC_EMAIL='', DEFAULT_FROM_EMAIL='noreply@clinica.com.br')
    def test_falha_sem_nenhum_canal_nao_manda_usar_outro_canal(self):
        """Regressao gap4-03: sem WhatsApp/telefone/e-mail a flash apontava p/ canal inexistente."""
        resp = self._post()
        msgs = [str(m) for m in resp.context['messages']]
        self.assertTrue(any('tente de novo mais tarde.' in m for m in msgs))
        self.assertFalse(any('outro canal' in m for m in msgs))

    @override_settings(CLINIC_EMAIL='', DEFAULT_FROM_EMAIL='noreply@clinica.com.br')
    def test_falha_com_whatsapp_sugere_outro_canal(self):
        with patch('aranha_estetica.views.public.get_branding',
                   return_value={'CLINIC_EMAIL': '', 'WHATSAPP_NUMERO': '5517991234567'}):
            resp = self._post()
        msgs = [str(m) for m in resp.context['messages']]
        self.assertTrue(any('outro canal de contato desta página' in m for m in msgs))

    @override_settings(CLINIC_EMAIL='', DEFAULT_FROM_EMAIL='noreply@clinica.com.br')
    def test_sem_caixa_de_destino_nao_envia_para_noreply(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(resp.context['envio_falhou'])

    def test_email_invalido_mostra_erro(self):
        resp = self._post(email='nao-e-email')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(any('e-mail válido' in str(m) for m in resp.context['messages']))

    # ── LGPD: aceite da politica separado do marketing ─────────────────────

    def test_marketing_e_opcional_e_separado_da_politica(self):
        resp = self._post()  # so privacy
        self.assertRedirects(resp, self.url)
        self.assertIn('novidades e promoções: não', mail.outbox[0].body)
        self._post(marketing='1')
        self.assertIn('novidades e promoções: sim', mail.outbox[1].body)

    def test_template_tem_checkbox_de_marketing_separado(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, 'name="marketing"')
        self.assertNotContains(resp, 'aceito receber comunicações')

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
