# Remodelagem v2.1 — Fase 4 (docs/specs/remodelagem-banco-v2.md secao 6.1)
#
# A invariante central do negocio — "um profissional nao atende dois clientes
# ao mesmo tempo" — sai da aplicacao (4 copias divergentes de check-then-insert
# com phantom read) e vira lei no Postgres:
#
#   EXCLUDE USING gist (profissional_id WITH =,
#                       tstzrange(data_hora_inicio, data_hora_fim) WITH &&)
#   WHERE status IN ('PENDENTE','AGENDADO','CONFIRMADO')
#   DEFERRABLE INITIALLY IMMEDIATE
#
# Decisao de design: constraint por EXPRESSAO sobre as colunas existentes
# (data_hora_inicio/fim), NAO um campo range novo — zero query do codebase
# muda, ganho de integridade integral. DEFERRABLE permite swap atomico de
# horarios num reagendamento (SET CONSTRAINTS ... DEFERRED na transacao).
#
# Postgres-only via RunPython: SQLite (testes) nao tem GiST/EXCLUDE.
# O cache-lock e os checks de aplicacao continuam como otimizacao de UX;
# a garantia agora e do banco (IntegrityError 23P01 = horario ja reservado).

from django.db import migrations


def aplicar_exclusion_pg(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute('CREATE EXTENSION IF NOT EXISTS btree_gist')
    schema_editor.execute(
        "ALTER TABLE atendimento ADD CONSTRAINT excl_atendimento_sobreposicao "
        "EXCLUDE USING gist ("
        "  profissional_id WITH =, "
        "  tstzrange(data_hora_inicio, data_hora_fim) WITH &&"
        ") WHERE (status IN ('PENDENTE', 'AGENDADO', 'CONFIRMADO')) "
        "DEFERRABLE INITIALLY IMMEDIATE"
    )


def remover_exclusion_pg(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(
        'ALTER TABLE atendimento DROP CONSTRAINT IF EXISTS excl_atendimento_sobreposicao'
    )


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0034_remodelagem_fase3_constraints'),
    ]

    operations = [
        migrations.RunPython(aplicar_exclusion_pg, remover_exclusion_pg),
    ]
