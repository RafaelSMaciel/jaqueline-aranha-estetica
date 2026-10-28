"""services.atendimento: efeitos da criacao e de cada transicao, chamados pela FSM e por
services.agenda (sem post_save nem EventBus)."""
from datetime import timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from aranha_estetica.models import Atendimento, ConsumoSessao, LogAuditoria
from aranha_estetica.services import agenda

from .factories import (
    ja_comecou,
    criar_atendimento,
    criar_cliente,
    criar_compra_pacote,
    criar_pacote,
    criar_procedimento,
    criar_profissional,
    limpar_feriados,
)

NOTIFICAR = 'aranha_estetica.services.lista_espera.notificar_compativeis'


class _Base(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()

    def _at(self, status='CONFIRMADO', horas=0):
        base = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        return criar_atendimento(self.cli, self.prof, self.proc, status=status,
                                 data_hora=base + timedelta(hours=horas))


class FaltasTests(_Base):
    def test_falta_incrementa_e_tres_bloqueiam_online(self):
        for i in range(3):
            ja_comecou(self._at(horas=i)).marcar_falta()
        self.cli.refresh_from_db()
        self.assertEqual(self.cli.faltas_consecutivas, 3)
        self.assertTrue(self.cli.bloqueado_online)

    def test_realizado_zera_as_faltas(self):
        self.cli.faltas_consecutivas = 2
        self.cli.save()
        ja_comecou(self._at()).marcar_realizado()
        self.cli.refresh_from_db()
        self.assertEqual(self.cli.faltas_consecutivas, 0)
        self.assertFalse(self.cli.bloqueado_online)

    def test_save_direto_nao_tem_efeito(self):
        """Status muda so pela FSM: `status = X; save()` nao aplica regra nenhuma."""
        at = self._at()
        at.status = 'FALTOU'
        at.save()
        self.cli.refresh_from_db()
        self.assertEqual(self.cli.faltas_consecutivas, 0)


class PacoteTests(_Base):
    def setUp(self):
        super().setUp()
        self.pc = criar_compra_pacote(self.cli, criar_pacote(procedimento=self.proc, sessoes=3))

    def test_realizado_debita_e_finaliza_o_pacote(self):
        for i in range(3):
            ja_comecou(self._at(horas=i)).marcar_realizado()
        self.assertEqual(ConsumoSessao.objects.filter(compra_pacote=self.pc).count(), 3)
        self.pc.refresh_from_db()
        self.assertEqual(self.pc.status, 'FINALIZADO')

    def test_retorno_nao_consome_sessao(self):
        origem = self._at()
        ja_comecou(origem).marcar_realizado()
        retorno = criar_atendimento(self.cli, self.prof, self.proc, status='CONFIRMADO',
                                    data_hora=origem.data_hora_inicio + timedelta(days=10))
        Atendimento.objects.filter(pk=retorno.pk).update(eh_retorno=True, atendimento_origem=origem,
                                                        valor_cobrado=0)
        retorno.refresh_from_db()
        ja_comecou(retorno).marcar_realizado()
        self.assertEqual(ConsumoSessao.objects.filter(compra_pacote=self.pc).count(), 1)


class ListaDeEsperaTests(_Base):
    @patch(NOTIFICAR)
    def test_cancelar_e_reagendar_liberam_a_vaga_uma_vez(self, notificar):
        self._at().cancelar(motivo='teste')
        self._at(horas=2).marcar_reagendado()
        self.assertEqual(notificar.call_count, 2)

    @patch(NOTIFICAR)
    def test_realizado_e_falta_nao_liberam(self, notificar):
        ja_comecou(self._at()).marcar_realizado()
        ja_comecou(self._at(horas=2)).marcar_falta()
        notificar.assert_not_called()

    @patch(NOTIFICAR, side_effect=RuntimeError('smtp'))
    def test_falha_no_aviso_nao_desfaz_o_cancelamento(self, _notificar):
        at = self._at()
        at.cancelar(motivo='teste')
        self.assertEqual(Atendimento.objects.get(pk=at.pk).status, 'CANCELADO')


class TrilhaTests(_Base):
    def test_cancelamento_pela_cliente_fica_na_trilha(self):
        at = self._at()
        at.cancelar(motivo='portal', pela_cliente=True)
        acao = LogAuditoria.objects.filter(tabela='atendimento', registro_id=at.pk).latest('pk').acao
        self.assertIn('-> CANCELADO (portal) [pela cliente]', acao)

    def test_cancelamento_pela_equipe_nao_e_da_cliente(self):
        at = self._at()
        at.cancelar(motivo='painel')
        acao = LogAuditoria.objects.filter(tabela='atendimento', registro_id=at.pk).latest('pk').acao
        self.assertNotIn('pela cliente', acao)


class NovoAtendimentoTests(TestCase):
    """Push de novo agendamento (services.agenda.reservar): hora LOCAL e link do portal."""

    def test_push_usa_hora_local_e_portal(self):
        from aranha_estetica.models import Usuario

        limpar_feriados()
        prof = criar_profissional()
        Usuario.objects.create_user(
            email='prof-push@test.com', password='x-senha-123', nome='Prof',
            papel=Usuario.PAPEL_PROFISSIONAL, profissional=prof,
        )
        prof.refresh_from_db()
        proc = criar_procedimento(profissional=prof)
        inicio_local = timezone.localtime(timezone.now() + timedelta(days=2)).replace(
            hour=14, minute=0, second=0, microsecond=0,
        )
        # Datetime em UTC (como volta do banco): o texto deve sair em hora local.
        inicio_utc = inicio_local.astimezone(dt_timezone.utc)
        with patch('aranha_estetica.services.push.send_push_to_user') as mock_push, \
                self.captureOnCommitCallbacks(execute=True):
            agenda.reservar(cliente=criar_cliente(nome='Bia'), profissional=prof, procedimento=proc,
                            inicio=inicio_utc, status=Atendimento.STATUS_PENDENTE)
        payload = mock_push.call_args[0][1]
        self.assertIn('Bia', payload['body'])
        self.assertIn('14:00', payload['body'])
        self.assertEqual(payload['url'], '/profissional/')

    def test_atendimento_criado_fora_do_servico_nao_avisa(self):
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        with patch('aranha_estetica.services.push.send_push_to_user') as mock_push, \
                self.captureOnCommitCallbacks(execute=True):
            criar_atendimento(criar_cliente(), prof, proc)
        mock_push.assert_not_called()


class DesfazerRealizacaoTests(_Base):
    """Realizado por engano volta (com motivo) e os efeitos do Realizado sao desfeitos juntos."""

    def setUp(self):
        super().setUp()
        from decimal import Decimal

        from aranha_estetica.models import RegraComissao
        RegraComissao.objects.create(percentual=Decimal('10'), ativo=True)
        self.compra = criar_compra_pacote(self.cli, criar_pacote(procedimento=self.proc, sessoes=1))

    def _realizar(self):
        at = self._at(horas=-2)
        Atendimento.objects.filter(pk=at.pk).update(valor_cobrado=100)
        at.refresh_from_db()
        ja_comecou(at).marcar_realizado()
        return at

    def test_volta_para_agendado_estorna_e_devolve_sessao(self):
        from aranha_estetica.models import MovimentoComissao
        at = self._realizar()
        self.compra.refresh_from_db()
        self.assertEqual(self.compra.status, 'FINALIZADO')
        at.desfazer_realizacao(Atendimento.STATUS_AGENDADO, 'marcado na cliente errada')
        at.refresh_from_db()
        self.compra.refresh_from_db()
        self.assertEqual(at.status, 'AGENDADO')
        self.assertFalse(ConsumoSessao.objects.filter(atendimento=at).exists())
        self.assertEqual(self.compra.status, 'ATIVO')
        self.assertFalse(MovimentoComissao.objects.creditos_ativos().filter(atendimento=at).exists())
        self.assertTrue(LogAuditoria.objects.filter(acao__contains='realização desfeita: marcado na cliente errada').exists())
        ja_comecou(at).marcar_realizado()   # refeito: novo debito e novo credito
        self.assertTrue(ConsumoSessao.objects.filter(atendimento=at).exists())
        self.assertTrue(MovimentoComissao.objects.creditos_ativos().filter(atendimento=at).exists())

    def test_comissao_paga_ou_sem_motivo_recusa(self):
        from aranha_estetica.excecoes import RegraDeNegocio
        from aranha_estetica.models import MovimentoComissao
        from aranha_estetica.services.comissao import ComissaoService
        at = self._realizar()
        with self.assertRaises(RegraDeNegocio):
            at.desfazer_realizacao(Atendimento.STATUS_CANCELADO, '  ')
        credito = MovimentoComissao.objects.creditos_ativos().get(atendimento=at)
        ComissaoService.marcar_paga(credito.pk)
        with self.assertRaises(RegraDeNegocio):
            at.desfazer_realizacao(Atendimento.STATUS_FALTOU, 'cliente nao veio')
        at.refresh_from_db()
        self.assertEqual(at.status, 'REALIZADO')
        self.assertTrue(ConsumoSessao.objects.filter(atendimento=at).exists())   # nada desfeito

    def test_tela_do_painel(self):
        from django.urls import reverse

        from aranha_estetica.models import Usuario
        at = self._realizar()
        admin = Usuario.objects.create_superuser(email='adm@desfazer.test', password='x')
        self.client.force_login(admin)
        self.client.post(reverse('aranha:admin_desfazer_realizacao', args=[at.pk]),
                         {'destino': 'FALTOU', 'motivo': 'a cliente nao veio'})
        at.refresh_from_db()
        self.assertEqual(at.status, 'FALTOU')
        self.cli.refresh_from_db()
        self.assertEqual(self.cli.faltas_consecutivas, 1)
