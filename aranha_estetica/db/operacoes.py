"""Operacoes de migration do padrao SQL (docs/specs/padrao-sql.md §3.6.2).

PadronizarNomes(tabelas=None) — so Postgres, idempotente, reverso = no-op.
O Django nomeia sozinho PK, FK, UNIQUE de campo e sequencia (hash/sufixo) e
cria a FK sem ``ON DELETE`` (a acao fica so no Python). Para cada tabela:

  PK             -> pk_<tabela>
  FK de 1 coluna -> fk_<tabela>_<coluna sem _id>, recriada com a acao do
                    on_delete do model (CASCADE / SET NULL; PROTECT, RESTRICT e
                    DO_NOTHING -> NO ACTION), DEFERRABLE INITIALLY DEFERRED como
                    o Django cria, NOT VALID + VALIDATE
  UNIQUE de 1 coluna fora do Meta -> uq_<tabela>_<coluna sem _id>
  sequencia do id -> <tabela>_id_seq
  indices *_pattern_ops (o "_like" do Django) -> DROP

Vai ao fim de toda migration que cria tabela (0048+), DEPOIS do DML e do
``SET CONSTRAINTS ALL IMMEDIATE`` (ALTER TABLE com trigger pendente falha).
Antes de introspectar, executa o ``deferred_sql`` pendente do schema editor (FK
e indices do CreateModel da mesma migration so seriam criados no fim dela).
O Django reencontra PK/FK/UNIQUE/sequencia por introspecao (§3.6.1), entao
renomear nao atrapalha migrations futuras.

Modulo CONGELADO (migrations aplicadas importam daqui): mudanca = classe nova.
"""
from django.db import migrations, models

LIMITE_NOME = 63  # NAMEDATALEN - 1: o PG trunca calado


def _acao_on_delete(on_delete):
    if on_delete is models.CASCADE:
        return 'c', 'CASCADE'
    if on_delete is models.SET_NULL:
        return 'n', 'SET NULL'
    return 'a', 'NO ACTION'


def _sem_id(coluna):
    return coluna[:-3] if coluna.endswith('_id') else coluna


def _nome(nome):
    if len(nome) > LIMITE_NOME:
        raise ValueError(f'PadronizarNomes: {nome!r} passa de {LIMITE_NOME} caracteres (spec §1.4)')
    return nome


