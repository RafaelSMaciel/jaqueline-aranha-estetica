"""Fila de e-mail (services/comunicacao): Notificacao EMAIL como outbox + historico, reenvio pelo
cron e o comando rodar_job (sem Celery)."""
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core import mail
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from aranha_estetica.models import Cliente, Notificacao
from aranha_estetica.services import comunicacao

from .factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional

CANCELAMENTO = 'aranha_estetica.utils.email.enviar_cancelamento_email'


class _Base(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.at = criar_atendimento(criar_cliente(email='cli@exemplo.com'), self.prof,
                                    criar_procedimento(profissional=self.prof))

    def _notif(self):
        return Notificacao.objects.get(atendimento=self.at, canal='EMAIL')


class FilaEmailTests(_Base):
    def test_sai_no_on_commit_e_fica_no_historico(self):
        with self.captureOnCommitCallbacks(execute=True):
            comunicacao.enfileirar_email(self.at, Notificacao.Tipo.CANCELAMENTO)
        notif = self._notif()
        self.assertEqual((notif.status, notif.tipo, notif.tentativas), ('ENVIADO', 'CANCELAMENTO', 0))
        self.assertIsNotNone(notif.enviado_em)
        self.assertIsNone(notif.proxima_tentativa_em)
        self.assertEqual(mail.outbox[0].to, ['cli@exemplo.com'])

    def test_sem_commit_nao_sai(self):
        with self.captureOnCommitCallbacks(execute=False):
            comunicacao.enfileirar_email(self.at, Notificacao.Tipo.CANCELAMENTO)
        self.assertEqual(self._notif().status, 'PENDENTE')
        self.assertEqual(mail.outbox, [])

    def test_falha_reagenda_e_o_cron_reenvia(self):
        with patch(CANCELAMENTO, return_value=False), self.captureOnCommitCallbacks(execute=True):
            comunicacao.enfileirar_email(self.at, Notificacao.Tipo.CANCELAMENTO)
        notif = self._notif()
        self.assertEqual((notif.status, notif.tentativas), ('PENDENTE', 1))
        self.assertGreater(notif.proxima_tentativa_em, timezone.now() + timedelta(minutes=4))
        # antes da hora o cron nao mexe; depois, envia
        self.assertEqual(comunicacao.reenviar_pendentes()['enviados'], 0)
        r = comunicacao.reenviar_pendentes(agora=timezone.now() + timedelta(minutes=6))
        self.assertEqual(r, {'enviados': 1, 'pendentes': 0})
        self.assertEqual(self._notif().status, 'ENVIADO')

    def test_esgotou_as_tentativas_vira_falhou(self):
        with patch(CANCELAMENTO, return_value=False):
            notif = comunicacao.enfileirar_email(self.at, Notificacao.Tipo.CANCELAMENTO)
            for _ in range(comunicacao.MAX_TENTATIVAS):
                comunicacao.enviar_da_fila(notif.pk)
        notif.refresh_from_db()
        self.assertEqual((notif.status, notif.tentativas), ('FALHOU', comunicacao.MAX_TENTATIVAS))
        self.assertFalse(comunicacao.enviar_da_fila(notif.pk))   # fora da fila

    def test_pedido_velho_nao_sai_mais(self):
        notif = comunicacao.enfileirar_email(self.at, Notificacao.Tipo.CANCELAMENTO)
        Notificacao.objects.filter(pk=notif.pk).update(criado_em=timezone.now() - timedelta(days=3))
        with patch(CANCELAMENTO, return_value=False):
            comunicacao.enviar_da_fila(notif.pk)
        self.assertEqual(self._notif().status, 'FALHOU')

    def test_sem_destinatario_nao_enfileira(self):
        Cliente.objects.filter(pk=self.at.cliente_id).update(email=None)
        self.at.refresh_from_db()
        self.assertIsNone(comunicacao.enfileirar_email(self.at, Notificacao.Tipo.CANCELAMENTO))
        self.assertIsNone(comunicacao.enfileirar_email(self.at, Notificacao.Tipo.APROVACAO))  # sem usuario
        self.assertFalse(Notificacao.objects.exists())

    def test_fila_nao_guarda_copia_do_dado(self):
        """Destino sai do cadastro na hora do envio: cliente esquecida = nada a enviar."""
        notif = comunicacao.enfileirar_email(self.at, Notificacao.Tipo.CANCELAMENTO)
        Cliente.objects.filter(pk=self.at.cliente_id).update(email=None, nome='Anonimizada')
        self.assertFalse(comunicacao.enviar_da_fila(notif.pk))
        self.assertEqual(self._notif().status, 'FALHOU')
        self.assertEqual(mail.outbox, [])


class RodarJobTests(_Base):
    def test_rodar_job_emails(self):
        with patch(CANCELAMENTO, return_value=False), self.captureOnCommitCallbacks(execute=True):
            comunicacao.enfileirar_email(self.at, Notificacao.Tipo.CANCELAMENTO)
        Notificacao.objects.update(proxima_tentativa_em=timezone.now() - timedelta(minutes=1))
        saida = StringIO()
        call_command('rodar_job', 'emails', stdout=saida)
        self.assertIn('1 e-mails enviados; 0 na fila', saida.getvalue())

    def test_lista_e_job_desconhecido(self):
        saida = StringIO()
        call_command('rodar_job', '--lista', stdout=saida)
        self.assertIn('housekeeping', saida.getvalue())
        with self.assertRaises(CommandError):
            call_command('rodar_job', 'nao-existe')

    def test_falha_do_job_vira_erro_do_comando(self):
        with patch.dict('aranha_estetica.jobs.JOB_MAP', {'x': lambda: 1 / 0}), \
                self.assertRaisesMessage(CommandError, 'x falhou'):
            call_command('rodar_job', 'x')
