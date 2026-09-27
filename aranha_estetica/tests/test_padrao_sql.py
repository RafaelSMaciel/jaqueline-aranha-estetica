"""Padrao SQL (docs/specs/padrao-sql.md): nomes, dominio dos choices e indices.

Roda no SQLite e no Postgres. A parte que confere o catalogo real (nome no
banco == nome no estado do Django, CHECKs validados) so roda no Postgres.
"""
import re
from unittest import skipUnless

from django.apps import apps
from django.db import connection, models
from django.test import SimpleTestCase, TestCase

MODELS = list(apps.get_app_config('aranha_estetica').get_models())

# Sigla registrada p/ indice quando idx_<tabela>_<colunas> passa de 30 chars
# (limite do Django, models.E034). Constraints usam sempre a tabela inteira.
SIGLAS_INDICE = {
    'formulario_anamnese': 'form_anamnese',
    'resposta_anamnese': 'resp_anamnese',
    'movimento_carteira': 'mov_carteira',
    'movimento_comissao': 'mov_comissao',
}
# Tabelas da agenda: a Fase 7 da remodelagem troca as tres por agenda_horario
# e agenda_excecao, entao os nomes delas ficam como estao ate la.
AGENDA_FASE_7 = {'disponibilidade_profissional', 'bloqueio_agenda', 'excecao_disponibilidade'}
# choices sem CHECK de dominio (tipo da excecao ja e limitado por
# chk_excecao_horario_completo; a tabela sai na Fase 7)
CHOICES_SEM_CHECK = {('excecao_disponibilidade', 'tipo')}

SNAKE = re.compile(r'^[a-z][a-z0-9]*(_[a-z0-9]+)*$')


def _campos_da_condicao(q):
    """(lookup, valor) de todos os filhos de um Q, recursivo."""
    for filho in q.children:
        if isinstance(filho, models.Q):
            yield from _campos_da_condicao(filho)
        else:
            yield filho


class NomesTests(SimpleTestCase):
    def test_tabelas_e_colunas_snake_case_com_db_table_e_comentario(self):
        for model in MODELS:
            meta = model._meta
            with self.subTest(model=model.__name__):
                self.assertRegex(meta.db_table, SNAKE)
                self.assertTrue(meta.db_table_comment, f'{meta.db_table} sem db_table_comment')
                for field in meta.local_fields:
                    self.assertRegex(field.column, SNAKE)

    def test_indices(self):
        for model in MODELS:
            tabela = model._meta.db_table
            prefixos = (f'idx_{tabela}_', f'idx_{SIGLAS_INDICE.get(tabela, tabela)}_')
            for index in model._meta.indexes:
                with self.subTest(index=index.name):
                    self.assertRegex(index.name, SNAKE)
                    self.assertLessEqual(len(index.name), 30)
                    if tabela not in AGENDA_FASE_7:
                        self.assertTrue(index.name.startswith(prefixos), index.name)

    def test_constraints(self):
        for model in MODELS:
            tabela = model._meta.db_table
            self.assertFalse(model._meta.unique_together, f'{tabela}: use UniqueConstraint nomeada')
            for c in model._meta.constraints:
                with self.subTest(constraint=c.name):
                    self.assertRegex(c.name, SNAKE)
                    self.assertLessEqual(len(c.name), 63)
                    prefixo = 'uniq' if isinstance(c, models.UniqueConstraint) else 'chk'
                    if tabela not in AGENDA_FASE_7:
                        self.assertTrue(c.name.startswith(f'{prefixo}_{tabela}_'), c.name)


class DominioTests(SimpleTestCase):
    def test_todo_campo_com_choices_tem_check_de_dominio(self):
        for model in MODELS:
            tabela = model._meta.db_table
            checks = [c for c in model._meta.constraints if isinstance(c, models.CheckConstraint)]
            for field in model._meta.local_fields:
                if not field.choices or isinstance(field, models.BooleanField):
                    continue
                if (tabela, field.name) in CHOICES_SEM_CHECK:
                    continue
                esperado = {valor for valor, _ in field.choices}
                with self.subTest(campo=f'{tabela}.{field.name}'):
                    dominios = [
                        set(valor)
                        for c in checks
                        for lookup, valor in _campos_da_condicao(c.condition)
                        if lookup == f'{field.name}__in'
                    ]
                    self.assertIn(esperado, dominios, 'CHECK ausente ou fora de sincronia com os choices')


class IndicesTests(SimpleTestCase):
    def test_fk_sem_indice_redundante(self):
        """FK com indice proprio nao pode ser prefixo de indice composto/UNIQUE."""
        for model in MODELS:
            meta = model._meta
            compostos = [list(i.fields) for i in meta.indexes if i.fields] + [
                list(c.fields) for c in meta.constraints
                if isinstance(c, models.UniqueConstraint) and c.fields and c.condition is None
            ]
            for field in meta.local_fields:
                if not field.is_relation or not field.db_index or field.unique:
                    continue
                if meta.db_table in AGENDA_FASE_7:
                    continue
                with self.subTest(fk=f'{meta.db_table}.{field.name}'):
                    lideres = [c for c in compostos if c[0].lstrip('-') == field.name]
                    self.assertFalse(lideres, f'indice da FK ja coberto por {lideres}; use db_index=False')


@skipUnless(connection.vendor == 'postgresql', 'catalogo do Postgres')
class CatalogoPostgresTests(TestCase):
    def test_nomes_do_estado_existem_no_banco(self):
        with connection.cursor() as cur:
            cur.execute("SELECT relname FROM pg_class WHERE relkind = 'i'")
            indices = {r[0] for r in cur.fetchall()}
            cur.execute('SELECT conname FROM pg_constraint')
            constraints = {r[0] for r in cur.fetchall()}
        for model in MODELS:
            for index in model._meta.indexes:
                self.assertIn(index.name, indices)
            for c in model._meta.constraints:
                # UniqueConstraint parcial/por expressao vira indice unico
                self.assertIn(c.name, constraints | indices)

    def test_checks_de_dominio_validados_em_banco_novo(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT conname FROM pg_constraint WHERE contype = 'c' AND NOT convalidated "
                "AND conname NOT IN ('chk_prontuario_extras_objeto', 'chk_resposta_anamnese_objeto', "
                "'chk_formulario_anamnese_schema_lista')"
            )
            self.assertEqual(cur.fetchall(), [])

    def test_comentarios_no_catalogo(self):
        with connection.cursor() as cur:
            for model in MODELS:
                cur.execute('SELECT obj_description(%s::regclass, %s)', [model._meta.db_table, 'pg_class'])
                self.assertEqual(cur.fetchone()[0], model._meta.db_table_comment)
