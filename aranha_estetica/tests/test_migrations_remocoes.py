"""Regressao das migrations de remocao (0065-0068): legado, integracoes mortas e dados sem finalidade.

Mesma base das data migrations da remodelagem (test_migrations_dados._MigracaoBase): desce ate o
estado anterior, planta o dado legado plausivel, sobe a migration real e confere a conversao — ou
que a guarda aborta com os ids e nada e aplicado. Tag ``migracoes`` (job separado no CI).
"""
from datetime import date

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase, tag

from .test_migrations_dados import APP, _local_dt, _MigracaoBase

M0064 = '0064_carteira_rotulos'
M0065 = '0065_google_agenda_removida'


class _AbortaBase(TransactionTestCase):
    """Guarda que aborta: estado anterior, dado legado e a migration tem de falhar sem aplicar."""
    serialized_rollback = True
    migrate_from = None
    migrate_to = None

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([(APP, self.migrate_from)])
        self.antes = executor.loader.project_state([(APP, self.migrate_from)]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def migrar_e_esperar_aborto(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        with self.assertRaises(RuntimeError) as ctx:
            executor.migrate([(APP, self.migrate_to)])
        self.assertNotIn((APP, self.migrate_to), MigrationRecorder(connection).applied_migrations())
        return str(ctx.exception)


# ─── 0065: Google Agenda removido ─────────────────────────────────────────
@tag('migracoes')
class GoogleAgendaRemovida0065Tests(_MigracaoBase):
    """0065: bloqueio importado do Google Agenda vira MANUAL (continua bloqueando, motivo mantido);
    as colunas gcal_* e origem_ref somem."""
    migrate_from = M0064
    migrate_to = M0065

    def preparar(self, apps):
        Profissional = apps.get_model(APP, 'Profissional')
        AgendaExcecao = apps.get_model(APP, 'AgendaExcecao')
        prof = Profissional.objects.create(nome='Dra Agenda 0065', slug='dra-agenda-0065',
                                           gcal_refresh_token='1//tok', gcal_calendar_id='primary')
        dia = date(2031, 3, 4)
        self.gcal = AgendaExcecao.objects.create(
            tipo='BLOQUEIO', profissional=prof, origem='GCAL', origem_ref='evt-0065',
            data_hora_inicio=_local_dt(dia, 9), data_hora_fim=_local_dt(dia, 10), motivo='gcal:Dentista',
        ).pk
        self.manual = AgendaExcecao.objects.create(
            tipo='BLOQUEIO', profissional=prof, origem='MANUAL',
            data_hora_inicio=_local_dt(dia, 14), data_hora_fim=_local_dt(dia, 15), motivo='Curso',
        ).pk

    def test_gcal_vira_manual_mantendo_motivo_e_ids_no_log(self):
        AgendaExcecao = self.apps.get_model(APP, 'AgendaExcecao')
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')
        ex = AgendaExcecao.objects.get(pk=self.gcal)
        self.assertEqual((ex.origem, ex.motivo, ex.tipo, ex.ativo), ('MANUAL', 'gcal:Dentista', 'BLOQUEIO', True))
        self.assertEqual(AgendaExcecao.objects.get(pk=self.manual).motivo, 'Curso')
        log = LogAuditoria.objects.get(acao__startswith='migration 0065')
        self.assertEqual(log.detalhes, {'agenda_excecao_ids': [self.gcal]})

    def test_colunas_do_google_somem(self):
        with connection.cursor() as cursor:
            prof = {c.name for c in connection.introspection.get_table_description(cursor, 'profissional')}
            excecao = {c.name for c in connection.introspection.get_table_description(cursor, 'agenda_excecao')}
            nomes = set(connection.introspection.get_constraints(cursor, 'agenda_excecao'))
        self.assertFalse({c for c in prof if c.startswith('gcal_')})
        self.assertNotIn('origem_ref', excecao)
        self.assertFalse({'ck_agenda_excecao_gcal', 'uq_agenda_excecao_gcal_evento'} & nomes)
