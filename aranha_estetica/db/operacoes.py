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

PadronizarFks(tabela, colunas) — o mesmo, mas so p/ as FKs de colunas NOVAS
numa tabela existente (0052+): o objeto novo nasce com o nome final sem
antecipar a fase de nomes no resto da tabela.

Fase de nomes (0054/0055, spec §3.6.2/§3.6.5), todas reversiveis:
  RenomearColuna(model_name, name, db_column)  — estado = db_column; banco = RENAME COLUMN
  RenomearConstraint(model_name, old_name, new_name) — constraint do Meta (PG: RENAME
      CONSTRAINT ou ALTER INDEX ... RENAME; SQLite: remove + recria)
  RenomearIndice(model_name, new_name, old_name) — RenameIndex + classe Indice no estado
  SoltarIndiceDeCampo(model_name, name, field)  — tira o db_index (indice automatico) sem
      apagar UNIQUE parcial do Meta na mesma coluna nem recriar a FK
  ComentarColuna(model_name, name, field)       — so COMMENT ON COLUMN (nada no SQLite)

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


class PadronizarFks(PadronizarNomes):
    """So as FKs de colunas NOVAS numa tabela existente (so PG, idempotente, reverso = no-op).

    Coluna FK acrescentada a uma tabela antiga (0052+: consumo_sessao.compra_pacote_item_id,
    movimento_comissao.movimento_comissao_origem_id/usuario_autor_id): o objeto novo ja nasce
    com o nome final (spec §6) — fk_<tabela>_<coluna sem _id> e a acao do on_delete — sem
    mexer no resto da tabela (PK, FKs, UNIQUE e sequencia antigos ficam p/ a fase de nomes,
    0054/0055). Mesma regra de nome/acao do PadronizarNomes; tambem executa o deferred_sql
    pendente (o AddField/AlterField deixam a FK p/ o fim da migration). Vai DEPOIS do
    AlterField que mexe na coluna (o Django solta e recria a FK com nome de hash).
    """

    def __init__(self, tabela, colunas, app_label='aranha_estetica'):
        self.tabela = tabela
        self.colunas = tuple(colunas)
        super().__init__(tabelas=[tabela], app_label=app_label)

    def deconstruct(self):
        kwargs = {'tabela': self.tabela, 'colunas': list(self.colunas)}
        if self.app_label != 'aranha_estetica':
            kwargs['app_label'] = self.app_label
        return (self.__class__.__qualname__, [], kwargs)

    def describe(self):
        return f'Padroniza nome/ON DELETE das FKs novas (so PG): {self.tabela}({", ".join(self.colunas)})'

    def _aplicar(self, apps, schema_editor):
        if schema_editor.connection.vendor != 'postgresql':
            return
        for sql in list(schema_editor.deferred_sql):
            schema_editor.execute(sql)
        schema_editor.deferred_sql.clear()
        modelo = next(
            m for m in apps.get_app_config(self.app_label).get_models()
            if m._meta.db_table == self.tabela
        )
        self._fks(schema_editor, self.tabela, modelo)

    def _fks(self, se, tabela, modelo):
        qn = se.quote_name
        campos = {
            f.column: f for f in modelo._meta.concrete_fields
            if f.is_relation and (f.many_to_one or f.one_to_one) and f.column in self.colunas
        }
        faltando = set(self.colunas) - set(campos)
        if faltando:
            raise ValueError(f'PadronizarFks: {tabela} sem FK no estado p/ {sorted(faltando)}')
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
                continue  # outra coluna: fica p/ a fase de nomes
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


# ═══════════════════════════════════════════════════════════════════════════
#  Fase de nomes (0054/0055) — nome so no banco (spec §1.1, §3.6.2)
# ═══════════════════════════════════════════════════════════════════════════

