"""Tests for Atendimento status change signals."""
from unittest.mock import patch

from django.test import TestCase

from app_shivazen.models import Atendimento, PacoteCliente, SessaoPacote

from .factories import (
    criar_atendimento,
    criar_cliente,
    criar_pacote,
    criar_pacote_cliente,
    criar_procedimento,
    criar_profissional,
)


class FaltaSignalTests(TestCase):
    """3-strike system via post_save signal."""

    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()

    @patch('app_shivazen.signals.job_notificar_fila_espera.delay')
    def test_falta_incrementa_contador(self, mock_delay):
        atd = criar_atendimento(self.cli, self.prof, self.proc, status='CONFIRMADO')
        atd.status = 'FALTOU'
        atd.save()
        self.cli.refresh_from_db()
        self.assertEqual(self.cli.faltas_consecutivas, 1)

    @patch('app_shivazen.signals.job_notificar_fila_espera.delay')
    def test_tres_faltas_bloqueiam_online(self, mock_delay):
        for i in range(3):
            atd = criar_atendimento(self.cli, self.prof, self.proc, status='CONFIRMADO')
            atd.status = 'FALTOU'
            atd.save()
        self.cli.refresh_from_db()
        self.assertTrue(self.cli.bloqueado_online)
        self.assertEqual(self.cli.faltas_consecutivas, 3)

    def test_realizado_reseta_faltas(self):
        self.cli.faltas_consecutivas = 2
        self.cli.save()
        atd = criar_atendimento(self.cli, self.prof, self.proc, status='CONFIRMADO')
        atd.status = 'REALIZADO'
        atd.save()
        self.cli.refresh_from_db()
        self.assertEqual(self.cli.faltas_consecutivas, 0)
        self.assertFalse(self.cli.bloqueado_online)


class PacoteDebitoSignalTests(TestCase):
    """Package session debit via post_save signal."""

    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()
        self.pacote = criar_pacote(procedimento=self.proc, sessoes=3)
        self.pc = criar_pacote_cliente(self.cli, self.pacote)

    def test_realizado_debita_sessao_pacote(self):
        atd = criar_atendimento(self.cli, self.prof, self.proc, status='CONFIRMADO')
        atd.status = 'REALIZADO'
        atd.save()
        self.assertEqual(SessaoPacote.objects.filter(pacote_cliente=self.pc).count(), 1)

    def test_pacote_finaliza_quando_todas_sessoes_usadas(self):
        for i in range(3):
            atd = criar_atendimento(self.cli, self.prof, self.proc, status='CONFIRMADO')
            atd.status = 'REALIZADO'
            atd.save()
        self.pc.refresh_from_db()
        self.assertEqual(self.pc.status, 'FINALIZADO')


class FilaEsperaSignalTests(TestCase):
    """Waitlist notification trigger via post_save signal."""

    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()

    @patch('app_shivazen.signals.job_notificar_fila_espera.delay')
    def test_cancelamento_dispara_notificacao_fila(self, mock_delay):
        atd = criar_atendimento(self.cli, self.prof, self.proc, status='CONFIRMADO')
        atd.status = 'CANCELADO'
        atd.save()
        mock_delay.assert_called_once()

    @patch('app_shivazen.signals.job_notificar_fila_espera.delay')
    def test_realizado_nao_dispara_fila(self, mock_delay):
        atd = criar_atendimento(self.cli, self.prof, self.proc, status='CONFIRMADO')
        atd.status = 'REALIZADO'
        atd.save()
        mock_delay.assert_not_called()
