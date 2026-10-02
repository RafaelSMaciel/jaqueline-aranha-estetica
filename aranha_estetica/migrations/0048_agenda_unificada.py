# Padrao SQL — fase (a): agenda 4 -> 2 (docs/specs/padrao-sql.md §2.4, §4.a, §6)
#
#   disponibilidade_profissional           -> agenda_horario (dia_semana ISO 1=seg..7=dom)
#   excecao_disponibilidade, bloqueio_agenda,
#   feriado                                -> agenda_excecao (FOLGA | HORARIO_DIFERENTE | BLOQUEIO)
#
# Ordem: CreateModel (CHECK/UNIQUE/indices portaveis) -> pre-checagem (aborta
# com a lista de ids) -> copia (bloqueios com o MESMO id, setval, excecoes e
# feriados com ids novos + LogAuditoria com o mapa id_antigo -> id_novo, grade
# ISO) -> SET CONSTRAINTS ALL IMMEDIATE -> PG: 2 EXCLUDE + CHECK da RRULE ->
# PadronizarNomes (pk_/fk_ com ON DELETE real) -> DeleteModel x4.
#
# Aborta (nada e aplicado; migrate_atomico desfaz tudo) se:
#   1. duas janelas do mesmo profissional/dia se sobrepoem (ex_agenda_horario_sobreposicao);
#   2. regra de recorrencia fora do subconjunto (utils.recorrencia).
# Pre-checagem p/ o runbook (schema 0026, mesmos nomes de tabela):
#   SELECT a.id, b.id, a.profissional_id, a.dia_semana
#     FROM disponibilidade_profissional a JOIN disponibilidade_profissional b
#       ON a.profissional_id = b.profissional_id AND a.dia_semana = b.dia_semana AND a.id < b.id
#      AND a.hora_inicio < b.hora_fim AND b.hora_inicio < a.hora_fim;
#   SELECT id, regra_recorrencia FROM bloqueio_agenda WHERE coalesce(regra_recorrencia, '') <> '';
#
# Saneamentos sem perda de comportamento (ficam no LogAuditoria da migration):
#   - HORARIO_DIFERENTE com fim <= inicio (legado anterior a validacao da tela:
#     zerava o dia) vira FOLGA — ou e descartado se ja ha FOLGA no dia;
#   - FOLGA com horas (o CHECK antigo aceitava; o motor ignorava) perde as horas;
#   - regra com COUNT/UNTIL vira data_fim_recorrencia equivalente; recorrencia_ate
#     sem regra (sem efeito) e descartado;
#   - motivo 'gcal:%' so vira origem GCAL com profissional (CHECK ck_agenda_excecao_gcal).
#
# Reverso (dev/testes; rollback de producao = restaurar o dump): recria as 4
# tabelas e copia de volta (dia ISO -> 1=Dom..7=Sab, FOLGA expandida por dia,
# FOLGA global -> feriado, BLOQUEIO -> bloqueio_agenda com o mesmo id). Perde o
# que o modelo antigo nao representa (excecao/bloqueio inativo, origem_ref) —
# registrado no LogAuditoria.

from collections import defaultdict
from datetime import date

import django.db.models.deletion
import django.db.models.functions.datetime
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import aranha_estetica.db.indices
from aranha_estetica.db.operacoes import PadronizarNomes

APP = 'aranha_estetica'

ORIGEM_POR_ESCOPO = {'NACIONAL': 'FERIADO_NACIONAL', 'LOCAL': 'FERIADO_LOCAL', 'CLINICA': 'RECESSO'}
ESCOPO_POR_ORIGEM = {'FERIADO_NACIONAL': 'NACIONAL', 'FERIADO_LOCAL': 'LOCAL', 'RECESSO': 'CLINICA', 'MANUAL': 'CLINICA'}

# Congelada aqui (DDL aplicado); utils.recorrencia.REGEX_SUBCONJUNTO e o espelho
# em Python — tests/test_migrations_dados confere que sao iguais.
REGEX_RRULE = (
    r'^(FREQ=(DAILY|WEEKLY)(;INTERVAL=[1-9][0-9]?)?(;BYDAY=(MO|TU|WE|TH|FR|SA|SU)(,(MO|TU|WE|TH|FR|SA|SU))*)?'
    r'|FREQ=MONTHLY(;INTERVAL=[1-9][0-9]?)?(;BYDAY=(-1|[1-4])?(MO|TU|WE|TH|FR|SA|SU)(,(-1|[1-4])?(MO|TU|WE|TH|FR|SA|SU))*'
    r'|;BYMONTHDAY=([1-9]|[12][0-9]|3[01]))?)$'
)

