# Trilhas append-only no banco: log_auditoria imutavel + expurgo so por idade (LGPD art. 37/46)
#
# So Postgres (RunPython com checagem de vendor; SQL em migrations/sql/v0059_trilha_append_only.py):
#   fn_log_auditoria_imutavel / tg_log_auditoria_imutavel  <- novo: so purga com 180+ dias,
#       pseudonimizacao (acao com "[ANONIMIZADO-<id>]", detalhes NULL) e SET NULL do autor
#   fn_historico_alteracao_imutavel                        <- CREATE OR REPLACE: a flag
#       app.expurgo_autorizado so apaga o que tem 180+ dias (prontuario: 20 anos)
# Mais o COMMENT da tabela (estado do Django). Sem DML, nao aborta. Reverso: devolve a funcao
# da 0056 e solta o trigger novo.
from django.db import migrations

from aranha_estetica.migrations.sql import v0056_triggers as sql_0056
from aranha_estetica.migrations.sql import v0059_trilha_append_only as sql


def _executar(schema_editor, comandos):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for comando in comandos:
        schema_editor.execute(comando, None)  # params=None: o '%' do RAISE nao e placeholder


def criar(apps, schema_editor):
    _executar(schema_editor, sql.CRIAR)


def remover(apps, schema_editor):
    _executar(schema_editor, [
        'DROP TRIGGER IF EXISTS tg_log_auditoria_imutavel ON log_auditoria',
        'DROP FUNCTION IF EXISTS fn_log_auditoria_imutavel()',
        sql_0056.FN_HISTORICO_ALTERACAO_IMUTAVEL,
    ])


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0058_logica_procedures'),
    ]

    operations = [
        migrations.RunPython(criar, remover),
        migrations.AlterModelTableComment(
            name='logauditoria',
            table_comment=(
                'Trilha de ACOES da aplicacao (LGPD art. 37): autor + snapshot "Nome <email>", acao, alvo, IP. '
                'Append-only (tg_log_auditoria_imutavel: so purga com 180+ dias, pseudonimizacao e SET NULL do '
                'autor). Retencao 5 anos. A trilha de DADOS (antes/depois) fica em historico_alteracao.'
            ),
        ),
    ]
