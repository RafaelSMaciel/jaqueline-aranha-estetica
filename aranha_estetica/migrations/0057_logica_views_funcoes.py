# Padrao SQL — fase de logica L2: extensoes, utilitarias, busca trigram, funcoes de leitura e views
# (docs/specs/padrao-sql.md §2.0, §5.1, §5.4, §5.5, §6)
#
# So Postgres (RunPython com checagem de vendor; SQL em migrations/sql/v0057_leitura.py, params=None):
#   1. CREATE EXTENSION IF NOT EXISTS pg_trgm, unaccent (WITH SCHEMA public). Sem privilegio/sem
#      contrib a migration FALHA de proposito (melhor que seguir sem o indice) — o Railway (PG 18)
#      tem as duas e o usuario e dono do banco (extensoes "trusted").
#   2. fn_fuso_clinica, fn_data_local, fn_sem_acento (IMMUTABLE: wrapper do unaccent com dicionario
#      explicito) + ix_cliente_nome_trgm (GIN trigram sobre fn_sem_acento(nome); a busca de cliente
#      do painel/admin usa a mesma expressao — utils/busca.filtrar_clientes)
#   3. 8 funcoes de leitura (espelho do Python, travadas pelo tests/test_pg_paridade.py):
#      fn_recorrencia_casa_dia, fn_ocorrencias_recorrencia, fn_horarios_livres, fn_saldo_pacote,
#      fn_nps_periodo, fn_saldo_comissao, fn_tem_consentimento, fn_clientes_para_anonimizar
#   4. 9 views de relatorio (o app nao le views): vw_agenda_dia, vw_faturamento_mensal,
#      vw_nps_mensal, vw_comissao, vw_comissao_saldo, vw_cliente_resumo, vw_consentimento_atual,
#      vw_pacote_saldo, vw_atendimento_sem_desfecho
#
# Sem estado do Django (nada no SQLite: o app nao depende destes objetos). Sem DML. Nao aborta
# (exceto sem as extensoes). Reverso: DROP ... IF EXISTS de tudo (extensoes ficam, como o
# btree_gist da 0035).
from django.db import migrations

from aranha_estetica.migrations.sql import v0057_leitura as sql


def _executar(schema_editor, comandos):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for comando in comandos:
        schema_editor.execute(comando, None)  # params=None: o '%' do RAISE/LIKE nao e placeholder


def criar_logica(apps, schema_editor):
    _executar(schema_editor, sql.CRIAR)


def remover_logica(apps, schema_editor):
    _executar(schema_editor, sql.REMOVER)


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0056_logica_triggers_historico'),
    ]

    operations = [
        migrations.RunPython(criar_logica, remover_logica),
    ]
