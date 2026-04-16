"""Testes de PacoteCliente.verificar_finalizacao e debito via signal."""
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


@patch('app_shivazen.signals.job_notificar_fila_espera.delay')
class PacoteFinalizacaoTests(TestCase):
    def setUp(self):
        self.cliente = criar_cliente()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.pacote = criar_pacote(procedimento=self.proc, sessoes=3)
        self.pc = criar_pacote_cliente(self.cliente, self.pacote)

    def test_verificar_finalizacao_mantem_ativo_com_sessoes_faltando(self, _mock):
        self.pc.verificar_finalizacao()
        self.pc.refresh_from_db()
        self.assertEqual(self.pc.status, 'ATIVO')

    def test_realizado_debita_sessao_do_pacote(self, _mock):
        atd = criar_atendimento(self.cliente, self.prof, self.proc)
        atd.status = 'REALIZADO'
        atd.save()

        self.assertTrue(SessaoPacote.objects.filter(atendimento=atd).exists())
        self.pc.refresh_from_db()
        self.assertEqual(self.pc.status, 'ATIVO')  # Ainda 1 de 3

    def test_ultima_sessao_finaliza_pacote(self, _mock):
        for _ in range(3):
            atd = criar_atendimento(self.cliente, self.prof, self.proc)
            atd.status = 'REALIZADO'
            atd.save()

        self.pc.refresh_from_db()
        self.assertEqual(self.pc.status, 'FINALIZADO')
        self.assertEqual(
            SessaoPacote.objects.filter(pacote_cliente=self.pc).count(),
            3
        )

    def test_verificar_finalizacao_com_todas_sessoes_via_direto(self, _mock):
        # Cria sessoes diretamente (bypass signal)
        for _ in range(3):
            atd = criar_atendimento(self.cliente, self.prof, self.proc)
            SessaoPacote.objects.create(pacote_cliente=self.pc, atendimento=atd)

        self.pc.verificar_finalizacao()
        self.pc.refresh_from_db()
        self.assertEqual(self.pc.status, 'FINALIZADO')
