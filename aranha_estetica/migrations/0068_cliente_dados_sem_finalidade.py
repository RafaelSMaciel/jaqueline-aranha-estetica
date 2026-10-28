# Cadastro da cliente sem dado sem finalidade (LGPD art. 6º III, necessidade): saem RG, profissao e
# url_foto. Ficam CPF, CEP e endereco (nota fiscal e contrato de pacote, art. 7º II e V).
#
# Ordem: RemoveField x3 -> PG: CREATE OR REPLACE sp_anonimizar_cliente sem as tres colunas
# (sql/v0068_anonimizar_cliente.py; o resto e o da 0066). Sem RunPython de dados: o valor dessas
# colunas some junto com elas (nenhuma tela, export ou relatorio as usa depois desta versao).
# Reverso (dev/testes): colunas voltam vazias e a procedure volta a da 0066.
from django.db import migrations

from aranha_estetica.migrations.sql import v0066_links as sql_0066
from aranha_estetica.migrations.sql import v0068_anonimizar_cliente as sql


def _executar(schema_editor, comandos):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for comando in comandos:
        schema_editor.execute(comando, None)  # params=None: o '%' do RAISE/LIKE nao e placeholder


def procedure_sem_colunas(apps, schema_editor):
    _executar(schema_editor, sql.CRIAR)


def procedure_da_0066(apps, schema_editor):
    _executar(schema_editor, [sql_0066.SP_ANONIMIZAR_CLIENTE])


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0067_formularios_notificacoes_modalidade'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='cliente',
            name='foto_url',
        ),
        migrations.RemoveField(
            model_name='cliente',
            name='profissao',
        ),
        migrations.RemoveField(
            model_name='cliente',
            name='rg',
        ),
        migrations.RunPython(procedure_sem_colunas, procedure_da_0066),
    ]
