"""Webhook do WhatsApp: verificacao HMAC, nota do NPS e o pedido de PARAR."""
import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch

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
        with patch.object(whatsapp_mod, 'WHATSAPP_APP_SECRET', APP_SECRET):
            resp = self.client.post(
                self.url,
                data=json.dumps({'body': 'hello'}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 403)

    def test_webhook_rejeita_assinatura_invalida(self):
        body = json.dumps({'body': 'hello'}).encode()
        with patch.object(whatsapp_mod, 'WHATSAPP_APP_SECRET', APP_SECRET):
            resp = self.client.post(
                self.url,
                data=body,
                content_type='application/json',
                HTTP_X_HUB_SIGNATURE_256='sha256=deadbeef',
            )
        self.assertEqual(resp.status_code, 403)

    def test_webhook_aceita_assinatura_valida(self):
        body = json.dumps(_payload_meta(texto='8')).encode()
        with patch.object(whatsapp_mod, 'WHATSAPP_APP_SECRET', APP_SECRET):
            resp = self.client.post(
                self.url,
                data=body,
                content_type='application/json',
                HTTP_X_HUB_SIGNATURE_256=_assinar(body),
            )
        self.assertEqual(resp.status_code, 200)

    def test_webhook_fail_closed_sem_secret_em_producao(self):
        """Se DEBUG=False e sem APP_SECRET, deve rejeitar por seguranca."""
        with patch.object(whatsapp_mod, 'WHATSAPP_APP_SECRET', ''):
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
        # patch.object com addCleanup restaura o valor original ao fim do teste.
        secret_patch = patch.object(whatsapp_mod, 'WHATSAPP_APP_SECRET', APP_SECRET)
        secret_patch.start()
        self.addCleanup(secret_patch.stop)

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
        secret_patch = patch.object(whatsapp_mod, 'WHATSAPP_APP_SECRET', APP_SECRET)
        secret_patch.start()
        self.addCleanup(secret_patch.stop)
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
