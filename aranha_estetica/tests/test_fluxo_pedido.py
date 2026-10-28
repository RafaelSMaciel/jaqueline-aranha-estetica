"""Pedido de agendamento: quem nasce aprovado (D8), quem e avisado de cada decisao e quando vence.

Matriz de comunicacao:
- pedido pelo site: cliente nova -> PENDENTE + e-mail de aprovacao a profissional; cliente de
  confianca -> AGENDADO + confirmacao a cliente;
- a clinica aprova/recusa (botoes OU seletor de status) -> e-mail a cliente;
- a clinica agenda pelo painel -> confirmacao a cliente;
- a cliente cancela/remarca -> push a profissional;
- pedido nao aprovado ate o horario -> CANCELADO + e-mail a cliente.
"""
import json
from datetime import datetime, timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.constants import LIMITE_AUTOAPROVACAO
from aranha_estetica.models import Atendimento, Notificacao, Usuario
from aranha_estetica.services import agenda

from .factories import (
    criar_atendimento, criar_cliente, criar_procedimento, criar_profissional, limpar_feriados,
)
from .test_confirmar_agendamento import slot_local, verificar_sessao


def _emails(at, tipo):
    return Notificacao.objects.filter(atendimento=at, tipo=tipo, canal=Notificacao.Canal.EMAIL).count()


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        limpar_feriados()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente(email='cliente@exemplo.test')


class StatusInicialPublicoTests(_Base):
    def _realizado(self):
        criar_atendimento(self.cli, self.prof, self.proc, status='REALIZADO',
                          data_hora=timezone.now() - timedelta(days=10))

    def test_primeira_vez_fica_pendente(self):
        self.assertEqual(agenda.status_inicial_publico(self.cli), 'PENDENTE')

    def test_cliente_ja_atendida_nasce_agendada(self):
        self._realizado()
        self.assertEqual(agenda.status_inicial_publico(self.cli), 'AGENDADO')

    def test_falta_em_aberto_ou_bloqueio_volta_para_a_fila(self):
        self._realizado()
        self.cli.faltas_consecutivas = 1
        self.assertEqual(agenda.status_inicial_publico(self.cli), 'PENDENTE')
        self.cli.faltas_consecutivas = 0
        self.cli.bloqueado_online = True
        self.assertEqual(agenda.status_inicial_publico(self.cli), 'PENDENTE')

    def test_muitos_horarios_em_aberto_volta_para_a_fila(self):
        self._realizado()
        for _ in range(LIMITE_AUTOAPROVACAO):
            criar_atendimento(self.cli, self.prof, self.proc)
        self.assertEqual(agenda.status_inicial_publico(self.cli), 'PENDENTE')


@override_settings(RATELIMIT_ENABLE=False)
@patch('aranha_estetica.utils.email.enviar_confirmacao_agendamento_email', return_value=True)
class PedidoPeloSiteTests(_Base):
    def _agendar(self):
        dados = {
            'nome': self.cli.nome, 'telefone': self.cli.telefone, 'data_nascimento': '1990-03-20',
            'procedimento': self.proc.pk, 'profissional': self.prof.pk,
            'datetime': slot_local(dias=3, hora=11), 'aceite_politica': 'on',
        }
        client = Client()
        verificar_sessao(client, dados['telefone'])
        resp = client.post(reverse('aranha:confirmar_agendamento'), dados)
        self.assertEqual(resp.status_code, 302)
        return client, Atendimento.objects.filter(cliente=self.cli).latest('pk')

    def test_cliente_de_confianca_recebe_confirmacao_na_hora(self, _mock):
        criar_atendimento(self.cli, self.prof, self.proc, status='REALIZADO',
                          data_hora=timezone.now() - timedelta(days=10))
        client, at = self._agendar()
        self.assertEqual(at.status, 'AGENDADO')
        self.assertEqual(_emails(at, 'CONFIRMACAO'), 1)
        self.assertEqual(_emails(at, 'APROVACAO'), 0)
        sucesso = client.get(reverse('aranha:agendamento_sucesso'))
        self.assertContains(sucesso, 'Agendamento confirmado!')
        self.assertNotContains(sucesso, 'Aguardando confirmação')

    def test_cliente_nova_aguarda_aprovacao(self, _mock):
        client, at = self._agendar()
        self.assertEqual(at.status, 'PENDENTE')
        self.assertEqual(_emails(at, 'CONFIRMACAO'), 0)
        self.assertContains(client.get(reverse('aranha:agendamento_sucesso')), 'Aguardando confirmação')


