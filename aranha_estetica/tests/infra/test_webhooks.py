"""Webhooks publicos (csrf_exempt): WhatsApp (assinatura HMAC, nota do NPS, PARAR e handshake da
Meta) e Zenvia (status de SMS: allowlist de IP e segredo no header)."""
import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

import aranha_estetica.views.infra.webhooks as whatsapp_mod
from aranha_estetica.models import AvaliacaoNPS, Consentimento, LogAuditoria, Notificacao
from aranha_estetica.services import consentimentos

from ..factories import (
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
        status='ENVIADO',
    )


APP_SECRET = 'test-secret-abcdef'


def _payload_meta(telefone='5517988887777', texto='9', tipo='text', statuses=None):
    """Payload real da Meta Cloud API (entry[].changes[].value.messages[])."""
    valor = {'messaging_product': 'whatsapp', 'metadata': {'phone_number_id': '123'}}
    if texto is not None:
        msg = {'from': telefone, 'id': 'wamid.X', 'timestamp': '1700000000', 'type': tipo}
        if tipo == 'text':
            msg['text'] = {'body': texto}
        elif tipo == 'button':
            msg['button'] = {'text': texto, 'payload': texto}
        elif tipo == 'interactive':
            msg['interactive'] = {'type': 'button_reply', 'button_reply': {'id': texto, 'title': texto}}
        valor['messages'] = [msg]
    if statuses is not None:
        valor['statuses'] = statuses
    return {
        'object': 'whatsapp_business_account',
        'entry': [{'id': 'WABA', 'changes': [{'field': 'messages', 'value': valor}]}],
    }


def _assinar(body_bytes, secret=APP_SECRET):
    sig = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
    return f'sha256={sig}'


