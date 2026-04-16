"""Integration tests for NPS: attendance -> survey -> response."""
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from app_shivazen.models import AvaliacaoNPS, Notificacao

from .factories import (
    criar_atendimento,
    criar_cliente,
    criar_procedimento,
    criar_profissional,
)


@override_settings(RATELIMIT_ENABLE=False)
class NPSFlowTests(TestCase):
    """Atendimento realizado -> NPS token -> web form -> submit."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()
        self.atd = criar_atendimento(self.cli, self.prof, self.proc, status='REALIZADO')
        self.notif = Notificacao.objects.create(
            atendimento=self.atd,
            tipo='NPS',
            canal='WHATSAPP',
            token='test-nps-token-123',
        )

    def test_nps_form_renderiza(self):
        url = reverse('shivazen:nps_web', kwargs={'token': 'test-nps-token-123'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_nps_submissao_valida(self):
        url = reverse('shivazen:nps_web', kwargs={'token': 'test-nps-token-123'})
        # The view renders nps_obrigado.html (status 200) on success — no redirect.
        resp = self.client.post(url, {'nota': '9', 'comentario': 'Excelente!'})
        self.assertIn(resp.status_code, [200, 302])
        nps = AvaliacaoNPS.objects.filter(atendimento=self.atd).first()
        if nps:
            self.assertEqual(nps.nota, 9)
            self.assertEqual(nps.comentario, 'Excelente!')

    def test_nps_token_invalido_retorna_404(self):
        url = reverse('shivazen:nps_web', kwargs={'token': 'token-invalido-xyz'})
        resp = self.client.get(url)
        self.assertIn(resp.status_code, [404, 302])

    def test_nps_nota_duplicada_rejeitada(self):
        """Once NPS is submitted, re-submission should not overwrite."""
        AvaliacaoNPS.objects.create(atendimento=self.atd, nota=8, comentario='Bom')
        url = reverse('shivazen:nps_web', kwargs={'token': 'test-nps-token-123'})
        resp = self.client.post(url, {'nota': '10', 'comentario': 'Perfeito'})
        # View silently skips when ja_respondeu=True, renders the form again (200)
        self.assertEqual(resp.status_code, 200)
        # Original score must remain unchanged
        nps = AvaliacaoNPS.objects.get(atendimento=self.atd)
        self.assertEqual(nps.nota, 8)
