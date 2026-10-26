"""Conformidade do padrao de nomes no catalogo do Postgres (docs/specs/padrao-sql.md §1, §3.6.4).

Le o pg_catalog do banco de teste (migrado do zero pelas migrations) e reprova, listando os
ofensores: objeto fora do padrao (pk_/fk_/uq_/ck_/ix_/ex_/tg_, fn_/sp_, vw_, <t>_id_seq), nome
de objeto do Meta ausente/truncado (> 63) no banco, FK sem indice, ON DELETE divergente do
on_delete do model (ou FK sem DEFERRABLE INITIALLY DEFERRED), tabela sem COMMENT, indice
*_pattern_ops (o "_like" do Django), coluna _id que nao e FK e nome fora de [a-z0-9_].

Tabelas de terceiros (auth_*, django_*, axes_*, otp_*: §1.5) ficam fora por allowlist.
"tabela com atualizado_em sem tg_<t>_atualizado_em" so vale a partir da fase de logica (L1, 0056):
o teste fica pulado ate a migration existir.

Quando reprovar depois de um AlterField novo numa FK (o Django recria a FK com nome de hash e sem
ON DELETE, §3.6.1 item 4): acrescente PadronizarNomes() ao fim da migration (e idempotente).
"""
import re
from unittest import skipUnless

from django.apps import apps
from django.db import connection, models
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

PG = connection.vendor == 'postgresql'
APP = 'aranha_estetica'
LIMITE = 63  # NAMEDATALEN - 1
NOME_OK = re.compile(r'^[a-z][a-z0-9_]*$')
# FK de autoria sem indice (spec §2.11 item 2): usuario nunca e excluido e nenhuma tela filtra por autor
AUTORIA_SEM_INDICE = {
    ('prontuario_versao', 'usuario_autor_id'), ('anotacao_sessao', 'usuario_autor_id'),
    ('movimento_carteira', 'usuario_autor_id'), ('movimento_comissao', 'usuario_autor_id'),
    ('log_auditoria', 'usuario_autor_id'),
}
# objetos so-PG (fora do estado do Django) que o padrao exige
SO_PG = {
    'ex_atendimento_sobreposicao', 'ex_agenda_horario_sobreposicao', 'ex_agenda_excecao_folga_sobreposta',
    'ck_cliente_telefone_formato', 'ck_cliente_cpf_formato', 'ck_prontuario_respostas_objeto',
    'ck_resposta_formulario_respostas_objeto', 'ck_formulario_perguntas_lista', 'ck_log_auditoria_detalhes_objeto',
    'ck_prontuario_versao_dados_objeto', 'ck_aceite_termo_conteudo_hash_formato', 'ck_codigo_otp_codigo_hash_formato',
    'ck_link_acesso_token_hash_formato', 'ck_agenda_excecao_regra_recorrencia_suportada',
    'fk_consumo_sessao_compra_pacote_item_compra',
}


def _modelos():
    return [m for m in apps.get_app_config(APP).get_models() if m._meta.managed and not m._meta.proxy]


def _fase_logica_existe():
    """A L1 (0056) cria fn_definir_atualizado_em + tg_<t>_atualizado_em nas 22 tabelas (§5.2)."""
    if not PG:
        return False
    loader = MigrationLoader(None, ignore_no_migrations=True)
    return any(app == APP and nome[:4] >= '0056' for app, nome in loader.disk_migrations)


def _acao_esperada(on_delete):
    if on_delete is models.CASCADE:
        return 'c'
    if on_delete is models.SET_NULL:
        return 'n'
    return 'a'  # PROTECT / RESTRICT / DO_NOTHING -> NO ACTION (§1.3)


