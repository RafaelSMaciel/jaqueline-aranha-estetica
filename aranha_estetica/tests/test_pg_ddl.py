"""DDL so-Postgres das migrations: existe e barra o que promete.

O SQLite nao tem nenhum destes objetos, e sem estes testes a suite passava
inteira (853/853) com todos removidos — um squash/AlterField que perdesse o
EXCLUDE anti-double-booking ou o trigger do ledger entraria verde no CI.

No SQLite a classe inteira e pulada. No CI (job postgres) PG_EXIGE_ICU=1 torna
obrigatoria a collation pt_br (0038 so avisa e segue quando o build nao tem ICU).
"""
import os
from datetime import timedelta
from decimal import Decimal
from unittest import skipUnless

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import TestCase

from aranha_estetica.models import (
    AceiteTermo, AgendaExcecao, AgendaHorario, Atendimento, Consentimento, FormularioAnamnese, LinkAcesso,
    Notificacao, Prontuario, ProntuarioVersao, RespostaAnamnese, VersaoTermo,
)
from aranha_estetica.services import consentimentos, links
from aranha_estetica.models.carteira import Carteira, MovimentoCarteira
from aranha_estetica.tests.factories import (
    criar_atendimento, criar_cliente, criar_link, criar_procedimento, criar_profissional,
)

PG = connection.vendor == 'postgresql'

# conname -> migration que cria (nomes do padrao desde a 0055: §3.3)
CONSTRAINTS_SO_PG = {
    'ex_atendimento_sobreposicao': '0035/0055',
    'ck_cliente_telefone_formato': '0034/0055',
    'ck_cliente_cpf_formato': '0034/0055',
    'ck_prontuario_respostas_objeto': '0043/0055',
    'ck_resposta_formulario_respostas_objeto': '0043/0055',
    'ck_formulario_perguntas_lista': '0043/0055',
    'ck_log_auditoria_detalhes_objeto': '0055',
    'ck_prontuario_versao_dados_objeto': '0055',
    'ck_aceite_termo_conteudo_hash_formato': '0055',
    'ck_codigo_otp_codigo_hash_formato': '0055',
    'ex_agenda_horario_sobreposicao': '0048',
    'ex_agenda_excecao_folga_sobreposta': '0048',
    'ck_agenda_excecao_regra_recorrencia_suportada': '0048',
    'ck_link_acesso_token_hash_formato': '0049',
    'fk_consumo_sessao_compra_pacote_item_compra': '0052',
    'ck_historico_alteracao_colunas_alteradas_lista': '0056',
}
# tgname -> (migration que cria, tabela). A 0047 (termo SAUDE, reembolso,
# promocao geral) so cria CHECKs de model (existem no SQLite tambem) e nao
# recria versao_termo: os triggers da 0046 tem de continuar la.
TRIGGERS_SO_PG = {
    'tg_movimento_carteira_imutavel': ('0038/0054', 'movimento_carteira'),
    'tg_aceite_termo_imutavel': ('0046/0054', 'aceite_termo'),
    'tg_versao_termo_imutavel': ('0046/0054', 'versao_termo'),
    'tg_prontuario_versao_imutavel': ('0046/0054', 'prontuario_versao'),
    'tg_link_acesso_imutavel': ('0049', 'link_acesso'),
    'tg_consentimento_imutavel': ('0050', 'consentimento'),
    'tg_compra_pacote_item_imutavel': ('0052', 'compra_pacote_item'),
    'tg_consumo_sessao_validar': ('0052', 'consumo_sessao'),
    'tg_movimento_comissao_validar': ('0053', 'movimento_comissao'),
    'tg_movimento_comissao_imutavel': ('0053', 'movimento_comissao'),
    # L1 (0056, spec §5.2/§5.3)
    'tg_anotacao_sessao_imutavel': ('0056', 'anotacao_sessao'),
    'tg_historico_alteracao_imutavel': ('0056', 'historico_alteracao'),
    'tg_log_auditoria_imutavel': ('0059', 'log_auditoria'),
    'tg_regra_comissao_validar': ('0066', 'regra_comissao'),
    **{f'tg_{t}_updated_at': ('0056/0079', t) for t in (
        'usuario', 'assinatura_push', 'cliente', 'profissional', 'procedimento', 'preco', 'promocao',
        'agenda_horario', 'agenda_excecao', 'atendimento', 'notificacao', 'lista_espera', 'avaliacao_nps',
        'prontuario', 'formulario', 'resposta_formulario', 'versao_termo', 'pacote', 'compra_pacote',
        'carteira', 'regra_comissao', 'configuracao')},
    **{f'tg_{t}_updated_at': ('0079', t) for t in ('codigo_otp', 'execucao_job')},
    **{f'tg_{t}_historico': ('0056', t) for t in (
        'prontuario', 'atendimento', 'compra_pacote', 'carteira', 'regra_comissao', 'preco', 'promocao',
        'pacote', 'versao_termo', 'usuario', 'configuracao')},
}
# rotinas e views da logica no banco (L1-L3, spec §5): nome -> prokind ('f' funcao, 'p' procedure, 'v' view)
ROTINAS_SO_PG = {
    **dict.fromkeys((
        'fn_bloquear_mutacao', 'fn_aceite_termo_imutavel', 'fn_versao_termo_imutavel',
        'fn_prontuario_versao_imutavel', 'fn_link_acesso_imutavel', 'fn_consentimento_imutavel',
        'fn_consumo_sessao_validar', 'fn_movimento_comissao_validar',
        'fn_set_updated_at', 'fn_historico_alteracao_imutavel', 'fn_registrar_historico',   # 0056 (0079)
        'fn_fuso_clinica', 'fn_data_local', 'fn_sem_acento',                                         # 0057
        'fn_recorrencia_casa_dia', 'fn_ocorrencias_recorrencia', 'fn_horarios_livres', 'fn_saldo_pacote',
        'fn_nps_periodo', 'fn_saldo_comissao', 'fn_tem_consentimento', 'fn_clientes_para_anonimizar',
        'fn_log_auditoria_imutavel',                                                                 # 0059
        'fn_regra_comissao_validar',                                                                 # 0066
        'fn_anotacao_sessao_imutavel',                                                               # 0077
    ), 'f'),
    **dict.fromkeys(('sp_anonimizar_cliente', 'sp_aplicar_retencao_lgpd', 'sp_purgar_dados_expirados',
                     'sp_expirar_pacotes'), 'p'),                                                    # 0058
}
VIEWS_SO_PG = {
    'vw_agenda_dia', 'vw_faturamento_mensal', 'vw_nps_mensal', 'vw_comissao', 'vw_comissao_saldo',
    'vw_cliente_resumo', 'vw_consentimento_atual', 'vw_pacote_saldo', 'vw_atendimento_sem_desfecho',
}


