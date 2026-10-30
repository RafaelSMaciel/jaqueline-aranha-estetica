# Padrao SQL — metadados de linha em ingles em todas as tabelas (docs/specs/padrao-sql.md §1.2).
#
# Nome so no banco (como a 0054): o atributo Python fica (criado_em, atualizado_em, deletado_em,
# usuario_autor/autor/usuario, autor_nome/usuario_nome); muda o db_column e o catalogo:
#   criado_em -> created_at, atualizado_em -> updated_at, deletado_em -> deleted_at,
#   usuario_autor_id -> created_by_id, usuario_autor_nome -> created_by_name,
#   execucao_job.iniciado_em -> created_at (a linha nasce no inicio do job: e a criacao).
# Ordem:
#   1. 71 RenomearColuna (ALTER TABLE ... RENAME COLUMN: so catalogo, FK nao e recriada) + COMMENT
#      das colunas/tabelas que citavam o nome antigo
#   2. 16 RenomearIndice do Meta (ix_..._criado_em -> ix_..._created_at, ..._usuario_autor_parcial ->
#      ..._created_by_parcial)
#   3. updated_at onde faltava UPDATE com carimbo: codigo_otp (tentativas, uso) e execucao_job
#      (conclusao) — a linha antiga recebe coalesce(usado_em/concluido_em, created_at)
#   4. PG: corpos PL/pgSQL vigentes com os nomes novos (sql/v0079_metadados.py), fn_definir_atualizado_em
#      -> fn_set_updated_at, tg_<t>_atualizado_em -> tg_<t>_updated_at, tg_<t>_historico com
#      'updated_at' na lista de colunas ignoradas, colunas de view (vw_comissao.created_at,
#      vw_cliente_resumo.created_at) e nomes de FK/indice so-PG (fk_<t>_usuario_autor ->
#      fk_<t>_created_by); confere que nenhum corpo de funcao em texto cita coluna antiga.
#
# Sem metadado (regra do §1.2): updated_at so em tabela com UPDATE de negocio. Ficam sem ele as
# append-only/imutaveis por trigger (log_auditoria, historico_alteracao, movimento_*, prontuario_versao,
# anotacao_sessao, aceite_termo, compra_pacote_item, consentimento), as so INSERT/DELETE (habilitacao,
# pacote_item, consumo_sessao) e link_acesso (o trigger so deixa carimbar usado_em/revogado_em uma vez).
#
# historico_alteracao.colunas_alteradas/dados_* de linhas antigas guardam os nomes antigos (trilha
# imutavel: fica como foi gravada). Reverso: cada passo tem o seu (nomes e corpos de origem).
import re

import django.db.models.functions.datetime
from django.db import migrations, models
from django.db.models.functions import Coalesce

from aranha_estetica.db.operacoes import ComentarColuna, RenomearColuna, RenomearIndice
from aranha_estetica.migrations.sql import v0079_metadados as sql

APP = 'aranha_estetica'
# nome de objeto (separado por _): fk_<t>_usuario_autor, tg_<t>_atualizado_em, ix_<t>_criado_em
NOMES = (('criado_em', 'created_at'), ('atualizado_em', 'updated_at'), ('deletado_em', 'deleted_at'),
         ('usuario_autor', 'created_by'))


def _trocar_nome(nome, pares):
    for velho, novo in pares:
        nome = re.sub(rf'(?<=_){velho}(?=_|$)', novo, nome)
    return nome


def _pg(schema_editor):
    return schema_editor.connection.vendor == 'postgresql'


def _linhas(schema_editor, consulta, params=None):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(consulta, params)
        return cursor.fetchall()


def preencher_updated_at(apps, schema_editor):
    apps.get_model(APP, 'CodigoOtp').objects.update(atualizado_em=Coalesce('usado_em', 'criado_em'))
    apps.get_model(APP, 'ExecucaoJob').objects.update(atualizado_em=Coalesce('concluido_em', 'iniciado_em'))
    if _pg(schema_editor):
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


