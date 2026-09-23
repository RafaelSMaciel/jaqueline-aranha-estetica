"""Cobertura de gaps identificados no audit G8:
DSAR export, unsubscribe one-click (RFC 8058), NPS expiry 410.
"""
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import (
    AvaliacaoNPS, Notificacao, CodigoOtp,
)
from .factories import (
    criar_atendimento, criar_cliente, criar_procedimento, criar_profissional,
)


@override_settings(RATELIMIT_ENABLE=False)
class LgpdUnsubscribeTests(TestCase):
    """Descadastro: GET so confirma (scanners de link), POST executa (form e
    one-click RFC 8058, sem CSRF)."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.cliente = criar_cliente(telefone='17911112222')
        self.cliente.consent_email_marketing = True
        self.cliente.consent_whatsapp_nps = True
        self.cliente.consent_whatsapp_confirmacao = True
        self.cliente.save()
        # token_descadastro gerado no save() via signal/save override
        self.cliente.refresh_from_db()
        self.url = reverse('aranha:lgpd_unsubscribe', args=[self.cliente.token_descadastro])

    def test_get_nao_altera_consentimento(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'method="post"')
        self.cliente.refresh_from_db()
        self.assertTrue(self.cliente.consent_email_marketing)
        self.assertTrue(self.cliente.aceita_comunicacao)

    def test_post_marca_optout(self):
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 200)
        self.cliente.refresh_from_db()
        self.assertFalse(self.cliente.consent_email_marketing)
        self.assertFalse(self.cliente.consent_whatsapp_nps)
        self.assertFalse(self.cliente.aceita_comunicacao)
        # Lembrete do agendamento e aviso transacional: continua (copy da pagina diz isso)
        self.assertTrue(self.cliente.consent_whatsapp_confirmacao)

    def test_one_click_rfc8058_sem_csrf(self):
        """Provedor (Gmail/Yahoo) faz POST server-side sem token CSRF."""
        client = Client(enforce_csrf_checks=True)
        resp = client.post(
            self.url, data='List-Unsubscribe=One-Click',
            content_type='application/x-www-form-urlencoded',
        )
        self.assertEqual(resp.status_code, 200)
        self.cliente.refresh_from_db()
        self.assertFalse(self.cliente.consent_email_marketing)

    def test_unsubscribe_token_invalido_404(self):
        url = reverse('aranha:lgpd_unsubscribe', args=['token-nao-existe'])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 404)


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
            status='ENVIADO', token='tok-nps-expiry',
        )

    def test_nps_dentro_prazo_ok(self):
        resp = self.client.get(reverse('aranha:nps_web', args=[self.notif.token]))
        self.assertEqual(resp.status_code, 200)

    def test_nps_expirado_410(self):
        # Forca criado_em > 7 dias atras
        Notificacao.objects.filter(pk=self.notif.pk).update(
            criado_em=timezone.now() - timedelta(days=8),
        )
        resp = self.client.get(reverse('aranha:nps_web', args=[self.notif.token]))
        self.assertEqual(resp.status_code, 410)
        self.assertFalse(AvaliacaoNPS.objects.filter(atendimento=self.atd).exists())


@override_settings(RATELIMIT_ENABLE=False)
class DsarExportTests(TestCase):
    """DSAR: cliente exporta dados via telefone + OTP."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.cliente = criar_cliente(telefone='17933334444', nome='Joao DSAR')
        self.url = reverse('aranha:lgpd_meus_dados')

    def test_dsar_exige_codigo(self):
        resp = self.client.post(self.url, {'telefone': '17933334444'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(CodigoOtp.objects.filter(
            telefone='17933334444', proposito=CodigoOtp.PROPOSITO_DSAR,
        ).exists())

    def test_dsar_nao_emite_otp_para_telefone_desconhecido(self):
        """Anti-enumeracao: resposta identica, mas OTP so existe se ha cadastro."""
        resp = self.client.post(self.url, {'telefone': '17900000000'})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(CodigoOtp.objects.filter(telefone='17900000000').exists())

    def test_dsar_codigo_invalido_rejeita(self):
        resp = self.client.post(self.url, {'telefone': '17933334444', 'codigo': '000000'})
        self.assertEqual(resp.status_code, 200)
        # Nao retornou JSON attachment
        self.assertNotIn('attachment', resp.get('Content-Disposition', ''))

    def test_dsar_fluxo_completo_exporta_json(self):
        codigo, _ = CodigoOtp.gerar_sms('17933334444', proposito=CodigoOtp.PROPOSITO_DSAR)
        resp = self.client.post(self.url, {'telefone': '17933334444', 'codigo': codigo})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('attachment', resp.get('Content-Disposition', ''))
        self.assertIn(b'Joao DSAR', resp.content)

    def test_dsar_com_avaliacao_nps_nao_quebra(self):
        """Regressao: AvaliacaoNPS nao tem respondida_em -> export dava 500."""
        import json
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        atd = criar_atendimento(self.cliente, prof, proc, status='REALIZADO')
        AvaliacaoNPS.objects.create(atendimento=atd, nota=9, comentario='Ótimo')
        codigo, _ = CodigoOtp.gerar_sms('17933334444', proposito=CodigoOtp.PROPOSITO_DSAR)
        resp = self.client.post(self.url, {'telefone': '17933334444', 'codigo': codigo})
        self.assertEqual(resp.status_code, 200)
        dados = json.loads(resp.content)
        self.assertEqual(dados['avaliacoes_nps'][0]['nota'], 9)
        self.assertIsNotNone(dados['avaliacoes_nps'][0]['respondida_em'])
        for chave in ('consentimentos', 'prontuario', 'anotacoes_sessao', 'anamneses',
                      'aceites_termos', 'pacotes', 'carteira', 'lista_espera', 'notificacoes'):
            self.assertIn(chave, dados)

    def test_dsar_mensagem_sem_termo_clinico(self):
        resp = self.client.post(self.url, {'telefone': '17933334444'})
        self.assertNotContains(resp, 'Paciente')
