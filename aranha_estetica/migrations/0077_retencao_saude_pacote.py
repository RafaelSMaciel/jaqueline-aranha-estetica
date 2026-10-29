# P09 (decisoes 33 e 34): retencao LGPD aplicada no banco — saude por janela de 20 anos com expurgo,
# pacote retido 5 anos depois de expirar/cancelar, pseudonimizacao sem codigo de indicacao, indicador,
# IP do aceite LGPD e inscricao anonima da lista de espera; triggers de imutabilidade aceitam o
# expurgo so com app.expurgo_autorizado E 20 anos de idade. SQL em migrations/sql/v0077_retencao.py.
#
# So Postgres (RunPython com checagem de vendor; params=None). fn_clientes_para_anonimizar,
# sp_aplicar_retencao_lgpd e sp_purgar_dados_expirados mudam de assinatura (DROP + CREATE: um CREATE
# OR REPLACE com parametro novo criaria sobrecarga). Sem dado a migrar.
# Reverso: volta as versoes da 0057 (fn), 0058 (procedures de retencao/purga), 0074
# (sp_anonimizar_cliente), 0054 (fn_aceite_termo_imutavel, fn_prontuario_versao_imutavel) e 0056
# (fn_registrar_historico; anotacao_sessao de volta ao fn_bloquear_mutacao).
import importlib

from django.db import migrations

from aranha_estetica.migrations.sql import v0056_triggers as sql_0056
from aranha_estetica.migrations.sql import v0057_leitura as sql_0057
from aranha_estetica.migrations.sql import v0058_procedures as sql_0058
from aranha_estetica.migrations.sql import v0074_anonimizar_cliente as sql_0074
from aranha_estetica.migrations.sql import v0077_retencao as sql


def _executar(schema_editor, comandos):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for comando in comandos:
        schema_editor.execute(comando, None)  # params=None: o '%' do RAISE/LIKE nao e placeholder


def _funcao_da_0054(nome):
    """Texto da funcao como a 0054 criou (o reverso volta a ela)."""
    m0054 = importlib.import_module('aranha_estetica.migrations.0054_padrao_nomes_tabelas_colunas')
    return next(s for s in m0054.SQL_FUNCOES if f'FUNCTION {nome}()' in s)


def aplicar(apps, schema_editor):
    _executar(schema_editor, sql.CRIAR)


def reverter(apps, schema_editor):
    _executar(schema_editor, [
        *sql.DROPAR_ASSINATURAS_NOVAS,
        sql_0057.FN_CLIENTES_PARA_ANONIMIZAR,
        sql_0074.SP_ANONIMIZAR_CLIENTE,
        sql_0058.SP_APLICAR_RETENCAO_LGPD,
        sql_0058.SP_PURGAR_DADOS_EXPIRADOS,
        _funcao_da_0054('fn_prontuario_versao_imutavel'),
        _funcao_da_0054('fn_aceite_termo_imutavel'),
        'DROP TRIGGER IF EXISTS tg_anotacao_sessao_imutavel ON anotacao_sessao',
        sql_0056.TG_ANOTACAO_SESSAO_IMUTAVEL,
        'DROP FUNCTION IF EXISTS fn_anotacao_sessao_imutavel()',
        sql_0056.FN_REGISTRAR_HISTORICO,
    ])


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0076_lista_espera_contato'),
    ]

    operations = [
        migrations.RunPython(aplicar, reverter),
    ]
