"""Testes do job_expirar_pacotes (Celery task)."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from app_shivazen.models import PacoteCliente
from app_shivazen.tasks import job_expirar_pacotes

from .factories import (
    criar_cliente,
    criar_pacote,
    criar_pacote_cliente,
    criar_procedimento,
    criar_profissional,
)


class JobExpirarPacotesTests(TestCase):
    def setUp(self):
        self.cliente = criar_cliente()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.pacote = criar_pacote(procedimento=self.proc, sessoes=4)

    def test_expira_pacote_com_data_passada(self):
        pc = criar_pacote_cliente(self.cliente, self.pacote)
        ontem = timezone.now().date() - timedelta(days=1)
        PacoteCliente.objects.filter(pk=pc.pk).update(data_expiracao=ontem)

        job_expirar_pacotes()

        pc.refresh_from_db()
        self.assertEqual(pc.status, 'EXPIRADO')

    def test_nao_expira_pacote_com_data_futura(self):
        pc = criar_pacote_cliente(self.cliente, self.pacote)
        amanha = timezone.now().date() + timedelta(days=1)
        PacoteCliente.objects.filter(pk=pc.pk).update(data_expiracao=amanha)

        job_expirar_pacotes()

        pc.refresh_from_db()
        self.assertEqual(pc.status, 'ATIVO')

    def test_nao_expira_pacote_com_data_hoje(self):
        pc = criar_pacote_cliente(self.cliente, self.pacote)
        hoje = timezone.now().date()
        PacoteCliente.objects.filter(pk=pc.pk).update(data_expiracao=hoje)

        job_expirar_pacotes()

        pc.refresh_from_db()
        self.assertEqual(pc.status, 'ATIVO')  # expira somente quando data < hoje

    def test_nao_altera_pacote_ja_finalizado(self):
        pc = criar_pacote_cliente(self.cliente, self.pacote, status='FINALIZADO')
        ontem = timezone.now().date() - timedelta(days=1)
        PacoteCliente.objects.filter(pk=pc.pk).update(data_expiracao=ontem)

        job_expirar_pacotes()

        pc.refresh_from_db()
        self.assertEqual(pc.status, 'FINALIZADO')

    def test_nao_altera_pacote_ja_cancelado(self):
        pc = criar_pacote_cliente(self.cliente, self.pacote, status='CANCELADO')
        ontem = timezone.now().date() - timedelta(days=1)
        PacoteCliente.objects.filter(pk=pc.pk).update(data_expiracao=ontem)

        job_expirar_pacotes()

        pc.refresh_from_db()
        self.assertEqual(pc.status, 'CANCELADO')

    def test_expira_multiplos_pacotes_ativos(self):
        pc1 = criar_pacote_cliente(
            criar_cliente(telefone='17900000001'), self.pacote,
        )
        pc2 = criar_pacote_cliente(
            criar_cliente(telefone='17900000002'), self.pacote,
        )
        ontem = timezone.now().date() - timedelta(days=1)
        PacoteCliente.objects.filter(pk__in=[pc1.pk, pc2.pk]).update(data_expiracao=ontem)

        job_expirar_pacotes()

        pc1.refresh_from_db()
        pc2.refresh_from_db()
        self.assertEqual(pc1.status, 'EXPIRADO')
        self.assertEqual(pc2.status, 'EXPIRADO')
