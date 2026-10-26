# Padrao SQL — fase (d): questionarios unificados (docs/specs/padrao-sql.md §2.6, §4.d, §6)
#
# As perguntas extras do prontuario saem de configuracao.prontuario_perguntas (JSON
# [{chave, texto, tipo: TEXTO|BOOLEAN}]) e viram UM formulario tipo PRONTUARIO no mesmo
# CRUD/formato/validador das anamneses ([{key, label, tipo, obrigatorio}]):
#   chave -> key (a mesma: as respostas em prontuario.respostas_extras NAO mudam)
#   texto -> label;  BOOLEAN -> 'bool';  TEXTO/outro -> 'longtext' (4000, nao reprova resposta
#   antiga longa);  obrigatorio = false.
# JSON invalido (nao-lista, item sem chave texto, chave repetida) -> nao cria nada, mantem a
# linha da configuracao e registra LogAuditoria (nada se perde). Valor vazio/lista vazia ->
# so apaga a linha (nao havia pergunta). Linhas duplicadas da chave (iexact; o leitor so usava a
# 1a por pk) ficam intocadas e vao para o log.
#
# Ordem: AlterField tipo (+PRONTUARIO) -> pre-checagem (aborta listando ids: tipo/escopo fora
# do dominio, escopo incoerente) -> AddConstraint ck_formulario_tipo/escopo/escopo_coerente/
# prontuario_global + uq_formulario_prontuario_ativo (nomes finais; a tabela so vira
# `formulario` na 0054) -> RunPython (Configuracao -> formulario; termina com SET CONSTRAINTS
# ALL IMMEDIATE no PG).
#
# Reverso (dev/testes): o formulario PRONTUARIO (o ativo, senao o mais recente) volta a ser
# configuracao.prontuario_perguntas e os formularios PRONTUARIO sao apagados.
#
# Pre-checagem p/ o runbook (schema 0026, tabela formulario_anamnese):
#   SELECT id, tipo, escopo, categoria, modalidade, procedimento_id FROM formulario_anamnese
#    WHERE tipo NOT IN ('ANAMNESE', 'PESQUISA')
#       OR escopo NOT IN ('GLOBAL', 'CATEGORIA', 'PROCEDIMENTO', 'MODALIDADE')
#       OR (escopo = 'PROCEDIMENTO' AND procedimento_id IS NULL)
#       OR (escopo = 'CATEGORIA' AND categoria = '')
#       OR (escopo = 'MODALIDADE' AND modalidade = '');

import json

from django.db import migrations, models

APP = 'aranha_estetica'
NOME_FORMULARIO = 'Questionário do prontuário'
TIPOS = ('ANAMNESE', 'PESQUISA')
ESCOPOS = ('GLOBAL', 'CATEGORIA', 'PROCEDIMENTO', 'MODALIDADE')


def _pg(schema_editor):
    return schema_editor.connection.vendor == 'postgresql'


def pre_checar_formularios(apps, schema_editor):
    Formulario = apps.get_model(APP, 'FormularioAnamnese')
    problemas = []
    for f in Formulario.objects.order_by('pk').values(
        'pk', 'tipo', 'escopo', 'categoria', 'modalidade', 'procedimento_id',
    ):
        if f['tipo'] not in TIPOS:
            problemas.append(f"id={f['pk']} tipo={f['tipo']!r} fora do dominio")
        if f['escopo'] not in ESCOPOS:
            problemas.append(f"id={f['pk']} escopo={f['escopo']!r} fora do dominio")
        elif f['escopo'] == 'PROCEDIMENTO' and f['procedimento_id'] is None:
            problemas.append(f"id={f['pk']} escopo PROCEDIMENTO sem procedimento")
        elif f['escopo'] == 'CATEGORIA' and not f['categoria']:
            problemas.append(f"id={f['pk']} escopo CATEGORIA sem categoria")
        elif f['escopo'] == 'MODALIDADE' and not f['modalidade']:
            problemas.append(f"id={f['pk']} escopo MODALIDADE sem modalidade")
    if problemas:
        raise RuntimeError(
            '0051 (questionarios unificados) abortada — corrija os formularios e rode de novo '
            '(pre-checagem SQL no runbook, docs/specs/padrao-sql.md §4.d): ' + ' | '.join(problemas)
        )


def _converter(valor):
    """[{chave, texto, tipo}] -> perguntas do formulario, ou None se o JSON nao serve."""
    try:
        legado = json.loads(valor)
    except (TypeError, ValueError):
        return None
    if not isinstance(legado, list):
        return None
    perguntas, vistas = [], set()
    for item in legado:
        if not isinstance(item, dict):
            return None
        chave = item.get('chave')
        if not isinstance(chave, str) or not chave.strip() or chave in vistas:
            return None
        vistas.add(chave)
        texto = item.get('texto')
        label = texto.strip() if isinstance(texto, str) and texto.strip() else chave.replace('_', ' ').capitalize()
        perguntas.append({
            'key': chave,
            'label': label,
            'tipo': 'bool' if item.get('tipo') == 'BOOLEAN' else 'longtext',
            'obrigatorio': False,
        })
    return perguntas


