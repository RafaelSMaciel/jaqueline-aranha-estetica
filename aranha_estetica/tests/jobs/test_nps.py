"""Job nps_24h e alerta de detrator: envio exatamente uma vez, falha isolada e as regras do D20."""
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.constants import NPS_INTERVALO_DIAS, VALIDADE_LINK_NPS
from aranha_estetica.jobs import job_detrator_alerta, job_nps_24h
from aranha_estetica.models import AvaliacaoNPS, Configuracao, LinkAcesso, Notificacao
from aranha_estetica.services import nps

from ..factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional

POST = 'aranha_estetica.utils.whatsapp.requests.post'
ENVIAR = 'aranha_estetica.utils.whatsapp.enviar_template'
WHATSAPP_OK = {'WHATSAPP_TOKEN': 'tok', 'WHATSAPP_PHONE_ID': '123'}


def _resposta(status=200):
    return mock.Mock(status_code=status, text='{}')


@override_settings(DEBUG=False, SITE_URL='https://clinica.example.com')
@mock.patch.dict('os.environ', WHATSAPP_OK)
class NpsJobTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente(nome='Bia', consent_whatsapp_nps=True)

    def _realizado(self, cliente=None, horas_atras=25, **campos):
        inicio = timezone.now() - timedelta(hours=horas_atras)
        at = criar_atendimento(cliente or self.cli, self.prof, self.proc, data_hora=inicio, status='REALIZADO')
        if campos:
            type(at).objects.filter(pk=at.pk).update(**campos)
            at.refresh_from_db()
        return at

    def test_caminho_feliz_notificacao_link_e_token_que_abre_a_pesquisa(self):
        at = self._realizado()
        with mock.patch(POST, return_value=_resposta()) as post:
            self.assertEqual(job_nps_24h(), '1 NPS enviados')
        notif = Notificacao.objects.get(atendimento=at, tipo='NPS')
        self.assertEqual((notif.canal, notif.status), ('WHATSAPP', 'ENVIADO'))
        self.assertIsNotNone(notif.enviado_em)
        link = LinkAcesso.objects.get(notificacao=notif, finalidade='NPS')
        self.assertEqual(link.expira_em, notif.criado_em + VALIDADE_LINK_NPS)
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['template']['name'], 'nps_pos_atendimento')
        url = payload['template']['components'][0]['parameters'][2]['text']
        self.assertTrue(url.startswith('https://clinica.example.com/nps/'))
        token = url.rstrip('/').split('/')[-1]
        self.assertEqual(self.client.get(reverse('aranha:nps_web', args=[token])).status_code, 200)
        self.assertNotIn('Bia', notif.mensagem)   # sem o nome da cliente no historico de envio

    def test_falha_isolada_vira_falhou_e_o_lote_segue(self):
        primeira = self._realizado(cliente=criar_cliente(nome='A', consent_whatsapp_nps=True))
        segunda = self._realizado(cliente=criar_cliente(nome='B', consent_whatsapp_nps=True), horas_atras=30)
        with mock.patch(ENVIAR, side_effect=[RuntimeError('meta fora'), True]):
            self.assertEqual(nps.enviar_pesquisas(), 1)
        status = dict(Notificacao.objects.filter(tipo='NPS').values_list('atendimento_id', 'status'))
        self.assertEqual(status, {primeira.pk: 'FALHOU', segunda.pk: 'ENVIADO'})

    def test_cliente_sem_telefone_nao_gera_nada(self):
        self._realizado(cliente=criar_cliente(nome='Sem tel', telefone='', consent_whatsapp_nps=True))
        with mock.patch(ENVIAR, return_value=True) as enviar:
            nps.enviar_pesquisas()
        enviar.assert_not_called()
        self.assertFalse(Notificacao.objects.exists())

    def test_rodar_duas_vezes_envia_uma(self):
        self._realizado()
        with mock.patch(ENVIAR, return_value=True) as enviar:
            nps.enviar_pesquisas()
            nps.enviar_pesquisas()
        enviar.assert_called_once()

    def test_retorno_false_vira_falhou_e_a_proxima_execucao_tenta_de_novo(self):
        at = self._realizado()
        with mock.patch(POST, return_value=_resposta(400)):
            self.assertEqual(nps.enviar_pesquisas(), 0)
        self.assertEqual(Notificacao.objects.get(atendimento=at).status, 'FALHOU')
        with mock.patch(ENVIAR, return_value=True) as enviar:
            self.assertEqual(nps.enviar_pesquisas(), 1)
        enviar.assert_called_once()
        self.assertEqual(Notificacao.objects.filter(atendimento=at, status='ENVIADO').count(), 1)

    def test_retorno_gratuito_nao_gera_nps(self):
        origem = self._realizado(horas_atras=24 * 20)
        self._realizado(eh_retorno=True, atendimento_origem=origem)
        self.assertFalse(nps.candidatos().exists())

    def test_segunda_pesquisa_dentro_do_intervalo_nao_sai(self):
        antigo = self._realizado(horas_atras=24 * 60)
        Notificacao.objects.create(atendimento=antigo, tipo='NPS', canal='WHATSAPP', status='ENVIADO')
        self._realizado()
        self.assertFalse(nps.candidatos().exists())
        Notificacao.objects.filter(atendimento=antigo).update(
            criado_em=timezone.now() - timedelta(days=NPS_INTERVALO_DIAS + 1))
        self.assertTrue(nps.candidatos().exists())

    def test_duas_sessoes_da_mesma_cliente_na_rodada_geram_uma_pesquisa(self):
        self._realizado(horas_atras=26)
        recente = self._realizado(horas_atras=25)
        with mock.patch(ENVIAR, return_value=True) as enviar:
            self.assertEqual(nps.enviar_pesquisas(), 1)
        enviar.assert_called_once()
        self.assertEqual(Notificacao.objects.get(tipo='NPS').atendimento_id, recente.pk)

    @mock.patch.dict('os.environ', {'WHATSAPP_TOKEN': '', 'WHATSAPP_PHONE_ID': ''})
    def test_sem_canal_nao_cria_notificacao(self):
        self._realizado()
        self.assertEqual(job_nps_24h(), '0 NPS enviados')
        self.assertFalse(Notificacao.objects.exists())

    def test_trava_diaria_do_job(self):
        from aranha_estetica.jobs import JobJaExecutado, executar
        self._realizado()
        with mock.patch(ENVIAR, return_value=True):
            executar('nps_24h')
            with self.assertRaises(JobJaExecutado):
                executar('nps_24h')


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AlertaDetratorTests(TestCase):
    def setUp(self):
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        at = criar_atendimento(criar_cliente(nome='Detratora'), prof, proc, status='REALIZADO')
        self.avaliacao = AvaliacaoNPS.objects.create(atendimento=at, nota=3, comentario='Atrasou')
        Configuracao.objects.create(chave='email_admin', valor='dona@example.com')

    def test_um_alerta_por_avaliacao_mesmo_rodando_duas_vezes(self):
        self.assertEqual(job_detrator_alerta(), '1 alertas de detrator')
        self.assertEqual(job_detrator_alerta(), '0 alertas de detrator')
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ['dona@example.com'])
        self.assertIn('Detratora nota 3', msg.subject)
        self.assertIn('Comentário: Atrasou', msg.body)
        self.assertEqual(msg.alternatives, [])   # texto puro, interno

    def test_falha_de_envio_nao_marca_e_tenta_na_proxima(self):
        with mock.patch('aranha_estetica.utils.email.EmailMultiAlternatives.send', side_effect=OSError('smtp')):
            self.assertEqual(nps.alertar_detratores(), 0)
        self.avaliacao.refresh_from_db()
        self.assertFalse(self.avaliacao.alerta_enviado)
        self.assertEqual(nps.alertar_detratores(), 1)

    @override_settings(ADMIN_EMAIL='env@example.com')
    def test_destino_sem_configuracao_usa_admin_email_do_settings(self):
        Configuracao.objects.all().delete()
        self.assertEqual(nps.destino_alerta(), 'env@example.com')

    def test_promotor_nao_gera_alerta(self):
        AvaliacaoNPS.objects.filter(pk=self.avaliacao.pk).update(nota=9)
        self.assertEqual(nps.alertar_detratores(), 0)
        self.assertEqual(mail.outbox, [])


