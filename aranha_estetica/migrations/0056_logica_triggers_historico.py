# Padrao SQL — fase de logica L1: triggers + trilha de alteracoes (docs/specs/padrao-sql.md §5.2, §5.3, §6)
#
# Ordem (§6, L1):
#   1. CreateModel HistoricoAlteracao (tabela historico_alteracao; CHECKs/indices do Meta existem
#      tambem no SQLite) + PadronizarNomes (pk_/fk_ com ON DELETE SET NULL/sequencia)
#   2. PG (RunPython so-Postgres, SQL em migrations/sql/v0056_triggers.py, params=None):
#      - ck_historico_alteracao_colunas_alteradas_lista (jsonb_typeof = 'array')
#      - fn_definir_atualizado_em + tg_<t>_atualizado_em nas 22 tabelas com atualizado_em
#        (QuerySet.update()/procedures/SQL manual passam a atualizar a coluna; o ORM ja manda)
#      - tg_anotacao_sessao_imutavel (fn_bloquear_mutacao da 0052: nota clinica append-only)
#      - fn_historico_alteracao_imutavel + tg_historico_alteracao_imutavel (append-only; DELETE so
#        com a flag local app.expurgo_autorizado da purga de retencao)
#      - fn_registrar_historico + tg_<t>_historico (AFTER INSERT/UPDATE/DELETE, nao deferido) nas 11
#        tabelas sensiveis; autor/IP/origem das GUCs app.* (ContextoBancoMiddleware, cron, Celery,
#        migrate_atomico = 'migracao'); usuario.senha_hash redigida
#
# Sem DML: no upgrade 0026 -> 0058 (migrate_atomico, uma transacao) a trilha e o atualizado_em
# nascem DEPOIS de todo DML das migrations anteriores — nenhuma linha de historico_alteracao vem
# da migracao (spec §6.1 item 2). Nao aborta.
#
# Reverso: derruba triggers e funcoes (fn_bloquear_mutacao fica: e da 0052) e a tabela.
from django.conf import settings
from django.db import migrations, models

import aranha_estetica.db.indices
import django.db.models.deletion
import django.db.models.functions.datetime
from aranha_estetica.db.operacoes import PadronizarNomes
from aranha_estetica.migrations.sql import v0056_triggers as sql


def _executar(schema_editor, comandos):
    if schema_editor.connection.vendor != 'postgresql':
        return  # SQLite dos testes: o Python cobre (auto_now, save()/delete() que recusam)
    for comando in comandos:
        schema_editor.execute(comando, None)  # params=None: o '%' do RAISE nao e placeholder


def criar_logica(apps, schema_editor):
    _executar(schema_editor, sql.CRIAR)


def remover_logica(apps, schema_editor):
    _executar(schema_editor, sql.REMOVER)


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0055_padrao_nomes_constraints_indices'),
    ]

    operations = [
        migrations.CreateModel(
            name='HistoricoAlteracao',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tabela', models.CharField(max_length=63)),
                ('registro_pk', models.BigIntegerField(db_comment='PK do registro alterado na tabela indicada em "tabela" (referencia polimorfica, sem FK).')),
                ('operacao', models.CharField(choices=[('INSERT', 'Inclusão'), ('UPDATE', 'Alteração'), ('DELETE', 'Exclusão')], max_length=6)),
                ('colunas_alteradas', models.JSONField(blank=True, db_comment='UPDATE: colunas que mudaram (ordem alfabetica). INSERT/DELETE: lista vazia.', db_default=[], default=list)),
                ('dados_antes', models.JSONField(blank=True, db_comment='UPDATE: valores anteriores so das colunas alteradas; DELETE: a linha inteira.', null=True)),
                ('dados_depois', models.JSONField(blank=True, db_comment='UPDATE: valores novos so das colunas alteradas; INSERT: a linha inteira.', null=True)),
                ('ip_origem', models.GenericIPAddressField(blank=True, null=True)),
                ('origem', models.CharField(blank=True, db_comment='current_setting(app.origem): web, cron:<job>, celery:<task>, migracao; vazio = SQL manual.', db_default='', default='', max_length=60)),
                ('transacao_ref', models.BigIntegerField(db_comment='pg_current_xact_id() - agrupa as linhas de uma mesma transacao/request.')),
                ('criado_em', models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now())),
                ('usuario_autor', models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'histórico de alteração',
                'verbose_name_plural': 'histórico de alterações',
                'db_table': 'historico_alteracao',
                'db_table_comment': 'Trilha de dados gravada por trigger (fn_registrar_historico) nas tabelas sensiveis: antes/depois so das colunas alteradas. Append-only (tg_historico_alteracao_imutavel). Retencao: 20 anos p/ prontuario, 5 anos p/ o resto.',
                'indexes': [aranha_estetica.db.indices.Indice(fields=['tabela', 'registro_pk', '-criado_em'], name='ix_historico_alteracao_tabela_registro'), aranha_estetica.db.indices.Indice(fields=['criado_em'], name='ix_historico_alteracao_criado_em'), aranha_estetica.db.indices.Indice(condition=models.Q(('usuario_autor__isnull', False)), fields=['usuario_autor', '-criado_em'], name='ix_historico_alteracao_usuario_autor_parcial')],
                'constraints': [models.CheckConstraint(condition=models.Q(('operacao__in', ['INSERT', 'UPDATE', 'DELETE'])), name='ck_historico_alteracao_operacao'), models.CheckConstraint(condition=models.Q(models.Q(('dados_antes__isnull', True), ('dados_depois__isnull', False), ('operacao', 'INSERT')), models.Q(('dados_antes__isnull', False), ('dados_depois__isnull', False), ('operacao', 'UPDATE')), models.Q(('dados_antes__isnull', False), ('dados_depois__isnull', True), ('operacao', 'DELETE')), _connector='OR'), name='ck_historico_alteracao_dados_coerentes')],
            },
        ),
        PadronizarNomes(['historico_alteracao']),
        migrations.RunPython(criar_logica, remover_logica),
    ]
