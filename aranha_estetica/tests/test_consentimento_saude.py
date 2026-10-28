"""Consentimento de dado de saude revogavel (decisao 32) e preferencias da area da cliente (origem PORTAL).

A concessao e o AceiteTermo SAUDE; a revogacao e Cliente.saude_revogada_em com trilha. Um aceite SAUDE
novo (nova ficha) reativa. Os alertas de saude mostram a revogacao sem esconder o registro ja feito.
"""
from datetime import timedelta
from unittest.mock import patch

from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import AceiteTermo, Cliente, Consentimento, LogAuditoria, Prontuario, VersaoTermo
from aranha_estetica.services import consentimentos
from aranha_estetica.utils.saude import alertas_em_lote, alertas_saude

from .factories import criar_cliente
from .test_auth_equipe import _admin, _com_totp, _token


def _request(ip='203.0.113.7'):
    req = RequestFactory().post('/', REMOTE_ADDR=ip, HTTP_USER_AGENT='Navegador da cliente')
    return req


class RevogarSaudeTests(TestCase):
    def setUp(self):
        self.cliente = criar_cliente(nome='Lia Saude', telefone='17991110001')
        self.termo = VersaoTermo.saude_vigente()
        AceiteTermo.registrar(self.cliente, self.termo, _request())

    def test_revoga_com_trilha_e_nao_repete(self):
        self.assertTrue(consentimentos.saude_consentida(self.cliente))
        self.assertTrue(consentimentos.revogar_saude(
            self.cliente, origem=Consentimento.ORIGEM_PORTAL, request=_request()))
        salvo = Cliente.objects.get(pk=self.cliente.pk)
        self.assertIsNotNone(salvo.saude_revogada_em)
        self.assertFalse(consentimentos.saude_consentida(salvo))
        log = LogAuditoria.objects.get(tabela='cliente', registro_id=self.cliente.pk,
                                       acao='LGPD: consentimento de saude revogado')
        self.assertEqual(log.detalhes['origem'], 'PORTAL')
        # nada a revogar de novo: no-op, sem trilha duplicada
        self.assertFalse(consentimentos.revogar_saude(salvo, origem=Consentimento.ORIGEM_PORTAL))
        self.assertEqual(LogAuditoria.objects.filter(acao='LGPD: consentimento de saude revogado').count(), 1)

    def test_sem_aceite_nao_ha_o_que_revogar(self):
        outra = criar_cliente(nome='Sem Ficha', telefone='17991110002')
        self.assertFalse(consentimentos.saude_consentida(outra))
        self.assertFalse(consentimentos.revogar_saude(outra, origem=Consentimento.ORIGEM_PORTAL))
        self.assertIsNone(Cliente.objects.get(pk=outra.pk).saude_revogada_em)

    def test_origem_invalida_e_recusada(self):
        with self.assertRaises(ValueError):
            consentimentos.revogar_saude(self.cliente, origem='QUALQUER')

    def test_novo_aceite_saude_reativa(self):
        consentimentos.revogar_saude(self.cliente, origem=Consentimento.ORIGEM_PORTAL)
        cliente = Cliente.objects.get(pk=self.cliente.pk)
        # nova ficha: o wizard/ficha publica gravam o aceite da versao vigente de novo
        AceiteTermo.registrar(cliente, self.termo, _request())
        self.assertIsNone(Cliente.objects.get(pk=self.cliente.pk).saude_revogada_em)
        self.assertTrue(consentimentos.saude_consentida(self.cliente))
        self.assertEqual(AceiteTermo.objects.filter(cliente=self.cliente).count(), 1)   # prova do 1o aceite

    def test_aceite_de_versao_nova_posterior_a_revogacao_vale(self):
        revogada = AceiteTermo.objects.get(cliente=self.cliente).criado_em   # mesmo instante do 1o aceite
        Cliente.objects.filter(pk=self.cliente.pk).update(saude_revogada_em=revogada)
        self.assertFalse(consentimentos.saude_consentida(self.cliente))
        VersaoTermo.objects.filter(tipo='SAUDE').update(ativa=False)
        nova = VersaoTermo.objects.create(tipo='SAUDE', titulo='Saude v2', conteudo='Texto v2.', versao='2.0',
                                          vigente_desde=timezone.localdate())
        # relogio do Windows tem resolucao grossa: o aceite novo nasce explicitamente depois
        with patch('django.utils.timezone.now', return_value=revogada + timedelta(seconds=1)):
            AceiteTermo.objects.create(cliente=self.cliente, versao_termo=nova, conteudo_sha256=nova.sha256_conteudo)
        self.assertTrue(consentimentos.saude_consentida(self.cliente))