class RenomearColuna(migrations.operations.base.Operation):
    """So a COLUNA de um campo muda (o atributo Python fica): estado = ``db_column``,
    banco = ``ALTER TABLE ... RENAME COLUMN`` (reversivel).

    Por que nao o AlterField que o makemigrations gera: numa FK ele solta e recria a
    constraint (nome de hash, sem ON DELETE, revalida a tabela). O RENAME COLUMN e so
    catalogo no PG; no SQLite (>= 3.25) reescreve indices/CHECK/FK que citam a coluna.
    CHECK, indice, EXCLUDE e FK do PG guardam a coluna por attnum e seguem o rename;
    corpo de funcao PL/pgSQL NAO (e texto) — por isso a 0054 derruba e recria os triggers.
    """

    reversible = True

    def __init__(self, model_name, name, db_column):
        self.model_name = model_name
        self.name = name
        self.db_column = db_column

    @property
    def model_name_lower(self):
        return self.model_name.lower()

    def deconstruct(self):
        return (self.__class__.__qualname__, [],
                {'model_name': self.model_name, 'name': self.name, 'db_column': self.db_column})

    def state_forwards(self, app_label, state):
        campo = state.models[app_label, self.model_name_lower].fields[self.name]
        _nome_campo, _path, args, kwargs = campo.deconstruct()
        kwargs['db_column'] = self.db_column
        state.alter_field(app_label, self.model_name_lower, self.name, campo.__class__(*args, **kwargs), True)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        self._renomear(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        self._renomear(app_label, schema_editor, from_state, to_state)

    def _renomear(self, app_label, schema_editor, from_state, to_state):
        modelo = to_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, modelo):
            return
        antiga = from_state.apps.get_model(app_label, self.model_name)._meta.get_field(self.name).column
        nova = modelo._meta.get_field(self.name).column
        if antiga == nova:
            return
        tabela = modelo._meta.db_table
        qn = schema_editor.quote_name
        schema_editor.execute(f'ALTER TABLE {qn(tabela)} RENAME COLUMN {qn(antiga)} TO {qn(nova)}', None)
        for sql in schema_editor.deferred_sql:  # SQL pendente de operacoes anteriores
            if hasattr(sql, 'rename_column_references'):
                sql.rename_column_references(tabela, antiga, nova)

    def describe(self):
        return f'Renomeia a coluna de {self.model_name}.{self.name} para {self.db_column}'

    @property
    def migration_name_fragment(self):
        return f'{self.model_name_lower}_{self.name}_coluna'


def _constraint_e_indice(constraint):
    """UNIQUE parcial/de expressao/INCLUDE/deferrable vira CREATE UNIQUE INDEX (nao pg_constraint)."""
    return isinstance(constraint, models.UniqueConstraint) and bool(
        constraint.condition or constraint.contains_expressions or constraint.include or constraint.deferrable
    )


class RenomearConstraint(migrations.operations.base.Operation):
    """Renomeia uma constraint do ``Meta`` no estado e no banco (spec §3.6.2), reversivel.

    PG: ``ALTER TABLE ... RENAME CONSTRAINT`` (CHECK/UNIQUE; o indice do UNIQUE vai junto) ou
    ``ALTER INDEX ... RENAME`` quando o objeto e indice (UNIQUE parcial/de expressao) — decide
    pelo catalogo. SQLite (nao renomeia constraint): remove + recria pelo estado novo (a
    tabela e refeita; volume de teste).
    """

    reversible = True

    def __init__(self, model_name, old_name, new_name):
        self.model_name = model_name
        self.old_name = old_name
        self.new_name = new_name

    @property
    def model_name_lower(self):
        return self.model_name.lower()

    def deconstruct(self):
        return (self.__class__.__qualname__, [],
                {'model_name': self.model_name, 'old_name': self.old_name, 'new_name': self.new_name})

    def state_forwards(self, app_label, state):
        model_state = state.models[app_label, self.model_name_lower]
        nova = model_state.get_constraint_by_name(self.old_name).clone()
        nova.name = _nome(self.new_name)
        model_state.options['constraints'] = [
            nova if c.name == self.old_name else c for c in model_state.options['constraints']
        ]
        state.reload_model(app_label, self.model_name_lower, delay=True)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        self._renomear(app_label, schema_editor, from_state, to_state, self.old_name, self.new_name)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        self._renomear(app_label, schema_editor, from_state, to_state, self.new_name, self.old_name)

    def _renomear(self, app_label, schema_editor, from_state, to_state, antigo, novo):
        modelo = to_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, modelo):
            return
        tabela = modelo._meta.db_table
        qn = schema_editor.quote_name
        if schema_editor.connection.vendor == 'postgresql':
            with schema_editor.connection.cursor() as cursor:
                cursor.execute(
                    'SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = %s::regclass AND conname = %s), '
                    'EXISTS (SELECT 1 FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid '
                    '        WHERE x.indrelid = %s::regclass AND i.relname = %s)',
                    [tabela, antigo, tabela, antigo],
                )
                e_constraint, e_indice = cursor.fetchone()
            if e_constraint:
                schema_editor.execute(f'ALTER TABLE {qn(tabela)} RENAME CONSTRAINT {qn(antigo)} TO {qn(novo)}', None)
            elif e_indice:
                schema_editor.execute(f'ALTER INDEX {qn(antigo)} RENAME TO {qn(novo)}', None)
            else:
                raise ValueError(f'RenomearConstraint: {antigo!r} nao existe em {tabela}')
            return
        velha = from_state.models[app_label, self.model_name_lower].get_constraint_by_name(antigo)
        nova = to_state.models[app_label, self.model_name_lower].get_constraint_by_name(novo)
        # SQLite: CHECK/UNIQUE de tabela -> a tabela e refeita pelo modelo NOVO (ja com o nome
        # novo); UNIQUE parcial/de expressao -> DROP INDEX + CREATE UNIQUE INDEX.
        schema_editor.remove_constraint(modelo, velha)
        if _constraint_e_indice(velha):
            schema_editor.add_constraint(modelo, nova)

    def describe(self):
        return f'Renomeia a constraint {self.old_name} para {self.new_name} ({self.model_name})'

    @property
    def migration_name_fragment(self):
        return f'rename_{self.old_name}_{self.new_name}'


