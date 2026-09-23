"""Comando `manage.py seed`: idempotente, nao-destrutivo e travado em producao."""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from aranha_estetica.models import (
    Atendimento, Cliente, DisponibilidadeProfissional, FormularioAnamnese,
    Habilitacao, Preco, Procedimento, Profissional, VersaoTermo,
)
from aranha_estetica.management.commands.seed import CATALOGO


def _seed(*args):
    call_command('seed', *args, stdout=StringIO())


@mock.patch.dict('os.environ', {'RAILWAY_ENVIRONMENT_NAME': '', 'DJANGO_ENV': ''})
class SeedTests(TestCase):
    def test_cria_catalogo_profissional_agenda_termo(self):
        _seed()
        prof = Profissional.objects.get(nome='Jaqueline Aranha')
        self.assertTrue(prof.ativo)
        self.assertEqual(Procedimento.objects.count(), len(CATALOGO))
        self.assertEqual(Habilitacao.objects.filter(profissional=prof).count(), len(CATALOGO))
        # item sem preco no catalogo fica "a consultar" (sem Preco)
        self.assertFalse(Preco.objects.filter(procedimento__nome='Depilação a Laser').exists())
        self.assertEqual(Preco.objects.count(), len(CATALOGO) - 1)
        self.assertEqual(DisponibilidadeProfissional.objects.filter(profissional=prof).count(), 6)
        self.assertTrue(VersaoTermo.objects.filter(tipo='LGPD', ativa=True).exists())
        form = FormularioAnamnese.objects.get(nome='Anamnese padrão')
        form.full_clean()  # schema valido
        self.assertFalse(Cliente.objects.exists())

    def test_idempotente_e_nao_sobrescreve(self):
        _seed()
        proc = Procedimento.objects.get(nome='Limpeza de Pele')
        proc.duracao_minutos = 75
        proc.save()
        _seed()
        self.assertEqual(Procedimento.objects.count(), len(CATALOGO))
        self.assertEqual(Profissional.objects.count(), 1)
        self.assertEqual(Preco.objects.count(), len(CATALOGO) - 1)
        self.assertEqual(VersaoTermo.objects.filter(tipo='LGPD', ativa=True).count(), 1)
        self.assertEqual(Procedimento.objects.get(pk=proc.pk).duracao_minutos, 75)

    def test_demo_cria_clientes_e_atendimentos_sem_sobrepor(self):
        _seed('--demo')
        _seed('--demo')
        self.assertEqual(Cliente.objects.filter(email__endswith='@exemplo.com').count(), 3)
        self.assertEqual(Atendimento.objects.count(), 6)
        for at in Atendimento.objects.filter(status='AGENDADO'):
            self.assertFalse(
                Atendimento.objects.conflito_com(at.profissional_id, at.data_hora_inicio, at.data_hora_fim)
                .exclude(pk=at.pk).exists()
            )


class SeedProducaoTests(TestCase):
    @mock.patch.dict('os.environ', {'RAILWAY_ENVIRONMENT_NAME': 'production'})
    def test_recusa_em_producao_sem_force(self):
        with self.assertRaises(CommandError):
            _seed()
        self.assertFalse(Procedimento.objects.exists())

    @mock.patch.dict('os.environ', {'RAILWAY_ENVIRONMENT_NAME': 'production'})
    def test_demo_nunca_em_producao(self):
        with self.assertRaises(CommandError):
            _seed('--force', '--demo')

    @mock.patch.dict('os.environ', {'RAILWAY_ENVIRONMENT_NAME': 'production'})
    def test_force_carrega_catalogo_em_producao(self):
        _seed('--force')
        self.assertEqual(Procedimento.objects.count(), len(CATALOGO))
        self.assertFalse(Cliente.objects.exists())