class AlertaRevogacaoTests(TestCase):
    def setUp(self):
        self.cliente = criar_cliente(nome='Rosa Alerta', telefone='17991110003')
        Prontuario.objects.create(cliente=self.cliente, alergias='Látex')
        AceiteTermo.registrar(self.cliente, VersaoTermo.saude_vigente())

    def test_sem_revogacao_sem_marcador(self):
        self.assertEqual([a['label'] for a in alertas_saude(self.cliente)], ['Alergias'])

    def test_revogacao_vira_o_primeiro_item_e_o_registro_continua(self):
        consentimentos.revogar_saude(self.cliente, origem=Consentimento.ORIGEM_ADMIN, motivo='Pedido no balcão')
        alertas = alertas_saude(Cliente.objects.get(pk=self.cliente.pk))
        self.assertEqual(alertas[0]['tipo'], 'revogado')
        self.assertEqual([a['label'] for a in alertas[1:]], ['Alergias'])   # o registro ja feito fica
        self.assertIn(self.cliente.pk, alertas_em_lote([self.cliente.pk]))
        html = render_to_string('partials/_alerta_saude.html', {'alertas': alertas})
        self.assertIn('Consentimento de saúde revogado em', html)
        self.assertIn('Látex', html)


class PreferenciasPortalTests(TestCase):
    def setUp(self):
        self.cliente = criar_cliente(nome='Ana Portal', telefone='17991110004')

    def test_marcar_concede_e_desmarcar_revoga_com_origem_portal(self):
        novas = consentimentos.registrar_do_portal(
            self.cliente, {'consent_email_marketing': True, 'consent_whatsapp_nps': True}, request=_request(),
        )
        self.assertEqual({(c.finalidade, c.canal) for c in novas}, {consentimentos.MARKETING_EMAIL,
                                                                    consentimentos.PESQUISA_WHATSAPP})
        self.assertEqual({c.origem for c in novas}, {'PORTAL'})
        self.assertEqual({c.ip_origem for c in novas}, {'203.0.113.7'})
        self.assertEqual(novas[0].texto, consentimentos.TEXTOS_WIZARD['consent_email_marketing'])
        # desmarcar o marketing revoga; o par sem mudanca nao grava nada
        novas = consentimentos.registrar_do_portal(self.cliente, {'consent_whatsapp_nps': True}, request=_request())
        self.assertEqual([(c.finalidade, c.concedido, c.origem) for c in novas], [('MARKETING', False, 'PORTAL')])
        estado = {p['campo']: p['concedido'] for p in consentimentos.preferencias_portal(self.cliente)}
        self.assertEqual(estado, {'consent_email_marketing': False, 'consent_whatsapp_nps': True,
                                  'consent_whatsapp_confirmacao': False})


class AdminRevogarSaudeTests(TestCase):
    URL = '/django-admin-sv/aranha_estetica/cliente/'

    def setUp(self):
        self.admin = _admin()
        device = _com_totp(self.admin)
        self.c = Client()
        self.c.force_login(self.admin)
        self.c.post(reverse('aranha:admin_2fa_verify'), {'token': _token(device)})
        self.cliente = criar_cliente(nome='Bia Admin', telefone='17991110005')
        AceiteTermo.registrar(self.cliente, VersaoTermo.saude_vigente())

    def test_exige_motivo(self):
        resp = self.c.post(self.URL, {'action': 'acao_revogar_saude', '_selected_action': [self.cliente.pk]})
        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(Cliente.objects.get(pk=self.cliente.pk).saude_revogada_em)

    def test_registra_com_autor_e_motivo(self):
        resp = self.c.post(self.URL, {
            'action': 'acao_revogar_saude', '_selected_action': [self.cliente.pk],
            'motivo': 'Pediu por telefone em 28/10',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIsNotNone(Cliente.objects.get(pk=self.cliente.pk).saude_revogada_em)
        log = LogAuditoria.objects.get(acao='LGPD: consentimento de saude revogado')
        self.assertEqual((log.usuario, log.detalhes['origem'], log.detalhes['motivo']),
                         (self.admin, 'ADMIN', 'Pediu por telefone em 28/10'))
