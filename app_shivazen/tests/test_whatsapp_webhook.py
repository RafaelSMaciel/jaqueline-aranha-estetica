"""Testes do webhook WhatsApp: verificacao HMAC e processamento NPS."""
import hashlib
import hmac
import json

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from app_shivazen.models import AvaliacaoNPS, Notificacao

from .factories import (
    criar_atendimento,
    criar_cliente,
    criar_procedimento,
    criar_profissional,
)


def _criar_notificacao_nps(atendimento):
    return Notificacao.objects.create(
        atendimento=atendimento,
        tipo='NPS',
        canal='WHATSAPP',
        status_envio='ENVIADO',
        token='tok-nps-wpp',
    )


APP_SECRET = 'test-secret-abcdef'


def _assinar(body_bytes, secret=APP_SECRET):
    sig = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
    return f'sha256={sig}'


@override_settings(DEBUG=False)
class WhatsAppWebhookAssinaturaTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = reverse('shivazen:whatsapp_webhook')

    def test_webhook_rejeita_sem_assinatura(self):
        with self.settings():
            import app_shivazen.views.whatsapp as whatsapp_mod
            whatsapp_mod.WHATSAPP_APP_SECRET = APP_SECRET
            resp = self.client.post(
                self.url,
                data=json.dumps({'body': 'hello'}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 403)

    def test_webhook_rejeita_assinatura_invalida(self):
        import app_shivazen.views.whatsapp as whatsapp_mod
        whatsapp_mod.WHATSAPP_APP_SECRET = APP_SECRET

        body = json.dumps({'body': 'hello'}).encode()
        resp = self.client.post(
            self.url,
            data=body,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256='sha256=deadbeef',
        )
        self.assertEqual(resp.status_code, 403)

    def test_webhook_aceita_assinatura_valida(self):
        import app_shivazen.views.whatsapp as whatsapp_mod
        whatsapp_mod.WHATSAPP_APP_SECRET = APP_SECRET

        body = json.dumps({'body': '8'}).encode()
        resp = self.client.post(
            self.url,
            data=body,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=_assinar(body),
        )
        self.assertEqual(resp.status_code, 200)

    def test_webhook_fail_closed_sem_secret_em_producao(self):
        """Se DEBUG=False e sem APP_SECRET, deve rejeitar por seguranca."""
        import app_shivazen.views.whatsapp as whatsapp_mod
        whatsapp_mod.WHATSAPP_APP_SECRET = ''

        resp = self.client.post(
            self.url,
            data=json.dumps({'body': 'hi'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403)


@override_settings(DEBUG=True)
class WhatsAppWebhookNPSTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = reverse('shivazen:whatsapp_webhook')
        # Sem APP_SECRET + DEBUG=True -> aceita (dev)
        import app_shivazen.views.whatsapp as whatsapp_mod
        whatsapp_mod.WHATSAPP_APP_SECRET = ''

        self.cliente = criar_cliente(telefone='17988887777')
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.atd = criar_atendimento(self.cliente, self.prof, self.proc)
        # Notificacao NPS enviada habilita correlacao segura da resposta.
        _criar_notificacao_nps(self.atd)

    def test_resposta_numerica_valida_registra_nota(self):
        body = json.dumps({'from': '5517988887777', 'body': '9'})
        resp = self.client.post(self.url, data=body, content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        nps = AvaliacaoNPS.objects.get(atendimento=self.atd)
        self.assertEqual(nps.nota, 9)

    def test_resposta_nao_numerica_ignora(self):
        body = json.dumps({'from': '5517988887777', 'body': 'obrigado'})
        resp = self.client.post(self.url, data=body, content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(AvaliacaoNPS.objects.filter(atendimento=self.atd).exists())

    def test_resposta_fora_do_intervalo_ignora(self):
        body = json.dumps({'from': '5517988887777', 'body': '11'})
        resp = self.client.post(self.url, data=body, content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(AvaliacaoNPS.objects.filter(atendimento=self.atd).exists())

    def test_sem_notificacao_nps_nao_registra(self):
        """Sem Notificacao NPS enviada recentemente, resposta e descartada (anti-IDOR)."""
        outro = criar_cliente(telefone='17911112222')
        atd_outro = criar_atendimento(outro, self.prof, self.proc)
        # Nenhuma Notificacao NPS criada para este cliente.
        body = json.dumps({'from': '5517911112222', 'body': '10'})
        resp = self.client.post(self.url, data=body, content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(AvaliacaoNPS.objects.filter(atendimento=atd_outro).exists())

    def test_nao_sobrescreve_avaliacao_existente(self):
        AvaliacaoNPS.objects.create(atendimento=self.atd, nota=7)
        body = json.dumps({'from': '5517988887777', 'body': '10'})
        resp = self.client.post(self.url, data=body, content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        nps = AvaliacaoNPS.objects.get(atendimento=self.atd)
        self.assertEqual(nps.nota, 7)
