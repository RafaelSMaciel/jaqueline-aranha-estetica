"""Testes do job_expirar_pacotes (Celery task)."""
from datetime import datetime, time, timedelta

from django.test import TestCase
from django.utils import timezone

from aranha_estetica.models import CompraPacote, ConsumoSessao
from aranha_estetica.tasks import job_expirar_pacotes

from .factories import (
    criar_atendimento,
    criar_cliente,
    criar_pacote,
    criar_compra_pacote,
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
        pc = criar_compra_pacote(self.cliente, self.pacote)
        ontem = timezone.localdate() - timedelta(days=1)
        CompraPacote.objects.filter(pk=pc.pk).update(data_expiracao=ontem)

        job_expirar_pacotes()

        pc.refresh_from_db()
        self.assertEqual(pc.status, 'EXPIRADO')

    def test_nao_expira_pacote_com_data_futura(self):
        pc = criar_compra_pacote(self.cliente, self.pacote)
        amanha = timezone.localdate() + timedelta(days=1)
        CompraPacote.objects.filter(pk=pc.pk).update(data_expiracao=amanha)

        job_expirar_pacotes()

        pc.refresh_from_db()
        self.assertEqual(pc.status, 'ATIVO')

    def test_nao_expira_pacote_com_data_hoje(self):
        pc = criar_compra_pacote(self.cliente, self.pacote)
        hoje = timezone.localdate()
        CompraPacote.objects.filter(pk=pc.pk).update(data_expiracao=hoje)

        job_expirar_pacotes()

        pc.refresh_from_db()
        self.assertEqual(pc.status, 'ATIVO')  # expira somente quando data < hoje

    def test_nao_altera_pacote_ja_finalizado(self):
        pc = criar_compra_pacote(self.cliente, self.pacote, status='FINALIZADO')
        ontem = timezone.localdate() - timedelta(days=1)
        CompraPacote.objects.filter(pk=pc.pk).update(data_expiracao=ontem)

        job_expirar_pacotes()

        pc.refresh_from_db()
        self.assertEqual(pc.status, 'FINALIZADO')

    def test_nao_altera_pacote_ja_cancelado(self):
        pc = criar_compra_pacote(self.cliente, self.pacote, status='CANCELADO')
        ontem = timezone.localdate() - timedelta(days=1)
        CompraPacote.objects.filter(pk=pc.pk).update(data_expiracao=ontem)

        job_expirar_pacotes()

        pc.refresh_from_db()
        self.assertEqual(pc.status, 'CANCELADO')

    def test_expira_multiplos_pacotes_ativos(self):
        pc1 = criar_compra_pacote(
            criar_cliente(telefone='17900000001'), self.pacote,
        )
        pc2 = criar_compra_pacote(
            criar_cliente(telefone='17900000002'), self.pacote,
        )
        ontem = timezone.localdate() - timedelta(days=1)
        CompraPacote.objects.filter(pk__in=[pc1.pk, pc2.pk]).update(data_expiracao=ontem)

        job_expirar_pacotes()

        pc1.refresh_from_db()
        pc2.refresh_from_db()
        self.assertEqual(pc1.status, 'EXPIRADO')
        self.assertEqual(pc2.status, 'EXPIRADO')


class ValidadePelaDataDaSessaoTests(TestCase):
    """Regressao gap3-07: a validade vale para a DATA DA SESSAO, nao para o
    dia em que a equipe marca REALIZADO (nem para quando o job rodou)."""

    def setUp(self):
        self.cliente = criar_cliente()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.pacote = criar_pacote(procedimento=self.proc, sessoes=4)
        self.pc = criar_compra_pacote(self.cliente, self.pacote)
        self.ontem = timezone.localdate() - timedelta(days=1)

    def _sessao_em(self, dia):
        inicio = timezone.make_aware(datetime.combine(dia, time(15, 0)))
        return criar_atendimento(self.cliente, self.prof, self.proc, data_hora=inicio)

    def _realizar(self, atd):
        atd.status = 'REALIZADO'
        atd.save()

    def test_sessao_no_ultimo_dia_marcada_no_dia_seguinte_debita(self):
        CompraPacote.objects.filter(pk=self.pc.pk).update(data_expiracao=self.ontem)
        atd = self._sessao_em(self.ontem)
        self._realizar(atd)
        self.assertTrue(ConsumoSessao.objects.filter(compra_pacote=self.pc, atendimento=atd).exists())

    def test_job_antes_da_marcacao_nao_impede_o_debito(self):
        CompraPacote.objects.filter(pk=self.pc.pk).update(data_expiracao=self.ontem)
        atd = self._sessao_em(self.ontem)
        job_expirar_pacotes()
        self.pc.refresh_from_db()
        self.assertEqual(self.pc.status, 'EXPIRADO')
        self._realizar(atd)
        self.assertEqual(ConsumoSessao.objects.filter(compra_pacote=self.pc).count(), 1)

    def test_sessao_depois_da_validade_nao_debita(self):
        anteontem = self.ontem - timedelta(days=1)
        CompraPacote.objects.filter(pk=self.pc.pk).update(data_expiracao=anteontem)
        atd = self._sessao_em(self.ontem)
        self._realizar(atd)
        self.assertFalse(ConsumoSessao.objects.filter(atendimento=atd).exists())
        self.pc.refresh_from_db()
        # o signal nao grava mais EXPIRADO (isso e do job)
        self.assertEqual(self.pc.status, 'ATIVO')