def migrar_perguntas(apps, schema_editor):
    Configuracao = apps.get_model(APP, 'Configuracao')
    Formulario = apps.get_model(APP, 'FormularioAnamnese')
    LogAuditoria = apps.get_model(APP, 'LogAuditoria')

    linhas = list(Configuracao.objects.filter(chave__iexact='prontuario_perguntas').order_by('pk'))
    if not linhas:
        return
    cfg, duplicadas = linhas[0], [c.pk for c in linhas[1:]]
    detalhes = {'configuracao_id': cfg.pk}
    if duplicadas:
        detalhes['configuracao_duplicada_ignorada'] = duplicadas

    if not (cfg.valor or '').strip():
        perguntas = []
    else:
        perguntas = _converter(cfg.valor)
    if perguntas is None:
        LogAuditoria.objects.create(
            acao='migration 0051: prontuario_perguntas com JSON invalido — mantido em configuracao',
            tabela='configuracao', registro_id=cfg.pk, detalhes={**detalhes, 'valor': cfg.valor},
        )
    else:
        if perguntas:
            ja_ativo = Formulario.objects.filter(tipo='PRONTUARIO', ativo=True).exists()
            form = Formulario.objects.create(
                nome=NOME_FORMULARIO, tipo='PRONTUARIO', escopo='GLOBAL', schema_json=perguntas,
                ativo=not ja_ativo, obrigatorio=False,
            )
            detalhes.update(formulario_id=form.pk, perguntas=len(perguntas))
        cfg.delete()
        LogAuditoria.objects.create(
            acao='migration 0051: perguntas do prontuario -> formulario PRONTUARIO',
            tabela='formulario_anamnese', registro_id=detalhes.get('formulario_id'), detalhes=detalhes,
        )
    if _pg(schema_editor):
        # FKs DEFERRABLE: dispara os checks agora (padrao 0034-0050)
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


def restaurar_configuracao(apps, schema_editor):
    """Reverso (dev/testes): formulario PRONTUARIO -> configuracao.prontuario_perguntas."""
    Configuracao = apps.get_model(APP, 'Configuracao')
    Formulario = apps.get_model(APP, 'FormularioAnamnese')
    LogAuditoria = apps.get_model(APP, 'LogAuditoria')

    forms = list(Formulario.objects.filter(tipo='PRONTUARIO').order_by('-ativo', '-pk'))
    if not forms:
        return
    origem = forms[0]
    legado = [
        {
            'chave': p['key'],
            'texto': p.get('label') or p['key'],
            'tipo': 'BOOLEAN' if p.get('tipo') == 'bool' else 'TEXTO',
        }
        for p in (origem.schema_json if isinstance(origem.schema_json, list) else [])
        if isinstance(p, dict) and p.get('key')
    ]
    if legado:
        Configuracao.objects.update_or_create(
            chave='prontuario_perguntas',
            defaults={
                'valor': json.dumps(legado, ensure_ascii=False),
                'descricao': 'Perguntas extras do prontuário (restaurado do formulário PRONTUARIO).',
            },
        )
    Formulario.objects.filter(pk__in=[f.pk for f in forms]).delete()
    LogAuditoria.objects.create(
        acao='migration 0051 (reversa): formulario PRONTUARIO -> configuracao.prontuario_perguntas',
        tabela='configuracao', detalhes={'formularios_removidos': [f.pk for f in forms], 'perguntas': len(legado)},
    )
    if _pg(schema_editor):
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0050_consentimento'),
    ]

    operations = [
        migrations.AlterField(
            model_name='formularioanamnese',
            name='tipo',
            field=models.CharField(choices=[('ANAMNESE', 'Anamnese pre-atendimento'), ('PESQUISA', 'Pesquisa pos-atendimento'), ('PRONTUARIO', 'Questionario do prontuario (equipe)')], db_index=True, default='ANAMNESE', max_length=12),
        ),
        migrations.RunPython(pre_checar_formularios, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='formularioanamnese',
            constraint=models.CheckConstraint(condition=models.Q(('tipo__in', ['ANAMNESE', 'PESQUISA', 'PRONTUARIO'])), name='ck_formulario_tipo'),
        ),
        migrations.AddConstraint(
            model_name='formularioanamnese',
            constraint=models.CheckConstraint(condition=models.Q(('escopo__in', ['GLOBAL', 'CATEGORIA', 'PROCEDIMENTO', 'MODALIDADE'])), name='ck_formulario_escopo'),
        ),
        migrations.AddConstraint(
            model_name='formularioanamnese',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(models.Q(('escopo', 'PROCEDIMENTO'), _negated=True), ('procedimento__isnull', False), _connector='OR'), models.Q(models.Q(('escopo', 'CATEGORIA'), _negated=True), models.Q(('categoria', ''), _negated=True), _connector='OR'), models.Q(models.Q(('escopo', 'MODALIDADE'), _negated=True), models.Q(('modalidade', ''), _negated=True), _connector='OR')), name='ck_formulario_escopo_coerente', violation_error_message='Escolha a categoria, a modalidade ou o procedimento do escopo.'),
        ),
        migrations.AddConstraint(
            model_name='formularioanamnese',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('tipo', 'PRONTUARIO'), _negated=True), models.Q(('escopo', 'GLOBAL'), ('obrigatorio', False), ('procedimento__isnull', True)), _connector='OR'), name='ck_formulario_prontuario_global', violation_error_message='O questionário do prontuário vale para todas as clientes: escopo Global e não obrigatório.'),
        ),
        migrations.AddConstraint(
            model_name='formularioanamnese',
            constraint=models.UniqueConstraint(condition=models.Q(('ativo', True), ('tipo', 'PRONTUARIO')), fields=('tipo',), name='uq_formulario_prontuario_ativo', violation_error_message='Já existe um questionário do prontuário ativo: edite-o ou desative-o antes.'),
        ),
        migrations.RunPython(migrar_perguntas, restaurar_configuracao),
    ]
