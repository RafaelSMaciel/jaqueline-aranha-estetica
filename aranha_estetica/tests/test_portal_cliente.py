"""Area da cliente (decisao 39) e cancelamento tardio (decisao 10).

Sessao aberta no fim do wizard sem novo SMS; preferencias com origem PORTAL; revogacao do
consentimento de saude; pedido de correcao/exclusao com trilha e e-mail; "baixar meus dados" so com
verificacao recente e sem saude; sair so por POST; cancelar com menos de 24 h conta como falta.
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.contrib.sessions.backends.db import SessionStore
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.excecoes import RegraDeNegocio
from aranha_estetica.models import AceiteTermo, Atendimento, Cliente, Consentimento, LogAuditoria, Prontuario, VersaoTermo
from aranha_estetica.services import agenda, otp, portal_cliente

from .factories import criar_cliente, criar_notificacao_com_link, criar_procedimento, criar_profissional

TEL = '17988886666'


def _local(dias=0, horas=0):
    return timezone.now() + timedelta(days=dias, hours=horas)


def _atendimento(cliente, prof, proc, inicio, status='AGENDADO'):
    return Atendimento.objects.create(
        cliente=cliente, profissional=prof, procedimento=proc, data_hora_inicio=inicio,
        data_hora_fim=inicio + timedelta(minutes=proc.duracao_minutos), status=status,
        valor_cobrado=Decimal('120.00'),
    )


def _logar(client, telefone=TEL, verificado_ha=timedelta(minutes=1)):
    session = client.session
    session[portal_cliente.SESSAO_MEUS_AGENDAMENTOS] = telefone
    session[portal_cliente.SESSAO_VERIFICADA_EM] = (timezone.now() - verificado_ha).isoformat()
    session.save()


class SessaoDoWizardTests(TestCase):
    def _request(self, expira_em):
        req = RequestFactory().get('/')
        req.session = SessionStore()
        req.session[otp.SESSAO_AGENDAMENTO] = TEL
        req.session[otp.SESSAO_AGENDAMENTO_EXPIRA] = expira_em.isoformat()
        return req

    def test_abre_sem_novo_sms_com_a_hora_do_otp_do_wizard(self):
        req = self._request(timezone.now() + otp.VALIDADE_VERIFICACAO - timedelta(minutes=2))
        portal_cliente.abrir_sessao_do_wizard(req, TEL)
        self.assertEqual(portal_cliente.telefone_da_sessao(req), TEL)
        self.assertTrue(portal_cliente.sessao_recente(req))
        self.assertEqual(req.session.get_expiry_age(), portal_cliente.DURACAO_SESSAO_SEGUNDOS)

    def test_otp_do_wizard_antigo_nao_libera_o_download(self):
        req = self._request(timezone.now() + timedelta(minutes=5))   # verificado ha 25 min
        portal_cliente.abrir_sessao_do_wizard(req, TEL)
        self.assertEqual(portal_cliente.telefone_da_sessao(req), TEL)
        self.assertFalse(portal_cliente.sessao_recente(req))


@override_settings(RATELIMIT_ENABLE=False)
class AreaDaClienteTests(TestCase):
    def setUp(self):
        self.cli = criar_cliente(nome='Clara Portal', telefone=TEL, email='clara@example.com')
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)

    def test_area_mostra_preferencias_privacidade_e_sair_por_post(self):
        _logar(self.client)
        html = self.client.get(reverse('aranha:meus_agendamentos')).content.decode()
        self.assertIn('Minhas preferências', html)
        self.assertIn('name="consent_email_marketing"', html)
        self.assertIn('Pedir correção ou exclusão', html)
        self.assertIn(f'action="{reverse("aranha:meus_agendamentos_logout")}"', html)
        self.assertNotIn('Revogar consentimento de saúde', html)   # sem aceite SAUDE

    def test_sair_por_get_da_405_e_por_post_encerra(self):
        _logar(self.client)
        self.assertEqual(self.client.get(reverse('aranha:meus_agendamentos_logout')).status_code, 405)
        self.client.post(reverse('aranha:meus_agendamentos_logout'))
        self.assertNotIn(portal_cliente.SESSAO_MEUS_AGENDAMENTOS, self.client.session)

    def test_preferencias_gravam_com_origem_portal(self):
        _logar(self.client)
        resp = self.client.post(reverse('aranha:portal_preferencias'),
                                {'consent_whatsapp_confirmacao': '1'}, REMOTE_ADDR='198.51.100.4')
        self.assertEqual(resp.status_code, 302)
        linha = Consentimento.objects.get(cliente=self.cli)
        self.assertEqual((linha.finalidade, linha.concedido, linha.origem, linha.ip_origem),
                         ('LEMBRETE_AGENDAMENTO', True, 'PORTAL', '198.51.100.4'))

    def test_sem_sessao_nada_grava(self):
        self.client.post(reverse('aranha:portal_preferencias'), {'consent_email_marketing': '1'})
        self.assertFalse(Consentimento.objects.exists())

    def test_revogar_saude_pelo_portal(self):
        AceiteTermo.registrar(self.cli, VersaoTermo.saude_vigente())
        _logar(self.client)
        self.assertContains(self.client.get(reverse('aranha:meus_agendamentos')), 'Revogar consentimento de saúde')
        self.client.post(reverse('aranha:portal_revogar_saude'))
        self.assertIsNotNone(Cliente.objects.get(pk=self.cli.pk).saude_revogada_em)
        log = LogAuditoria.objects.get(acao='LGPD: consentimento de saude revogado')
        self.assertEqual(log.detalhes['origem'], 'PORTAL')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                       ADMIN_EMAIL='dona@clinica.example')
    def test_pedido_de_correcao_fica_na_trilha_e_vai_por_email(self):
        _logar(self.client)
        resp = self.client.post(reverse('aranha:portal_pedido'),
                                {'tipo': 'correcao', 'descricao': 'Meu e-mail novo é clara.nova@example.com'},
                                follow=True)
        prazo = (timezone.localdate() + timedelta(days=15)).isoformat()
        log = LogAuditoria.objects.get(acao='LGPD: pedido de correcao')
        self.assertEqual((log.registro_id, log.detalhes), (self.cli.pk, {'prazo': prazo}))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['dona@clinica.example'])
        self.assertIn('clara.nova@example.com', mail.outbox[0].body)   # o texto livre so no e-mail
        self.assertContains(resp, 'responde até')

    def test_pedido_de_tipo_invalido_nao_registra(self):
        _logar(self.client)
        self.client.post(reverse('aranha:portal_pedido'), {'tipo': 'outra-coisa'})
        self.assertFalse(LogAuditoria.objects.filter(acao__startswith='LGPD: pedido').exists())

    def test_baixar_dados_exige_verificacao_de_ate_10_min_e_sai_sem_saude(self):
        Prontuario.objects.create(cliente=self.cli, alergias='Látex')
        _logar(self.client, verificado_ha=timedelta(minutes=25))
        resp = self.client.post(reverse('aranha:portal_meus_dados'))
        self.assertRedirects(resp, reverse('aranha:meus_agendamentos'), fetch_redirect_response=False)
        self.assertNotIn(portal_cliente.SESSAO_MEUS_AGENDAMENTOS, self.client.session)   # pede codigo novo

        _logar(self.client, verificado_ha=timedelta(minutes=3))
        resp = self.client.post(reverse('aranha:portal_meus_dados'))
        self.assertIn('attachment', resp['Content-Disposition'])
        dados = json.loads(resp.content)
        self.assertEqual(dados['pessoal']['nome'], 'Clara Portal')
        self.assertNotIn('prontuario', dados)
        self.assertTrue(LogAuditoria.objects.filter(acao='DSAR: exportacao de dados', registro_id=self.cli.pk).exists())


class CancelamentoTardioServiceTests(TestCase):
    def setUp(self):
        self.cli = criar_cliente(nome='Ana Tardia', telefone='17988885555')
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)

    def test_com_10_horas_soma_falta_e_na_terceira_bloqueia(self):
        for i in range(3):
            at = _atendimento(self.cli, self.prof, self.proc, _local(horas=10 + i))
            _at, tardio = agenda.cancelar_pela_cliente(at.pk, origem='portal')
            self.assertTrue(tardio)
            self.assertEqual(Atendimento.objects.get(pk=at.pk).status, 'CANCELADO')
        cli = Cliente.objects.get(pk=self.cli.pk)
        self.assertEqual((cli.faltas_consecutivas, cli.bloqueado_online), (3, True))

    def test_com_48_horas_nao_soma(self):
        at = _atendimento(self.cli, self.prof, self.proc, _local(dias=2))
        _at, tardio = agenda.cancelar_pela_cliente(at.pk, origem='portal')
        self.assertFalse(tardio)
        self.assertEqual(Cliente.objects.get(pk=self.cli.pk).faltas_consecutivas, 0)

    def test_pedido_pendente_nao_conta_falta(self):
        at = _atendimento(self.cli, self.prof, self.proc, _local(horas=5), status='PENDENTE')
        _at, tardio = agenda.cancelar_pela_cliente(at.pk, origem='portal')
        self.assertFalse(tardio)

    def test_recusas(self):
        passado = _atendimento(self.cli, self.prof, self.proc, _local(horas=-2))
        with self.assertRaises(RegraDeNegocio) as ctx:
            agenda.cancelar_pela_cliente(passado.pk, origem='portal')
        self.assertEqual(ctx.exception.codigo, 'passado')
        pendente = _atendimento(self.cli, self.prof, self.proc, _local(dias=3), status='PENDENTE')
        with self.assertRaises(RegraDeNegocio) as ctx:
            agenda.cancelar_pela_cliente(pendente.pk, origem='link', so_aprovados=True)
        self.assertEqual(ctx.exception.codigo, 'nao_cancelavel')


@override_settings(RATELIMIT_ENABLE=False)
class CancelamentoTardioTelasTests(TestCase):
    def setUp(self):
        self.cli = criar_cliente(nome='Bia Tela', telefone=TEL)
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)

    def test_portal_avisa_antes_e_a_resposta_confirma(self):
        perto = _atendimento(self.cli, self.prof, self.proc, _local(horas=10))
        longe = _atendimento(self.cli, self.prof, self.proc, _local(dias=3))
        _logar(self.client)
        html = self.client.get(reverse('aranha:meus_agendamentos')).content.decode()
        self.assertIn('id="cancelModalTardio"', html)
        self.assertRegex(html, rf'data-id="{perto.pk}"[^>]*data-tardio="1"')
        self.assertRegex(html, rf'data-id="{longe.pk}"[^>]*data-tardio=""')
        resp = self.client.post(reverse('aranha:cancelar_agendamento'),
                                data=json.dumps({'atendimento_id': perto.pk}), content_type='application/json')
        self.assertEqual((resp.status_code, resp.json()['ok'], resp.json()['tardio']), (200, True, True))
        self.assertIn('conta como falta', resp.json()['mensagem'])
        self.assertEqual(Cliente.objects.get(pk=self.cli.pk).faltas_consecutivas, 1)

    def test_link_do_lembrete_avisa_e_conta_falta(self):
        at = _atendimento(self.cli, self.prof, self.proc, _local(horas=8))
        _notif, token = criar_notificacao_com_link(at, 'LEMBRETE', 'WHATSAPP', status='ENVIADO')
        url = reverse('aranha:confirmar_presenca', args=[token])
        self.assertContains(self.client.get(url), 'conta como falta')
        resp = self.client.post(url, {'acao': 'cancelar'})
        self.assertContains(resp, 'contou como falta')
        self.assertEqual(Atendimento.objects.get(pk=at.pk).status, 'CANCELADO')
        self.assertEqual(Cliente.objects.get(pk=self.cli.pk).faltas_consecutivas, 1)


