"""Lembrete D-1 (WhatsApp p/ quem autorizou, e-mail p/ as demais — D19) e o push de sem desfecho."""
import re
from datetime import datetime, time, timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from aranha_estetica.jobs import job_lembrete_diario
from aranha_estetica.models import Notificacao, Usuario
from aranha_estetica.services import comunicacao, lembretes, links

from ..factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional, ja_comecou

ENVIAR_WA = 'aranha_estetica.utils.whatsapp.enviar_template'
WHATSAPP_OK = {'WHATSAPP_TOKEN': 'tok', 'WHATSAPP_PHONE_ID': '123'}
LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


def _amanha_as(hora=14):
    return timezone.make_aware(datetime.combine(timezone.localdate() + timedelta(days=1), time(hora, 0)))


@override_settings(DEBUG=False, SITE_URL='https://clinica.example.com', EMAIL_BACKEND=LOCMEM)
@mock.patch.dict('os.environ', WHATSAPP_OK)
class LembreteD1Tests(TestCase):
    def setUp(self):
        self.prof = criar_profissional(nome='Dra. Jaqueline')
        self.proc = criar_procedimento(profissional=self.prof)

    def _at(self, cliente, hora=14, status='AGENDADO'):
        return criar_atendimento(cliente, self.prof, self.proc, data_hora=_amanha_as(hora), status=status)

    def test_whatsapp_e_idempotente(self):
        self._at(criar_cliente(consent_whatsapp_confirmacao=True))
        with mock.patch(ENVIAR_WA, return_value=True) as fake:
            job_lembrete_diario()
            job_lembrete_diario()   # 18:00: quem ja recebeu nao recebe de novo
        fake.assert_called_once()

    def test_falha_isolada_e_retentada_na_proxima_rodada(self):
        a = self._at(criar_cliente(nome='A', consent_whatsapp_confirmacao=True), hora=10)
        b = self._at(criar_cliente(nome='B', consent_whatsapp_confirmacao=True), hora=11)
        with mock.patch(ENVIAR_WA, side_effect=[RuntimeError('meta fora'), True]):
            self.assertEqual(lembretes.enviar_lembretes_d1(), {'whatsapp': 1, 'email': 0})
        self.assertEqual(Notificacao.objects.get(atendimento=a).status, 'FALHOU')
        self.assertEqual(Notificacao.objects.get(atendimento=b).status, 'ENVIADO')
        with mock.patch(ENVIAR_WA, return_value=True) as fake:
            self.assertEqual(lembretes.enviar_lembretes_d1(), {'whatsapp': 1, 'email': 0})
        self.assertEqual(fake.call_args.args[0], a.cliente.telefone)

    def test_sem_opt_in_com_email_recebe_o_lembrete_por_email_com_os_links(self):
        at = self._at(criar_cliente(nome='Sem WhatsApp', email='semwa@example.com'))
        with mock.patch(ENVIAR_WA) as fake, self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(job_lembrete_diario(), '0 lembretes por WhatsApp; 1 por e-mail')
        fake.assert_not_called()
        notif = Notificacao.objects.get(atendimento=at)
        self.assertEqual((notif.tipo, notif.canal, notif.status), ('LEMBRETE', 'EMAIL', 'ENVIADO'))
        self.assertEqual(len(mail.outbox), 1)
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('14:00', html)
        confirmar = re.search(r'https://clinica\.example\.com/confirmar/([^/]+)/\?acao=confirmar', html)
        self.assertIsNotNone(confirmar)
        self.assertIn(f'/confirmar/{confirmar.group(1)}/?acao=cancelar', html)
        link = links.resolver(links.CONFIRMAR_PRESENCA, confirmar.group(1))
        self.assertEqual((link.notificacao_id, link.expira_em), (notif.pk, at.data_hora_inicio))
        # o link do e-mail confirma presenca como o do WhatsApp
        self.client.post(f'/confirmar/{confirmar.group(1)}/', {'acao': 'confirmar'})
        at.refresh_from_db()
        self.assertEqual(at.status, 'CONFIRMADO')
        with self.captureOnCommitCallbacks(execute=True):
            job_lembrete_diario()
        self.assertEqual(len(mail.outbox), 1)   # idempotente tambem por e-mail

    def test_sem_opt_in_e_sem_email_fica_sem_lembrete(self):
        self._at(criar_cliente(nome='Sem nada'))
        with mock.patch(ENVIAR_WA) as fake:
            self.assertEqual(lembretes.enviar_lembretes_d1(), {'whatsapp': 0, 'email': 0})
        fake.assert_not_called()
        self.assertFalse(Notificacao.objects.exists())

    @mock.patch.dict('os.environ', {'WHATSAPP_TOKEN': '', 'WHATSAPP_PHONE_ID': ''})
    def test_sem_canal_whatsapp_quem_autorizou_e_tem_email_vai_por_email(self):
        self._at(criar_cliente(email='autorizou@example.com', consent_whatsapp_confirmacao=True))
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(lembretes.enviar_lembretes_d1(), {'whatsapp': 0, 'email': 1})
        self.assertEqual(mail.outbox[0].to, ['autorizou@example.com'])

    def test_email_na_fila_conta_como_lembrado_e_pendente_nao_lembra(self):
        cli = criar_cliente(email='fila@example.com')
        lembrado = self._at(cli, hora=10)
        Notificacao.objects.create(atendimento=lembrado, tipo='LEMBRETE', canal='EMAIL', status='PENDENTE')
        self._at(criar_cliente(email='pendente@example.com'), hora=11, status='PENDENTE')
        self.assertEqual(lembretes.enviar_lembretes_d1(), {'whatsapp': 0, 'email': 0})

    def test_lembrete_na_fila_de_atendimento_cancelado_nao_sai(self):
        at = self._at(criar_cliente(email='cancelou@example.com'))
        notif = comunicacao.enfileirar_email(at, Notificacao.Tipo.LEMBRETE)   # on_commit nao roda no TestCase
        at.cancelar(motivo='teste')
        self.assertFalse(comunicacao.enviar_da_fila(notif.pk))
        notif.refresh_from_db()
        self.assertEqual(notif.status, 'FALHOU')
        self.assertEqual(mail.outbox, [])
        self.assertFalse(notif.links_acesso.exists())

    def test_envio_que_falha_descarta_o_link_da_tentativa(self):
        at = self._at(criar_cliente(email='falha@example.com'))
        notif = comunicacao.enfileirar_email(at, Notificacao.Tipo.LEMBRETE)
        with mock.patch('aranha_estetica.utils.email.EmailMultiAlternatives.send', side_effect=OSError('smtp')):
            self.assertFalse(comunicacao.enviar_da_fila(notif.pk))
        notif.refresh_from_db()
        self.assertEqual((notif.status, notif.tentativas), ('PENDENTE', 1))
        self.assertFalse(notif.links_acesso.exists())
        self.assertTrue(comunicacao.enviar_da_fila(notif.pk))
        self.assertEqual(notif.links_acesso.count(), 1)


class SemDesfechoTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.usuario = Usuario.objects.create_user(
            email='prof-desfecho@test.com', password='senha-forte-123', nome='Dra. Ana',
            papel=Usuario.PAPEL_PROFISSIONAL, profissional=self.prof,
        )

    def test_push_por_profissional_com_a_contagem(self):
        cli = criar_cliente()
        for hora in (9, 11):
            ja_comecou(criar_atendimento(cli, self.prof, self.proc, data_hora=_amanha_as(hora)))
        criar_atendimento(cli, self.prof, self.proc, data_hora=_amanha_as(15))   # futuro: nao conta
        with mock.patch('aranha_estetica.services.lembretes.send_push_to_user', return_value=1) as push:
            self.assertEqual(lembretes.avisar_sem_desfecho(), 1)
        usuario, payload = push.call_args.args
        self.assertEqual(usuario, self.usuario)
        self.assertIn('2 atendimentos aguardam desfecho', payload['body'])
        self.assertEqual(payload['url'], '/profissional/')

    def test_sem_pendencia_nao_manda_nada(self):
        with mock.patch('aranha_estetica.services.lembretes.send_push_to_user') as push:
            self.assertEqual(lembretes.avisar_sem_desfecho(), 0)
        push.assert_not_called()