class PadronizarNomes(migrations.RunPython):
    """Nomes de PK/FK/UNIQUE/sequencia no padrao + ON DELETE real (so PG)."""

    def __init__(self, tabelas=None, app_label='aranha_estetica'):
        self.tabelas = tuple(tabelas) if tabelas else None
        self.app_label = app_label
        super().__init__(self._aplicar, migrations.RunPython.noop)

    def deconstruct(self):
        kwargs = {}
        if self.tabelas:
            kwargs['tabelas'] = list(self.tabelas)
        if self.app_label != 'aranha_estetica':
            kwargs['app_label'] = self.app_label
        return (self.__class__.__qualname__, [], kwargs)

    def describe(self):
        alvo = ', '.join(self.tabelas) if self.tabelas else 'todas as tabelas do app'
        return f'Padroniza nomes de PK/FK/UNIQUE/sequencia (so PG): {alvo}'

    # ─── execucao ──────────────────────────────────────────────────────
    def _aplicar(self, apps, schema_editor):
        conn = schema_editor.connection
        if conn.vendor != 'postgresql':
            return
        # O CreateModel da MESMA migration deixa FK, indices e UNIQUE parcial em
        # schema_editor.deferred_sql (o Django so os cria no fim da migration):
        # executa agora, senao a FK nasceria depois, com nome de hash e sem ON DELETE.
        for sql in list(schema_editor.deferred_sql):
            schema_editor.execute(sql)
        schema_editor.deferred_sql.clear()
        modelos = {
            m._meta.db_table: m
            for m in apps.get_app_config(self.app_label).get_models()
            if m._meta.managed and not m._meta.proxy
        }
        for tabela in (self.tabelas or sorted(modelos)):
            modelo = modelos[tabela]
            self._pk(schema_editor, tabela)
            self._fks(schema_editor, tabela, modelo)
            self._uniques(schema_editor, tabela, modelo)
            self._sequencia(schema_editor, tabela, modelo)
            self._pattern_ops(schema_editor, tabela)

    @staticmethod
    def _linhas(schema_editor, sql, params):
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    def _pk(self, se, tabela):
        qn = se.quote_name
        for (atual,) in self._linhas(
            se, "SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass AND contype = 'p'",
            [tabela],
        ):
            novo = _nome(f'pk_{tabela}')
            if atual != novo:
                se.execute(f'ALTER TABLE {qn(tabela)} RENAME CONSTRAINT {qn(atual)} TO {qn(novo)}', None)

    def _fks(self, se, tabela, modelo):
        qn = se.quote_name
        campos = {
            f.column: f for f in modelo._meta.concrete_fields
            if f.is_relation and (f.many_to_one or f.one_to_one)
        }
        fks = self._linhas(se, """
            SELECT c.conname, a.attname, c.confrelid::regclass::text, af.attname,
                   c.confdeltype, c.condeferrable, c.condeferred
              FROM pg_constraint c
              JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
              JOIN pg_attribute af ON af.attrelid = c.confrelid AND af.attnum = c.confkey[1]
             WHERE c.conrelid = %s::regclass AND c.contype = 'f'
               AND array_length(c.conkey, 1) = 1
        """, [tabela])
        for atual, coluna, tabela_ref, coluna_ref, deltype, deferrable, deferred in fks:
            campo = campos.get(coluna)
            if campo is None:
                continue  # FK fora do estado do Django: nao e nossa
            codigo, acao = _acao_on_delete(campo.remote_field.on_delete)
            novo = _nome(f'fk_{tabela}_{_sem_id(coluna)}')
            if atual == novo and deltype == codigo and deferrable and deferred:
                continue
            se.execute(f'ALTER TABLE {qn(tabela)} DROP CONSTRAINT {qn(atual)}', None)
            se.execute(
                f'ALTER TABLE {qn(tabela)} ADD CONSTRAINT {qn(novo)} FOREIGN KEY ({qn(coluna)}) '
                f'REFERENCES {tabela_ref} ({qn(coluna_ref)}) ON DELETE {acao} '
                f'DEFERRABLE INITIALLY DEFERRED NOT VALID', None,
            )
            se.execute(f'ALTER TABLE {qn(tabela)} VALIDATE CONSTRAINT {qn(novo)}', None)

    def _uniques(self, se, tabela, modelo):
        qn = se.quote_name
        do_meta = {c.name for c in modelo._meta.constraints}
        for atual, coluna in self._linhas(se, """
            SELECT c.conname, a.attname
              FROM pg_constraint c
              JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
             WHERE c.conrelid = %s::regclass AND c.contype = 'u'
               AND array_length(c.conkey, 1) = 1
        """, [tabela]):
            novo = _nome(f'uq_{tabela}_{_sem_id(coluna)}')
            if atual in do_meta or atual == novo:
                continue
            se.execute(f'ALTER TABLE {qn(tabela)} RENAME CONSTRAINT {qn(atual)} TO {qn(novo)}', None)

    def _sequencia(self, se, tabela, modelo):
        pk = modelo._meta.pk
        if pk is None or not isinstance(pk, models.AutoField):
            return
        ((seq,),) = self._linhas(se, 'SELECT pg_get_serial_sequence(%s, %s)', [tabela, pk.column])
        if not seq:
            return
        novo = _nome(f'{tabela}_{pk.column}_seq')
        if seq.split('.')[-1].strip('"') != novo:
            se.execute(f'ALTER SEQUENCE {seq} RENAME TO {se.quote_name(novo)}', None)

    def _pattern_ops(self, se, tabela):
        for (indice,) in self._linhas(se, """
            SELECT DISTINCT i.relname
              FROM pg_index x
              JOIN pg_class i ON i.oid = x.indexrelid
              JOIN pg_opclass o ON o.oid = ANY (x.indclass::oid[])
             WHERE x.indrelid = %s::regclass
               AND o.opcname IN ('varchar_pattern_ops', 'text_pattern_ops', 'bpchar_pattern_ops')
        """, [tabela]):
            se.execute(f'DROP INDEX IF EXISTS {se.quote_name(indice)}', None)
