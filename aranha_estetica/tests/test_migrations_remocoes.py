"""Regressao das migrations de remocao (0068-0071): legado, integracoes mortas e dados sem finalidade.

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

M0067 = '0067_carteira_origens_com_fluxo'   # ultima antes das remocoes (P12)
M0068 = '0068_google_agenda_removida'


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


# ─── 0068: Google Agenda removido ─────────────────────────────────────────
@tag('migracoes')
class GoogleAgendaRemovida0068Tests(_MigracaoBase):
    """0068: bloqueio importado do Google Agenda vira MANUAL (continua bloqueando, motivo mantido);
    as colunas gcal_* e origem_ref somem."""
    migrate_from = M0067
    migrate_to = M0068

    def preparar(self, apps):
        Profissional = apps.get_model(APP, 'Profissional')
        AgendaExcecao = apps.get_model(APP, 'AgendaExcecao')
        prof = Profissional.objects.create(nome='Dra Agenda 0068', slug='dra-agenda-0068',
                                           gcal_refresh_token='1//tok', gcal_calendar_id='primary')
        dia = date(2031, 3, 4)
        self.gcal = AgendaExcecao.objects.create(
            tipo='BLOQUEIO', profissional=prof, origem='GCAL', origem_ref='evt-0068',
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
        log = LogAuditoria.objects.get(acao__startswith='migration 0068')
        self.assertEqual(log.detalhes, {'agenda_excecao_ids': [self.gcal]})

    def test_colunas_do_google_somem(self):
        with connection.cursor() as cursor:
            prof = {c.name for c in connection.introspection.get_table_description(cursor, 'profissional')}
            excecao = {c.name for c in connection.introspection.get_table_description(cursor, 'agenda_excecao')}
            nomes = set(connection.introspection.get_constraints(cursor, 'agenda_excecao'))
        self.assertFalse({c for c in prof if c.startswith('gcal_')})
        self.assertNotIn('origem_ref', excecao)
        self.assertFalse({'ck_agenda_excecao_gcal', 'uq_agenda_excecao_gcal_evento'} & nomes)


# ─── 0069: papel RECEPCAO e link legado de reagendar ──────────────────────
M0069 = '0069_papeis_e_links'


@tag('migracoes')
class PapeisELinks0069Tests(_MigracaoBase):
    """0069: link GERENCIAR_AGENDAMENTO e apagado (ids no log); os outros links ficam; a coluna
    link_acesso.atendimento some."""
    migrate_from = M0068
    migrate_to = M0069

    def preparar(self, apps):
        Profissional = apps.get_model(APP, 'Profissional')
        Cliente = apps.get_model(APP, 'Cliente')
        Procedimento = apps.get_model(APP, 'Procedimento')
        Atendimento = apps.get_model(APP, 'Atendimento')
        LinkAcesso = apps.get_model(APP, 'LinkAcesso')
        Usuario = apps.get_model(APP, 'Usuario')
        prof = Profissional.objects.create(nome='Dra Links 0069', slug='dra-links-0069')
        cli = Cliente.objects.create(nome='Cli 0069', telefone='11966660066')
        proc = Procedimento.objects.create(nome='Proc 0069', slug='proc-0069', duracao_minutos=30)
        ini = _local_dt(date(2031, 5, 6), 10)
        at = Atendimento.objects.create(cliente=cli, profissional=prof, procedimento=proc, status='AGENDADO',
                                        data_hora_inicio=ini, data_hora_fim=ini + timedelta(hours=1))
        self.legado = LinkAcesso.objects.create(finalidade='GERENCIAR_AGENDAMENTO', atendimento=at,
                                                token_hash=_sha('tok-legado-0069'), expira_em=ini).pk
        self.descadastro = LinkAcesso.objects.create(finalidade='DESCADASTRO', cliente=cli,
                                                     token_hash=_sha('tok-descad-0069')).pk
        self.admin = Usuario.objects.create(email='adm0069@x.com', nome='Adm', password='!', papel='ADMIN').pk

    def test_link_legado_apagado_e_os_outros_ficam(self):
        LinkAcesso = self.apps.get_model(APP, 'LinkAcesso')
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')
        self.assertEqual(list(LinkAcesso.objects.values_list('pk', flat=True)), [self.descadastro])
        log = LogAuditoria.objects.get(acao__startswith='migration 0069')
        self.assertEqual(log.detalhes, {'link_acesso_ids': [self.legado]})
        with connection.cursor() as cursor:
            colunas = {c.name for c in connection.introspection.get_table_description(cursor, 'link_acesso')}
        self.assertNotIn('atendimento_id', colunas)
        self.assertEqual(self.apps.get_model(APP, 'Usuario').objects.get(pk=self.admin).papel, 'ADMIN')


@tag('migracoes')
class PapelRecepcao0069AbortaTests(_AbortaBase):
    """Guarda: usuario RECEPCAO aborta a 0069 (nada aplicado) listando os ids."""
    migrate_from = M0068
    migrate_to = M0069

    def test_recepcao_aborta_com_os_ids(self):
        Usuario = self.antes.get_model(APP, 'Usuario')
        rec = Usuario.objects.create(email='rec0069@x.com', nome='Rec', password='!', papel='RECEPCAO')
        Usuario.objects.create(email='prof0069@x.com', nome='Prof', password='!', papel='PROFISSIONAL')
        msg = self.migrar_e_esperar_aborto()
        self.assertIn('0069', msg)
        self.assertTrue(msg.endswith(f'usuario.id={rec.pk}'), msg)
        Usuario.objects.filter(pk=rec.pk).delete()   # o tearDown sobe ate o fim


# ─── 0070: modalidade, LEMBRETE_2H e notificacao PESQUISA ─────────────────
M0070 = '0070_formularios_notificacoes_modalidade'


def _cenario_0070(apps):
    Profissional = apps.get_model(APP, 'Profissional')
    Cliente = apps.get_model(APP, 'Cliente')
    Procedimento = apps.get_model(APP, 'Procedimento')
    Atendimento = apps.get_model(APP, 'Atendimento')
    prof = Profissional.objects.create(nome='Dra 0070', slug='dra-0070')
    cli = Cliente.objects.create(nome='Cli 0070', telefone='11966670067')
    proc = Procedimento.objects.create(nome='Proc 0070', slug='proc-0070', duracao_minutos=30)
    ini = _local_dt(date(2031, 6, 3), 10)
    at = Atendimento.objects.create(cliente=cli, profissional=prof, procedimento=proc, status='AGENDADO',
                                    data_hora_inicio=ini, data_hora_fim=ini + timedelta(minutes=30))
    return at, proc


@tag('migracoes')
class FormulariosNotificacoesModalidade0070Tests(_MigracaoBase):
    """0070: ficha MODALIDADE PRESENCIAL/HIBRIDO vira GLOBAL e a ONLINE e desativada; notificacao
    LEMBRETE_2H/PESQUISA sem resposta e apagada (com o link); procedimento online vai p/ o log."""
    migrate_from = M0069
    migrate_to = M0070

    def preparar(self, apps):
        Formulario = apps.get_model(APP, 'FormularioAnamnese')
        Notificacao = apps.get_model(APP, 'Notificacao')
        LinkAcesso = apps.get_model(APP, 'LinkAcesso')
        Procedimento = apps.get_model(APP, 'Procedimento')
        at, proc = _cenario_0070(apps)

        def ficha(nome, modalidade):
            return Formulario.objects.create(nome=nome, escopo='MODALIDADE', modalidade=modalidade,
                                             obrigatorio=True, schema_json=[]).pk

        self.presencial = ficha('Presencial', 'PRESENCIAL')
        self.hibrida = ficha('Hibrida', 'HIBRIDO')
        self.online = ficha('Online', 'ONLINE')
        self.categoria = Formulario.objects.create(nome='Facial', escopo='CATEGORIA', categoria='FACIAL',
                                                   schema_json=[]).pk
        self.n_2h = Notificacao.objects.create(atendimento=at, tipo='LEMBRETE_2H', status='ENVIADO').pk
        LinkAcesso.objects.create(finalidade='CONFIRMAR_PRESENCA', notificacao_id=self.n_2h,
                                  token_hash=_sha('tok-2h-0070'))
        self.n_pesquisa = Notificacao.objects.create(atendimento=at, tipo='PESQUISA').pk
        self.n_d1 = Notificacao.objects.create(atendimento=at, tipo='LEMBRETE', resposta='CONFIRMOU').pk
        self.online_proc = Procedimento.objects.create(
            nome='Consulta online', slug='consulta-online-0070', duracao_minutos=30, modalidade='ONLINE',
            link_videoconferencia='https://video.example/sala',
        ).pk
        self.proc = proc.pk

    def test_conversoes_e_ids_no_log(self):
        Formulario = self.apps.get_model(APP, 'FormularioAnamnese')
        Notificacao = self.apps.get_model(APP, 'Notificacao')
        LinkAcesso = self.apps.get_model(APP, 'LinkAcesso')
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')

        def estado(pk):
            f = Formulario.objects.get(pk=pk)
            return f.escopo, f.ativo, f.obrigatorio

        self.assertEqual(estado(self.presencial), ('GLOBAL', True, True))
        self.assertEqual(estado(self.hibrida), ('GLOBAL', True, True))
        self.assertEqual(estado(self.online), ('GLOBAL', False, True))
        self.assertEqual(Formulario.objects.get(pk=self.categoria).escopo, 'CATEGORIA')
        self.assertEqual(list(Notificacao.objects.values_list('pk', flat=True)), [self.n_d1])
        self.assertFalse(LinkAcesso.objects.exists())
        log = LogAuditoria.objects.get(acao__startswith='migration 0070')
        self.assertEqual(log.detalhes, {
            'notificacao_apagada': [self.n_2h, self.n_pesquisa],
            'formulario_modalidade_vira_global': [self.presencial, self.hibrida],
            'formulario_online_desativado': [self.online],
            'procedimento_vira_so_presencial': [self.online_proc],
        })
        with connection.cursor() as cursor:
            proc = {c.name for c in connection.introspection.get_table_description(cursor, 'procedimento')}
            form = {c.name for c in connection.introspection.get_table_description(cursor, 'formulario')}
        self.assertFalse({'modalidade', 'url_videoconferencia'} & proc)
        self.assertNotIn('modalidade', form)


@tag('migracoes')
class NotificacaoRespondida0070AbortaTests(_AbortaBase):
    """Guarda: LEMBRETE_2H/PESQUISA com resposta da cliente aborta a 0070 (nada aplicado)."""
    migrate_from = M0069
    migrate_to = M0070

    def test_respondida_aborta_com_os_ids(self):
        Notificacao = self.antes.get_model(APP, 'Notificacao')
        at, _proc = _cenario_0070(self.antes)
        respondida = Notificacao.objects.create(atendimento=at, tipo='LEMBRETE_2H', resposta='CANCELOU')
        sem_resposta = Notificacao.objects.create(atendimento=at, tipo='LEMBRETE_2H')
        msg = self.migrar_e_esperar_aborto()
        self.assertIn('0070', msg)
        self.assertIn(f'notificacao.id={respondida.pk} (LEMBRETE_2H)', msg)
        self.assertNotIn(f'notificacao.id={sem_resposta.pk} ', msg)
        self.assertTrue(Notificacao.objects.filter(pk=sem_resposta.pk).exists())   # nada aplicado
        Notificacao.objects.filter(pk=respondida.pk).delete()   # o tearDown sobe ate o fim


# ─── 0071: cadastro sem RG, profissao e foto ──────────────────────────────
M0071 = '0071_cliente_dados_sem_finalidade'


@tag('migracoes')
class ClienteSemDadoSemFinalidade0071Tests(_MigracaoBase):
    """0071: rg, profissao e url_foto saem; o resto do cadastro fica intacto."""
    migrate_from = M0070
    migrate_to = M0071

    def preparar(self, apps):
        Cliente = apps.get_model(APP, 'Cliente')
        self.cli = Cliente.objects.create(
            nome='Cli 0071', telefone='11966680068', cpf='52998224725', cep='15000000', endereco='Rua A, 1',
            rg='123456', profissao='Designer', foto_url='https://exemplo.test/f.jpg',
        ).pk

    def test_colunas_saem_e_o_cadastro_fica(self):
        with connection.cursor() as cursor:
            colunas = {c.name for c in connection.introspection.get_table_description(cursor, 'cliente')}
        self.assertFalse({'rg', 'profissao', 'url_foto'} & colunas)
        cli = self.apps.get_model(APP, 'Cliente').objects.get(pk=self.cli)
        self.assertEqual((cli.nome, cli.cpf, cli.cep, cli.endereco),
                         ('Cli 0071', '52998224725', '15000000', 'Rua A, 1'))
