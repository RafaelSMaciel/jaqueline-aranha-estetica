"""Telas com lista: o numero de consultas nao cresce com o numero de linhas (N+1).

Alerta de saude por linha custava 2-3 consultas por cliente (utils.saude.alertas_em_lote
resolve a lista em 3); aqui o painel e a lista de prontuarios com 1 e com 4 clientes.
"""
from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import Prontuario, Usuario

from .factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional


class ConsultasPorTelaTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.admin = Usuario.objects.create_user(email='adm@consultas.com', password='x',
                                                 papel=Usuario.PAPEL_ADMIN)
        self.client.force_login(self.admin)
        self.n = 0

    def _cliente_com_alerta(self):
        self.n += 1
        cli = criar_cliente(nome=f'Cliente {self.n}', telefone=f'1799999{self.n:04d}')
        Prontuario.objects.create(cliente=cli, alergias='dipirona')
        # horarios em sequencia (EXCLUDE do Postgres)
        criar_atendimento(cli, self.prof, self.proc, status='AGENDADO',
                          data_hora=timezone.now() + timedelta(days=2, hours=self.n))
        return cli

    def _consultas(self, url):
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        return len(ctx), resp

    def test_painel_e_prontuarios_sem_n_mais_1(self):
        for nome in ('aranha:painel_overview', 'aranha:prontuario_consentimento'):
            with self.subTest(tela=nome):
                url = reverse(nome)
                Prontuario.objects.all().delete()
                self._cliente_com_alerta()
                self._consultas(url)        # aquece o cache (Configuracao, branding)
                um, _ = self._consultas(url)
                for _ in range(3):
                    self._cliente_com_alerta()
                quatro, resp = self._consultas(url)
                self.assertEqual(quatro, um)
                self.assertContains(resp, 'dipirona')