class RenomearIndice(migrations.RenameIndex):
    """``RenameIndex`` que tambem troca o objeto do estado por ``Indice`` (spec §3.6.2).

    O nome do padrao (``ix_<tabela>_<colunas>``) quase sempre passa dos 30 caracteres do
    ``models.E034``; o ``Indice`` (limite 63) e o mesmo ``models.Index``. Banco: o do
    RenameIndex (PG ``ALTER INDEX ... RENAME``; SQLite DROP + CREATE).
    """

    def __init__(self, model_name, new_name, old_name):
        super().__init__(model_name, _nome(new_name), old_name=old_name)

    def deconstruct(self):
        return (self.__class__.__qualname__, [],
                {'model_name': self.model_name, 'new_name': self.new_name, 'old_name': self.old_name})

    def state_forwards(self, app_label, state):
        from .indices import Indice

        model_state = state.models[app_label, self.model_name_lower]
        novos = []
        for indice in model_state.options['indexes']:
            if indice.name == self.old_name:
                _path, args, kwargs = indice.deconstruct()
                kwargs['name'] = self.new_name
                indice = Indice(*args, **kwargs)
            novos.append(indice)
        model_state.options['indexes'] = novos
        state.reload_model(app_label, self.model_name_lower, delay=True)


class SoltarIndiceDeCampo(migrations.AlterField):
    """``AlterField`` que so tira o ``db_index`` de um campo (e, se mudar, o COMMENT) — o indice
    passa a ser um ``ix_`` explicito no ``Meta`` ou some (spec §2.11, §3.6.2).

    PG e SQLite: ``DROP INDEX`` do indice automatico (btree NAO unico da coluna, achado por
    introspecao, excluindo os indices E as constraints do ``Meta``) e nada mais. Por que nao o
    AlterField do Django: o ``_alter_field`` exclui so os nomes de ``Meta.indexes`` e apagaria um
    UNIQUE parcial do ``Meta`` na mesma coluna (ex.: ``uq_atendimento_retorno_por_origem``,
    ``uq_formulario_prontuario_ativo``, ``uq_movimento_comissao_credito_por_atendimento``); numa
    FK ele ainda solta e recria a constraint; e no SQLite refaria a tabela inteira. Outros bancos:
    o AlterField normal. Reverso: recria o indice com o nome do Django (e o ``_like`` no PG).
    """

    VENDORS = ('postgresql', 'sqlite')

    def _campos(self, app_label, from_state, to_state):
        de = from_state.apps.get_model(app_label, self.model_name)
        para = to_state.apps.get_model(app_label, self.model_name)
        velho, novo = de._meta.get_field(self.name), para._meta.get_field(self.name)
        _n, pv, av, kv = velho.deconstruct()
        _n, pn, an, kn = novo.deconstruct()
        kv, kn = dict(kv), dict(kn)
        for attr in ('db_index', 'db_comment'):
            kv.pop(attr, None)
            kn.pop(attr, None)
        if (pv, av, kv) != (pn, an, kn):
            raise ValueError(f'SoltarIndiceDeCampo: {self.model_name}.{self.name} muda mais que db_index/db_comment')
        return de, para, velho, novo

    @staticmethod
    def _comentario(schema_editor, modelo, campo):
        if schema_editor.connection.features.supports_comments:
            sql, params = schema_editor._alter_column_comment_sql(
                modelo, campo, campo.db_type(schema_editor.connection), campo.db_comment,
            )
            if sql:
                schema_editor.execute(sql, params)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor not in self.VENDORS:
            return super().database_forwards(app_label, schema_editor, from_state, to_state)
        de, para, velho, novo = self._campos(app_label, from_state, to_state)
        if not self.allow_migrate_model(schema_editor.connection.alias, para):
            return
        if velho.db_index and not novo.db_index:
            do_meta = {i.name for i in de._meta.indexes} | {c.name for c in de._meta.constraints}
            for nome in schema_editor._constraint_names(
                de, [velho.column], index=True, unique=False, type_=models.Index.suffix, exclude=do_meta,
            ):
                schema_editor.execute(schema_editor._delete_index_sql(de, nome))
        if velho.db_comment != novo.db_comment:
            self._comentario(schema_editor, para, novo)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor not in self.VENDORS:
            return super().database_backwards(app_label, schema_editor, from_state, to_state)
        # reverso: from_state = sem indice, to_state = com indice
        de, para, velho, novo = self._campos(app_label, from_state, to_state)
        if not self.allow_migrate_model(schema_editor.connection.alias, para):
            return
        if novo.db_index and not velho.db_index:
            schema_editor.execute(schema_editor._create_index_sql(para, fields=[novo]))
            criar_like = getattr(schema_editor, '_create_like_index_sql', None)  # so o PG tem
            like = criar_like(para, novo) if criar_like else None
            if like is not None:
                schema_editor.execute(like)
        if velho.db_comment != novo.db_comment:
            self._comentario(schema_editor, para, novo)

    def describe(self):
        return f'Tira o indice automatico de {self.model_name}.{self.name} (ix_ explicito no Meta)'


