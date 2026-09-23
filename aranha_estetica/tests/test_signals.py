"""Tests for Atendimento status change signals."""
from datetime import timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from aranha_estetica.models import ConsumoSessao

from .factories import (
    criar_atendimento,
    criar_cliente,
    criar_pacote,
    criar_compra_pacote,
    criar_procedimento,
    criar_profissional,
)

NOTIFICAR = 'aranha_estetica.services.lista_espera_service.ListaEsperaService.notificar_compativeis'


class FaltaSignalTests(TestCase):
    """3-strike system via post_save signal."""

    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()

    def test_falta_incrementa_contador(self):
        atd = criar_atendimento(self.cli, self.prof, self.proc, status='CONFIRMADO')
        atd.status = 'FALTOU'
        atd.save()
        self.cli.refresh_from_db()
        self.assertEqual(self.cli.faltas_consecutivas, 1)

    def test_tres_faltas_bloqueiam_online(self):
        base = timezone.now() + timedelta(days=1)
        for i in range(3):
            atd = criar_atendimento(
                self.cli, self.prof, self.proc, status='CONFIRMADO',
                data_hora=base.replace(hour=10 + i, minute=0, second=0, microsecond=0),
            )
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
        self.pc = criar_compra_pacote(self.cli, self.pacote)

    def test_realizado_debita_sessao_pacote(self):
        atd = criar_atendimento(self.cli, self.prof, self.proc, status='CONFIRMADO')
        atd.status = 'REALIZADO'
        atd.save()
        self.assertEqual(ConsumoSessao.objects.filter(compra_pacote=self.pc).count(), 1)

    def test_pacote_finaliza_quando_todas_sessoes_usadas(self):
        for _i in range(3):
            atd = criar_atendimento(self.cli, self.prof, self.proc, status='CONFIRMADO')
            atd.status = 'REALIZADO'
            atd.save()
        self.pc.refresh_from_db()
        self.assertEqual(self.pc.status, 'FINALIZADO')


class FilaEsperaSignalTests(TestCase):
    """Lista de espera: um unico gatilho (signal) p/ qualquer caminho de gravacao."""

    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()

    @patch(NOTIFICAR)
    def test_cancelamento_dispara_lista_espera(self, mock_notificar):
        atd = criar_atendimento(self.cli, self.prof, self.proc, status='CONFIRMADO')
        atd.status = 'CANCELADO'
        atd.save()
        mock_notificar.assert_called_once()

    @patch(NOTIFICAR)
    def test_cancelamento_pela_fsm_dispara_uma_vez(self, mock_notificar):
        """FSM (evento) + signal nao podem avisar a lista duas vezes."""
        atd = criar_atendimento(self.cli, self.prof, self.proc, status='AGENDADO')
        atd.cancelar(motivo='teste')
        mock_notificar.assert_called_once()

    @patch(NOTIFICAR)
    def test_reagendamento_libera_vaga(self, mock_notificar):
        atd = criar_atendimento(self.cli, self.prof, self.proc, status='AGENDADO')
        atd.status = 'REAGENDADO'
        atd.save()
        mock_notificar.assert_called_once()

    @patch(NOTIFICAR)
    def test_realizado_e_falta_nao_disparam(self, mock_notificar):
        atd = criar_atendimento(self.cli, self.prof, self.proc, status='CONFIRMADO')
        atd.status = 'REALIZADO'
        atd.save()
        outro = criar_atendimento(
            self.cli, self.prof, self.proc, status='CONFIRMADO',
            data_hora=atd.data_hora_inicio + timedelta(hours=2),
        )
        outro.status = 'FALTOU'
        outro.save()
        mock_notificar.assert_not_called()


class PushProfissionalSignalTests(TestCase):
    """Push de novo agendamento: hora LOCAL e link do portal do profissional."""

    def test_push_usa_hora_local_e_portal(self):
        from aranha_estetica.models import Usuario

        prof = criar_profissional()
        Usuario.objects.create_user(
            email='prof-push@test.com', password='x-senha-123', nome='Prof',
            papel=Usuario.PAPEL_PROFISSIONAL, profissional=prof,
        )
        prof.refresh_from_db()
        proc = criar_procedimento(profissional=prof)
        cli = criar_cliente(nome='Bia')
        inicio_local = timezone.localtime(timezone.now() + timedelta(days=2)).replace(
            hour=14, minute=0, second=0, microsecond=0,
        )
        # Datetime em UTC (como volta do banco): o texto deve sair em hora local.
        inicio_utc = inicio_local.astimezone(dt_timezone.utc)
        with patch('aranha_estetica.services.push.send_push_to_user') as mock_push, \
                self.captureOnCommitCallbacks(execute=True):
            criar_atendimento(cli, prof, proc, data_hora=inicio_utc)
        payload = mock_push.call_args[0][1]
        self.assertIn('14:00', payload['body'])
        self.assertEqual(payload['url'], '/profissional/')
