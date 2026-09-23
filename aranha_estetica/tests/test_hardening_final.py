"""Regressao: Referrer-Policy em rotas com token e rotacao do token do feed ICS."""
from django.test import TestCase
from django.urls import reverse

from aranha_estetica.models import LogAuditoria, Usuario
from aranha_estetica.tests.factories import criar_profissional


class ReferrerPolicyTokenTests(TestCase):
    def test_rota_com_token_sai_sem_referer(self):
        resp = self.client.get('/confirmar/token-inexistente/')
        self.assertEqual(resp['Referrer-Policy'], 'no-referrer')

    def test_rota_publica_comum_mantem_politica_padrao(self):
        resp = self.client.get(reverse('aranha:inicio'))
        self.assertEqual(resp['Referrer-Policy'], 'strict-origin-when-cross-origin')


class RotacionarIcsTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            email='admin-ics@test.com', password='senha-forte-123', nome='Admin ICS',
        )
        self.prof = criar_profissional()
        self.prof.refresh_from_db()
        self.url = reverse('aranha:profissional_rotacionar_ics', args=[self.prof.pk])

    def test_post_gera_token_novo_e_audita(self):
        antigo = self.prof.ics_token
        self.client.force_login(self.admin)
        resp = self.client.post(self.url)
        self.assertRedirects(resp, reverse('aranha:painel_profissionais'), fetch_redirect_response=False)
        self.prof.refresh_from_db()
        self.assertTrue(self.prof.ics_token)
        self.assertNotEqual(self.prof.ics_token, antigo)
        self.assertTrue(LogAuditoria.objects.filter(
            tabela='profissional', registro_id=self.prof.pk, acao__icontains='ICS',
        ).exists())
        feed = f'/agenda/{self.prof.slug}/feed.ics?token={antigo}'
        self.assertNotEqual(self.client.get(feed).status_code, 200)

    def test_get_nao_altera(self):
        antigo = self.prof.ics_token
        self.client.force_login(self.admin)
        self.client.get(self.url)
        self.prof.refresh_from_db()
        self.assertEqual(self.prof.ics_token, antigo)

    def test_anonimo_nao_rotaciona(self):
        antigo = self.prof.ics_token
        self.client.post(self.url)
        self.prof.refresh_from_db()
        self.assertEqual(self.prof.ics_token, antigo)
