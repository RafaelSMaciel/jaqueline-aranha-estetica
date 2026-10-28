# Papeis e links: sai o papel RECEPCAO (YAGNI: a dona faz a recepcao) e o link legado de
# reagendar/cancelar por token (GERENCIAR_AGENDAMENTO) — a cliente tem um acesso so, o portal por OTP.
#
# Ordem: guarda (aborta listando os ids se houver usuario RECEPCAO: decidir o papel de cada um
# antes) -> RunPython apaga os links GERENCIAR_AGENDAMENTO (nada mais emite nem resolve; ids no
# LogAuditoria) -> PG: fn_link_acesso_imutavel e sp_anonimizar_cliente sem link_acesso.atendimento_id
# (sql/v0069_links.py) -> solta e recria ck_usuario_papel, ck_link_acesso_finalidade e
# ck_link_acesso_alvo_por_finalidade -> RemoveField link_acesso.atendimento (o legado era o unico
# alvo atendimento).
#
# Num banco novo (go-live: 0001 -> ultima) a guarda e o RunPython nao acham nada.
# Reverso (dev/testes): devolve a funcao da 0049 e a procedure da 0058; links apagados nao voltam.
#
# Pre-checagem p/ o runbook:
#   SELECT id, email FROM usuario WHERE papel = 'RECEPCAO';
from django.db import migrations, models

from aranha_estetica.migrations.sql import v0058_procedures as sql_0058
from aranha_estetica.migrations.sql import v0069_links as sql

APP = 'aranha_estetica'

