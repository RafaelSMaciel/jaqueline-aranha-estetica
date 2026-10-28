"""Regressao das migrations de remocao (0065-0068): legado, integracoes mortas e dados sem finalidade.

Mesma base das data migrations da remodelagem (test_migrations_dados._MigracaoBase): desce ate o
estado anterior, planta o dado legado plausivel, sobe a migration real e confere a conversao — ou
que a guarda aborta com os ids e nada e aplicado. Tag ``migracoes`` (job separado no CI).
"""
from datetime import date, timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase, tag

from .test_migrations_dados import APP, _local_dt, _MigracaoBase, _sha

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


# ─── 0066: papel RECEPCAO e link legado de reagendar ──────────────────────
M0066 = '0066_papeis_e_links'


@tag('migracoes')
class PapeisELinks0066Tests(_MigracaoBase):
    """0066: link GERENCIAR_AGENDAMENTO e apagado (ids no log); os outros links ficam; a coluna
    link_acesso.atendimento some."""
    migrate_from = M0065
    migrate_to = M0066

    def preparar(self, apps):
        Profissional = apps.get_model(APP, 'Profissional')
        Cliente = apps.get_model(APP, 'Cliente')
        Procedimento = apps.get_model(APP, 'Procedimento')
        Atendimento = apps.get_model(APP, 'Atendimento')
        LinkAcesso = apps.get_model(APP, 'LinkAcesso')
        Usuario = apps.get_model(APP, 'Usuario')
        prof = Profissional.objects.create(nome='Dra Links 0066', slug='dra-links-0066')
        cli = Cliente.objects.create(nome='Cli 0066', telefone='11966660066')
        proc = Procedimento.objects.create(nome='Proc 0066', slug='proc-0066', duracao_minutos=30)
        ini = _local_dt(date(2031, 5, 6), 10)
        at = Atendimento.objects.create(cliente=cli, profissional=prof, procedimento=proc, status='AGENDADO',
                                        data_hora_inicio=ini, data_hora_fim=ini + timedelta(hours=1))
        self.legado = LinkAcesso.objects.create(finalidade='GERENCIAR_AGENDAMENTO', atendimento=at,
                                                token_hash=_sha('tok-legado-0066'), expira_em=ini).pk
        self.descadastro = LinkAcesso.objects.create(finalidade='DESCADASTRO', cliente=cli,
                                                     token_hash=_sha('tok-descad-0066')).pk
        self.admin = Usuario.objects.create(email='adm0066@x.com', nome='Adm', password='!', papel='ADMIN').pk

    def test_link_legado_apagado_e_os_outros_ficam(self):
        LinkAcesso = self.apps.get_model(APP, 'LinkAcesso')
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')
        self.assertEqual(list(LinkAcesso.objects.values_list('pk', flat=True)), [self.descadastro])
        log = LogAuditoria.objects.get(acao__startswith='migration 0066')
        self.assertEqual(log.detalhes, {'link_acesso_ids': [self.legado]})
        with connection.cursor() as cursor:
            colunas = {c.name for c in connection.introspection.get_table_description(cursor, 'link_acesso')}
        self.assertNotIn('atendimento_id', colunas)
        self.assertEqual(self.apps.get_model(APP, 'Usuario').objects.get(pk=self.admin).papel, 'ADMIN')


@tag('migracoes')
class PapelRecepcao0066AbortaTests(_AbortaBase):
    """Guarda: usuario RECEPCAO aborta a 0066 (nada aplicado) listando os ids."""
    migrate_from = M0065
    migrate_to = M0066

    def test_recepcao_aborta_com_os_ids(self):
        Usuario = self.antes.get_model(APP, 'Usuario')
        rec = Usuario.objects.create(email='rec0066@x.com', nome='Rec', password='!', papel='RECEPCAO')
        Usuario.objects.create(email='prof0066@x.com', nome='Prof', password='!', papel='PROFISSIONAL')
        msg = self.migrar_e_esperar_aborto()
        self.assertIn('0066', msg)
        self.assertTrue(msg.endswith(f'usuario.id={rec.pk}'), msg)
        Usuario.objects.filter(pk=rec.pk).delete()   # o tearDown sobe ate o fim