def _catalogo(apps, schema_editor, pares, colunas):
    """Triggers, argumentos do tg_<t>_historico, colunas de view e nomes so-PG (fora do Meta)."""
    qn = schema_editor.quote_name
    modelos = [m for m in apps.get_app_config(APP).get_models() if m._meta.managed and not m._meta.proxy]
    tabelas = sorted(m._meta.db_table for m in modelos)
    do_meta = {o.name for m in modelos for o in (*m._meta.indexes, *m._meta.constraints)}
    rx_coluna = re.compile(r'\b(' + '|'.join(colunas) + r')\b')
    for nome, tabela, definicao in _linhas(schema_editor, """
        SELECT t.tgname, t.tgrelid::regclass::text, pg_get_triggerdef(t.oid)
          FROM pg_trigger t
         WHERE NOT t.tgisinternal AND t.tgfoid = 'fn_registrar_historico'::regproc
    """):
        nova = rx_coluna.sub(lambda m: colunas[m.group(1)], definicao)
        if nova != definicao:   # so a lista de colunas ignoradas (TG_ARGV) muda
            schema_editor.execute(f'DROP TRIGGER {qn(nome)} ON {tabela}', None)
            schema_editor.execute(nova, None)
    for nome, tabela in _linhas(schema_editor, """
        SELECT tgname, tgrelid::regclass::text FROM pg_trigger
         WHERE NOT tgisinternal AND tgrelid = ANY(%s::regclass[])
    """, [tabelas]):
        novo = _trocar_nome(nome, pares)
        if novo != nome:
            schema_editor.execute(f'ALTER TRIGGER {qn(nome)} ON {qn(tabela)} RENAME TO {qn(novo)}', None)
    for visao, coluna in _linhas(schema_editor, """
        SELECT c.relname, a.attname FROM pg_attribute a
          JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind = 'v' AND a.attnum > 0 AND a.attname = ANY(%s)
    """, [list(colunas)]):
        schema_editor.execute(
            f'ALTER VIEW {qn(visao)} RENAME COLUMN {qn(coluna)} TO {qn(colunas[coluna])}', None)
    for tabela, nome in _linhas(schema_editor, """
        SELECT conrelid::regclass::text, conname FROM pg_constraint WHERE conrelid = ANY(%s::regclass[])
    """, [tabelas]):
        novo = _trocar_nome(nome, pares)
        if novo != nome and nome not in do_meta:
            schema_editor.execute(f'ALTER TABLE {qn(tabela)} RENAME CONSTRAINT {qn(nome)} TO {qn(novo)}', None)
    for (nome,) in _linhas(schema_editor, """
        SELECT i.relname FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid
         WHERE x.indrelid = ANY(%s::regclass[])
           AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = x.indexrelid)
    """, [tabelas]):
        novo = _trocar_nome(nome, pares)
        if novo != nome and nome not in do_meta:
            schema_editor.execute(f'ALTER INDEX {qn(nome)} RENAME TO {qn(novo)}', None)


def aplicar(apps, schema_editor):
    if not _pg(schema_editor):
        return
    for comando in sql.CRIAR:
        schema_editor.execute(comando, None)  # params=None: o '%' do RAISE/LIKE nao e placeholder
    _catalogo(apps, schema_editor, NOMES, sql.COLUNAS)
    sobrou = _linhas(schema_editor, """
        SELECT p.proname FROM pg_proc p JOIN pg_namespace s ON s.oid = p.pronamespace
         WHERE s.nspname = 'public' AND p.prosqlbody IS NULL AND p.prosrc ~ %s
           AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass
                            AND d.objid = p.oid AND d.deptype = 'e')
    """, [r'\m(' + '|'.join(sql.COLUNAS) + r')\M'])
    if sobrou:
        raise RuntimeError(f'0079: corpo de funcao ainda cita coluna antiga: {sorted(n for (n,) in sobrou)}')