@skipUnless(PG, 'catalogo do Postgres (pg_constraint, pg_index, pg_trigger, pg_proc)')
class NomesPadraoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tabelas = sorted(m._meta.db_table for m in _modelos())

    def _linhas(self, sql, params=None):
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    def _falhar_se(self, ofensores, regra):
        self.assertEqual(sorted(ofensores), [], f'{regra}: {len(ofensores)} ofensor(es)')

    # ─── tabelas e colunas ──────────────────────────────────────────────
    def test_tabelas_do_app_tem_comment(self):
        sem = [t for (t,) in self._linhas(
            "SELECT relname FROM pg_class WHERE oid = ANY(%s::regclass[]) "
            "AND coalesce(obj_description(oid, 'pg_class'), '') = ''", [self.tabelas])]
        self._falhar_se(sem, 'tabela sem COMMENT')

    def test_nomes_de_tabela_e_coluna_ascii_snake_case(self):
        ruins = [f'{t}.{c}' for t, c in self._linhas(
            'SELECT attrelid::regclass::text, attname FROM pg_attribute '
            'WHERE attrelid = ANY(%s::regclass[]) AND attnum > 0 AND NOT attisdropped', [self.tabelas])
            if not (NOME_OK.match(t) and NOME_OK.match(c))]
        self._falhar_se(ruins, 'nome fora de [a-z0-9_]')

    def test_coluna_id_e_sempre_fk(self):
        """_id e reservado a FK real (§1.2): id externo = _ref, polimorfica = registro_pk."""
        fks = {(t, c) for t, c in self._linhas(
            "SELECT conrelid::regclass::text, a.attname FROM pg_constraint c "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
            "WHERE c.contype = 'f' AND c.conrelid = ANY(%s::regclass[])", [self.tabelas])}
        soltas = [f'{t}.{c}' for t, c in self._linhas(
            "SELECT attrelid::regclass::text, attname FROM pg_attribute WHERE attrelid = ANY(%s::regclass[]) "
            "AND attnum > 0 AND NOT attisdropped AND attname LIKE '%%\\_id'", [self.tabelas])
            if (t, c) not in fks]
        self._falhar_se(soltas, 'coluna *_id sem FK')

    # ─── constraints e indices ──────────────────────────────────────────
    def test_constraints_no_padrao(self):
        prefixo = {'p': 'pk', 'f': 'fk', 'u': 'uq', 'c': 'ck', 'x': 'ex'}
        ruins = []
        for tabela, nome, tipo, colunas in self._linhas(
            "SELECT conrelid::regclass::text, conname, contype, "
            "       (SELECT array_agg(a.attname::text ORDER BY k.ord) FROM unnest(c.conkey) WITH ORDINALITY k(n, ord) "
            "          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n) "
            "  FROM pg_constraint c WHERE conrelid = ANY(%s::regclass[]) AND contype IN ('p','f','u','c','x')",
            [self.tabelas],
        ):
            esperado = f'{prefixo[tipo]}_{tabela}'
            ok = nome == esperado if tipo == 'p' else nome.startswith(esperado + '_')
            if tipo == 'f' and colunas and len(colunas) == 1:
                coluna = colunas[0][:-3] if colunas[0].endswith('_id') else colunas[0]
                ok = nome == f'fk_{tabela}_{coluna}'
            if not ok or len(nome) > LIMITE:
                ruins.append(f'{tabela}: {nome} ({tipo})')
        self._falhar_se(ruins, 'constraint fora do padrao')

    def test_indices_no_padrao_e_sem_like(self):
        ruins, like = [], []
        for tabela, nome, unico, e_constraint, opclasses in self._linhas(
            "SELECT x.indrelid::regclass::text, i.relname, x.indisunique, "
            "       EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = x.indexrelid), "
            "       (SELECT array_agg(o.opcname::text) FROM pg_opclass o WHERE o.oid = ANY (x.indclass::oid[])) "
            "  FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid WHERE x.indrelid = ANY(%s::regclass[])",
            [self.tabelas],
        ):
            if any(o.endswith('_pattern_ops') for o in (opclasses or [])):
                like.append(f'{tabela}: {nome}')
            if e_constraint:
                continue  # nome = o da constraint (pk_/uq_/ex_), conferido acima
            if not nome.startswith((f'uq_{tabela}_' if unico else f'ix_{tabela}_')):
                ruins.append(f'{tabela}: {nome}')
        self._falhar_se(ruins, 'indice fora do padrao (ix_<t>_ / uq_<t>_)')
        self._falhar_se(like, 'indice *_pattern_ops (_like do Django)')

    def test_objetos_do_meta_e_so_pg_existem_com_o_nome_exato(self):
        """Nome > 63 e truncado calado pelo PG; e um AlterField pode apagar indice sem avisar."""
        no_banco = {nome for (nome,) in self._linhas(
            'SELECT conname FROM pg_constraint WHERE conrelid = ANY(%s::regclass[]) '
            'UNION SELECT i.relname FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid '
            'WHERE x.indrelid = ANY(%s::regclass[])', [self.tabelas, self.tabelas])}
        declarados = {o.name for m in _modelos() for o in (*m._meta.indexes, *m._meta.constraints)}
        longos = [n for n in declarados if len(n) > LIMITE]
        self._falhar_se(longos, 'nome do Meta > 63')
        self._falhar_se(declarados - no_banco, 'objeto do Meta ausente no banco')
        self._falhar_se(SO_PG - no_banco, 'objeto so-PG ausente no banco')

    def test_fk_com_indice(self):
        indices = {}
        for tabela, chave in self._linhas(
            'SELECT indrelid::regclass::text, pg_get_indexdef(indexrelid, 1, true) FROM pg_index '
            'WHERE indrelid = ANY(%s::regclass[])', [self.tabelas],
        ):
            indices.setdefault(tabela, []).append(chave.strip('"'))
        sem = []
        for tabela, nome, primeira in self._linhas(
            "SELECT conrelid::regclass::text, conname, a.attname FROM pg_constraint c "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
            "WHERE c.contype = 'f' AND c.conrelid = ANY(%s::regclass[])", [self.tabelas],
        ):
            if (tabela, primeira) in AUTORIA_SEM_INDICE:
                continue
            # 1a chave = a coluna, ou expressao sobre ela (o EXCLUDE gist da agenda; no PG sem
            # btree_gist do harness local ela vira int8range(col, col))
            if not any(k == primeira or re.search(rf'\b{primeira}\b', k) for k in indices.get(tabela, [])):
                sem.append(f'{tabela}.{primeira} ({nome})')
        self._falhar_se(sem, 'FK sem indice cujo prefixo seja a coluna')

    def test_on_delete_real_e_deferrable(self):
        esperado = {}
        for m in _modelos():
            for f in m._meta.concrete_fields:
                if f.is_relation and (f.many_to_one or f.one_to_one):
                    esperado[(m._meta.db_table, f.column)] = _acao_esperada(f.remote_field.on_delete)
        ruins = []
        for tabela, nome, colunas, acao, deferrable, deferred in self._linhas(
            "SELECT conrelid::regclass::text, conname, "
            "       (SELECT array_agg(a.attname::text) FROM pg_attribute a "
            "         WHERE a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)), "
            "       confdeltype, condeferrable, condeferred "
            "  FROM pg_constraint c WHERE c.contype = 'f' AND c.conrelid = ANY(%s::regclass[])", [self.tabelas],
        ):
            alvo = esperado.get((tabela, colunas[0])) if len(colunas) == 1 else 'a'
            if alvo is None:
                ruins.append(f'{tabela}: {nome} sem campo no model')
            elif acao != alvo or not (deferrable and deferred):
                ruins.append(f'{tabela}: {nome} ON DELETE {acao!r} (esperado {alvo!r}), deferred={deferred}')
        self._falhar_se(ruins, 'FK com ON DELETE divergente do on_delete do model')

    # ─── sequencias, triggers, funcoes, views ───────────────────────────
    def test_sequencias(self):
        ruins = []
        for tabela in self.tabelas:
            ((seq,),) = self._linhas("SELECT pg_get_serial_sequence(%s, 'id')", [tabela])
            if seq and seq != f'public.{tabela}_id_seq':
                ruins.append(f'{tabela}: {seq}')
        self._falhar_se(ruins, 'sequencia fora de <tabela>_id_seq')

    def test_triggers_funcoes_e_views(self):
        triggers = [f'{t}: {n}' for t, n in self._linhas(
            'SELECT tgrelid::regclass::text, tgname FROM pg_trigger '
            'WHERE NOT tgisinternal AND tgrelid = ANY(%s::regclass[])', [self.tabelas])
            if not n.startswith(f'tg_{t}_')]
        rotinas = [n for (n,) in self._linhas(
            "SELECT p.proname FROM pg_proc p JOIN pg_namespace s ON s.oid = p.pronamespace "
            "WHERE s.nspname = 'public' AND NOT EXISTS (SELECT 1 FROM pg_depend d "
            "WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')")
            if not re.match(r'^(fn|sp)_', n)]
        views = [n for (n,) in self._linhas(
            "SELECT viewname FROM pg_views WHERE schemaname = 'public'") if not n.startswith('vw_')]
        self._falhar_se(triggers, 'trigger fora de tg_<tabela>_<acao>')
        self._falhar_se(rotinas, 'funcao/procedure fora de fn_/sp_')
        self._falhar_se(views, 'view fora de vw_')

    @skipUnless(_fase_logica_existe(), 'vale a partir da fase de logica (L1, 0056: tg_<t>_atualizado_em)')
    def test_atualizado_em_tem_trigger(self):
        com_coluna = {t for (t,) in self._linhas(
            "SELECT attrelid::regclass::text FROM pg_attribute WHERE attrelid = ANY(%s::regclass[]) "
            "AND attname = 'atualizado_em' AND NOT attisdropped", [self.tabelas])}
        com_trigger = {t for (t,) in self._linhas(
            "SELECT tgrelid::regclass::text FROM pg_trigger WHERE NOT tgisinternal "
            "AND tgname = 'tg_' || tgrelid::regclass::text || '_atualizado_em'")}
        self._falhar_se(com_coluna - com_trigger, 'tabela com atualizado_em sem tg_<t>_atualizado_em')
