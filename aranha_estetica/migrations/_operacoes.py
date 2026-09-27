"""Operacoes de migration do padrao SQL (docs/specs/padrao-sql.md).

O prefixo "_" tira o modulo da lista de migrations (o loader do Django ignora
nomes que comecam com "_" ou "~").

- RenomearConstraint: troca so o NOME de uma constraint do estado do Django.
  No Postgres e um rename de catalogo (sem revalidar CHECK nem reconstruir
  indice): ALTER TABLE ... RENAME CONSTRAINT, ou ALTER INDEX ... RENAME quando
  a UniqueConstraint e parcial/por expressao (vira indice unico, nao
  constraint). No SQLite recria como o Django faria.
- AdicionarCheckNaoValidado: CHECK novo em tabela que ja tem dados de
  producao. No Postgres entra NOT VALID (vale para toda escrita nova) e, num
  savepoint, tenta o VALIDATE: com dado limpo termina validado; com dado fora
  do dominio fica NOT VALID, avisa em stderr e o upgrade segue. Nunca aborta o
  migrate_atomico. Validar depois: ALTER TABLE <t> VALIDATE CONSTRAINT <nome>.
"""
import sys

from django.db import transaction
from django.db.migrations.operations.base import Operation
from django.db.models import UniqueConstraint


def _constraint(state, app_label, model_name, nome):
    for c in state.models[app_label, model_name.lower()].options.get('constraints', []):
        if c.name == nome:
            return c
    raise ValueError(f'{model_name} nao tem a constraint {nome!r}')


def _eh_indice(constraint):
    """UniqueConstraint parcial/por expressao vira CREATE UNIQUE INDEX."""
    return isinstance(constraint, UniqueConstraint) and bool(
        constraint.condition or constraint.expressions
        or constraint.include or constraint.opclasses
    )


class RenomearConstraint(Operation):
    reduces_to_sql = False
    reversible = True

    def __init__(self, model_name, old_name, new_name):
        self.model_name = model_name
        self.old_name = old_name
        self.new_name = new_name

    def deconstruct(self):
        return (
            self.__class__.__name__, [],
            {'model_name': self.model_name, 'old_name': self.old_name, 'new_name': self.new_name},
        )

    def state_forwards(self, app_label, state):
        antiga = _constraint(state, app_label, self.model_name, self.old_name)
        nova = antiga.clone()
        nova.name = self.new_name
        state.remove_constraint(app_label, self.model_name.lower(), self.old_name)
        state.add_constraint(app_label, self.model_name.lower(), nova)

    def _renomear(self, app_label, schema_editor, from_state, to_state, de, para):
        from_model = from_state.apps.get_model(app_label, self.model_name)
        to_model = to_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, to_model):
            return
        antiga = _constraint(from_state, app_label, self.model_name, de)
        nova = _constraint(to_state, app_label, self.model_name, para)
        q = schema_editor.quote_name
        tabela = to_model._meta.db_table
        if schema_editor.connection.vendor == 'postgresql':
            if _eh_indice(antiga):
                schema_editor.execute(f'ALTER INDEX {q(de)} RENAME TO {q(para)}', None)
            else:
                schema_editor.execute(
                    f'ALTER TABLE {q(tabela)} RENAME CONSTRAINT {q(de)} TO {q(para)}', None,
                )
            return
        schema_editor.remove_constraint(from_model, antiga)
        schema_editor.add_constraint(to_model, nova)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        self._renomear(app_label, schema_editor, from_state, to_state, self.old_name, self.new_name)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        self._renomear(app_label, schema_editor, from_state, to_state, self.new_name, self.old_name)

    def describe(self):
        return f'Renomeia a constraint {self.old_name} -> {self.new_name} em {self.model_name}'

    @property
    def migration_name_fragment(self):
        return f'renomear_{self.new_name.lower()}'


class AdicionarCheckNaoValidado(Operation):
    reduces_to_sql = False
    reversible = True

    def __init__(self, model_name, constraint):
        self.model_name = model_name
        self.constraint = constraint

    def deconstruct(self):
        return (
            self.__class__.__name__, [],
            {'model_name': self.model_name, 'constraint': self.constraint},
        )

    def state_forwards(self, app_label, state):
        state.add_constraint(app_label, self.model_name.lower(), self.constraint)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return
        if schema_editor.connection.vendor != 'postgresql':
            schema_editor.add_constraint(model, self.constraint)
            return
        q = schema_editor.quote_name
        tabela, nome = model._meta.db_table, self.constraint.name
        check = self.constraint._get_check_sql(model, schema_editor)
        schema_editor.execute(
            f'ALTER TABLE {q(tabela)} ADD CONSTRAINT {q(nome)} CHECK ({check}) NOT VALID', None,
        )
        try:
            with transaction.atomic(using=schema_editor.connection.alias):
                schema_editor.execute(f'ALTER TABLE {q(tabela)} VALIDATE CONSTRAINT {q(nome)}', None)
        except Exception as exc:  # noqa: BLE001 — qualquer violacao deixa NOT VALID
            sys.stderr.write(
                f'AVISO {nome}: ha linhas em {tabela} fora do dominio; a constraint ficou '
                f'NOT VALID (so barra escrita nova). Corrigir os dados e rodar '
                f'ALTER TABLE {tabela} VALIDATE CONSTRAINT {nome}. ({exc})\n'
            )

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        model = from_state.apps.get_model(app_label, self.model_name)
        if self.allow_migrate_model(schema_editor.connection.alias, model):
            schema_editor.remove_constraint(model, self.constraint)

    def describe(self):
        return f'CHECK {self.constraint.name} em {self.model_name} (NOT VALID no Postgres)'

    @property
    def migration_name_fragment(self):
        return f'check_{self.constraint.name.lower()}'