SQL_PG = [
    # grade: varias janelas por dia, nenhuma sobreposta (substitui o UNIQUE (prof, dia, hora_inicio))
    ('ex_agenda_horario_sobreposicao', 'agenda_horario',
     "ALTER TABLE agenda_horario ADD CONSTRAINT ex_agenda_horario_sobreposicao EXCLUDE USING gist ("
     "profissional_id WITH =, dia_semana WITH =, "
     "tsrange(DATE '2000-01-01' + hora_inicio, DATE '2000-01-01' + hora_fim, '[)') WITH &&)"),
    ('ex_agenda_excecao_folga_sobreposta', 'agenda_excecao',
     "ALTER TABLE agenda_excecao ADD CONSTRAINT ex_agenda_excecao_folga_sobreposta EXCLUDE USING gist ("
     "profissional_id WITH =, daterange(data_inicio, data_fim, '[]') WITH &&) "
     "WHERE (tipo = 'FOLGA' AND eh_ativo AND profissional_id IS NOT NULL)"),
    ('ck_agenda_excecao_regra_recorrencia_suportada', 'agenda_excecao',
     "ALTER TABLE agenda_excecao ADD CONSTRAINT ck_agenda_excecao_regra_recorrencia_suportada CHECK ("
     f"regra_recorrencia IS NULL OR regra_recorrencia ~ '{REGEX_RRULE}')"),
]


def _dia_iso(dia_antigo):
    """1=Dom..7=Sab -> ISO 1=seg..7=dom."""
    return ((dia_antigo + 5) % 7) + 1


def _dia_antigo(dia_iso):
    """ISO 1=seg..7=dom -> 1=Dom..7=Sab."""
    return (dia_iso % 7) + 1


def _pg(schema_editor):
    return schema_editor.connection.vendor == 'postgresql'


def _janelas_sobrepostas(Disponibilidade):
    por_dia = defaultdict(list)
    for d in Disponibilidade.objects.order_by('profissional_id', 'dia_semana', 'hora_inicio', 'pk').values(
        'pk', 'profissional_id', 'dia_semana', 'hora_inicio', 'hora_fim',
    ):
        por_dia[(d['profissional_id'], d['dia_semana'])].append(d)
    pares = []
    for janelas in por_dia.values():
        for i, a in enumerate(janelas):
            for b in janelas[i + 1:]:
                if a['hora_inicio'] < b['hora_fim'] and b['hora_inicio'] < a['hora_fim']:
                    pares.append((a['pk'], b['pk']))
    return pares


def _regras(Bloqueio):
    """({id: (regra canonica|None, limite de COUNT/UNTIL|None)}, [(id, regra, erro)])."""
    from aranha_estetica.utils.recorrencia import RegraNaoSuportada, normalizar_regra_recorrencia

    ok, ruins = {}, []
    for pk, regra, inicio in Bloqueio.objects.order_by('pk').values_list(
        'pk', 'regra_recorrencia', 'data_hora_inicio',
    ):
        try:
            ok[pk] = normalizar_regra_recorrencia(regra, dtstart=inicio)
        except RegraNaoSuportada as exc:
            ruins.append((pk, regra, str(exc)))
    return ok, ruins


def pre_checar(apps, schema_editor):
    erros = []
    pares = _janelas_sobrepostas(apps.get_model(APP, 'DisponibilidadeProfissional'))
    if pares:
        erros.append(
            'janelas de expediente sobrepostas do mesmo profissional/dia '
            f'(disponibilidade_profissional, pares de id): {pares}'
        )
    _ok, ruins = _regras(apps.get_model(APP, 'BloqueioAgenda'))
    if ruins:
        erros.append(
            'regra de recorrencia fora do subconjunto suportado (bloqueio_agenda id, regra, motivo): '
            f'{ruins}'
        )
    if erros:
        raise RuntimeError(
            '0048 (agenda unificada) abortada — corrija os dados e rode de novo '
            '(pre-checagem SQL no runbook, docs/specs/padrao-sql.md §4.a): ' + ' | '.join(erros)
        )


