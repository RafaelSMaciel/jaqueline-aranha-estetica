"""Comando `manage.py popular_demo`: operacao ficticia ENXUTA para o ambiente de testes (3 clientes,
2 profissionais), criada pelos servicos do sistema, sem duplicar e travada em producao."""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from aranha_estetica.models import (
    AgendaExcecao, AnotacaoSessao, Atendimento, AvaliacaoNPS, Cliente, CompraPacote, ListaEspera,
    MovimentoComissao, Notificacao, Profissional, Promocao, Prontuario, RespostaAnamnese, Usuario,
)
from aranha_estetica.management.commands.popular_demo import CLIENTES
from aranha_estetica.services import retorno


def _rodar():
    # o pre-deploy do dev roda `seed` (sem --demo: as "Clientes Demonstracao" nao entram) e depois este
    call_command('seed', stdout=StringIO())
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

    def test_ambiente_enxuto_tres_clientes_e_duas_profissionais(self):
        self.assertEqual(len(CLIENTES), 3)
        self.assertEqual(Cliente.objects.count(), 3)
        self.assertEqual(set(Profissional.objects.values_list('nome', flat=True)), {'Jaqueline Aranha', 'Bianca Moraes'})
        self.assertTrue(all(c.email.endswith('@example.com') for c in Cliente.objects.all()))
        self.assertLess(Atendimento.objects.count(), 40)   # enxuto: nada de centenas de linhas sorteadas

    def test_operacao_coerente_em_todas_as_frentes(self):
        realizados = Atendimento.objects.filter(status=Atendimento.STATUS_REALIZADO)
        self.assertGreaterEqual(realizados.count(), 12)
        for status in (Atendimento.STATUS_FALTOU, Atendimento.STATUS_CANCELADO, Atendimento.STATUS_PENDENTE,
                       Atendimento.STATUS_CONFIRMADO):
            with self.subTest(status=status):
                self.assertTrue(Atendimento.objects.filter(status=status).exists())
        # retorno do botox: um feito, um marcado e um ainda a marcar (tela Retornos)
        self.assertTrue(Atendimento.objects.filter(eh_retorno=True, status=Atendimento.STATUS_REALIZADO).exists())
        self.assertTrue(Atendimento.objects.filter(eh_retorno=True, status=Atendimento.STATUS_AGENDADO).exists())
        self.assertEqual(len(retorno.devidos()), 1)
        # ja passou e ninguem marcou o desfecho: o aviso da visao geral tem o que mostrar
        self.assertTrue(Atendimento.objects.filter(status=Atendimento.STATUS_AGENDADO,
                                                   data_hora_fim__lt=timezone.now()).exists())
        # um pacote vendido com sessoes debitadas, comissao, NPS publicado, ficha e prontuario
        self.assertEqual(CompraPacote.objects.count(), 1)
        self.assertTrue(CompraPacote.objects.filter(itens__consumos__isnull=False).exists())
        self.assertTrue(MovimentoComissao.objects.filter(tipo='CREDITO').exists())
        self.assertTrue(MovimentoComissao.objects.filter(tipo='PAGAMENTO').exists())
        self.assertGreaterEqual(AvaliacaoNPS.objects.count(), 5)
        self.assertEqual(AvaliacaoNPS.objects.filter(aprovado_publicacao=True).count(), 3)
        self.assertEqual(RespostaAnamnese.objects.filter(respondida_em__isnull=False).count(), 3)
        self.assertEqual(Prontuario.objects.count(), 2)
        self.assertTrue(AnotacaoSessao.objects.exists())
        self.assertEqual(ListaEspera.objects.count(), 1)
        self.assertTrue(Notificacao.objects.exists())
        self.assertTrue(Promocao.objects.filter(ativa=True).exists())
        self.assertTrue(AgendaExcecao.objects.filter(tipo=AgendaExcecao.TIPO_BLOQUEIO).exists())
        # indicacao com cashback: a Camila foi indicada pela Ana
        camila = Cliente.objects.get(telefone=CLIENTES[1][1])
        self.assertEqual(camila.indicado_por.telefone, CLIENTES[0][1])
        bianca = Profissional.objects.get(nome='Bianca Moraes')
        self.assertTrue(Usuario.objects.get(profissional=bianca).has_usable_password())

    def test_dez_horas_livres_nos_proximos_dias(self):
        futuros = Atendimento.objects.filter(data_hora_inicio__gte=timezone.now())
        self.assertTrue(futuros.exists())
        self.assertFalse([a for a in futuros if timezone.localtime(a.data_hora_inicio).hour == 10])

    def test_segunda_execucao_nao_duplica(self):
        antes = (Cliente.objects.count(), Atendimento.objects.count(), AvaliacaoNPS.objects.count())
        call_command('popular_demo', stdout=StringIO())
        self.assertEqual((Cliente.objects.count(), Atendimento.objects.count(), AvaliacaoNPS.objects.count()), antes)

    def test_funciona_tambem_depois_do_seed_demo(self):
        """`seed --demo` (uso local) depois nao quebra nem duplica a demonstracao enxuta."""
        call_command('seed', '--demo', stdout=StringIO())
        call_command('popular_demo', stdout=StringIO())
        self.assertEqual(Cliente.objects.filter(telefone__in=[c[1] for c in CLIENTES]).count(), 3)

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
