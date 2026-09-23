"""Testes de CompraPacote.verificar_finalizacao e debito via signal."""
from datetime import timedelta

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


class PacoteFinalizacaoTests(TestCase):
    def setUp(self):
        self.cliente = criar_cliente()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.pacote = criar_pacote(procedimento=self.proc, sessoes=3)
        self.pc = criar_compra_pacote(self.cliente, self.pacote)

    def test_verificar_finalizacao_mantem_ativo_com_sessoes_faltando(self):
        self.pc.verificar_finalizacao()
        self.pc.refresh_from_db()
        self.assertEqual(self.pc.status, 'ATIVO')

    def test_realizado_debita_sessao_do_pacote(self):
        atd = criar_atendimento(self.cliente, self.prof, self.proc)
        atd.status = 'REALIZADO'
        atd.save()

        self.assertTrue(ConsumoSessao.objects.filter(atendimento=atd).exists())
        self.pc.refresh_from_db()
        self.assertEqual(self.pc.status, 'ATIVO')  # Ainda 1 de 3

    def test_ultima_sessao_finaliza_pacote(self):
        for _ in range(3):
            atd = criar_atendimento(self.cliente, self.prof, self.proc)
            atd.status = 'REALIZADO'
            atd.save()

        self.pc.refresh_from_db()
        self.assertEqual(self.pc.status, 'FINALIZADO')
        self.assertEqual(
            ConsumoSessao.objects.filter(compra_pacote=self.pc).count(),
            3
        )

    def test_verificar_finalizacao_com_todas_sessoes_via_direto(self):
        # Cria sessoes diretamente (bypass signal). Horarios distintos: no
        # Postgres o EXCLUDE excl_atendimento_sobreposicao barra 3 no mesmo slot.
        base = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        for i in range(3):
            atd = criar_atendimento(
                self.cliente, self.prof, self.proc, data_hora=base + timedelta(hours=i),
            )
            ConsumoSessao.objects.create(compra_pacote=self.pc, atendimento=atd)

        self.pc.verificar_finalizacao()
        self.pc.refresh_from_db()
        self.assertEqual(self.pc.status, 'FINALIZADO')