def copiar_agenda(apps, schema_editor):
    Disponibilidade = apps.get_model(APP, 'DisponibilidadeProfissional')
    ExcecaoAntiga = apps.get_model(APP, 'ExcecaoDisponibilidade')
    Bloqueio = apps.get_model(APP, 'BloqueioAgenda')
    Feriado = apps.get_model(APP, 'Feriado')
    AgendaHorario = apps.get_model(APP, 'AgendaHorario')
    AgendaExcecao = apps.get_model(APP, 'AgendaExcecao')
    LogAuditoria = apps.get_model(APP, 'LogAuditoria')
    Cliente = apps.get_model(APP, 'Cliente')

    # 1. grade semanal (dia ISO)
    AgendaHorario.objects.bulk_create([
        AgendaHorario(profissional_id=d.profissional_id, dia_semana=_dia_iso(d.dia_semana),
                      hora_inicio=d.hora_inicio, hora_fim=d.hora_fim)
        for d in Disponibilidade.objects.order_by('pk')
    ], batch_size=500)

    # 2. bloqueios com o MESMO id (logs com tabela='bloqueio_agenda' seguem validos)
    avisos = defaultdict(list)
    normalizadas = {}
    regras, _ruins = _regras(Bloqueio)  # pre_checar ja garantiu _ruins vazio
    bloqueios = []
    for b in Bloqueio.objects.order_by('pk'):
        regra, limite = regras[b.pk]
        fim_recorrencia = b.recorrencia_ate if regra else None
        if limite and (fim_recorrencia is None or limite < fim_recorrencia):
            fim_recorrencia = limite
        if regra and (regra != b.regra_recorrencia or fim_recorrencia != b.recorrencia_ate):
            normalizadas[str(b.pk)] = {
                'de': [b.regra_recorrencia, b.recorrencia_ate.isoformat() if b.recorrencia_ate else None],
                'para': [regra, fim_recorrencia.isoformat() if fim_recorrencia else None],
            }
        if b.recorrencia_ate and not regra:
            avisos['fim_recorrencia_sem_regra_descartado'].append(b.pk)
        motivo = b.motivo or ''
        gcal = motivo.startswith('gcal:') and b.profissional_id is not None
        bloqueios.append(AgendaExcecao(
            id=b.pk, tipo='BLOQUEIO', origem='GCAL' if gcal else 'MANUAL',
            profissional_id=b.profissional_id,
            data_hora_inicio=b.data_hora_inicio, data_hora_fim=b.data_hora_fim,
            regra_recorrencia=regra, data_fim_recorrencia=fim_recorrencia, motivo=motivo,
        ))
    AgendaExcecao.objects.bulk_create(bloqueios, batch_size=500)
    if bloqueios and _pg(schema_editor):
        schema_editor.execute(
            "SELECT setval(pg_get_serial_sequence('agenda_excecao', 'id'), "
            "(SELECT MAX(id) FROM agenda_excecao))", None,
        )

    # 3. excecoes de 1 dia (ids novos; mapa no log)
    mapa_excecoes = {}
    folgas = set(
        ExcecaoAntiga.objects.filter(tipo='FOLGA').values_list('profissional_id', 'data')
    )
    for e in ExcecaoAntiga.objects.order_by('pk'):
        tipo, hora_inicio, hora_fim = e.tipo, e.hora_inicio, e.hora_fim
        if tipo == 'FOLGA':
            if hora_inicio or hora_fim:
                avisos['folga_com_horas_ignoradas'].append(e.pk)
            hora_inicio = hora_fim = None
        elif hora_fim <= hora_inicio:  # CHECK antigo garante as duas horas
            if (e.profissional_id, e.data) in folgas:
                avisos['horario_diferente_invalido_descartado'].append(e.pk)
                continue
            avisos['horario_diferente_invalido_vira_folga'].append(e.pk)
            folgas.add((e.profissional_id, e.data))
            tipo, hora_inicio, hora_fim = 'FOLGA', None, None
        nova = AgendaExcecao.objects.create(
            tipo=tipo, origem='MANUAL', profissional_id=e.profissional_id,
            data_inicio=e.data, data_fim=e.data, hora_inicio=hora_inicio, hora_fim=hora_fim,
            motivo=e.motivo or '',
        )
        mapa_excecoes[str(e.pk)] = nova.pk

    # 4. feriados/recessos = FOLGA da clinica (profissional NULL)
    mapa_feriados = {}
    for f in Feriado.objects.order_by('pk'):
        origem = ORIGEM_POR_ESCOPO.get(f.escopo)
        if origem is None:
            avisos['feriado_escopo_desconhecido_vira_recesso'].append(f.pk)
            origem = 'RECESSO'
        nova = AgendaExcecao.objects.create(
            tipo='FOLGA', origem=origem, profissional_id=None,
            data_inicio=f.data, data_fim=f.data, motivo=f.nome or '',
            observacao=f.observacao or '', ativo=f.bloqueia_agendamento,
            criado_em=f.criado_em or django.utils.timezone.now(),
        )
        mapa_feriados[str(f.pk)] = nova.pk

    # 5. trilha: mapa de ids (logs antigos de excecao/feriado continuam rastreaveis)
    if Cliente.objects.exists() or mapa_excecoes or bloqueios or avisos:
        LogAuditoria.objects.create(
            acao='migration 0048: agenda unificada',
            tabela='agenda_excecao',
            detalhes={
                'excecao_disponibilidade': mapa_excecoes,
                'feriado': mapa_feriados,
                'bloqueio_agenda': 'ids preservados',
                'recorrencia_normalizada': normalizadas,
                **{k: v for k, v in avisos.items()},
            },
        )
    if _pg(schema_editor):
        # FKs DEFERRABLE: dispara os checks agora (ALTER TABLE seguinte nao pode
        # ver trigger events pendentes — padrao 0034-0047)
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