class SeletorDeStatusTests(_Base):
    """O seletor do painel decide o pedido pelo mesmo caso de uso dos botoes Aprovar/Recusar."""

    def setUp(self):
        super().setUp()
        self.admin = Usuario.objects.create_user(
            email='admin-pedido@test.com', password='senha-forte-123', papel=Usuario.PAPEL_ADMIN,
        )
        self.client.force_login(self.admin)

    def _mudar(self, at, status):
        return self.client.post(
            reverse('aranha:admin_atualizar_status'),
            data=json.dumps({'atendimento_id': at.pk, 'status': status}), content_type='application/json',
        )

    def test_confirmar_pedido_aprova_e_avisa_a_cliente(self):
        at = criar_atendimento(self.cli, self.prof, self.proc, status='PENDENTE')
        self.assertEqual(self._mudar(at, 'CONFIRMADO').status_code, 200)
        at.refresh_from_db()
        self.assertEqual(at.status, 'CONFIRMADO')
        self.assertEqual(_emails(at, 'CONFIRMACAO'), 1)

    def test_agendado_pelo_seletor_tambem_avisa(self):
        at = criar_atendimento(self.cli, self.prof, self.proc, status='PENDENTE')
        self.assertEqual(self._mudar(at, 'AGENDADO').status_code, 200)
        self.assertEqual(_emails(at, 'CONFIRMACAO'), 1)

    def test_recusar_pedido_avisa_uma_vez(self):
        at = criar_atendimento(self.cli, self.prof, self.proc, status='PENDENTE')
        self.assertEqual(self._mudar(at, 'CANCELADO').status_code, 200)
        self.assertEqual(_emails(at, 'CANCELAMENTO'), 1)

    def test_cancelar_agendado_continua_avisando(self):
        at = criar_atendimento(self.cli, self.prof, self.proc)
        self.assertEqual(self._mudar(at, 'CANCELADO').status_code, 200)
        self.assertEqual(_emails(at, 'CANCELAMENTO'), 1)

    def test_agendado_nao_volta_para_agendado(self):
        at = criar_atendimento(self.cli, self.prof, self.proc, status='CONFIRMADO')
        self.assertEqual(self._mudar(at, 'AGENDADO').status_code, 400)

    def test_fsm_nao_confirma_pedido_sem_aprovacao(self):
        at = criar_atendimento(self.cli, self.prof, self.proc, status='PENDENTE')
        with self.assertRaises(Atendimento.TransicaoInvalida):
            at.confirmar()


class AvisoAProfissionalTests(_Base):
    def setUp(self):
        super().setUp()
        Usuario.objects.create_user(
            email='prof-aviso@test.com', password='x-senha-123', nome='Prof',
            papel=Usuario.PAPEL_PROFISSIONAL, profissional=self.prof,
        )
        self.prof.refresh_from_db()

    def test_cliente_cancelou_avisa_a_profissional(self):
        at = criar_atendimento(self.cli, self.prof, self.proc)
        with patch('aranha_estetica.services.push.send_push_to_user') as push, \
                self.captureOnCommitCallbacks(execute=True):
            at.cancelar(motivo='imprevisto', pela_cliente=True)
        self.assertEqual(push.call_args[0][1]['head'], 'Cliente cancelou')

    def test_cliente_remarcou_avisa_a_profissional(self):
        at = criar_atendimento(self.cli, self.prof, self.proc)
        novo_inicio = datetime.fromisoformat(slot_local(dias=4, hora=15))
        with patch('aranha_estetica.services.push.send_push_to_user') as push, \
                self.captureOnCommitCallbacks(execute=True):
            agenda.reagendar(at, novo_inicio, self.prof)
        titulos = [c[0][1]['head'] for c in push.call_args_list]
        self.assertEqual(sorted(titulos), ['Cliente remarcou', 'Novo agendamento'])

    def test_clinica_cancelou_nao_avisa_a_profissional(self):
        at = criar_atendimento(self.cli, self.prof, self.proc)
        with patch('aranha_estetica.services.push.send_push_to_user') as push, \
                self.captureOnCommitCallbacks(execute=True):
            at.cancelar(motivo='agenda fechada')
        push.assert_not_called()


class PedidoVencidoTests(_Base):
    def test_pedido_vence_uma_hora_antes_do_inicio(self):
        from aranha_estetica.constants import MSG_PEDIDO_EXPIRADO
        from aranha_estetica.jobs import job_limpeza_status
        quase = criar_atendimento(self.cli, self.prof, self.proc, status='PENDENTE',
                                  data_hora=timezone.now() + timedelta(minutes=30))
        futuro = criar_atendimento(criar_cliente(nome='Outra'), self.prof, self.proc, status='PENDENTE')
        job_limpeza_status()
        quase.refresh_from_db()
        futuro.refresh_from_db()
        self.assertEqual((quase.status, futuro.status), ('CANCELADO', 'PENDENTE'))
        aviso = Notificacao.objects.get(atendimento=quase, tipo='CANCELAMENTO')
        self.assertEqual(aviso.mensagem, MSG_PEDIDO_EXPIRADO)