def reverter(apps, schema_editor):
    if not _pg(schema_editor):
        return
    for comando in sql.REMOVER:   # antes do catalogo: tg_<t>_updated_at das tabelas novas sai pelo nome
        schema_editor.execute(comando, None)
    _catalogo(apps, schema_editor, [(novo, velho) for velho, novo in NOMES],
              {novo: velho for velho, novo in sql.COLUNAS.items()})


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0078_termo_lgpd_v1_completo'),
    ]

    operations = [
        RenomearColuna(model_name='aceitetermo', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='agendaexcecao', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='agendaexcecao', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='agendaexcecao', name='usuario_autor', db_column='created_by_id'),
        RenomearColuna(model_name='agendahorario', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='agendahorario', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='anotacaosessao', name='autor', db_column='created_by_id'),
        RenomearColuna(model_name='anotacaosessao', name='autor_nome', db_column='created_by_name'),
        RenomearColuna(model_name='anotacaosessao', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='assinaturapush', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='assinaturapush', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='atendimento', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='atendimento', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='avaliacaonps', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='avaliacaonps', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='carteira', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='carteira', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='cliente', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='cliente', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='cliente', name='deletado_em', db_column='deleted_at'),
        RenomearColuna(model_name='codigootp', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='comprapacote', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='comprapacote', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='comprapacoteitem', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='configuracao', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='configuracao', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='consentimento', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='consentimento', name='usuario_autor', db_column='created_by_id'),
        RenomearColuna(model_name='consumosessao', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='execucaojob', name='iniciado_em', db_column='created_at'),
        RenomearColuna(model_name='formularioanamnese', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='formularioanamnese', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='habilitacao', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='historicoalteracao', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='historicoalteracao', name='usuario_autor', db_column='created_by_id'),
        RenomearColuna(model_name='itempacote', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='linkacesso', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='listaespera', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='listaespera', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='logauditoria', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='logauditoria', name='usuario', db_column='created_by_id'),
        RenomearColuna(model_name='logauditoria', name='usuario_nome', db_column='created_by_name'),
        RenomearColuna(model_name='movimentocarteira', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='movimentocarteira', name='usuario', db_column='created_by_id'),
        RenomearColuna(model_name='movimentocomissao', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='movimentocomissao', name='usuario_autor', db_column='created_by_id'),
        RenomearColuna(model_name='notificacao', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='notificacao', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='pacote', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='pacote', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='preco', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='preco', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='procedimento', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='procedimento', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='profissional', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='profissional', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='promocao', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='promocao', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='prontuario', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='prontuario', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='prontuarioversao', name='autor', db_column='created_by_id'),
        RenomearColuna(model_name='prontuarioversao', name='autor_nome', db_column='created_by_name'),
        RenomearColuna(model_name='prontuarioversao', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='regracomissao', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='regracomissao', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='respostaanamnese', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='respostaanamnese', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='usuario', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='usuario', name='criado_em', db_column='created_at'),
        RenomearColuna(model_name='versaotermo', name='atualizado_em', db_column='updated_at'),
        RenomearColuna(model_name='versaotermo', name='criado_em', db_column='created_at'),
        ComentarColuna(
            model_name='atendimento',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_column='updated_at', db_comment='NULL legado preenchido com created_at na 0054.', db_default=django.db.models.functions.datetime.Now()),
        ),
        ComentarColuna(
            model_name='cliente',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_column='updated_at', db_comment='NULL legado preenchido com created_at na 0054.', db_default=django.db.models.functions.datetime.Now()),
        ),
        ComentarColuna(
            model_name='formularioanamnese',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_column='updated_at', db_comment='NULL legado preenchido com created_at na 0054.', db_default=django.db.models.functions.datetime.Now()),
        ),
        ComentarColuna(
            model_name='execucaojob',
            name='iniciado_em',
            field=models.DateTimeField(auto_now_add=True, db_column='created_at', db_comment='Inicio da execucao: a linha nasce antes do job.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterModelTableComment(
            name='cliente',
            table_comment='Cliente da clinica. Telefone (so digitos, 10-11) e a chave natural do agendamento publico (uq_cliente_telefone_ativo). Soft delete em deleted_at; anonimizacao LGPD troca nome por [ANONIMIZADO-<id>] (sp_anonimizar_cliente). Consentimentos em consentimento.',
        ),
        migrations.AlterModelTableComment(
            name='prontuarioversao',
            table_comment='Historico append-only do prontuario (CFM 1.638/2002): foto do estado ANTERIOR a cada edicao. Imutavel (tg_prontuario_versao_imutavel; so anular created_by_id e aceito).',
        ),
        RenomearIndice(model_name='agendaexcecao', new_name='ix_agenda_excecao_created_by_parcial', old_name='ix_agenda_excecao_usuario_autor_parcial'),
        RenomearIndice(model_name='anotacaosessao', new_name='ix_anotacao_sessao_atendimento_created_at', old_name='ix_anotacao_sessao_atendimento_criado_em'),
        RenomearIndice(model_name='avaliacaonps', new_name='ix_avaliacao_nps_created_at', old_name='ix_avaliacao_nps_criado_em'),
        RenomearIndice(model_name='cliente', new_name='ix_cliente_deleted_at_parcial', old_name='ix_cliente_deletado_em_parcial'),
        RenomearIndice(model_name='codigootp', new_name='ix_codigo_otp_identificador_proposito_created_at', old_name='ix_codigo_otp_identificador_proposito_criado_em'),
        RenomearIndice(model_name='comprapacote', new_name='ix_compra_pacote_created_at', old_name='ix_compra_pacote_criado_em'),
        RenomearIndice(model_name='consentimento', new_name='ix_consentimento_created_by_parcial', old_name='ix_consentimento_usuario_autor_parcial'),
        RenomearIndice(model_name='historicoalteracao', new_name='ix_historico_alteracao_created_at', old_name='ix_historico_alteracao_criado_em'),
        RenomearIndice(model_name='historicoalteracao', new_name='ix_historico_alteracao_created_by_parcial', old_name='ix_historico_alteracao_usuario_autor_parcial'),
        RenomearIndice(model_name='logauditoria', new_name='ix_log_auditoria_created_at', old_name='ix_log_auditoria_criado_em'),
        RenomearIndice(model_name='movimentocarteira', new_name='ix_movimento_carteira_carteira_created_at', old_name='ix_movimento_carteira_carteira_criado_em'),
        RenomearIndice(model_name='movimentocomissao', new_name='ix_movimento_comissao_created_at', old_name='ix_movimento_comissao_criado_em'),
        RenomearIndice(model_name='movimentocomissao', new_name='ix_movimento_comissao_profissional_created_at', old_name='ix_movimento_comissao_profissional_criado_em'),
        RenomearIndice(model_name='notificacao', new_name='ix_notificacao_created_at', old_name='ix_notificacao_criado_em'),
        RenomearIndice(model_name='prontuarioversao', new_name='ix_prontuario_versao_prontuario_created_at', old_name='ix_prontuario_versao_prontuario_criado_em'),
        RenomearIndice(model_name='respostaanamnese', new_name='ix_resposta_formulario_cliente_created_at', old_name='ix_resposta_formulario_cliente_criado_em'),
        migrations.AddField(
            model_name='codigootp',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_column='updated_at', db_comment='Linhas anteriores a 0079 receberam o ultimo evento conhecido (uso/conclusao) ou a criacao.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='execucaojob',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_column='updated_at', db_comment='Linhas anteriores a 0079 receberam o ultimo evento conhecido (uso/conclusao) ou a criacao.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.RunPython(preencher_updated_at, migrations.RunPython.noop),
        migrations.RunPython(aplicar, reverter),
    ]
