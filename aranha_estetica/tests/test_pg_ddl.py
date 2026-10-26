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
from aranha_estetica.models.extras import Carteira, MovimentoCarteira
from aranha_estetica.tests.factories import (
    criar_atendimento, criar_cliente, criar_link, criar_procedimento, criar_profissional,
)

PG = connection.vendor == 'postgresql'

# conname -> migration que cria
CONSTRAINTS_SO_PG = {
    'excl_atendimento_sobreposicao': '0035',
    'chk_cliente_telefone_digits': '0034',
    'chk_cliente_cpf_digits': '0034',
    'chk_prontuario_extras_objeto': '0043',
    'chk_resposta_anamnese_objeto': '0043',
    'chk_formulario_schema_lista': '0043',
    'ex_agenda_horario_sobreposicao': '0048',
    'ex_agenda_excecao_folga_sobreposta': '0048',
    'ck_agenda_excecao_regra_recorrencia_suportada': '0048',
    'ck_link_acesso_token_hash_formato': '0049',
}
# tgname -> (migration que cria, tabela). A 0047 (termo SAUDE, reembolso,
# promocao geral) so cria CHECKs de model (existem no SQLite tambem) e nao
# recria versao_termo: os triggers da 0046 tem de continuar la.
TRIGGERS_SO_PG = {
    'trg_movimento_carteira_imutavel': ('0038', 'movimento_carteira'),
    'trg_aceite_termo_imutavel': ('0046', 'aceite_termo'),
    'trg_versao_termo_imutavel': ('0046', 'versao_termo'),
    'trg_prontuario_versao_imutavel': ('0046', 'prontuario_versao'),
    'tg_link_acesso_imutavel': ('0049', 'link_acesso'),
    'tg_consentimento_imutavel': ('0050', 'consentimento'),
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
            ('fk_link_acesso_atendimento', 'f', 'c'),
            ('fk_link_acesso_notificacao', 'f', 'c'),
            ('fk_link_acesso_resposta_formulario', 'f', 'c'),
            ('fk_link_acesso_cliente', 'f', 'c'),
            ('fk_link_acesso_profissional', 'f', 'c'),
        })
        self.assertEqual(tipo, 'character(64)')
        self.assertEqual(indices, {
            'pk_link_acesso', 'uq_link_acesso_token_hash', 'ix_link_acesso_atendimento_parcial',
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
                "OR (column_name = 'token' AND table_name IN ('notificacao', 'resposta_anamnese')))"
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
            ("UPDATE link_acesso SET criado_em = now() - interval '1 day' WHERE id = %s", link.pk),
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
        self.assertEqual(Consentimento.objects.filter(pk=linha.pk).minimizar_dados_de_origem(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._sql("UPDATE consentimento SET ip_origem = '10.1.1.1' WHERE id = %s", [linha.pk])
        # esquecimento completo pelo servico no PG (revoga + minimiza) e o CHECK de par
        consentimentos.esquecer(self.cli)
        self.assertFalse(consentimentos.vigente(self.cli, 'MARKETING', 'EMAIL'))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Consentimento.objects.create(cliente=self.cli, finalidade='MARKETING', canal='WHATSAPP',
                                         concedido=True, origem='PAINEL')

    def test_formulario_checks_e_um_prontuario_ativo(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT conname FROM pg_constraint WHERE conrelid = 'formulario_anamnese'::regclass "
                           "AND conname LIKE 'ck_formulario_%%'")
            checks = {r[0] for r in cursor.fetchall()}
            cursor.execute("SELECT pg_get_indexdef('uq_formulario_prontuario_ativo'::regclass)")
            indice = cursor.fetchone()[0]
        self.assertEqual(checks, {'ck_formulario_tipo', 'ck_formulario_escopo', 'ck_formulario_escopo_coerente',
                                  'ck_formulario_prontuario_global'})
        self.assertIn('UNIQUE INDEX', indice)
        FormularioAnamnese.objects.create(nome='Q', tipo='PRONTUARIO', schema_json=[])
        with self.assertRaises(IntegrityError), transaction.atomic():
            FormularioAnamnese.objects.create(nome='Q2', tipo='PRONTUARIO', schema_json=[])
        for sql in ("UPDATE formulario_anamnese SET obrigatorio = true WHERE tipo = 'PRONTUARIO'",
                    "UPDATE formulario_anamnese SET escopo = 'PROCEDIMENTO' WHERE tipo = 'PRONTUARIO'",
                    "UPDATE formulario_anamnese SET tipo = 'OUTRO' WHERE tipo = 'PRONTUARIO'"):
            with self.subTest(sql=sql), self.assertRaises(IntegrityError), transaction.atomic():
                self._sql(sql)

    def test_ledger_imutavel(self):
        carteira = Carteira.objects.create(cliente=self.cli, saldo=Decimal('10'))
        mov = MovimentoCarteira.objects.create(
            carteira=carteira, tipo='CREDITO', origem='OUTRO',
            valor=Decimal('10'), saldo_resultante=Decimal('10'),
        )
        # RAISE do plpgsql (P0001) -> InternalError, subclasse de DatabaseError
        with self.assertRaises(DatabaseError), transaction.atomic():
            MovimentoCarteira.objects.filter(pk=mov.pk).update(valor=Decimal('99'))
        with self.assertRaises(DatabaseError), transaction.atomic():
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
            ("UPDATE aceite_termo SET ip = '1.1.1.1' WHERE id = %s", aceite.pk),
            ('DELETE FROM aceite_termo WHERE id = %s', aceite.pk),
            ("UPDATE versao_termo SET conteudo = 'Outro texto' WHERE id = %s", termo.pk),
            ("UPDATE prontuario_versao SET dados = '{}'::jsonb WHERE id = %s", versao.pk),
            ('DELETE FROM prontuario_versao WHERE id = %s', versao.pk),
        )
        for sql, pk in bloqueados:
            # RAISE do plpgsql (P0001) -> InternalError, subclasse de DatabaseError
            with self.subTest(sql=sql), self.assertRaises(DatabaseError), transaction.atomic():
                self._sql(sql, [pk])
        # permitido: desativar o termo e o SET_NULL das FKs
        self._sql('UPDATE versao_termo SET ativa = false WHERE id = %s', [termo.pk])
        self._sql('UPDATE aceite_termo SET atendimento_id = NULL WHERE id = %s', [aceite.pk])
        aceite.refresh_from_db()
        self.assertEqual((aceite.ip, aceite.atendimento_id), (None, None))

    def test_checks_regex_cliente(self):
        for coluna, valor in (('telefone', 'abc'), ('telefone', '123'),
                              ('cpf', '123456789ab'), ('cpf', '1234')):
            with self.subTest(coluna=coluna, valor=valor), self.assertRaises(IntegrityError), \
                    transaction.atomic():
                self._sql(f'UPDATE cliente SET {coluna} = %s WHERE id = %s', [valor, self.cli.pk])
        # formato valido segue aceito
        self._sql('UPDATE cliente SET cpf = %s WHERE id = %s', ['12345678901', self.cli.pk])

    def test_checks_jsonb(self):
        # NOT VALID: nao revalida o legado, mas vale p/ toda escrita nova
        prontuario = Prontuario.objects.create(cliente=self.cli)
        formulario = FormularioAnamnese.objects.create(nome='Ficha', schema_json=[])
        resposta = RespostaAnamnese.objects.create(
            formulario=formulario, cliente=self.cli, respostas_json={},
        )
        casos = (
            ("UPDATE prontuario SET respostas_extras = '[]'::jsonb WHERE id = %s", prontuario.pk),
            ("UPDATE resposta_anamnese SET respostas_json = '[]'::jsonb WHERE id = %s", resposta.pk),
            ("UPDATE formulario_anamnese SET schema_json = '{}'::jsonb WHERE id = %s", formulario.pk),
        )
        for sql, pk in casos:
            with self.subTest(sql=sql), self.assertRaises(IntegrityError), transaction.atomic():
                self._sql(sql, [pk])

    def test_collation_nome(self):
        if os.environ.get('PG_EXIGE_ICU') != '1':
            # Sem ICU a 0038 so avisa. Nao usar to_regcollation('pt_br') como
            # gatilho: se o passo sumisse, o teste pularia em vez de reprovar.
            self.skipTest('collation pt_br so e exigida com PG_EXIGE_ICU=1 (CI)')
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name, collation_name FROM information_schema.columns "
                "WHERE column_name = 'nome' AND table_name IN ('cliente', 'profissional', 'procedimento')"
            )
            collations = dict(cursor.fetchall())
        self.assertEqual(
            collations, {'cliente': 'pt_br', 'profissional': 'pt_br', 'procedimento': 'pt_br'},
        )
