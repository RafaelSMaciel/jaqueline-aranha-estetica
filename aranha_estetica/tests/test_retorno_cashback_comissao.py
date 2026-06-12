"""Tests Sprint 1-2 + 3-4: F-RET, F-CSB, Comissao, Lista Espera."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from aranha_estetica.models import (
    Atendimento,
    Carteira,
    ListaEspera,
    MovimentoComissao,
    MovimentoCarteira,
    RegraComissao,
)
from aranha_estetica.services.comissao_service import ComissaoService
from aranha_estetica.services.fidelidade_service import FidelidadeService
from aranha_estetica.services.retorno_service import RetornoService

from .factories import (
    criar_atendimento, criar_cliente, criar_procedimento, criar_profissional,
)


# ─── F-RET ────────────────────────────────────────────────────────────
class RetornoServiceTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.proc.exige_retorno = True
        self.proc.retorno_minimo_dias = 15
        self.proc.retorno_maximo_dias = 21
        self.proc.duracao_retorno_minutos = 30
        self.proc.save()
        self.cliente = criar_cliente()

    def test_cria_retorno_quando_procedimento_requer(self):
        atend = criar_atendimento(self.cliente, self.prof, self.proc, status='AGENDADO')
        atend.marcar_realizado()
        atend.refresh_from_db()
        retorno = Atendimento.objects.filter(atendimento_origem=atend, eh_retorno=True).first()
        self.assertIsNotNone(retorno)
        self.assertEqual(retorno.valor_cobrado, Decimal('0'))
        self.assertEqual(retorno.status, Atendimento.STATUS_PENDENTE)

    def test_idempotente_nao_duplica_retorno(self):
        atend = criar_atendimento(self.cliente, self.prof, self.proc, status='AGENDADO')
        atend.marcar_realizado()
        # Tenta sugerir de novo direto
        RetornoService.sugerir_retorno(atend)
        count = Atendimento.objects.filter(atendimento_origem=atend, eh_retorno=True).count()
        self.assertEqual(count, 1)

    def test_nao_cria_se_procedimento_nao_requer(self):
        proc_simples = criar_procedimento(nome='Drenagem', profissional=self.prof)
        atend = criar_atendimento(self.cliente, self.prof, proc_simples, status='AGENDADO')
        atend.marcar_realizado()
        self.assertFalse(
            Atendimento.objects.filter(atendimento_origem=atend, eh_retorno=True).exists(),
        )

    def test_retorno_data_dentro_da_janela(self):
        atend = criar_atendimento(self.cliente, self.prof, self.proc, status='AGENDADO')
        atend.marcar_realizado()
        retorno = Atendimento.objects.get(atendimento_origem=atend, eh_retorno=True)
        delta = (retorno.data_hora_inicio - atend.data_hora_fim).days
        self.assertGreaterEqual(delta, 15)
        self.assertLessEqual(delta, 21)


# ─── F-CSB ────────────────────────────────────────────────────────────
class FidelidadeServiceTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof, preco=Decimal('500.00'))
        self.indicadora = criar_cliente(nome='Ana Indicadora')
        self.indicada = criar_cliente(
            nome='Bia Indicada', indicado_por=self.indicadora,
        )

    def _atend_realizado_pago(self, cliente, valor=Decimal('500.00')):
        atend = criar_atendimento(cliente, self.prof, self.proc, status='AGENDADO')
        atend.valor_cobrado = valor
        atend.save()
        atend.marcar_realizado()
        atend.refresh_from_db()
        return atend

    def test_credita_indicador_no_primeiro_pago(self):
        self._atend_realizado_pago(self.indicada)
        cred = Carteira.objects.get(cliente=self.indicadora)
        self.assertEqual(cred.saldo, Decimal('50.00'))
        mov = MovimentoCarteira.objects.get(carteira=cred, origem='CASHBACK_INDICACAO')
        self.assertEqual(mov.valor, Decimal('50.00'))

    def test_nao_credita_se_segundo_pago(self):
        self._atend_realizado_pago(self.indicada)
        # 2o atendimento pago da indicada — nao deve gerar novo cashback
        self._atend_realizado_pago(self.indicada)
        cred = Carteira.objects.get(cliente=self.indicadora)
        self.assertEqual(cred.saldo, Decimal('50.00'))  # ainda 1 credito so

    def test_nao_credita_se_indicada_sem_indicador(self):
        cliente_sem_indicador = criar_cliente(nome='Sozinha')
        self._atend_realizado_pago(cliente_sem_indicador)
        self.assertFalse(Carteira.objects.filter(cliente=self.indicadora).exists())

    def test_nao_credita_se_valor_zero(self):
        atend = criar_atendimento(self.indicada, self.prof, self.proc, status='AGENDADO')
        atend.valor_cobrado = Decimal('0.00')
        atend.save()
        atend.marcar_realizado()
        self.assertFalse(Carteira.objects.filter(cliente=self.indicadora).exists())

    def test_estorna_em_cancelamento(self):
        atend = self._atend_realizado_pago(self.indicada)
        # Forcar cancelamento (FSM nao permite REALIZADO->CANCELADO normal,
        # entao chamamos service direto p/ teste do estorno)
        FidelidadeService.estornar_cashback_de_atendimento(atend, motivo='teste')
        cred = Carteira.objects.get(cliente=self.indicadora)
        self.assertEqual(cred.saldo, Decimal('0.00'))
        estorno = MovimentoCarteira.objects.filter(
            carteira=cred, origem='CASHBACK_ESTORNO',
        ).first()
        self.assertIsNotNone(estorno)


# ─── Comissao ─────────────────────────────────────────────────────────
class ComissaoServiceTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof, preco=Decimal('1000.00'))
        self.cliente = criar_cliente()
        self.regra = RegraComissao.objects.create(
            profissional=self.prof, procedimento=self.proc,
            percentual=Decimal('30.00'), ativo=True,
        )

    def test_calcula_comissao_percentual(self):
        atend = criar_atendimento(self.cliente, self.prof, self.proc, status='AGENDADO')
        atend.valor_cobrado = Decimal('1000.00')
        atend.save()
        atend.marcar_realizado()
        atend.refresh_from_db()
        mov = MovimentoComissao.objects.filter(atendimento=atend).first()
        self.assertIsNotNone(mov)
        self.assertEqual(mov.valor, Decimal('300.00'))

    def test_resolver_regra_mais_especifica(self):
        # Regra geral menos especifica
        RegraComissao.objects.create(
            profissional=None, procedimento=None,
            percentual=Decimal('10.00'), ativo=True,
        )
        regra = ComissaoService.resolver_regra(self.prof.pk, self.proc.pk)
        self.assertEqual(regra.percentual, Decimal('30.00'))

    def test_idempotente_nao_duplica(self):
        atend = criar_atendimento(self.cliente, self.prof, self.proc, status='AGENDADO')
        atend.valor_cobrado = Decimal('1000.00')
        atend.save()
        atend.marcar_realizado()
        # Tenta calcular de novo
        ComissaoService.calcular_comissao(atend)
        count = MovimentoComissao.objects.filter(atendimento=atend).count()
        self.assertEqual(count, 1)


# ─── Lista de Espera ──────────────────────────────────────────────────
class ListaEsperaHandlerTests(TestCase):
    def test_notifica_compativeis_em_cancelamento(self):
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        cliente_agendado = criar_cliente(nome='Agendado')
        cliente_espera = criar_cliente(
            nome='Espera', telefone='17999991111',
            consent_whatsapp_confirmacao=True,
        )

        atend = criar_atendimento(
            cliente_agendado, prof, proc, status='AGENDADO',
            data_hora=timezone.now() + timedelta(days=2),
        )

        ListaEspera.objects.create(
            cliente=cliente_espera,
            procedimento=proc,
            data_desejada=atend.data_hora_inicio.date(),
            notificado=False,
        )

        atend.cancelar(motivo='teste')

        espera = ListaEspera.objects.get(cliente=cliente_espera)
        self.assertTrue(espera.notificado)
        self.assertIsNotNone(espera.token_reserva)
        self.assertIsNotNone(espera.expira_em)