class NpsPainelPublicacaoTests(TestCase):
    """A regra da publicacao do depoimento mora no model (RegraDeNegocio)."""

    def setUp(self):
        prof = criar_profissional()
        at = criar_atendimento(criar_cliente(), prof, criar_procedimento(profissional=prof), status='REALIZADO')
        self.avaliacao = AvaliacaoNPS.objects.create(atendimento=at, nota=10, comentario='Amei')

    def test_aprovar_exige_opt_in_e_comentario(self):
        from aranha_estetica.excecoes import RegraDeNegocio
        with self.assertRaises(RegraDeNegocio) as erro:
            self.avaliacao.aprovar_publicacao()
        self.assertEqual(erro.exception.codigo, 'sem_autorizacao')
        AvaliacaoNPS.objects.filter(pk=self.avaliacao.pk).update(autoriza_publicacao=True, comentario='  ')
        self.avaliacao.refresh_from_db()
        with self.assertRaises(RegraDeNegocio) as erro:
            self.avaliacao.aprovar_publicacao()
        self.assertEqual(erro.exception.codigo, 'sem_comentario')
        AvaliacaoNPS.objects.filter(pk=self.avaliacao.pk).update(comentario='Amei')
        self.avaliacao.refresh_from_db()
        self.avaliacao.aprovar_publicacao()
        self.avaliacao.refresh_from_db()
        self.assertTrue(self.avaliacao.aprovado_publicacao)
        self.avaliacao.retirar_publicacao()
        self.avaliacao.refresh_from_db()
        self.assertFalse(self.avaliacao.aprovado_publicacao)

    def test_painel_mostra_a_recusa_do_model(self):
        from aranha_estetica.models import Usuario
        admin = Usuario.objects.create_superuser(email='adm-nps@test.com', password='senha-forte-123', nome='Adm')
        self.client.force_login(admin)
        resp = self.client.post(reverse('aranha:admin_nps_publicacao', args=[self.avaliacao.pk]),
                                {'acao': 'aprovar', 'periodo': '30'}, follow=True)
        self.assertRedirects(resp, reverse('aranha:painel_nps') + '?periodo=30')
        self.assertContains(resp, 'não autorizou publicar')
        self.assertFalse(AvaliacaoNPS.objects.get(pk=self.avaliacao.pk).aprovado_publicacao)
