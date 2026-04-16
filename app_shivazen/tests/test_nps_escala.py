"""Testes de consistencia da escala NPS (0-10) no template web e na view."""
from unittest.mock import patch

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
class NPSWebTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.cliente_obj = criar_cliente()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.atd = criar_atendimento(self.cliente_obj, self.prof, self.proc)
        self.notif = Notificacao.objects.create(
            atendimento=self.atd, tipo='NPS', token='token-nps-123'
        )
        self.url = reverse('shivazen:nps_web', kwargs={'token': 'token-nps-123'})

    def test_template_renderiza_11_botoes_de_0_a_10(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        for nota in range(11):
            self.assertContains(resp, f'data-nota="{nota}"')
        # Nao deve renderizar 11
        self.assertNotContains(resp, 'data-nota="11"')

    def test_post_com_nota_valida_salva_avaliacao(self):
        resp = self.client.post(self.url, {'nota': '9', 'comentario': 'Otimo!'})
        self.assertEqual(resp.status_code, 200)
        nps = AvaliacaoNPS.objects.filter(atendimento=self.atd).first()
        self.assertIsNotNone(nps)
        self.assertEqual(nps.nota, 9)
        self.assertEqual(nps.comentario, 'Otimo!')

    def test_post_com_nota_acima_de_10_nao_salva(self):
        self.client.post(self.url, {'nota': '11'})
        self.assertFalse(AvaliacaoNPS.objects.filter(atendimento=self.atd).exists())

    def test_nota_zero_eh_aceita(self):
        self.client.post(self.url, {'nota': '0'})
        nps = AvaliacaoNPS.objects.filter(atendimento=self.atd).first()
        self.assertIsNotNone(nps)
        self.assertEqual(nps.nota, 0)

    def test_nao_sobrescreve_avaliacao_existente(self):
        AvaliacaoNPS.objects.create(atendimento=self.atd, nota=7)
        self.client.post(self.url, {'nota': '9'})
        nps = AvaliacaoNPS.objects.get(atendimento=self.atd)
        self.assertEqual(nps.nota, 7)  # Mantem a original
