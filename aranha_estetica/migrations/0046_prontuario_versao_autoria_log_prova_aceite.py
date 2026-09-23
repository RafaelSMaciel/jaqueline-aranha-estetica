# Verificacao final da auditoria pre-producao (wave 3):
#  - ProntuarioVersao: historico append-only do prontuario (editar sobrescrevia
#    alergias/historico sem guardar o valor anterior — CFM 1.638/2002).
#  - LogAuditoria.usuario_nome: snapshot 'Nome <email>' do autor. usuario e
#    SET_NULL: excluir o Usuario fazia a trilha dele virar "acao do sistema".
#    Backfill p/ os logs existentes.
#  - RespostaAnamnese.respondida_em das fichas do booking legado (criadas sem
#    o campo): alergias declaradas no agendamento nao viravam alerta de saude.
#    So ficha de atendimento com resposta ({} = convite pendente/pesquisa vazia).
#  - Postgres: triggers de imutabilidade da prova de aceite (aceite_termo;
#    versao_termo ja aceita, espelho de VersaoTermo.CAMPOS_IMUTAVEIS) e do
#    historico do prontuario. SET_NULL das FKs (atendimento do aceite, autor da
#    versao) continua permitido; desativar termo (ativa/vigente_desde) tambem.
#    Sem '%' no SQL (RAISE ... USING DETAIL) e params=None, como na 0038.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import CharField, F, OuterRef, Subquery, Value
from django.db.models.functions import Concat


def preencher_usuario_nome(apps, schema_editor):
    LogAuditoria = apps.get_model('aranha_estetica', 'LogAuditoria')
    Usuario = apps.get_model('aranha_estetica', 'Usuario')
    # mesmo formato de models.sistema.rotulo_usuario ('Nome <email>')
    rotulo = (
        Usuario.objects.filter(pk=OuterRef('usuario_id'))
        .annotate(rotulo=Concat('nome', Value(' <'), 'email', Value('>'), output_field=CharField()))
        .values('rotulo')[:1]
    )
    LogAuditoria.objects.filter(usuario__isnull=False, usuario_nome='').update(usuario_nome=Subquery(rotulo))


def preencher_respondida_em_legado(apps, schema_editor):
    RespostaAnamnese = apps.get_model('aranha_estetica', 'RespostaAnamnese')
    (
        RespostaAnamnese.objects
        .filter(atendimento__isnull=False, respondida_em__isnull=True)
        .exclude(respostas_json={})
        .update(respondida_em=F('criado_em'))
    )