def restaurar_agenda(apps, schema_editor):
    """Reverso (dev/testes): copia de volta p/ as 4 tabelas antigas."""
    Disponibilidade = apps.get_model(APP, 'DisponibilidadeProfissional')
    ExcecaoAntiga = apps.get_model(APP, 'ExcecaoDisponibilidade')
    Bloqueio = apps.get_model(APP, 'BloqueioAgenda')
    Feriado = apps.get_model(APP, 'Feriado')
    AgendaHorario = apps.get_model(APP, 'AgendaHorario')
    AgendaExcecao = apps.get_model(APP, 'AgendaExcecao')
    LogAuditoria = apps.get_model(APP, 'LogAuditoria')

    grade, vistos = [], set()
    for h in AgendaHorario.objects.order_by('pk'):
        chave = (h.profissional_id, _dia_antigo(h.dia_semana), h.hora_inicio)
        if chave in vistos:  # UNIQUE antigo (prof, dia, hora_inicio)
            continue
        vistos.add(chave)
        grade.append(Disponibilidade(profissional_id=h.profissional_id, dia_semana=chave[1],
                                     hora_inicio=h.hora_inicio, hora_fim=h.hora_fim))
    Disponibilidade.objects.bulk_create(grade, batch_size=500)

    perdas = defaultdict(list)
    bloqueios, excecoes, feriados = [], [], []
    vistos_exc, vistos_fer = set(), set()
    for e in AgendaExcecao.objects.order_by('pk'):
        if e.origem_ref:
            perdas['origem_ref'].append(e.pk)
        if e.tipo == 'BLOQUEIO':
            if not e.ativo:
                perdas['bloqueio_inativo'].append(e.pk)
                continue
            bloqueios.append(Bloqueio(
                id=e.pk, profissional_id=e.profissional_id,
                data_hora_inicio=e.data_hora_inicio, data_hora_fim=e.data_hora_fim,
                motivo=e.motivo or None, regra_recorrencia=e.regra_recorrencia or '',
                recorrencia_ate=e.data_fim_recorrencia,
            ))
            continue
        n_dias = (e.data_fim - e.data_inicio).days
        dias = [date.fromordinal(e.data_inicio.toordinal() + i) for i in range(n_dias + 1)]
        if e.profissional_id is None:  # FOLGA da clinica -> feriado
            escopo = ESCOPO_POR_ORIGEM.get(e.origem, 'CLINICA')
            for dia in dias:
                if (dia, escopo) in vistos_fer:
                    continue
                vistos_fer.add((dia, escopo))
                feriados.append((e.criado_em, Feriado(
                    data=dia, nome=(e.motivo or 'Feriado')[:120], escopo=escopo,
                    bloqueia_agendamento=e.ativo, observacao=e.observacao[:255] or None,
                )))
            continue
        if not e.ativo:
            perdas['excecao_inativa'].append(e.pk)
            continue
        for dia in dias:
            if (e.profissional_id, dia, e.tipo) in vistos_exc:
                continue
            vistos_exc.add((e.profissional_id, dia, e.tipo))
            excecoes.append(ExcecaoAntiga(
                profissional_id=e.profissional_id, data=dia, tipo=e.tipo,
                hora_inicio=e.hora_inicio, hora_fim=e.hora_fim, motivo=e.motivo or None,
            ))
    Bloqueio.objects.bulk_create(bloqueios, batch_size=500)
    ExcecaoAntiga.objects.bulk_create(excecoes, batch_size=500)
    for criado_em, feriado in feriados:
        feriado.save()
        Feriado.objects.filter(pk=feriado.pk).update(criado_em=criado_em)  # auto_now_add sobrescreve
    if _pg(schema_editor) and bloqueios:
        schema_editor.execute(
            "SELECT setval(pg_get_serial_sequence('bloqueio_agenda', 'id'), "
            "(SELECT MAX(id) FROM bloqueio_agenda))", None,
        )
    if perdas:
        LogAuditoria.objects.create(
            acao='migration 0048 (reversa): agenda antiga restaurada com perdas',
            tabela='agenda_excecao', detalhes=dict(perdas),
        )
    if _pg(schema_editor):
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


