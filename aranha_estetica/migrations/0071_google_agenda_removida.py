# Google Agenda removido (decisao de negocio: a profissional recebe a agenda pelo feed ICS assinado).
#
# Ordem: RunPython (excecao de origem GCAL vira MANUAL; o motivo 'gcal:...' fica, a linha continua
# bloqueando a agenda e os ids vao p/ o LogAuditoria) -> solta ck_agenda_excecao_gcal,
# uq_agenda_excecao_gcal_evento e o CHECK de origem -> recria ck_agenda_excecao_origem sem 'GCAL'
# -> RemoveField de agenda_excecao.origem_ref (so a importacao do Google usava) e das 4 colunas
# gcal_* do profissional (token OAuth cifrado, calendario, sync token e ultimo sync).
#
# Num banco novo (go-live: 0001 -> ultima) nao ha linha GCAL e o RunPython nao faz nada.
# Reverso (dev/testes): as colunas voltam vazias; a origem convertida continua MANUAL.
from django.db import migrations, models

APP = 'aranha_estetica'


def _pg(schema_editor):
    return schema_editor.connection.vendor == 'postgresql'


def converter_gcal_em_manual(apps, schema_editor):
    AgendaExcecao = apps.get_model(APP, 'AgendaExcecao')
    LogAuditoria = apps.get_model(APP, 'LogAuditoria')

    ids = list(AgendaExcecao.objects.filter(origem='GCAL').order_by('pk').values_list('pk', flat=True))
    if not ids:
        return
    AgendaExcecao.objects.filter(pk__in=ids).update(origem='MANUAL')
    LogAuditoria.objects.create(
        acao='migration 0071: excecoes importadas do Google Agenda viram MANUAL (motivo mantido)',
        tabela='agenda_excecao', detalhes={'agenda_excecao_ids': ids},
    )
    if _pg(schema_editor):
        # FKs DEFERRABLE: dispara os checks agora, antes do ALTER TABLE (padrao 0034-0051)
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0070_execucao_job'),
    ]

    operations = [
        migrations.RunPython(converter_gcal_em_manual, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='agendaexcecao',
            name='ck_agenda_excecao_gcal',
        ),
        migrations.RemoveConstraint(
            model_name='agendaexcecao',
            name='uq_agenda_excecao_gcal_evento',
        ),
        migrations.RemoveConstraint(
            model_name='agendaexcecao',
            name='ck_agenda_excecao_origem',
        ),
        migrations.AlterField(
            model_name='agendaexcecao',
            name='origem',
            field=models.CharField(choices=[('MANUAL', 'Manual (painel)'), ('FERIADO_NACIONAL', 'Feriado nacional'), ('FERIADO_LOCAL', 'Feriado local (estadual/municipal)'), ('RECESSO', 'Recesso da clínica')], db_comment='MANUAL (painel) | FERIADO_NACIONAL/FERIADO_LOCAL (carregar_feriados) | RECESSO (clinica).', db_default='MANUAL', default='MANUAL', max_length=20),
        ),
        migrations.AddConstraint(
            model_name='agendaexcecao',
            constraint=models.CheckConstraint(condition=models.Q(('origem__in', ['MANUAL', 'FERIADO_NACIONAL', 'FERIADO_LOCAL', 'RECESSO'])), name='ck_agenda_excecao_origem'),
        ),
        migrations.RemoveField(
            model_name='agendaexcecao',
            name='origem_ref',
        ),
        migrations.RemoveField(
            model_name='profissional',
            name='gcal_calendar_id',
        ),
        migrations.RemoveField(
            model_name='profissional',
            name='gcal_refresh_token',
        ),
        migrations.RemoveField(
            model_name='profissional',
            name='gcal_sync_token',
        ),
        migrations.RemoveField(
            model_name='profissional',
            name='gcal_ultimo_sync_em',
        ),
    ]
