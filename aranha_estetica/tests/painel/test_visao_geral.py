"""Visao geral do painel (P06): consultas fixas, leitura de saude na trilha e o select de status."""
from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import LogAuditoria, Prontuario, Usuario

from ..factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional


class VisaoGeralTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            email='admin-visao-p06@test.com', password='senha-forte-123', nome='Admin Visão',
        )
        self.client.force_login(self.admin)
        self.prof = criar_profissional('Dra. Visão')
        self.proc = criar_procedimento(profissional=self.prof)

    def _criar(self, n, inicio_dias=1):
        for i in range(n):
            cli = criar_cliente(f'Cliente {inicio_dias}-{i}')
            Prontuario.objects.create(cliente=cli, alergias='Dipirona')
            status = 'PENDENTE' if i % 2 else 'AGENDADO'
            criar_atendimento(cli, self.prof, self.proc, status=status,
                              data_hora=timezone.now() + timedelta(days=inicio_dias, hours=i))

    def _consultas(self):
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(reverse('aranha:painel_overview'))
        self.assertEqual(resp.status_code, 200)
        return len(ctx.captured_queries)

    def test_consultas_nao_crescem_com_a_lista(self):
        self._criar(1)
        com_um = self._consultas()
        self._criar(19, inicio_dias=3)
        self.assertEqual(self._consultas(), com_um)

    def test_alerta_mostrado_vai_para_a_trilha(self):
        self._criar(2)
        html = self.client.get(reverse('aranha:painel_overview')).content.decode()
        self.assertIn('Dipirona', html)
        log = LogAuditoria.objects.get(acao='Leu alertas de saúde')
        self.assertEqual(log.detalhes['tela'], 'visao_geral')
        self.assertEqual(len(log.detalhes['clientes']), 2)

    def test_pendente_mostra_aguardando_e_nao_o_select(self):
        pendente = criar_atendimento(criar_cliente('Pedido'), self.prof, self.proc, status='PENDENTE')
        agendado = criar_atendimento(criar_cliente('Marcada'), self.prof, self.proc)
        html = self.client.get(reverse('aranha:painel_overview')).content.decode()
        self.assertNotIn(f'data-atend-id="{pendente.pk}"', html)
        self.assertIn('Aguardando aprovação', html)
        self.assertIn(f'data-atend-id="{agendado.pk}"', html)
        self.assertIn('js/painel-status.js', html)

    def test_card_sem_desfecho(self):
        criar_atendimento(criar_cliente('Passou'), self.prof, self.proc, data_hora=timezone.now() - timedelta(days=2))
        html = self.client.get(reverse('aranha:painel_overview')).content.decode()
        self.assertIn('1 atendimento sem desfecho', html)
        self.assertIn(f"{reverse('aranha:painel_agendamentos')}?status=sem_desfecho", html)