def aplicar_objetos_pg(apps, schema_editor):
    if not _pg(schema_editor):
        return
    schema_editor.execute('CREATE EXTENSION IF NOT EXISTS btree_gist')  # ja vem da 0035
    for _nome, _tabela, sql in SQL_PG:
        schema_editor.execute(sql, None)


def remover_objetos_pg(apps, schema_editor):
    if not _pg(schema_editor):
        return
    for nome, tabela, _sql in reversed(SQL_PG):
        schema_editor.execute(f'ALTER TABLE {tabela} DROP CONSTRAINT IF EXISTS {nome}', None)


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0047_termo_saude_reembolso_promocao_geral'),
    ]

    operations = [
        migrations.CreateModel(
            name='AgendaExcecao',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(choices=[('FOLGA', 'Folga'), ('HORARIO_DIFERENTE', 'Horário diferente'), ('BLOQUEIO', 'Bloqueio')], max_length=20)),
                ('origem', models.CharField(choices=[('MANUAL', 'Manual (painel)'), ('FERIADO_NACIONAL', 'Feriado nacional'), ('FERIADO_LOCAL', 'Feriado local (estadual/municipal)'), ('RECESSO', 'Recesso da clínica'), ('GCAL', 'Google Agenda')], db_comment='MANUAL (painel) | FERIADO_NACIONAL/FERIADO_LOCAL (carregar_feriados) | RECESSO (clinica) | GCAL (importado do Google Calendar).', db_default='MANUAL', default='MANUAL', max_length=20)),
                ('origem_ref', models.CharField(blank=True, db_comment='Id do evento no sistema de origem (GCAL): torna a importacao idempotente.', max_length=255, null=True)),
                ('data_inicio', models.DateField(blank=True, db_comment='FOLGA/HORARIO_DIFERENTE: primeiro dia (data local). NULL em BLOQUEIO.', null=True)),
                ('data_fim', models.DateField(blank=True, db_comment='FOLGA: ultimo dia (inclusivo). HORARIO_DIFERENTE: = data_inicio.', null=True)),
                ('hora_inicio', models.TimeField(blank=True, null=True)),
                ('hora_fim', models.TimeField(blank=True, null=True)),
                ('data_hora_inicio', models.DateTimeField(blank=True, null=True)),
                ('data_hora_fim', models.DateTimeField(blank=True, null=True)),
                ('regra_recorrencia', models.CharField(blank=True, db_comment='RRULE (RFC 5545) no subconjunto suportado (fn_ocorrencias_recorrencia): FREQ DAILY/WEEKLY/MONTHLY, INTERVAL, BYDAY (ordinal so em MONTHLY) ou BYMONTHDAY. DTSTART = data_hora_inicio (hora local). Sem COUNT/UNTIL: use data_fim_recorrencia.', help_text='Só BLOQUEIO. Ex.: FREQ=WEEKLY;BYDAY=WE (toda quarta), FREQ=MONTHLY;BYDAY=1SA (1º sábado), FREQ=DAILY;INTERVAL=2. Vazio = bloqueio único.', max_length=255, null=True)),
                ('data_fim_recorrencia', models.DateField(blank=True, db_comment='Ultimo dia (inclusivo) em que uma ocorrencia pode comecar.', help_text='Último dia (inclusivo) em que uma ocorrência pode começar. Vazio = sem fim.', null=True)),
                ('motivo', models.TextField(blank=True, db_default='', default='')),
                ('observacao', models.TextField(blank=True, db_default='', default='')),
                ('ativo', models.BooleanField(db_column='eh_ativo', db_default=True, default=True)),
                ('criado_em', models.DateTimeField(db_default=django.db.models.functions.datetime.Now(), default=django.utils.timezone.now, editable=False)),
                ('atualizado_em', models.DateTimeField(auto_now=True, db_default=django.db.models.functions.datetime.Now())),
                ('profissional', models.ForeignKey(blank=True, db_index=False, help_text='Vazio = clínica inteira (feriado, recesso, bloqueio global).', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='agenda_excecoes', to='aranha_estetica.profissional')),
                ('usuario_autor', models.ForeignKey(blank=True, db_index=False, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'agenda_excecao',
                'db_table_comment': 'Excecoes da grade semanal: FOLGA (dia(s) inteiro(s)), HORARIO_DIFERENTE (substitui a grade num dia) e BLOQUEIO (intervalo exato, pontual ou recorrente). profissional_id NULL = clinica inteira (feriado, recesso, bloqueio global). eh_ativo=false = registrada mas ignorada (ex.: feriado nacional em que a clinica abre).',
                'indexes': [
                    aranha_estetica.db.indices.Indice(fields=['profissional', 'data_fim'], name='ix_agenda_excecao_profissional_data_fim'),
                    aranha_estetica.db.indices.Indice(fields=['profissional', 'data_hora_fim'], name='ix_agenda_excecao_profissional_data_hora_fim'),
                    aranha_estetica.db.indices.Indice(condition=models.Q(('usuario_autor__isnull', False)), fields=['usuario_autor'], name='ix_agenda_excecao_usuario_autor_parcial'),
                ],
                'constraints': [
                    models.CheckConstraint(condition=models.Q(('tipo__in', ['FOLGA', 'HORARIO_DIFERENTE', 'BLOQUEIO'])), name='ck_agenda_excecao_tipo'),
                    models.CheckConstraint(condition=models.Q(('origem__in', ['MANUAL', 'FERIADO_NACIONAL', 'FERIADO_LOCAL', 'RECESSO', 'GCAL'])), name='ck_agenda_excecao_origem'),
                    models.CheckConstraint(condition=models.Q(models.Q(('tipo', 'FOLGA'), _negated=True), models.Q(('data_fim__gte', models.F('data_inicio')), ('data_fim__isnull', False), ('data_fim_recorrencia__isnull', True), ('data_hora_fim__isnull', True), ('data_hora_inicio__isnull', True), ('data_inicio__isnull', False), ('hora_fim__isnull', True), ('hora_inicio__isnull', True), ('regra_recorrencia__isnull', True)), _connector='OR'), name='ck_agenda_excecao_folga'),
                    models.CheckConstraint(condition=models.Q(models.Q(('tipo', 'HORARIO_DIFERENTE'), _negated=True), models.Q(('data_fim', models.F('data_inicio')), ('data_fim__isnull', False), ('data_fim_recorrencia__isnull', True), ('data_hora_fim__isnull', True), ('data_hora_inicio__isnull', True), ('data_inicio__isnull', False), ('hora_fim__gt', models.F('hora_inicio')), ('hora_fim__isnull', False), ('hora_inicio__isnull', False), ('profissional__isnull', False), ('regra_recorrencia__isnull', True)), _connector='OR'), name='ck_agenda_excecao_horario_diferente'),
                    models.CheckConstraint(condition=models.Q(models.Q(('tipo', 'BLOQUEIO'), _negated=True), models.Q(('data_fim__isnull', True), ('data_hora_fim__gt', models.F('data_hora_inicio')), ('data_hora_fim__isnull', False), ('data_hora_inicio__isnull', False), ('data_inicio__isnull', True), ('hora_fim__isnull', True), ('hora_inicio__isnull', True)), _connector='OR'), name='ck_agenda_excecao_bloqueio'),
                    models.CheckConstraint(condition=models.Q(('regra_recorrencia__isnull', False), ('data_fim_recorrencia__isnull', True), _connector='OR'), name='ck_agenda_excecao_recorrencia'),
                    models.CheckConstraint(condition=models.Q(models.Q(('origem__in', ['FERIADO_NACIONAL', 'FERIADO_LOCAL', 'RECESSO']), _negated=True), models.Q(('profissional__isnull', True), ('tipo', 'FOLGA')), _connector='OR'), name='ck_agenda_excecao_feriado_global'),
                    models.CheckConstraint(condition=models.Q(models.Q(('origem', 'GCAL'), _negated=True), models.Q(('profissional__isnull', False), ('tipo', 'BLOQUEIO')), _connector='OR'), name='ck_agenda_excecao_gcal'),
                    models.UniqueConstraint(condition=models.Q(('ativo', True), ('tipo', 'HORARIO_DIFERENTE')), fields=('profissional', 'data_inicio'), name='uq_agenda_excecao_horario_diferente_dia'),
                    models.UniqueConstraint(condition=models.Q(('origem__in', ['FERIADO_NACIONAL', 'FERIADO_LOCAL'])), fields=('origem', 'data_inicio'), name='uq_agenda_excecao_feriado'),
                    models.UniqueConstraint(condition=models.Q(('origem', 'GCAL'), ('origem_ref__isnull', False)), fields=('profissional', 'origem_ref'), name='uq_agenda_excecao_gcal_evento'),
                ],
            },
        ),
        migrations.CreateModel(
            name='AgendaHorario',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('dia_semana', models.SmallIntegerField(choices=[(1, 'Segunda-feira'), (2, 'Terça-feira'), (3, 'Quarta-feira'), (4, 'Quinta-feira'), (5, 'Sexta-feira'), (6, 'Sábado'), (7, 'Domingo')], db_comment='ISO 8601: 1=segunda ... 7=domingo (= extract(isodow) e date.isoweekday()).')),
                ('hora_inicio', models.TimeField()),
                ('hora_fim', models.TimeField()),
                ('criado_em', models.DateTimeField(db_default=django.db.models.functions.datetime.Now(), default=django.utils.timezone.now, editable=False)),
                ('atualizado_em', models.DateTimeField(auto_now=True, db_default=django.db.models.functions.datetime.Now())),
                ('profissional', models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.CASCADE, related_name='agenda_horarios', to='aranha_estetica.profissional')),
            ],
            options={
                'db_table': 'agenda_horario',
                'db_table_comment': 'Grade semanal de expediente do profissional (varias janelas por dia, sem sobreposicao).',
                'constraints': [
                    models.CheckConstraint(condition=models.Q(('dia_semana__range', (1, 7))), name='ck_agenda_horario_dia_semana'),
                    models.CheckConstraint(condition=models.Q(('hora_fim__gt', models.F('hora_inicio'))), name='ck_agenda_horario_fim_apos_inicio'),
                ],
            },
        ),
        migrations.RunPython(pre_checar, migrations.RunPython.noop),
        migrations.RunPython(copiar_agenda, restaurar_agenda),
        migrations.RunPython(aplicar_objetos_pg, remover_objetos_pg),
        PadronizarNomes(tabelas=['agenda_horario', 'agenda_excecao']),
        migrations.DeleteModel(name='Feriado'),
        migrations.DeleteModel(name='BloqueioAgenda'),
        migrations.DeleteModel(name='DisponibilidadeProfissional'),
        migrations.DeleteModel(name='ExcecaoDisponibilidade'),
    ]
