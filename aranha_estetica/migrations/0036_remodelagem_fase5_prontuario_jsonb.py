# Remodelagem v2.1 — Fase 5 (docs/specs/remodelagem-banco-v2.md secao 5)
# EAV (ProntuarioPergunta/ProntuarioResposta) -> Prontuario.respostas_extras
# JSONB. Schema das perguntas migra p/ Configuracao 'prontuario_perguntas'.
# Ordem: add campo -> copia dados -> delete models. GIN index pg-only.

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

    # 1. perguntas -> Configuracao (schema do questionario)
    perguntas = list(Pergunta.objects.filter(ativa=True).order_by('pk'))
    chave_por_pk = {}
    schema = []
    usadas = set()
    for p in perguntas:
        chave = _slug_chave(p.texto)
        while chave in usadas:
            chave += '_x'
        usadas.add(chave)
        chave_por_pk[p.pk] = (chave, p.tipo_resposta)
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

    # 2. respostas -> respostas_extras
    for r in Resposta.objects.select_related('prontuario').all():
        info = chave_por_pk.get(r.pergunta_id)
        if not info:
            continue
        chave, tipo = info
        pront = r.prontuario
        extras = dict(pront.respostas_extras or {})
        if tipo == 'BOOLEAN':
            if r.resposta_boolean is not None:
                extras[chave] = bool(r.resposta_boolean)
        else:
            if r.resposta_texto:
                extras[chave] = r.resposta_texto
        pront.respostas_extras = extras
        pront.save(update_fields=['respostas_extras'])


def criar_gin_index_pg(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
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
