"""Integration tests for package purchase -> session consumption -> finalization."""
from unittest.mock import patch

from django.test import TestCase

from app_shivazen.models import PacoteCliente, SessaoPacote

from .factories import (
    criar_atendimento,
    criar_cliente,
    criar_pacote,
    criar_pacote_cliente,
    criar_procedimento,
    criar_profissional,
)


class PackageLifecycleTests(TestCase):
    """Buy package -> consume sessions -> auto-finalize."""

    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()

    def test_compra_e_consumo_completo(self):
        pacote = criar_pacote(procedimento=self.proc, sessoes=2)
        pc = criar_pacote_cliente(self.cli, pacote)
        self.assertEqual(pc.status, 'ATIVO')

        # Session 1
        atd1 = criar_atendimento(self.cli, self.prof, self.proc, status='CONFIRMADO')
        atd1.status = 'REALIZADO'
        atd1.save()
        pc.refresh_from_db()
        self.assertEqual(pc.status, 'ATIVO')
        self.assertEqual(SessaoPacote.objects.filter(pacote_cliente=pc).count(), 1)

        # Session 2 — should auto-finalize
        atd2 = criar_atendimento(self.cli, self.prof, self.proc, status='CONFIRMADO')
        atd2.status = 'REALIZADO'
        atd2.save()
        pc.refresh_from_db()
        self.assertEqual(pc.status, 'FINALIZADO')
        self.assertEqual(SessaoPacote.objects.filter(pacote_cliente=pc).count(), 2)

    @patch('app_shivazen.signals.job_notificar_fila_espera.delay')
    def test_tres_strikes_e_reset(self, mock_delay):
        # 3 faltas consecutivas — mock needed because FALTOU triggers waitlist notification
        for _ in range(3):
            atd = criar_atendimento(self.cli, self.prof, self.proc, status='CONFIRMADO')
            atd.status = 'FALTOU'
            atd.save()

        self.cli.refresh_from_db()
        self.assertTrue(self.cli.bloqueado_online)
        self.assertEqual(self.cli.faltas_consecutivas, 3)

        # 1 realizado reseta tudo
        atd = criar_atendimento(self.cli, self.prof, self.proc, status='CONFIRMADO')
        atd.status = 'REALIZADO'
        atd.save()

        self.cli.refresh_from_db()
        self.assertFalse(self.cli.bloqueado_online)
        self.assertEqual(self.cli.faltas_consecutivas, 0)

    def test_pacote_nao_debita_procedimento_diferente(self):
        """Session for a different procedure should NOT debit the package."""
        pacote = criar_pacote(procedimento=self.proc, sessoes=2)
        pc = criar_pacote_cliente(self.cli, pacote)

        outro_proc = criar_procedimento(nome='Outro Procedimento', profissional=self.prof)
        atd = criar_atendimento(self.cli, self.prof, outro_proc, status='CONFIRMADO')
        atd.status = 'REALIZADO'
        atd.save()

        self.assertEqual(SessaoPacote.objects.filter(pacote_cliente=pc).count(), 0)
        pc.refresh_from_db()
        self.assertEqual(pc.status, 'ATIVO')
