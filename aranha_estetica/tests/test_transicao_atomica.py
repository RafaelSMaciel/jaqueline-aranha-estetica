"""Transicao de status e seus efeitos numa transacao so; autor do cancelamento; aviso a cliente.

- Comissao, cashback e retorno (services.atendimento, chamados pela FSM) gravam junto com o status
  ou nada grava: antes a falha virava so um log com o atendimento ja REALIZADO e sem comissao.
- "pela cliente" so nos fluxos da propria cliente (o job marcava como dela).
- Cancelamento pela clinica avisa a cliente por e-mail em todo caminho (painel, Django admin,
  pedido PENDENTE vencido), nao so no painel.
"""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.admin.sites import site
from django.core import mail
from django.db import transaction
from django.test import RequestFactory, TestCase
from django.utils import timezone

from aranha_estetica.models import Atendimento, LogAuditoria, Usuario
from aranha_estetica.services.comissao import ComissaoService
from aranha_estetica.services.fidelidade import FidelidadeService

from .factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional


class _Base(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente(email='cliente@exemplo.com')

    def _at(self, status='AGENDADO', dias=1):
        return criar_atendimento(self.cli, self.prof, self.proc,
                                 data_hora=timezone.now() + timedelta(days=dias), status=status)


class TransicaoAtomicaTests(_Base):
    def test_falha_no_efeito_desfaz_a_transicao(self):
        at = self._at()
        with mock.patch.object(ComissaoService, 'calcular_comissao', side_effect=RuntimeError('ledger')), \
                self.assertRaises(RuntimeError):
            at.marcar_realizado()
        self.assertEqual(Atendimento.objects.get(pk=at.pk).status, 'AGENDADO')

    def test_credito_duplicado_nao_quebra_a_transacao_de_quem_chama(self):
        indicadora = criar_cliente('Indicadora', email='ind@exemplo.com')
        self.cli.indicado_por = indicadora
        self.cli.save(update_fields=['indicado_por'])
        at = self._at(status='REALIZADO', dias=-1)
        Atendimento.objects.filter(pk=at.pk).update(valor_cobrado=Decimal('150.00'))   # 1o pago
        at.refresh_from_db()
        with transaction.atomic():
            self.assertIsNotNone(FidelidadeService.liberar_cashback_indicacao(at))
            # 2a chamada: o pre-check deixa passar e o UNIQUE do credito recusa (como numa corrida)
            self.assertIsNone(FidelidadeService.liberar_cashback_indicacao(at))
            # a transacao segue utilizavel (no Postgres, sem savepoint ela ficava abortada)
            self.assertTrue(Atendimento.objects.filter(pk=at.pk).exists())


class AutorDoCancelamentoTests(_Base):
    def _trilha_de_cancelamento(self, at):
        return list(LogAuditoria.objects.filter(
            tabela='atendimento', registro_id=at.pk, acao__contains='-> CANCELADO',
        ).values_list('acao', flat=True))

    def test_job_de_pendente_vencido_nao_e_cancelamento_da_cliente(self):
        from aranha_estetica.jobs import job_limpeza_status_atendimentos

        at = self._at(status='PENDENTE', dias=-3)
        job_limpeza_status_atendimentos()
        trilha = self._trilha_de_cancelamento(at)
        self.assertEqual(len(trilha), 1)
        self.assertNotIn('pela cliente', trilha[0])

    def test_cancelamento_pela_cliente_e_marcado(self):
        at = self._at()
        at.cancelar(motivo='portal', pela_cliente=True)
        self.assertIn('[pela cliente]', self._trilha_de_cancelamento(at)[0])


class AvisoDeCancelamentoTests(_Base):
    def _admin(self):
        return Usuario.objects.create_user(email='adm-aviso@test.com', password='Senha-Forte-2026!',
                                           nome='Admin', papel=Usuario.PAPEL_ADMIN)

    def _cancelamentos_enviados(self):
        return [m for m in mail.outbox if m.to == ['cliente@exemplo.com']]

    def test_django_admin_avisa_a_cliente(self):
        at = self._at()
        model_admin = site._registry[Atendimento]
        request = RequestFactory().post('/django-admin-sv/')
        request.user = self._admin()
        with mock.patch.object(model_admin, 'message_user'), self.captureOnCommitCallbacks(execute=True):
            model_admin.acao_marcar_cancelado(request, Atendimento.objects.filter(pk=at.pk))
        self.assertEqual(Atendimento.objects.get(pk=at.pk).status, 'CANCELADO')
        self.assertEqual(len(self._cancelamentos_enviados()), 1)

    def test_pendente_vencido_avisa_a_cliente(self):
        from aranha_estetica.jobs import job_limpeza_status_atendimentos

        self._at(status='PENDENTE', dias=-3)
        with self.captureOnCommitCallbacks(execute=True):
            job_limpeza_status_atendimentos()
        self.assertEqual(len(self._cancelamentos_enviados()), 1)

    def test_cancelamento_pela_cliente_nao_avisa_ela_mesma(self):
        at = self._at()
        with self.captureOnCommitCallbacks(execute=True):
            at.cancelar(motivo='portal', pela_cliente=True)
        self.assertEqual(self._cancelamentos_enviados(), [])