@skipUnless(PG, 'DDL so-Postgres (EXCLUDE, CHECK regex/jsonb, trigger, collation)')
class DDLSoPostgresTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()
        self.at = criar_atendimento(self.cli, self.prof, self.proc)

    def _sql(self, sql, params=None):
        with connection.cursor() as cursor:
            cursor.execute(sql, params)

    def test_objetos_existem(self):
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT conname FROM pg_constraint WHERE conname = ANY(%s)',
                [list(CONSTRAINTS_SO_PG)],
            )
            constraints = {row[0] for row in cursor.fetchall()}
            cursor.execute(
                'SELECT tgname, tgrelid::regclass::text FROM pg_trigger '
                'WHERE tgname = ANY(%s) AND NOT tgisinternal',
                [list(TRIGGERS_SO_PG)],
            )
            triggers = dict(cursor.fetchall())
        self.assertEqual(constraints, set(CONSTRAINTS_SO_PG))
        self.assertEqual(triggers, {nome: tabela for nome, (_, tabela) in TRIGGERS_SO_PG.items()})

    def test_logica_no_banco_rotinas_views_e_indice_trigram(self):
        """L1-L3 (0056-0059, spec §5) + 0066 + 0077 + 0079: 49 triggers, 25 funcoes, 4 procedures, 9 views, trigram."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT p.proname, p.prokind FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND p.proname ~ '^(fn|sp)_'"
            )
            rotinas = dict(cursor.fetchall())
            cursor.execute("SELECT viewname FROM pg_views WHERE schemaname = 'public'")
            views = {row[0] for row in cursor.fetchall()}
            cursor.execute("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname ~ '^tg_'")
            (qtd_triggers,) = cursor.fetchone()
            cursor.execute("SELECT extname FROM pg_extension WHERE extname IN ('pg_trgm', 'unaccent')")
            extensoes = {row[0] for row in cursor.fetchall()}
            cursor.execute("SELECT pg_get_indexdef('ix_cliente_nome_trgm'::regclass)")
            (indice,) = cursor.fetchone()
            cursor.execute("SELECT fn_sem_acento('Joao Avila'), fn_sem_acento('JOÃO ÁVILA')")
            sem_acento = cursor.fetchone()
            cursor.execute("SELECT fn_fuso_clinica()")
            (fuso,) = cursor.fetchone()
        self.assertEqual(rotinas, ROTINAS_SO_PG)
        self.assertEqual(views, VIEWS_SO_PG)
        self.assertEqual(qtd_triggers, len(TRIGGERS_SO_PG))
        self.assertEqual(len(TRIGGERS_SO_PG), 49)
        self.assertEqual(extensoes, {'pg_trgm', 'unaccent'})
        self.assertIn('fn_sem_acento', indice)
        self.assertEqual(sem_acento, ('joao avila', 'joao avila'))
        from django.conf import settings
        self.assertEqual(fuso, settings.TIME_ZONE)

    def _outro(self, prof=None, status='PENDENTE', delta=10):
        return Atendimento.objects.create(
            cliente=criar_cliente(), profissional=prof or self.prof, procedimento=self.proc,
            data_hora_inicio=self.at.data_hora_inicio + timedelta(minutes=delta),
            data_hora_fim=self.at.data_hora_fim + timedelta(minutes=delta), status=status,
        )

    def test_exclude_barra_sobreposicao_do_mesmo_profissional(self):
        # DEFERRABLE INITIALLY IMMEDIATE: falha no proprio INSERT
        for status in ('PENDENTE', 'AGENDADO', 'CONFIRMADO'):
            with self.subTest(status=status), self.assertRaises(IntegrityError), \
                    transaction.atomic():
                self._outro(status=status)
        self._outro(status='CANCELADO')                     # inativo nao conflita
        self._outro(prof=criar_profissional('Dra. Bia'))    # outro profissional
        self._outro(delta=self.proc.duracao_minutos)        # tstzrange [) contiguo

    def test_agenda_exclude_e_check_da_rrule(self):
        from datetime import date, time

        # grade: janela sobreposta no mesmo dia recusada; encostada e outro dia ok
        # (a factory ja criou 09:00-18:00 em todos os dias)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgendaHorario.objects.create(profissional=self.prof, dia_semana=1,
                                         hora_inicio=time(17), hora_fim=time(19))
        AgendaHorario.objects.create(profissional=self.prof, dia_semana=1, hora_inicio=time(18), hora_fim=time(20))
        # folga do mesmo profissional nao sobrepoe; inativa, global e de outro profissional podem
        d = date(2031, 5, 5)
        AgendaExcecao.objects.create(tipo='FOLGA', profissional=self.prof, data_inicio=d, data_fim=d + timedelta(days=4))
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgendaExcecao.objects.create(tipo='FOLGA', profissional=self.prof,
                                         data_inicio=d + timedelta(days=4), data_fim=d + timedelta(days=6))
        AgendaExcecao.objects.create(tipo='FOLGA', profissional=self.prof, ativo=False, data_inicio=d, data_fim=d)
        AgendaExcecao.objects.create(tipo='FOLGA', profissional=None, origem='RECESSO', data_inicio=d, data_fim=d)
        AgendaExcecao.objects.create(tipo='FOLGA', profissional=criar_profissional('Dra. Folga'),
                                     data_inicio=d, data_fim=d)
        # RRULE so no subconjunto (o clean normaliza; o banco barra SQL cru/bulk)
        bloqueio = AgendaExcecao.objects.create(
            tipo='BLOQUEIO', profissional=self.prof, regra_recorrencia='FREQ=MONTHLY;BYDAY=-1FR',
            data_hora_inicio=self.at.data_hora_inicio, data_hora_fim=self.at.data_hora_fim,
        )
        for regra in ('FREQ=YEARLY', 'FREQ=WEEKLY;BYDAY=1MO', 'FREQ=DAILY;COUNT=3', 'RRULE:FREQ=DAILY',
                      'FREQ=MONTHLY;BYDAY=5FR', 'FREQ=WEEKLY;BYMONTHDAY=3'):
            with self.subTest(regra=regra), self.assertRaises(IntegrityError), transaction.atomic():
                self._sql('UPDATE agenda_excecao SET regra_recorrencia = %s WHERE id = %s', [regra, bloqueio.pk])

    def test_agenda_nomes_padrao_e_on_delete_real(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT conrelid::regclass::text, conname, contype, confdeltype FROM pg_constraint "
                "WHERE conrelid IN ('agenda_horario'::regclass, 'agenda_excecao'::regclass) "
                "AND contype IN ('p', 'f')"
            )
            objetos = {(t, nome, tipo, acao) for t, nome, tipo, acao in cursor.fetchall()}
            cursor.execute("SELECT pg_get_serial_sequence('agenda_excecao', 'id'), "
                           "pg_get_serial_sequence('agenda_horario', 'id')")
            sequencias = cursor.fetchone()
        self.assertEqual(objetos, {
            ('agenda_horario', 'pk_agenda_horario', 'p', ' '),
            ('agenda_horario', 'fk_agenda_horario_profissional', 'f', 'c'),
            ('agenda_excecao', 'pk_agenda_excecao', 'p', ' '),
            ('agenda_excecao', 'fk_agenda_excecao_profissional', 'f', 'c'),
            ('agenda_excecao', 'fk_agenda_excecao_usuario_autor', 'f', 'n'),
        })
        self.assertEqual(sequencias, ('public.agenda_excecao_id_seq', 'public.agenda_horario_id_seq'))

    def test_link_acesso_nomes_tipo_e_on_delete_real(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT conname, contype, confdeltype FROM pg_constraint "
                "WHERE conrelid = 'link_acesso'::regclass AND contype IN ('p', 'f', 'u')"
            )
            objetos = set(cursor.fetchall())
            cursor.execute("SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                           "WHERE attrelid = 'link_acesso'::regclass AND attname = 'token_hash'")
            tipo = cursor.fetchone()[0]
            cursor.execute("SELECT indexrelid::regclass::text FROM pg_index WHERE indrelid = 'link_acesso'::regclass")
            indices = {r[0] for r in cursor.fetchall()}
            cursor.execute("SELECT pg_get_serial_sequence('link_acesso', 'id'), "
                           "obj_description('link_acesso'::regclass, 'pg_class')")
            sequencia, comentario = cursor.fetchone()
        self.assertEqual(objetos, {
            ('pk_link_acesso', 'p', ' '),
            ('uq_link_acesso_token_hash', 'u', ' '),
            ('fk_link_acesso_notificacao', 'f', 'c'),
            ('fk_link_acesso_resposta_formulario', 'f', 'c'),
            ('fk_link_acesso_cliente', 'f', 'c'),
            ('fk_link_acesso_profissional', 'f', 'c'),
        })
        self.assertEqual(tipo, 'character(64)')
        self.assertEqual(indices, {
            'pk_link_acesso', 'uq_link_acesso_token_hash',
            'ix_link_acesso_notificacao_parcial', 'ix_link_acesso_resposta_formulario_parcial',
            'ix_link_acesso_cliente_parcial', 'ix_link_acesso_profissional_parcial',
            'ix_link_acesso_expira_em_parcial',
        })
        self.assertEqual(sequencia, 'public.link_acesso_id_seq')
        self.assertIn('SHA-256', comentario)
        # tokens em texto puro: colunas e seus UNIQUE/_like legados sumiram (DROP COLUMN)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public' "
                "AND (column_name IN ('token_cancelamento', 'token_descadastro', 'ics_token', 'token_reserva') "
                "OR (column_name = 'token' AND table_name IN ('notificacao', 'resposta_formulario')))"
            )
            self.assertEqual(cursor.fetchall(), [])

    def test_link_acesso_bulk_create_nao_trunca_o_hash(self):
        # Django 5.2 no PG: bulk_create por UNNEST faria cast p/ char[] (= char(1)) e
        # truncaria o hash; HashSha256Field.get_placeholder desliga o UNNEST
        hashes = [links.sha256_hex(f'tok-bulk-{i}') for i in range(3)]
        LinkAcesso.objects.bulk_create([
            LinkAcesso(finalidade='FEED_ICS', profissional=self.prof, token_hash=h) for h in hashes
        ])
        self.assertEqual(sorted(LinkAcesso.objects.values_list('token_hash', flat=True)), sorted(hashes))
        self.assertEqual(links.resolver('FEED_ICS', 'tok-bulk-1').token_hash, hashes[1])

    def test_link_acesso_hash_formato_e_imutavel(self):
        notif = Notificacao.objects.create(atendimento=self.at, tipo='NPS')
        # CHECK de formato: sha256 hex minusculo, 64 chars (o SQLite nao tem)
        for ruim in ('A' * 64, 'g' * 64, 'abc', links.sha256_hex('x').upper()):
            with self.subTest(hash=ruim), self.assertRaises(IntegrityError), transaction.atomic():
                LinkAcesso.objects.create(finalidade='NPS', notificacao=notif, token_hash=ruim)
        token = criar_link('NPS', notif)
        link = LinkAcesso.objects.get()
        # depois de criado: finalidade/alvo/hash/validade nunca mudam
        bloqueados = (
            ("UPDATE link_acesso SET expira_em = now() + interval '1 year' WHERE id = %s", link.pk),
            ("UPDATE link_acesso SET token_hash = %s WHERE id = %s", links.sha256_hex('outro'), link.pk),
            ("UPDATE link_acesso SET finalidade = 'TERMO' WHERE id = %s", link.pk),
            ("UPDATE link_acesso SET created_at = now() - interval '1 day' WHERE id = %s", link.pk),
        )
        for sql, *params in bloqueados:
            with self.subTest(sql=sql), self.assertRaises(DatabaseError), transaction.atomic():
                self._sql(sql, params)
        # usado_em e revogado_em: preenchidos UMA vez
        links.marcar_usado(link)
        links.marcar_usado(link)  # no-op (filtro usado_em IS NULL)
        with self.assertRaises(DatabaseError), transaction.atomic():
            self._sql('UPDATE link_acesso SET usado_em = now() WHERE id = %s', [link.pk])
        self.assertEqual(links.revogar('NPS', notif), 1)
        with self.assertRaises(DatabaseError), transaction.atomic():
            self._sql('UPDATE link_acesso SET revogado_em = NULL WHERE id = %s', [link.pk])
        self.assertIsNone(links.resolver('NPS', token))
        # DELETE livre (purga/cascata do alvo)
        notif.delete()
        self.assertFalse(LinkAcesso.objects.exists())

    def test_consentimento_nomes_e_on_delete_real(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT conname, contype, confdeltype FROM pg_constraint "
                "WHERE conrelid = 'consentimento'::regclass AND contype IN ('p', 'f', 'u')"
            )
            objetos = set(cursor.fetchall())
            cursor.execute("SELECT indexrelid::regclass::text, pg_get_indexdef(indexrelid) FROM pg_index "
                           "WHERE indrelid = 'consentimento'::regclass")
            indices = dict(cursor.fetchall())
            cursor.execute("SELECT pg_get_serial_sequence('consentimento', 'id'), "
                           "obj_description('consentimento'::regclass, 'pg_class'), "
                           "format_type(atttypid, atttypmod) FROM pg_attribute "
                           "WHERE attrelid = 'consentimento'::regclass AND attname = 'ip_origem'")
            sequencia, comentario, tipo_ip = cursor.fetchone()
        self.assertEqual(objetos, {
            ('pk_consentimento', 'p', ' '),
            ('fk_consentimento_cliente', 'f', 'a'),
            ('fk_consentimento_usuario_autor', 'f', 'a'),
        })
        self.assertEqual(set(indices), {
            'pk_consentimento', 'ix_consentimento_cliente_finalidade_canal_registrado_em',
            'ix_consentimento_usuario_autor_parcial',
        })
        self.assertIn('(cliente_id, finalidade, canal, registrado_em DESC, id DESC)',
                      indices['ix_consentimento_cliente_finalidade_canal_registrado_em'])
        self.assertEqual((sequencia, tipo_ip), ('public.consentimento_id_seq', 'inet'))
        self.assertIn('append-only', comentario)
        # colunas antigas do cliente sumiram
        with connection.cursor() as cursor:
            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'cliente' "
                           "AND (column_name LIKE 'consent%%' OR column_name = 'aceita_comunicacao')")
            self.assertEqual(cursor.fetchall(), [])

    def test_consentimento_append_only_so_aceita_minimizacao(self):
        req = type('R', (), {'META': {'HTTP_USER_AGENT': 'UA', 'REMOTE_ADDR': '10.1.1.1'}})()
        linha = consentimentos.registrar(self.cli, 'MARKETING', 'EMAIL', True, origem='AGENDAMENTO_ONLINE',
                                         texto='Aceito', request=req)
        self.assertEqual((linha.ip_origem, linha.user_agent), ('10.1.1.1', 'UA'))
        bloqueados = (
            "UPDATE consentimento SET eh_concedido = false WHERE id = %s",
            "UPDATE consentimento SET registrado_em = now() - interval '1 day' WHERE id = %s",
            "UPDATE consentimento SET texto = 'outro' WHERE id = %s",
            "UPDATE consentimento SET origem = 'PAINEL' WHERE id = %s",
            "UPDATE consentimento SET ip_origem = '10.2.2.2' WHERE id = %s",
            "UPDATE consentimento SET user_agent = 'outro UA' WHERE id = %s",
            'DELETE FROM consentimento WHERE id = %s',
        )
        for sql in bloqueados:
            # ERRCODE 23000 -> IntegrityError (capturavel, nao 500)
            with self.subTest(sql=sql), self.assertRaises(IntegrityError), transaction.atomic():
                self._sql(sql, [linha.pk])
        # unica mutacao aceita: apagar IP/UA (anonimizacao) — e depois nao volta
        self.assertEqual(Consentimento.objects.filter(pk=linha.pk).update(ip_origem=None, user_agent=''), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._sql("UPDATE consentimento SET ip_origem = '10.1.1.1' WHERE id = %s", [linha.pk])
        # esquecimento completo (sp_anonimizar_cliente: revoga + minimiza) e o CHECK de par
        from aranha_estetica.services.lgpd import LgpdService
        LgpdService.esquecer_cliente(self.cli)
        self.assertFalse(consentimentos.vigente(self.cli, 'MARKETING', 'EMAIL'))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Consentimento.objects.create(cliente=self.cli, finalidade='MARKETING', canal='WHATSAPP',
                                         concedido=True, origem='PAINEL')

    def test_formulario_checks_e_um_prontuario_ativo(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT conname FROM pg_constraint WHERE conrelid = 'formulario'::regclass "
                           "AND conname LIKE 'ck_formulario_%%'")
            checks = {r[0] for r in cursor.fetchall()}
            cursor.execute("SELECT pg_get_indexdef('uq_formulario_prontuario_ativo'::regclass)")
            indice = cursor.fetchone()[0]
        self.assertEqual(checks, {'ck_formulario_tipo', 'ck_formulario_escopo', 'ck_formulario_escopo_coerente',
                                  'ck_formulario_prontuario_global', 'ck_formulario_perguntas_lista'})
        self.assertIn('UNIQUE INDEX', indice)
        FormularioAnamnese.objects.create(nome='Q', tipo='PRONTUARIO', schema_json=[])
        with self.assertRaises(IntegrityError), transaction.atomic():
            FormularioAnamnese.objects.create(nome='Q2', tipo='PRONTUARIO', schema_json=[])
        for sql in ("UPDATE formulario SET eh_obrigatorio = true WHERE tipo = 'PRONTUARIO'",
                    "UPDATE formulario SET escopo = 'PROCEDIMENTO' WHERE tipo = 'PRONTUARIO'",
                    "UPDATE formulario SET tipo = 'OUTRO' WHERE tipo = 'PRONTUARIO'"):
            with self.subTest(sql=sql), self.assertRaises(IntegrityError), transaction.atomic():
                self._sql(sql)

    def test_regra_de_comissao_usada_nao_muda_no_banco(self):
        """tg_regra_comissao_validar (0066): so o eh_ativo muda depois que um CREDITO cita a regra."""
        from aranha_estetica.models import RegraComissao
        from aranha_estetica.tests.factories import criar_comissao

        usada = RegraComissao.objects.create(percentual=Decimal('30'), ativo=True)
        livre = RegraComissao.objects.create(percentual=Decimal('10'), ativo=True)
        criar_comissao(self.at, regra=usada)
        for sql, params in (('UPDATE regra_comissao SET percentual = 40 WHERE id = %s', [usada.pk]),
                            ('UPDATE regra_comissao SET procedimento_id = %s WHERE id = %s', [self.proc.pk, usada.pk])):
            with self.subTest(sql=sql), self.assertRaises(IntegrityError), transaction.atomic():
                self._sql(sql, params)
        self._sql('UPDATE regra_comissao SET eh_ativo = false WHERE id = %s', [usada.pk])
        self._sql('UPDATE regra_comissao SET percentual = 15 WHERE id = %s', [livre.pk])
        self.assertEqual(RegraComissao.objects.get(pk=livre.pk).percentual, Decimal('15.00'))
        # comissao nunca acima da base congelada (ck_movimento_comissao_valor_ate_base_calculo, 0066)
        outro = criar_atendimento(self.cli, self.prof, self.proc)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._sql("INSERT INTO movimento_comissao (tipo, profissional_id, atendimento_id, valor, "
                      "valor_base_calculo, observacao) VALUES ('CREDITO', %s, %s, 50, 40, '')",
                      [self.prof.pk, outro.pk])

    def test_ledger_imutavel(self):
        carteira = Carteira.objects.create(cliente=self.cli, saldo=Decimal('10'))
        mov = MovimentoCarteira.objects.create(
            carteira=carteira, tipo='CREDITO', origem='AJUSTE_MANUAL',
            valor=Decimal('10'), saldo_resultante=Decimal('10'),
        )
        # fn_bloquear_mutacao (0054): SQLSTATE 23000 -> IntegrityError (antes P0001/InternalError)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MovimentoCarteira.objects.filter(pk=mov.pk).update(valor=Decimal('99'))
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._sql('DELETE FROM movimento_carteira WHERE id = %s', [mov.pk])
        mov.refresh_from_db()
        self.assertEqual(mov.valor, Decimal('10'))

    def test_prova_de_aceite_e_historico_clinico_imutaveis(self):
        # 0046: SQL cru passa por fora dos guards de save()/QuerySet do Django
        termo = VersaoTermo.objects.create(
            tipo='PROCEDIMENTO', procedimento=self.proc, titulo='Termo', conteudo='Texto aceito',
            versao='1.0', vigente_desde=self.at.data_hora_inicio.date(),
        )
        aceite = AceiteTermo.registrar(self.cli, termo, atendimento=self.at)
        versao = ProntuarioVersao.objects.create(
            prontuario=Prontuario.objects.create(cliente=self.cli), dados={'alergias': 'Dipirona'},
        )
        bloqueados = (
            ("UPDATE aceite_termo SET ip_origem = '1.1.1.1' WHERE id = %s", aceite.pk),
            ("UPDATE aceite_termo SET conteudo_hash = NULL WHERE id = %s", aceite.pk),
            ('DELETE FROM aceite_termo WHERE id = %s', aceite.pk),
            ("UPDATE versao_termo SET conteudo = 'Outro texto' WHERE id = %s", termo.pk),
            ("UPDATE prontuario_versao SET dados = '{}'::jsonb WHERE id = %s", versao.pk),
            ("UPDATE prontuario_versao SET created_by_name = 'x' WHERE id = %s", versao.pk),
            ('DELETE FROM prontuario_versao WHERE id = %s', versao.pk),
        )
        for sql, pk in bloqueados:
            # fn_*_imutavel (0054): SQLSTATE 23000 -> IntegrityError (antes P0001/InternalError)
            with self.subTest(sql=sql), self.assertRaises(IntegrityError), transaction.atomic():
                self._sql(sql, [pk])
        # permitido: desativar o termo e o SET_NULL das FKs
        self._sql('UPDATE versao_termo SET eh_ativo = false WHERE id = %s', [termo.pk])
        self._sql('UPDATE aceite_termo SET atendimento_id = NULL WHERE id = %s', [aceite.pk])
        self._sql('UPDATE prontuario_versao SET created_by_id = NULL WHERE id = %s', [versao.pk])
        aceite.refresh_from_db()
        self.assertEqual((aceite.ip, aceite.atendimento_id), (None, None))

    def test_checks_regex_cliente(self):
        # 0055: vazio deixou de ser aceito (ausente = NULL)
        for coluna, valor in (('telefone', 'abc'), ('telefone', '123'), ('telefone', ''),
                              ('cpf', '123456789ab'), ('cpf', '1234'), ('cpf', '')):
            with self.subTest(coluna=coluna, valor=valor), self.assertRaises(IntegrityError), \
                    transaction.atomic():
                self._sql(f'UPDATE cliente SET {coluna} = %s WHERE id = %s', [valor, self.cli.pk])
        # formato valido segue aceito
        self._sql('UPDATE cliente SET cpf = %s WHERE id = %s', ['12345678901', self.cli.pk])

    def test_checks_jsonb(self):
        # 0043 NOT VALID -> 0055 renomeados e validados (pre-checagem aborta se o legado viola)
        prontuario = Prontuario.objects.create(cliente=self.cli)
        formulario = FormularioAnamnese.objects.create(nome='Ficha', schema_json=[])
        resposta = RespostaAnamnese.objects.create(
            formulario=formulario, cliente=self.cli, respostas_json={},
        )
        versao = ProntuarioVersao.objects.create(prontuario=prontuario, dados={'alergias': ''})
        casos = (
            ("UPDATE prontuario SET respostas = '[]'::jsonb WHERE id = %s", [prontuario.pk]),
            ("UPDATE resposta_formulario SET respostas = '[]'::jsonb WHERE id = %s", [resposta.pk]),
            ("UPDATE formulario SET perguntas = '{}'::jsonb WHERE id = %s", [formulario.pk]),
            ("INSERT INTO log_auditoria (acao, detalhes) VALUES ('x', '[1]'::jsonb)", None),
            ("INSERT INTO prontuario_versao (prontuario_id, dados) VALUES (%s, '[]'::jsonb)", [prontuario.pk]),
        )
        self.assertTrue(versao.pk)
        for sql, params in casos:
            with self.subTest(sql=sql), self.assertRaises(IntegrityError), transaction.atomic():
                self._sql(sql, params)
        with connection.cursor() as cursor:
            cursor.execute("SELECT conname, convalidated FROM pg_constraint WHERE conname IN "
                           "('ck_prontuario_respostas_objeto', 'ck_resposta_formulario_respostas_objeto', "
                           "'ck_formulario_perguntas_lista')")
            self.assertEqual(dict(cursor.fetchall()), {
                'ck_prontuario_respostas_objeto': True, 'ck_resposta_formulario_respostas_objeto': True,
                'ck_formulario_perguntas_lista': True,
            })

    def test_collation_nome(self):
        # 0054: db_collation='pt_br' no estado (a migration aborta se a collation nao existe)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name, collation_name FROM information_schema.columns "
                "WHERE column_name = 'nome' AND table_name IN ('cliente', 'profissional', 'procedimento')"
            )
            collations = dict(cursor.fetchall())
            cursor.execute("SELECT collprovider FROM pg_collation WHERE collname = 'pt_br'")
            provedor = cursor.fetchone()[0]
        self.assertEqual(
            collations, {'cliente': 'pt_br', 'profissional': 'pt_br', 'procedimento': 'pt_br'},
        )
        if os.environ.get('PG_EXIGE_ICU') == '1':  # CI/producao: ICU de verdade (harness local emula)
            self.assertEqual(provedor, 'i')

    # ─── 0052: pacote congelado ──────────────────────────────────────────
    def _venda(self, sessoes=2):
        from aranha_estetica.tests.factories import criar_compra_pacote, criar_pacote
        compra = criar_compra_pacote(self.cli, criar_pacote(procedimento=self.proc, sessoes=sessoes,
                                                            preco=Decimal('300.00')))
        return compra, compra.itens.get()

    def test_pacote_congelado_nomes_e_on_delete_real(self):
        with connection.cursor() as cursor:
            cursor.execute(
                # contype 'n' (PG 18+): NOT NULL vira constraint nomeada pelo proprio Postgres
                # (<tabela>_<coluna>_not_null) — fora do padrao por definicao, ignorado
                # tambem pelo test_nomes_padrao.
                "SELECT conrelid::regclass::text, conname, contype, confdeltype FROM pg_constraint "
                "WHERE contype <> 'n' AND (conrelid = 'compra_pacote_item'::regclass "
                "OR (conrelid = 'consumo_sessao'::regclass AND conname LIKE 'fk_consumo_sessao_compra_pacote_item%%'))"
            )
            objetos = set(cursor.fetchall())
            cursor.execute("SELECT pg_get_serial_sequence('compra_pacote_item', 'id'), "
                           "obj_description('compra_pacote_item'::regclass, 'pg_class'), "
                           "col_description('compra_pacote'::regclass, "
                           "(SELECT attnum FROM pg_attribute WHERE attrelid = 'compra_pacote'::regclass "
                           "AND attname = 'pacote_nome')::int)")
            sequencia, comentario, comentario_nome = cursor.fetchone()
            cursor.execute("SELECT indexrelid::regclass::text FROM pg_index "
                           "WHERE indrelid IN ('compra_pacote_item'::regclass, 'consumo_sessao'::regclass)")
            indices = {r[0] for r in cursor.fetchall()}
            cursor.execute("SELECT attnotnull FROM pg_attribute WHERE attrelid = 'consumo_sessao'::regclass "
                           "AND attname = 'compra_pacote_item_id'")
            nao_nulo = cursor.fetchone()[0]
        self.assertEqual(objetos, {
            ('compra_pacote_item', 'pk_compra_pacote_item', 'p', ' '),
            ('compra_pacote_item', 'fk_compra_pacote_item_compra_pacote', 'f', 'c'),
            ('compra_pacote_item', 'fk_compra_pacote_item_procedimento', 'f', 'a'),
            ('compra_pacote_item', 'uq_compra_pacote_item_compra_procedimento', 'u', ' '),
            ('compra_pacote_item', 'uq_compra_pacote_item_id_compra', 'u', ' '),
            ('compra_pacote_item', 'ck_compra_pacote_item_qtd_sessoes_positiva', 'c', ' '),
            ('compra_pacote_item', 'ck_compra_pacote_item_valor_unitario_nao_negativo', 'c', ' '),
            ('consumo_sessao', 'fk_consumo_sessao_compra_pacote_item', 'f', 'a'),
            ('consumo_sessao', 'fk_consumo_sessao_compra_pacote_item_compra', 'f', 'a'),
        })
        self.assertTrue({'ix_compra_pacote_item_procedimento', 'ix_consumo_sessao_compra_pacote_item'} <= indices)
        self.assertEqual(sequencia, 'public.compra_pacote_item_id_seq')
        self.assertIn('snapshot', comentario)
        self.assertIn('momento da venda', comentario_nome)
        self.assertTrue(nao_nulo)

    def test_consumo_sessao_validado_no_banco(self):
        from aranha_estetica.models import ConsumoSessao
        compra, item = self._venda(sessoes=1)
        outra, item_outra = self._venda(sessoes=1)
        outro_proc = criar_procedimento('Peeling', profissional=self.prof)
        de_outro_proc = criar_atendimento(self.cli, self.prof, outro_proc)
        retorno = criar_atendimento(self.cli, self.prof, self.proc)
        Atendimento.objects.filter(pk=retorno.pk).update(eh_retorno=True, valor_cobrado=0, atendimento_origem=self.at)
        casos = {
            'item de outra compra': dict(compra_pacote=compra, compra_pacote_item=item_outra, atendimento=self.at),
            'retorno': dict(compra_pacote=compra, compra_pacote_item=item, atendimento=retorno),
            'outro procedimento': dict(compra_pacote=compra, compra_pacote_item=item, atendimento=de_outro_proc),
        }
        for nome, campos in casos.items():
            # bulk_create pula a validacao do save(): o trigger tem de barrar sozinho
            with self.subTest(nome), self.assertRaises(IntegrityError), transaction.atomic():
                ConsumoSessao.objects.bulk_create([ConsumoSessao(**campos)])
        ConsumoSessao.objects.bulk_create([ConsumoSessao(compra_pacote=compra, compra_pacote_item=item,
                                                         atendimento=self.at)])
        with self.assertRaises(IntegrityError), transaction.atomic():  # sem saldo (1 de 1)
            ConsumoSessao.objects.bulk_create([ConsumoSessao(
                compra_pacote=compra, compra_pacote_item=item,
                atendimento=criar_atendimento(self.cli, self.prof, self.proc))])
        # FK composta: mudar so a compra do consumo p/ outra (o trigger e so de INSERT)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._sql('UPDATE consumo_sessao SET compra_pacote_id = %s WHERE atendimento_id = %s',
                      [outra.pk, self.at.pk])
            self._sql('SET CONSTRAINTS ALL IMMEDIATE')

    def test_item_vendido_imutavel_mas_sai_com_a_compra(self):
        compra, item = self._venda()
        for sql in ('UPDATE compra_pacote_item SET qtd_sessoes = 99 WHERE id = %s',
                    "UPDATE compra_pacote_item SET procedimento_nome = 'x' WHERE id = %s"):
            # fn_bloquear_mutacao: SQLSTATE 23000 -> IntegrityError
            with self.subTest(sql=sql), self.assertRaises(IntegrityError), transaction.atomic():
                self._sql(sql, [item.pk])
        compra.delete()
        self.assertFalse(type(item).objects.filter(pk=item.pk).exists())

    # ─── 0053: comissao como livro-razao ─────────────────────────────────
    def test_comissao_ledger_nomes_e_triggers(self):
        from aranha_estetica.models import MovimentoComissao
        from aranha_estetica.tests.factories import criar_comissao

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT conname, confdeltype FROM pg_constraint WHERE conrelid = 'movimento_comissao'::regclass "
                "AND contype = 'f' AND conname LIKE 'fk_movimento_comissao_%%'"
            )
            fks = set(cursor.fetchall())
            cursor.execute("SELECT indexrelid::regclass::text, pg_get_indexdef(indexrelid) FROM pg_index "
                           "WHERE indrelid = 'movimento_comissao'::regclass")
            indices = dict(cursor.fetchall())
        # 0055: as FKs antigas tambem no padrao (PadronizarNomes), todas NO ACTION (PROTECT)
        self.assertEqual(fks, {('fk_movimento_comissao_movimento_comissao_origem', 'a'),
                               ('fk_movimento_comissao_usuario_autor', 'a'),
                               ('fk_movimento_comissao_profissional', 'a'),
                               ('fk_movimento_comissao_atendimento', 'a'),
                               ('fk_movimento_comissao_regra_comissao', 'a')})
        # 0063: sai o UNIQUE de 1 CREDITO p/ sempre; "1 CREDITO ATIVO" fica no gatilho de validacao
        self.assertNotIn('uq_movimento_comissao_credito_por_atendimento', indices)
        for nome, trecho in (('uq_movimento_comissao_estorno_por_origem', "'ESTORNO'"),
                             ('uq_movimento_comissao_pagamento_por_origem', "'PAGAMENTO'"),
                             ('ix_movimento_comissao_origem_parcial', 'IS NOT NULL')):
            with self.subTest(nome):
                self.assertIn('WHERE', indices[nome])
                self.assertIn(trecho, indices[nome])
        Atendimento.objects.filter(pk=self.at.pk).update(status='REALIZADO')
        credito = criar_comissao(self.at, valor='20.00')
        base = {'profissional': self.prof, 'atendimento': self.at, 'movimento_origem': credito}
        invalidos = {
            'valor diferente': dict(tipo='PAGAMENTO', valor=Decimal('10.00')),
            'outro profissional': dict(tipo='PAGAMENTO', valor=Decimal('20.00'),
                                       profissional=criar_profissional('Dra. Outra')),
        }
        for nome, campos in invalidos.items():
            with self.subTest(nome), self.assertRaises(IntegrityError), transaction.atomic():
                MovimentoComissao.objects.bulk_create([MovimentoComissao(**{**base, **campos})])
        estorno = MovimentoComissao.objects.bulk_create([MovimentoComissao(tipo='ESTORNO', valor=Decimal('20.00'),
                                                                           **base)])[0]
        with self.assertRaises(IntegrityError), transaction.atomic():  # estornado nao se paga
            MovimentoComissao.objects.bulk_create([MovimentoComissao(tipo='PAGAMENTO', valor=Decimal('20.00'), **base)])
        with self.assertRaises(IntegrityError), transaction.atomic():  # origem tem de ser CREDITO
            MovimentoComissao.objects.bulk_create([MovimentoComissao(
                tipo='ESTORNO', valor=Decimal('20.00'), **{**base, 'movimento_origem': estorno})])
        # append-only (fn_bloquear_mutacao, SQLSTATE 23000)
        for sql in ('UPDATE movimento_comissao SET valor = 1 WHERE id = %s',
                    'DELETE FROM movimento_comissao WHERE id = %s'):
            with self.subTest(sql=sql), self.assertRaises(IntegrityError), transaction.atomic():
                self._sql(sql, [credito.pk])

    def test_regra_comissao_usada_protegida(self):
        from django.db.models import ProtectedError

        from aranha_estetica.models import RegraComissao
        from aranha_estetica.tests.factories import criar_comissao

        regra = RegraComissao.objects.create(percentual=Decimal('10'))
        criar_comissao(self.at, valor='5.00', regra=regra)
        with self.assertRaises(ProtectedError):
            regra.delete()
        with self.assertRaises(IntegrityError), transaction.atomic():  # SQL cru: FK NO ACTION
            self._sql('DELETE FROM regra_comissao WHERE id = %s', [regra.pk])
            self._sql('SET CONSTRAINTS ALL IMMEDIATE')

    def test_ledger_da_carteira_segue_imutavel_com_a_funcao_generica(self):
        # 0053 derruba/recria o trigger da 0038 (nome da indicada); 0054 troca p/ tg_/fn_bloquear_mutacao
        with connection.cursor() as cursor:
            cursor.execute("SELECT tgname, tgfoid::regproc::text FROM pg_trigger "
                           "WHERE tgrelid = 'movimento_carteira'::regclass AND NOT tgisinternal")
            self.assertEqual(cursor.fetchall(), [('tg_movimento_carteira_imutavel', 'fn_bloquear_mutacao')])
            cursor.execute("SELECT count(*) FROM pg_proc WHERE proname IN ('bloquear_mutacao_ledger', "
                           "'aceite_termo_imutavel', 'versao_termo_imutavel', 'prontuario_versao_imutavel')")
            self.assertEqual(cursor.fetchone()[0], 0)

    # ─── 0054/0055: fase de nomes ────────────────────────────────────────
    def test_fase_de_nomes_tipos_e_tabelas(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name || '.' || column_name, "
                "       format_type(a.atttypid, a.atttypmod), is_nullable, column_default "
                "  FROM information_schema.columns c "
                "  JOIN pg_attribute a ON a.attrelid = (c.table_schema || '.' || c.table_name)::regclass "
                "   AND a.attname = c.column_name "
                " WHERE (table_name, column_name) IN (('log_auditoria', 'registro_pk'), ('codigo_otp', 'codigo_hash'), "
                "       ('aceite_termo', 'conteudo_hash'), ('codigo_otp', 'qtd_tentativas'), "
                "       ('procedimento', 'duracao_retorno_minutos'), ('cliente', 'updated_at'), "
                "       ('usuario', 'created_at'), ('log_auditoria', 'tabela'), ('cliente', 'eh_ativo'))"
            )
            colunas = {nome: (tipo, nulo, default) for nome, tipo, nulo, default in cursor.fetchall()}
            cursor.execute("SELECT to_regclass('pacote_item'), to_regclass('formulario'), "
                           "to_regclass('resposta_formulario'), to_regclass('item_pacote'), "
                           "to_regclass('formulario_anamnese'), to_regclass('resposta_anamnese')")
            tabelas = cursor.fetchone()
        self.assertEqual(colunas['log_auditoria.registro_pk'][0], 'bigint')
        self.assertEqual(colunas['codigo_otp.codigo_hash'][:2], ('character(64)', 'NO'))
        self.assertEqual(colunas['aceite_termo.conteudo_hash'][:2], ('character(64)', 'YES'))
        self.assertEqual(colunas['codigo_otp.qtd_tentativas'], ('smallint', 'NO', '0'))
        self.assertEqual(colunas['procedimento.duracao_retorno_minutos'], ('smallint', 'NO', '30'))
        self.assertEqual(colunas['cliente.updated_at'][1], 'NO')
        self.assertIn('statement_timestamp', colunas['usuario.created_at'][2])
        self.assertEqual(colunas['log_auditoria.tabela'][1:], ('NO', "''::character varying"))
        self.assertEqual(colunas['cliente.eh_ativo'][2], 'true')
        self.assertEqual(tabelas, ('pacote_item', 'formulario', 'resposta_formulario', None, None, None))

    def test_fase_de_nomes_checks_novos(self):
        from aranha_estetica.models import CodigoOtp
        from aranha_estetica.models.carteira import Carteira, MovimentoCarteira

        carteira = Carteira.objects.create(cliente=self.cli)
        otp = CodigoOtp.gerar('a@b.com')[1]
        casos = (
            ("UPDATE notificacao SET canal = 'TELEGRAM' WHERE atendimento_id = %s", [self.at.pk]),
            ("UPDATE atendimento SET valor_cobrado = -1 WHERE id = %s", [self.at.pk]),
            ("UPDATE cliente SET email = '' WHERE id = %s", [self.cli.pk]),
            ("UPDATE codigo_otp SET codigo_hash = repeat('G', 64) WHERE id = %s", [otp.pk]),
            ("UPDATE codigo_otp SET qtd_tentativas = -1 WHERE id = %s", [otp.pk]),
            ("INSERT INTO movimento_carteira (carteira_id, tipo, origem, valor, valor_saldo_resultante, observacao) "
             "VALUES (%s, 'CREDITO', 'AJUSTE_MANUAL', 0, 0, '')", [carteira.pk]),
        )
        Notificacao.objects.create(atendimento=self.at, tipo='NPS')
        for sql, params in casos:
            with self.subTest(sql=sql), self.assertRaises(IntegrityError), transaction.atomic():
                self._sql(sql, params)
        self.assertFalse(MovimentoCarteira.objects.exists())
