"""Aprovar/rejeitar pelo painel (P06): service unico, aviso por avisar_transicao e lote por item."""
from unittest import mock

from django.contrib.messages import get_messages
from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse

from aranha_estetica.models import Atendimento, LogAuditoria, Notificacao, Usuario
from aranha_estetica.services.agendamento import AgendamentoService

from ..factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional


def _mensagens(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


def _emails(at, tipo):
    return Notificacao.objects.filter(atendimento=at, tipo=tipo, canal=Notificacao.Canal.EMAIL).count()


class AprovacaoPainelTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            email='admin-aprova-p06@test.com', password='senha-forte-123', nome='Admin Aprova',
        )
        self.client.force_login(self.admin)
        self.prof = criar_profissional('Dra. Aprova')
        self.proc = criar_procedimento(profissional=self.prof)

    def _pedido(self, nome='Pedido'):
        cliente = criar_cliente(nome, email=f'{nome.lower()}@cliente.test')   # sem e-mail nao ha aviso
        return criar_atendimento(cliente, self.prof, self.proc, status='PENDENTE')

    def test_aprovar_gera_uma_confirmacao_e_a_trilha(self):
        at = self._pedido('Bruna')
        resp = self.client.post(reverse('aranha:admin_aprovar_agendamento', args=[at.pk]),
                                HTTP_REFERER='/painel/overview/')
        self.assertRedirects(resp, '/painel/overview/', fetch_redirect_response=False)
        self.assertIn('Agendamento de Bruna aprovado.', _mensagens(resp))
        at.refresh_from_db()
        self.assertEqual(at.status, 'AGENDADO')
        self.assertEqual(_emails(at, 'CONFIRMACAO'), 1)
        self.assertTrue(LogAuditoria.objects.filter(acao='Aprovou agendamento', registro_id=at.pk,
                                                    usuario=self.admin).exists())
        # 2o clique (outra aba): nada muda e o aviso diz o estado atual
        resp = self.client.post(reverse('aranha:admin_aprovar_agendamento', args=[at.pk]))
        self.assertIn('Atendimento já está como agendado.', _mensagens(resp))
        self.assertEqual(_emails(at, 'CONFIRMACAO'), 1)

    def test_referer_externo_volta_para_a_agenda(self):
        at = self._pedido()
        resp = self.client.post(reverse('aranha:admin_rejeitar_agendamento', args=[at.pk]),
                                HTTP_REFERER='https://evil.example/x')
        self.assertRedirects(resp, reverse('aranha:painel_agendamentos'), fetch_redirect_response=False)
        at.refresh_from_db()
        self.assertEqual(at.status, 'CANCELADO')
        self.assertEqual(_emails(at, 'CANCELAMENTO'), 1)

    def test_corrida_vira_false_sem_excecao(self):
        at = self._pedido()
        Atendimento.objects.filter(pk=at.pk).update(status='CANCELADO')   # outra tela decidiu
        self.assertFalse(AgendamentoService().aprovar(at, by_user=self.admin))   # memoria ainda PENDENTE
        self.assertEqual(_emails(at, 'CONFIRMACAO'), 0)

    def test_lote_com_um_invalido_nao_quebra_os_demais(self):
        a, b, c = self._pedido('A'), self._pedido('B'), self._pedido('C')
        ja_decidido = criar_atendimento(criar_cliente('D'), self.prof, self.proc)   # AGENDADO
        resp = self.client.post(reverse('aranha:admin_bulk_agendamentos'), {
            'acao': 'aprovar', 'ids': [a.pk, b.pk, c.pk, ja_decidido.pk],
        })
        self.assertIn('3 agendamento(s) aprovado(s). 1 ignorado(s) (não estavam pendentes).', _mensagens(resp))
        self.assertEqual(Atendimento.objects.filter(pk__in=[a.pk, b.pk, c.pk], status='AGENDADO').count(), 3)

    def test_lote_falha_de_banco_em_um_item_nao_desfaz_os_outros(self):
        a, b = self._pedido('A'), self._pedido('B')
        original = AgendamentoService.aprovar

        def aprovar(servico, atendimento, **kw):
            if atendimento.pk == a.pk:
                raise DatabaseError('falha simulada')
            return original(servico, atendimento, **kw)

        with mock.patch.object(AgendamentoService, 'aprovar', aprovar), \
                self.assertLogs('aranha_estetica.services.agendamento', level='ERROR'):
            decididos = AgendamentoService().decidir_em_lote([a.pk, b.pk], 'aprovar', by_user=self.admin)
        self.assertEqual(decididos, 1)
        self.assertEqual(Atendimento.objects.get(pk=b.pk).status, 'AGENDADO')
        self.assertEqual(Atendimento.objects.get(pk=a.pk).status, 'PENDENTE')

    def test_lote_com_id_invalido_recusa_tudo(self):
        a = self._pedido()
        resp = self.client.post(reverse('aranha:admin_bulk_agendamentos'), {'acao': 'aprovar', 'ids': [a.pk, '²']})
        self.assertIn('Seleção inválida.', _mensagens(resp))
        self.assertEqual(Atendimento.objects.get(pk=a.pk).status, 'PENDENTE')