@override_settings(DEBUG=False)
class WhatsAppWebhookAssinaturaTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = reverse('aranha:whatsapp_webhook')

    def test_webhook_rejeita_sem_assinatura(self):
        with override_settings(WHATSAPP_APP_SECRET=APP_SECRET):
            resp = self.client.post(
                self.url,
                data=json.dumps({'body': 'hello'}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 403)

    def test_webhook_rejeita_assinatura_invalida(self):
        body = json.dumps({'body': 'hello'}).encode()
        with override_settings(WHATSAPP_APP_SECRET=APP_SECRET):
            resp = self.client.post(
                self.url,
                data=body,
                content_type='application/json',
                HTTP_X_HUB_SIGNATURE_256='sha256=deadbeef',
            )
        self.assertEqual(resp.status_code, 403)

    def test_webhook_aceita_assinatura_valida(self):
        body = json.dumps(_payload_meta(texto='8')).encode()
        with override_settings(WHATSAPP_APP_SECRET=APP_SECRET):
            resp = self.client.post(
                self.url,
                data=body,
                content_type='application/json',
                HTTP_X_HUB_SIGNATURE_256=_assinar(body),
            )
        self.assertEqual(resp.status_code, 200)

    def test_webhook_fail_closed_sem_secret_em_producao(self):
        """Se DEBUG=False e sem APP_SECRET, deve rejeitar por seguranca."""
        with override_settings(WHATSAPP_APP_SECRET=''):
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
        self.url = reverse('aranha:whatsapp_webhook')
        # Secret obrigatorio (fail-closed) — mesmo em DEBUG.
        secret = override_settings(WHATSAPP_APP_SECRET=APP_SECRET)
        secret.enable()
        self.addCleanup(secret.disable)

        self.cliente = criar_cliente(telefone='17988887777')
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.atd = criar_atendimento(self.cliente, self.prof, self.proc)
        # Notificacao NPS enviada habilita correlacao segura da resposta.
        _criar_notificacao_nps(self.atd)

    def _post(self, payload):
        body = json.dumps(payload).encode()
        return self.client.post(
            self.url,
            data=body,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=_assinar(body),
        )

    def test_resposta_numerica_valida_registra_nota(self):
        resp = self._post(_payload_meta('5517988887777', '9'))
        self.assertEqual(resp.status_code, 200)
        nps = AvaliacaoNPS.objects.get(atendimento=self.atd)
        self.assertEqual(nps.nota, 9)

    def test_resposta_por_botao_registra_nota(self):
        resp = self._post(_payload_meta('5517988887777', '10', tipo='button'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(AvaliacaoNPS.objects.get(atendimento=self.atd).nota, 10)

    def test_resposta_interativa_registra_nota(self):
        resp = self._post(_payload_meta('5517988887777', '7', tipo='interactive'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(AvaliacaoNPS.objects.get(atendimento=self.atd).nota, 7)

    def test_evento_de_status_sem_mensagem_responde_200(self):
        """sent/delivered/read nao tem 'messages': 200 (4xx faz a Meta reenviar)."""
        payload = _payload_meta(texto=None, statuses=[{'id': 'wamid.X', 'status': 'delivered'}])
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(AvaliacaoNPS.objects.exists())

    def test_formato_plano_antigo_e_ignorado(self):
        resp = self._post({'from': '5517988887777', 'body': '9'})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(AvaliacaoNPS.objects.exists())

    def test_resposta_nao_numerica_ignora(self):
        resp = self._post(_payload_meta('5517988887777', 'obrigado'))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(AvaliacaoNPS.objects.filter(atendimento=self.atd).exists())

    def test_resposta_fora_do_intervalo_ignora(self):
        resp = self._post(_payload_meta('5517988887777', '11'))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(AvaliacaoNPS.objects.filter(atendimento=self.atd).exists())

    def test_sem_notificacao_nps_nao_registra(self):
        """Sem Notificacao NPS enviada recentemente, resposta e descartada (anti-IDOR)."""
        outro = criar_cliente(telefone='17911112222')
        # Horario distinto: no Postgres o EXCLUDE barra 2 atendimentos no mesmo slot.
        atd_outro = criar_atendimento(
            outro, self.prof, self.proc, data_hora=self.atd.data_hora_inicio + timedelta(hours=2),
        )
        resp = self._post(_payload_meta('5517911112222', '10'))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(AvaliacaoNPS.objects.filter(atendimento=atd_outro).exists())

    def test_nao_sobrescreve_avaliacao_existente(self):
        AvaliacaoNPS.objects.create(atendimento=self.atd, nota=7)
        resp = self._post(_payload_meta('5517988887777', '10'))
        self.assertEqual(resp.status_code, 200)
        nps = AvaliacaoNPS.objects.get(atendimento=self.atd)
        self.assertEqual(nps.nota, 7)


@override_settings(DEBUG=True)
class WhatsAppPararTests(TestCase):
    """'PARAR' & cia. revogam lembrete e pesquisa por WhatsApp (LGPD art. 8 §5) e respondem como voltar."""

    def setUp(self):
        secret = override_settings(WHATSAPP_APP_SECRET=APP_SECRET)
        secret.enable()
        self.addCleanup(secret.disable)
        self.url = reverse('aranha:whatsapp_webhook')
        self.cliente = criar_cliente(telefone='17988887777', consent_whatsapp_confirmacao=True,
                                     consent_whatsapp_nps=True, consent_email_marketing=True)

    def _post(self, texto, telefone='5517988887777'):
        body = json.dumps(_payload_meta(telefone, texto)).encode()
        with patch('aranha_estetica.utils.whatsapp.enviar_texto', return_value=True) as resposta:
            resp = self.client.post(self.url, data=body, content_type='application/json',
                                    HTTP_X_HUB_SIGNATURE_256=_assinar(body))
        self.assertEqual(resp.status_code, 200)
        return resposta

    def _estado(self):
        return consentimentos.estado_atual(self.cliente)

    def test_parar_revoga_os_dois_pares_do_whatsapp_com_origem_e_trilha(self):
        resposta = self._post('  Parar! ')
        estado = self._estado()
        self.assertFalse(estado[('LEMBRETE_AGENDAMENTO', 'WHATSAPP')])
        self.assertFalse(estado[('PESQUISA_SATISFACAO', 'WHATSAPP')])
        self.assertTrue(estado[('MARKETING', 'EMAIL')])   # o e-mail de marketing tem descadastro proprio
        revogacoes = Consentimento.objects.filter(cliente=self.cliente, concedido=False)
        self.assertEqual(sorted(revogacoes.values_list('origem', flat=True)), ['WHATSAPP', 'WHATSAPP'])
        self.assertTrue(LogAuditoria.objects.filter(registro_id=self.cliente.pk, acao__contains='PARAR').exists())
        resposta.assert_called_once()
        self.assertEqual(resposta.call_args.args[0], '5517988887777')
        self.assertIn('voltar a receber', resposta.call_args.args[1])

    def test_variacoes_aceitas_e_numero_sem_55(self):
        for texto in ('SAIR', 'stop', 'Cancelar  mensagens', 'descadastrar.'):
            with self.subTest(texto=texto):
                consentimentos.registrar(self.cliente, 'PESQUISA_SATISFACAO', 'WHATSAPP', True,
                                         origem='AGENDAMENTO_ONLINE')
                self._post(texto, telefone='17988887777')
                self.assertFalse(self._estado()[('PESQUISA_SATISFACAO', 'WHATSAPP')])

    def test_frase_com_a_palavra_nao_revoga(self):
        resposta = self._post('vou sair mais cedo do trabalho')
        estado = self._estado()
        self.assertTrue(estado[('LEMBRETE_AGENDAMENTO', 'WHATSAPP')])
        self.assertTrue(estado[('PESQUISA_SATISFACAO', 'WHATSAPP')])
        resposta.assert_not_called()

    def test_numero_desconhecido_so_recebe_a_resposta(self):
        resposta = self._post('parar', telefone='5517900001111')
        self.assertTrue(self._estado()[('PESQUISA_SATISFACAO', 'WHATSAPP')])
        resposta.assert_called_once()
        self.assertFalse(AvaliacaoNPS.objects.exists())

    def test_erro_interno_ainda_responde_200(self):
        with patch.object(whatsapp_mod, '_processar_opt_out', side_effect=RuntimeError('boom')):
            self._post('parar')


SEGREDO = 'segredo-zenvia-' + 'k7' * 10
CORPO = '{"messageId": "m1", "messageStatus": {"code": "DELIVERED"}, "to": "5517999990000"}'


@override_settings(DEBUG=False, ZENVIA_WEBHOOK_SECRET='', ZENVIA_ALLOWED_IPS=[])
class ZenviaWebhookTests(TestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse('aranha:zenvia_sms_webhook')

    def _post(self, corpo=CORPO, ip='203.0.113.5', **headers):
        return self.client.post(self.url, data=corpo, content_type='application/json', REMOTE_ADDR=ip, **headers)

    def test_sem_segredo_nem_allowlist_recusa_com_503(self):
        self.assertEqual(self._post().status_code, 503)

    @override_settings(ZENVIA_ALLOWED_IPS=['abc', '10.0.0.0/8'])
    def test_allowlist_de_cidr_e_entrada_invalida_ignorada(self):
        self.assertEqual(self._post(ip='10.1.2.3').status_code, 200)
        self.assertEqual(self._post(ip='192.168.0.1').status_code, 403)

    @override_settings(ZENVIA_WEBHOOK_SECRET=SEGREDO)
    def test_segredo_no_header(self):
        self.assertEqual(self._post().status_code, 403)
        self.assertEqual(self._post(HTTP_X_ZENVIA_SIGNATURE='errado').status_code, 403)
        self.assertEqual(self._post(HTTP_X_ZENVIA_SIGNATURE='çã').status_code, 403)  # nao 500
        self.assertEqual(self._post(HTTP_X_ZENVIA_SIGNATURE=SEGREDO).status_code, 200)

    @override_settings(ZENVIA_WEBHOOK_SECRET=SEGREDO)
    def test_corpo_invalido_e_400(self):
        for corpo in ('x', '[]', '\xff'):
            with self.subTest(corpo=corpo):
                self.assertEqual(self._post(corpo=corpo, HTTP_X_ZENVIA_SIGNATURE=SEGREDO).status_code, 400)
        estranho = '{"messageStatus": "texto"}'
        self.assertEqual(self._post(corpo=estranho, HTTP_X_ZENVIA_SIGNATURE=SEGREDO).status_code, 200)


class HandshakeMetaTests(TestCase):
    VERIFY = 'verify-token-' + 'p4' * 10

    def _get(self, token, modo='subscribe'):
        return self.client.get(reverse('aranha:whatsapp_webhook'),
                               {'hub.mode': modo, 'hub.verify_token': token, 'hub.challenge': '12345'})

    @override_settings(WHATSAPP_VERIFY_TOKEN=VERIFY)
    def test_token_certo_ecoa_o_challenge(self):
        resp = self._get(self.VERIFY)
        self.assertEqual((resp.status_code, resp.content), (200, b'12345'))

    @override_settings(WHATSAPP_VERIFY_TOKEN=VERIFY)
    def test_token_errado_vazio_ou_modo_errado_e_403(self):
        for token, modo in (('errado', 'subscribe'), ('', 'subscribe'), ('çã', 'subscribe'), (self.VERIFY, 'x')):
            with self.subTest(token=token, modo=modo):
                self.assertEqual(self._get(token, modo).status_code, 403)

    @override_settings(WHATSAPP_VERIFY_TOKEN='')
    def test_sem_token_configurado_ninguem_confirma(self):
        self.assertEqual(self._get('').status_code, 403)
