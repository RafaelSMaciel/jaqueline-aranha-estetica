# Padrao SQL — fase de logica L3: procedures, fonte UNICA no Postgres (docs/specs/padrao-sql.md §5.6, §6)
#
# So Postgres (RunPython com checagem de vendor; SQL em migrations/sql/v0058_procedures.py, params=None):
#   sp_expirar_pacotes         <- tasks.job_expirar_pacotes
#   sp_anonimizar_cliente      <- LgpdService.esquecer_cliente (acao LGPD do Django admin)
#   sp_aplicar_retencao_lgpd   <- tasks.job_lgpd_purgar_inativos (COMMIT por cliente em autocommit)
#   sp_purgar_dados_expirados  <- tasks_manutencao.job_housekeeping (OTP, texto de notificacao,
#                                 auditoria, link_acesso vencido/revogado, historico_alteracao)
# O Python chama por utils/banco.chamar() quando usa_logica_pg(); o fallback Python fica so p/ o
# SQLite dos testes (e o teste de paridade, com FORCAR_LOGICA_PYTHON). Depende da 0057
# (vw_consentimento_atual, fn_data_local, fn_clientes_para_anonimizar).
#
# Sem estado do Django, sem DML, nao aborta. Reverso: DROP PROCEDURE IF EXISTS.
from django.db import migrations

from aranha_estetica.migrations.sql import v0058_procedures as sql


def _executar(schema_editor, comandos):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for comando in comandos:
        schema_editor.execute(comando, None)  # params=None: o '%' do RAISE/LIKE nao e placeholder


def criar_procedures(apps, schema_editor):
    _executar(schema_editor, sql.CRIAR)


def remover_procedures(apps, schema_editor):
    _executar(schema_editor, sql.REMOVER)


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0057_logica_views_funcoes'),
    ]

    operations = [
        migrations.RunPython(criar_procedures, remover_procedures),
    ]