class ComentarColuna(migrations.AlterField):
    """``AlterField`` em que so muda o ``db_comment``: no banco, so ``COMMENT ON COLUMN`` (§2).

    O AlterField do Django trata mudanca de comentario como mudanca de tipo e manda um
    ``ALTER COLUMN ... TYPE <mesmo tipo>`` junto (o PG reprocessa CHECK/indice/FK da coluna); e o
    SQLite (sem COMMENT) refaria a tabela inteira por nada. Aqui: PG = so o COMMENT; banco sem
    suporte a comentario = nada.
    """

    def _comentar(self, app_label, schema_editor, from_state, to_state):
        de = from_state.apps.get_model(app_label, self.model_name)
        para = to_state.apps.get_model(app_label, self.model_name)
        velho, novo = de._meta.get_field(self.name), para._meta.get_field(self.name)
        _n, pv, av, kv = velho.deconstruct()
        _n, pn, an, kn = novo.deconstruct()
        kv, kn = dict(kv), dict(kn)
        kv.pop('db_comment', None)
        kn.pop('db_comment', None)
        if (pv, av, kv) != (pn, an, kn):
            raise ValueError(f'ComentarColuna: {self.model_name}.{self.name} muda mais que o db_comment')
        if not self.allow_migrate_model(schema_editor.connection.alias, para):
            return
        if schema_editor.connection.features.supports_comments and velho.db_comment != novo.db_comment:
            sql, params = schema_editor._alter_column_comment_sql(
                para, novo, novo.db_type(schema_editor.connection), novo.db_comment,
            )
            if sql:
                schema_editor.execute(sql, params)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        self._comentar(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        self._comentar(app_label, schema_editor, from_state, to_state)

    def describe(self):
        return f'COMMENT ON COLUMN de {self.model_name}.{self.name}'