TRIGGERS_SQL = (
    """
    CREATE OR REPLACE FUNCTION aceite_termo_imutavel() RETURNS trigger AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'aceite_termo e prova legal (LGPD art. 8): exclusao bloqueada'
                USING DETAIL = 'aceite_termo.id=' || OLD.id;
        END IF;
        IF (NEW.id, NEW.cliente_id, NEW.versao_termo_id, NEW.ip, NEW.user_agent,
            NEW.conteudo_sha256, NEW.criado_em)
           IS DISTINCT FROM
           (OLD.id, OLD.cliente_id, OLD.versao_termo_id, OLD.ip, OLD.user_agent,
            OLD.conteudo_sha256, OLD.criado_em)
           OR (NEW.atendimento_id IS DISTINCT FROM OLD.atendimento_id
               AND NEW.atendimento_id IS NOT NULL) THEN
            RAISE EXCEPTION 'aceite_termo e prova legal (LGPD art. 8): registro imutavel'
                USING DETAIL = 'aceite_termo.id=' || OLD.id;
        END IF;
        RETURN NEW;
    END $$ LANGUAGE plpgsql
    """,
    'DROP TRIGGER IF EXISTS trg_aceite_termo_imutavel ON aceite_termo',
    """
    CREATE TRIGGER trg_aceite_termo_imutavel
    BEFORE UPDATE OR DELETE ON aceite_termo
    FOR EACH ROW EXECUTE FUNCTION aceite_termo_imutavel()
    """,
    """
    CREATE OR REPLACE FUNCTION versao_termo_imutavel() RETURNS trigger AS $$
    BEGIN
        IF (NEW.id, NEW.tipo, NEW.procedimento_id, NEW.titulo, NEW.conteudo, NEW.versao)
           IS DISTINCT FROM
           (OLD.id, OLD.tipo, OLD.procedimento_id, OLD.titulo, OLD.conteudo, OLD.versao)
           AND EXISTS (SELECT 1 FROM aceite_termo WHERE versao_termo_id = OLD.id) THEN
            RAISE EXCEPTION 'versao_termo ja aceita e imutavel: publique uma nova versao'
                USING DETAIL = 'versao_termo.id=' || OLD.id;
        END IF;
        RETURN NEW;
    END $$ LANGUAGE plpgsql
    """,
    'DROP TRIGGER IF EXISTS trg_versao_termo_imutavel ON versao_termo',
    """
    CREATE TRIGGER trg_versao_termo_imutavel
    BEFORE UPDATE ON versao_termo
    FOR EACH ROW EXECUTE FUNCTION versao_termo_imutavel()
    """,
    """
    CREATE OR REPLACE FUNCTION prontuario_versao_imutavel() RETURNS trigger AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'prontuario_versao e historico clinico: exclusao bloqueada'
                USING DETAIL = 'prontuario_versao.id=' || OLD.id;
        END IF;
        IF (NEW.id, NEW.prontuario_id, NEW.autor_nome, NEW.criado_em, NEW.dados)
           IS DISTINCT FROM
           (OLD.id, OLD.prontuario_id, OLD.autor_nome, OLD.criado_em, OLD.dados)
           OR (NEW.autor_id IS DISTINCT FROM OLD.autor_id AND NEW.autor_id IS NOT NULL) THEN
            RAISE EXCEPTION 'prontuario_versao e historico clinico: registro imutavel'
                USING DETAIL = 'prontuario_versao.id=' || OLD.id;
        END IF;
        RETURN NEW;
    END $$ LANGUAGE plpgsql
    """,
    'DROP TRIGGER IF EXISTS trg_prontuario_versao_imutavel ON prontuario_versao',
    """
    CREATE TRIGGER trg_prontuario_versao_imutavel
    BEFORE UPDATE OR DELETE ON prontuario_versao
    FOR EACH ROW EXECUTE FUNCTION prontuario_versao_imutavel()
    """,
)

TRIGGERS_REVERSO_SQL = (
    'DROP TRIGGER IF EXISTS trg_prontuario_versao_imutavel ON prontuario_versao',
    'DROP FUNCTION IF EXISTS prontuario_versao_imutavel()',
    'DROP TRIGGER IF EXISTS trg_versao_termo_imutavel ON versao_termo',
    'DROP FUNCTION IF EXISTS versao_termo_imutavel()',
    'DROP TRIGGER IF EXISTS trg_aceite_termo_imutavel ON aceite_termo',
    'DROP FUNCTION IF EXISTS aceite_termo_imutavel()',
)


def criar_triggers_pg(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    # UPDATEs acima nao deixam trigger events pendentes p/ o DDL seguinte
    schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)
    for sql in TRIGGERS_SQL:
        schema_editor.execute(sql, None)


def remover_triggers_pg(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for sql in TRIGGERS_REVERSO_SQL:
        schema_editor.execute(sql, None)


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0045_dados_termo_lgpd_autoria'),
    ]

    operations = [
        migrations.AddField(
            model_name='logauditoria',
            name='usuario_nome',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.CreateModel(
            name='ProntuarioVersao',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('autor_nome', models.CharField(blank=True, default='', max_length=100)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('dados', models.JSONField(default=dict)),
                ('autor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('prontuario', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='versoes', to='aranha_estetica.prontuario')),
            ],
            options={
                'db_table': 'prontuario_versao',
                'managed': True,
                'indexes': [models.Index(fields=['prontuario', '-criado_em'], name='idx_pront_versao')],
            },
        ),
        migrations.RunPython(preencher_usuario_nome, migrations.RunPython.noop),
        migrations.RunPython(preencher_respondida_em_legado, migrations.RunPython.noop),
        migrations.RunPython(criar_triggers_pg, remover_triggers_pg),
    ]
