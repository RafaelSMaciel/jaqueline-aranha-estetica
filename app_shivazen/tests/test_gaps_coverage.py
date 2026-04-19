"""Cobertura de gaps identificados no audit G8:
DSAR export, unsubscribe one-click (RFC 8058), NPS expiry 410, promo dispatch ACL.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app_shivazen.models import (
    AvaliacaoNPS, Cliente, CodigoVerificacao, Notificacao, Usuario,
)
from .factories import (
    criar_atendimento, criar_cliente, criar_procedimento, criar_profissional,
)


@override_settings(RATELIMIT_ENABLE=False)
class LgpdUnsubscribeTests(TestCase):
    """RFC 8058 one-click: URL resolve, marca opt-out, devolve 200."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.cliente = criar_cliente(telefone='17911112222')
        self.cliente.consent_email_marketing = True
        self.cliente.save()
        # unsubscribe_token gerado no save() via signal/save override
        self.cliente.refresh_from_db()

    def test_unsubscribe_valido_marca_optout(self):
        token = self.cliente.unsubscribe_token
        self.assertTrue(token)
        resp = self.client.get(reverse('shivazen:lgpd_unsubscribe', args=[token]))
        self.assertEqual(resp.status_code, 200)
        self.cliente.refresh_from_db()
        self.assertFalse(self.cliente.consent_email_marketing)

    def test_unsubscribe_token_invalido_404(self):
        resp = self.client.get(reverse('shivazen:lgpd_unsubscribe', args=['token-nao-existe']))
        self.assertEqual(resp.status_code, 404)


@override_settings(RATELIMIT_ENABLE=False)
class NpsExpiryTests(TestCase):
    """Token NPS > 7 dias devolve 410 Gone."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        cli = criar_cliente(telefone='17922223333')
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        self.atd = criar_atendimento(cli, prof, proc, status='REALIZADO')
        self.notif = Notificacao.objects.create(
            atendimento=self.atd, tipo='NPS', canal='WHATSAPP',
            status_envio='ENVIADO', token='tok-nps-expiry',
        )

    def test_nps_dentro_prazo_ok(self):
        resp = self.client.get(reverse('shivazen:nps_web', args=[self.notif.token]))
        self.assertEqual(resp.status_code, 200)

    def test_nps_expirado_410(self):
        # Forca criado_em > 7 dias atras
        Notificacao.objects.filter(pk=self.notif.pk).update(
            criado_em=timezone.now() - timedelta(days=8),
        )
        resp = self.client.get(reverse('shivazen:nps_web', args=[self.notif.token]))
        self.assertEqual(resp.status_code, 410)
        self.assertFalse(AvaliacaoNPS.objects.filter(atendimento=self.atd).exists())


@override_settings(RATELIMIT_ENABLE=False)
class DsarExportTests(TestCase):
    """DSAR: cliente exporta dados via telefone + OTP."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.cliente = criar_cliente(telefone='17933334444', nome='Joao DSAR')
        self.url = reverse('shivazen:lgpd_meus_dados')

    def test_dsar_exige_codigo(self):
        resp = self.client.post(self.url, {'telefone': '17933334444'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(CodigoVerificacao.objects.filter(telefone='17933334444').exists())

    def test_dsar_codigo_invalido_rejeita(self):
        resp = self.client.post(self.url, {'telefone': '17933334444', 'codigo': '000000'})
        self.assertEqual(resp.status_code, 200)
        # Nao retornou JSON attachment
        self.assertNotIn('attachment', resp.get('Content-Disposition', ''))

    def test_dsar_fluxo_completo_exporta_json(self):
        cv = CodigoVerificacao.objects.create(telefone='17933334444', codigo='123456')
        resp = self.client.post(self.url, {'telefone': '17933334444', 'codigo': '123456'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('attachment', resp.get('Content-Disposition', ''))
        self.assertIn(b'Joao DSAR', resp.content)


@override_settings(RATELIMIT_ENABLE=False)
class PromocaoDispatchTests(TestCase):
    """Disparo de promocao requer staff; anonimo redireciona/403."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        from app_shivazen.models import Promocao
        self.promo = Promocao.objects.create(
            nome='Black Friday', desconto_percentual=Decimal('20'),
            data_inicio=timezone.now().date(),
            data_fim=timezone.now().date() + timedelta(days=30),
        )
        self.url = reverse('shivazen:admin_disparar_promocao', args=[self.promo.pk])

    def test_anonimo_nao_dispara(self):
        resp = self.client.post(self.url, {'cupom': 'XYZ', 'validade_dias': '7'})
        # login_required redireciona
        self.assertEqual(resp.status_code, 302)

    @patch('app_shivazen.tasks.job_promocao_mensal.delay')
    def test_staff_dispara(self, mock_delay):
        admin = Usuario.objects.create_superuser(
            email='admin-promo@test.com', password='senha123', nome='Admin',
        )
        self.client.force_login(admin)
        resp = self.client.post(self.url, {'cupom': 'XYZ', 'validade_dias': '7'})
        self.assertEqual(resp.status_code, 302)
        mock_delay.assert_called_once()
