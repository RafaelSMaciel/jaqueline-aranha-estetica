"""manage.py consultas_lentas: top do pg_stat_statements, ou como ligar a extensao."""
from io import StringIO
from unittest import mock, skipIf, skipUnless

from django.core.management import CommandError, call_command
from django.db import DatabaseError, connection
from django.test import TestCase

from aranha_estetica.management.commands import consultas_lentas as mod


class ConsultasLentasTests(TestCase):
    @skipIf(connection.vendor == 'postgresql', 'fora do Postgres')
    def test_fora_do_postgres_recusa(self):
        with self.assertRaisesMessage(CommandError, 'so no Postgres'):
            call_command('consultas_lentas')

    def _rodar(self, *args, linhas=None, erro=None):
        cursor = mock.MagicMock()
        cursor.fetchall.return_value = linhas or []
        if erro:
            cursor.execute.side_effect = erro
        falso = mock.MagicMock(vendor='postgresql')
        falso.cursor.return_value.__enter__.return_value = cursor
        out = StringIO()
        with mock.patch.object(mod, 'connection', falso):
            call_command('consultas_lentas', *args, stdout=out)
        return out.getvalue(), cursor

    def test_lista_pela_ordem_pedida(self):
        saida, cursor = self._rodar('--ordem', 'media', '--top', '3', linhas=[
            (120, 950.5, 7.92, 120, 'SELECT "atendimento"."id"\n  FROM "atendimento" WHERE x = $1'),
        ])
        sql, params = cursor.execute.call_args.args
        self.assertIn('ORDER BY mean_exec_time DESC', sql)
        self.assertEqual(params, [3])
        self.assertIn('950.5', saida)
        self.assertIn('SELECT "atendimento"."id" FROM "atendimento" WHERE x = $1', saida)

    def test_zerar_e_extensao_ausente(self):
        saida, cursor = self._rodar('--zerar')
        cursor.execute.assert_called_once_with('SELECT pg_stat_statements_reset()')
        self.assertIn('zeradas', saida)
        with self.assertRaisesMessage(CommandError, 'shared_preload_libraries'):
            self._rodar(erro=DatabaseError('relation "pg_stat_statements" does not exist'))

    @skipUnless(connection.vendor == 'postgresql', 'so no Postgres')
    def test_no_postgres_sem_preload_explica(self):
        # CI/docker: a imagem tem a extensao, mas sem shared_preload_libraries
        with self.assertRaisesMessage(CommandError, 'CREATE EXTENSION'):
            call_command('consultas_lentas', stdout=StringIO())