SQL_FN_LINK_ACESSO_0049 = """
CREATE OR REPLACE FUNCTION fn_link_acesso_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- DELETE livre (purga/cascata). UPDATE: so marcar usado_em e revogado_em, uma vez cada.
    IF (NEW.id, NEW.finalidade, NEW.atendimento_id, NEW.notificacao_id, NEW.resposta_formulario_id,
        NEW.cliente_id, NEW.profissional_id, NEW.token_hash, NEW.expira_em, NEW.criado_em)
       IS DISTINCT FROM
       (OLD.id, OLD.finalidade, OLD.atendimento_id, OLD.notificacao_id, OLD.resposta_formulario_id,
        OLD.cliente_id, OLD.profissional_id, OLD.token_hash, OLD.expira_em, OLD.criado_em)
       OR (OLD.usado_em IS NOT NULL AND NEW.usado_em IS DISTINCT FROM OLD.usado_em)
       OR (OLD.revogado_em IS NOT NULL AND NEW.revogado_em IS DISTINCT FROM OLD.revogado_em) THEN
        RAISE EXCEPTION 'link_acesso: so usado_em/revogado_em podem ser preenchidos (uma vez)'
            USING DETAIL = 'link_acesso.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def _pg(schema_editor):
    return schema_editor.connection.vendor == 'postgresql'


def guarda_recepcao(apps, schema_editor):
    Usuario = apps.get_model(APP, 'Usuario')
    ids = list(Usuario.objects.filter(papel='RECEPCAO').order_by('pk').values_list('pk', flat=True))
    if ids:
        raise RuntimeError(
            '0069 abortada: o papel RECEPCAO deixou de existir (papeis: ADMIN e PROFISSIONAL). '
            'Defina o papel destes usuarios (ou desative-os) e rode de novo: usuario.id='
            + ', '.join(map(str, ids))
        )


def apagar_links_gerenciar(apps, schema_editor):
    LinkAcesso = apps.get_model(APP, 'LinkAcesso')
    LogAuditoria = apps.get_model(APP, 'LogAuditoria')

    legado = LinkAcesso.objects.filter(finalidade='GERENCIAR_AGENDAMENTO')
    ids = list(legado.order_by('pk').values_list('pk', flat=True))
    if not ids:
        return
    legado.delete()
    LogAuditoria.objects.create(
        acao='migration 0069: links legados de reagendar/cancelar (GERENCIAR_AGENDAMENTO) apagados',
        tabela='link_acesso', detalhes={'link_acesso_ids': ids},
    )
    if _pg(schema_editor):
        # FKs DEFERRABLE: dispara os checks agora, antes do ALTER TABLE (padrao 0034-0051)
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


def _executar(schema_editor, comandos):
    if not _pg(schema_editor):
        return
    for comando in comandos:
        schema_editor.execute(comando, None)  # params=None: o '%' do RAISE/LIKE nao e placeholder


def logica_sem_atendimento(apps, schema_editor):
    _executar(schema_editor, sql.CRIAR)


def logica_da_0058(apps, schema_editor):
    _executar(schema_editor, [SQL_FN_LINK_ACESSO_0049, sql_0058.SP_ANONIMIZAR_CLIENTE])


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0068_google_agenda_removida'),
    ]

    operations = [
        migrations.RunPython(guarda_recepcao, migrations.RunPython.noop),
        migrations.RunPython(apagar_links_gerenciar, migrations.RunPython.noop),
        # ─── papel ───
        migrations.RemoveConstraint(
            model_name='usuario',
            name='ck_usuario_papel',
        ),
        migrations.AlterField(
            model_name='usuario',
            name='papel',
            field=models.CharField(choices=[('ADMIN', 'Administrador'), ('PROFISSIONAL', 'Profissional')], db_default='PROFISSIONAL', default='PROFISSIONAL', max_length=20),
        ),
        migrations.AddConstraint(
            model_name='usuario',
            constraint=models.CheckConstraint(condition=models.Q(('papel__in', ['ADMIN', 'PROFISSIONAL'])), name='ck_usuario_papel'),
        ),
        migrations.AlterModelTableComment(
            name='usuario',
            table_comment='Equipe (login por e-mail). papel ADMIN/PROFISSIONAL; is_staff = ADMIN. Nunca excluir: desativar (eh_ativo=false).',
        ),
        # ─── link_acesso ───
        migrations.RemoveConstraint(
            model_name='linkacesso',
            name='ck_link_acesso_finalidade',
        ),
        migrations.RemoveConstraint(
            model_name='linkacesso',
            name='ck_link_acesso_alvo_por_finalidade',
        ),
        migrations.AlterField(
            model_name='linkacesso',
            name='finalidade',
            field=models.CharField(choices=[('CONFIRMAR_PRESENCA', 'Confirmar presença'), ('NPS', 'Pesquisa NPS'), ('TERMO', 'Aceite de termo'), ('FORMULARIO', 'Ficha/pesquisa'), ('DESCADASTRO', 'Descadastro de comunicação'), ('FEED_ICS', 'Feed do calendário (ICS)')], max_length=30),
        ),
        migrations.AlterField(
            model_name='linkacesso',
            name='usado_em',
            field=models.DateTimeField(blank=True, db_comment='Primeiro uso efetivo (acao concluida). Links de uso multiplo (ICS, descadastro) continuam validos.', null=True),
        ),
        migrations.RunPython(logica_sem_atendimento, logica_da_0058),
        migrations.RemoveIndex(
            model_name='linkacesso',
            name='ix_link_acesso_atendimento_parcial',
        ),
        migrations.RemoveField(
            model_name='linkacesso',
            name='atendimento',
        ),
        migrations.AddConstraint(
            model_name='linkacesso',
            constraint=models.CheckConstraint(condition=models.Q(('finalidade__in', ['CONFIRMAR_PRESENCA', 'NPS', 'TERMO', 'FORMULARIO', 'DESCADASTRO', 'FEED_ICS'])), name='ck_link_acesso_finalidade'),
        ),
        migrations.AddConstraint(
            model_name='linkacesso',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('finalidade__in', ['CONFIRMAR_PRESENCA', 'NPS', 'TERMO']), ('cliente__isnull', True), ('notificacao__isnull', False), ('profissional__isnull', True), ('resposta_formulario__isnull', True)), models.Q(('finalidade', 'FORMULARIO'), ('cliente__isnull', True), ('notificacao__isnull', True), ('profissional__isnull', True), ('resposta_formulario__isnull', False)), models.Q(('finalidade', 'DESCADASTRO'), ('cliente__isnull', False), ('notificacao__isnull', True), ('profissional__isnull', True), ('resposta_formulario__isnull', True)), models.Q(('finalidade', 'FEED_ICS'), ('cliente__isnull', True), ('notificacao__isnull', True), ('profissional__isnull', False), ('resposta_formulario__isnull', True)), _connector='OR'), name='ck_link_acesso_alvo_por_finalidade'),
        ),
    ]
