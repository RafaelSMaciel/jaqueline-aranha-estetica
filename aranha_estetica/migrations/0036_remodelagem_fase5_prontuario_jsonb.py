# Remodelagem v2.1 — Fase 5 (docs/specs/remodelagem-banco-v2.md secao 5)
# EAV (ProntuarioPergunta/ProntuarioResposta) -> Prontuario.respostas_extras
# JSONB. Schema das perguntas migra p/ Configuracao 'prontuario_perguntas'.
# Ordem: add campo -> copia dados -> delete models. GIN index pg-only.
#
# Auditoria pre-producao (editada in-place: prod nunca aplicou a 0036):
#  - respostas agregadas em memoria e gravadas com 1 UPDATE por prontuario
#    (antes: 1 instancia por resposta via select_related -> cada save
#    sobrescrevia o anterior e so a ultima resposta sobrevivia; e o 2o UPDATE
#    na mesma linha deixava 'pending trigger events' p/ o CREATE INDEX);
#  - respostas de perguntas DESATIVADAS sao preservadas em respostas_extras
#    (so nao entram no schema do formulario) — dado clinico, retencao 20 anos.

import json
import re

from django.db import migrations, models


def _slug_chave(texto):
    """Chave estavel a partir do texto da pergunta (snake_case ascii)."""
    s = texto.lower()
    s = re.sub(r'[áàâã]', 'a', s)
    s = re.sub(r'[éèê]', 'e', s)
    s = re.sub(r'[íì]', 'i', s)
    s = re.sub(r'[óòôõ]', 'o', s)
    s = re.sub(r'[úù]', 'u', s)
    s = re.sub(r'[ç]', 'c', s)
    s = re.sub(r'[^a-z0-9]+', '_', s).strip('_')
    return s[:50] or 'pergunta'


def migrar_eav_para_jsonb(apps, schema_editor):
    Pergunta = apps.get_model('aranha_estetica', 'ProntuarioPergunta')
    Resposta = apps.get_model('aranha_estetica', 'ProntuarioResposta')
    Configuracao = apps.get_model('aranha_estetica', 'Configuracao')

    Prontuario = apps.get_model('aranha_estetica', 'Prontuario')

    # 1. perguntas -> Configuracao (schema do questionario). TODAS ganham
    #    chave (as respostas historicas sao preservadas); so as ativas vao
    #    p/ o schema exibido no formulario.
    perguntas = list(Pergunta.objects.order_by('pk'))
    chave_por_pk = {}
    schema = []
    usadas = set()
    for p in perguntas:
        chave = _slug_chave(p.texto)
        while chave in usadas:
            chave += '_x'
        usadas.add(chave)
        chave_por_pk[p.pk] = (chave, p.tipo_resposta)
        if not p.ativa:
            continue
        schema.append({
            'chave': chave,
            'texto': p.texto,
            'tipo': 'BOOLEAN' if p.tipo_resposta == 'BOOLEAN' else 'TEXTO',
        })
    if schema:
        Configuracao.objects.update_or_create(
            chave='prontuario_perguntas',
            defaults={
                'valor': json.dumps(schema, ensure_ascii=False),
                'descricao': 'Schema do questionario do prontuario (migrado do EAV na fase 5).',
            },
        )

    # 2. respostas -> respostas_extras (agrega em memoria, 1 UPDATE por prontuario)
    por_prontuario = {}
    for r in Resposta.objects.order_by('pk'):
        info = chave_por_pk.get(r.pergunta_id)
        if not info:
            continue
        chave, tipo = info
        extras = por_prontuario.setdefault(r.prontuario_id, {})
        if tipo == 'BOOLEAN':
            if r.resposta_boolean is not None:
                extras[chave] = bool(r.resposta_boolean)
        elif r.resposta_texto:
            extras[chave] = r.resposta_texto
    for prontuario_id, extras in por_prontuario.items():
        if extras:
            Prontuario.objects.filter(pk=prontuario_id).update(respostas_extras=extras)
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


def criar_gin_index_pg(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    # UPDATEs da copia deixam RI triggers DEFERRED pendentes; CREATE INDEX
    # recusa tabela com trigger events pendentes.
    schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)
    schema_editor.execute(
        'CREATE INDEX IF NOT EXISTS gin_prontuario_extras '
        'ON prontuario USING gin (respostas_extras)'
    )


def remover_gin_index_pg(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute('DROP INDEX IF EXISTS gin_prontuario_extras')


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0035_remodelagem_fase4_exclusion_booking'),
    ]

    operations = [
        migrations.AddField(
            model_name='prontuario',
            name='respostas_extras',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(migrar_eav_para_jsonb, migrations.RunPython.noop),
        # filho primeiro (FK -> Pergunta/Prontuario)
        migrations.DeleteModel(name='ProntuarioResposta'),
        migrations.DeleteModel(name='ProntuarioPergunta'),
        migrations.RunPython(criar_gin_index_pg, remover_gin_index_pg),
    ]
