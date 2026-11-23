"""Comando `manage.py popular_demo`: operacao ficticia completa para o ambiente de testes, criada
pelos servicos do sistema, sem duplicar e travada em producao."""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from aranha_estetica.models import (
    AgendaExcecao, AnotacaoSessao, Atendimento, AvaliacaoNPS, Cliente, CompraPacote, ListaEspera,
    MovimentoComissao, Notificacao, Profissional, Promocao, Prontuario, RespostaAnamnese, Usuario,
)
from aranha_estetica.management.commands.popular_demo import CLIENTES


def _rodar():
    call_command('seed', '--demo', stdout=StringIO())
    call_command('popular_demo', stdout=StringIO(), stderr=StringIO(), verbosity=2)


@mock.patch.dict('os.environ', {'RAILWAY_ENVIRONMENT_NAME': '', 'DJANGO_ENV': '',
                                'DEMO_PROFISSIONAL_SENHA': 'Bianca-Demo#2026'})
@override_settings(ADMIN_2FA_OBRIGATORIO=False, PROFISSIONAL_2FA_OBRIGATORIO=False)
class PopularDemoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = Usuario.objects.create_user('admin@demo.test', password='x-Senha-forte-1', nome='Admin',
                                                papel=Usuario.PAPEL_ADMIN)
        with mock.patch.dict('os.environ', {'RAILWAY_ENVIRONMENT_NAME': '', 'DJANGO_ENV': '',
                                            'DEMO_PROFISSIONAL_SENHA': 'Bianca-Demo#2026'}):
            _rodar()

    def test_operacao_coerente_em_todas_as_frentes(self):
        demo = Cliente.objects.filter(telefone__in=[c[1] for c in CLIENTES])
        self.assertEqual(demo.count(), len(CLIENTES))
        self.assertTrue(all(c.email.endswith('@example.com') for c in demo))
        realizados = Atendimento.objects.filter(cliente__in=demo, status=Atendimento.STATUS_REALIZADO)
        self.assertGreater(realizados.count(), 40)
        self.assertTrue(Atendimento.objects.filter(cliente__in=demo, status=Atendimento.STATUS_FALTOU).exists())
        self.assertTrue(Atendimento.objects.filter(cliente__in=demo, status=Atendimento.STATUS_CANCELADO).exists())
        self.assertTrue(Atendimento.objects.filter(cliente__in=demo, status=Atendimento.STATUS_PENDENTE).exists())
        self.assertTrue(Atendimento.objects.filter(cliente__in=demo, status=Atendimento.STATUS_CONFIRMADO).exists())
        self.assertTrue(Atendimento.objects.filter(eh_retorno=True, status=Atendimento.STATUS_REALIZADO).exists())
        # pacotes com sessoes debitadas, comissao paga e pendente, NPS publicado
        self.assertEqual(CompraPacote.objects.filter(cliente__in=demo).count(), 3)
        self.assertTrue(CompraPacote.objects.filter(cliente__in=demo, itens__consumos__isnull=False).exists())
        self.assertTrue(MovimentoComissao.objects.exists())
        self.assertGreater(AvaliacaoNPS.objects.count(), 15)
        self.assertTrue(AvaliacaoNPS.objects.filter(aprovado_publicacao=True).exists())
        self.assertTrue(RespostaAnamnese.objects.filter(respondida_em__isnull=False).exists())
        self.assertTrue(Prontuario.objects.exists())
        self.assertTrue(AnotacaoSessao.objects.exists())
        self.assertEqual(ListaEspera.objects.count(), 4)
        self.assertTrue(Notificacao.objects.exists())
        self.assertTrue(Promocao.objects.filter(ativa=True).exists())
        self.assertTrue(AgendaExcecao.objects.filter(tipo=AgendaExcecao.TIPO_BLOQUEIO).exists())
        bianca = Profissional.objects.get(nome='Bianca Moraes')
        self.assertTrue(Usuario.objects.get(profissional=bianca).has_usable_password())

    def test_dez_horas_livres_nos_proximos_dias(self):
        from django.utils import timezone

        futuros = Atendimento.objects.filter(data_hora_inicio__gte=timezone.now(),
                                             cliente__telefone__in=[c[1] for c in CLIENTES])
        self.assertTrue(futuros.exists())
        self.assertFalse([a for a in futuros if timezone.localtime(a.data_hora_inicio).hour == 10])

    def test_segunda_execucao_nao_duplica(self):
        antes = (Cliente.objects.count(), Atendimento.objects.count(), AvaliacaoNPS.objects.count())
        call_command('popular_demo', stdout=StringIO())
        self.assertEqual((Cliente.objects.count(), Atendimento.objects.count(), AvaliacaoNPS.objects.count()), antes)

    def test_telas_do_painel_e_portal_abrem_com_os_dados(self):
        self.client.force_login(self.admin)
        rotas = ['overview', 'agendamentos', 'calendario', 'retornos', 'clientes', 'prontuario', 'notificacoes',
                 'promocoes', 'anamneses', 'pacotes', 'profissionais', 'financeiro', 'comissoes', 'nps',
                 'bloqueios', 'lista-espera', 'termos', 'usuarios', 'auditoria']
        for rota in rotas:
            with self.subTest(rota=rota):
                self.assertEqual(self.client.get(f'/painel/{rota}/').status_code, 200)
        cliente = Cliente.objects.get(telefone=CLIENTES[0][1])
        self.assertEqual(self.client.get(f'/painel/clientes/{cliente.pk}/').status_code, 200)
        self.client.force_login(Usuario.objects.get(profissional__nome='Bianca Moraes'))
        self.assertEqual(self.client.get('/profissional/').status_code, 200)

    def test_recusado_em_producao(self):
        with mock.patch.dict('os.environ', {'DJANGO_ENV': 'prod'}):
            erro = StringIO()
            call_command('popular_demo', stdout=StringIO(), stderr=erro)
        self.assertIn('recusado', erro.getvalue())
