# Formularios, notificacoes e modalidade: todo atendimento e presencial e os tipos de notificacao
# sem produtor saem.
#
# - Modalidade: sai Procedimento.modalidade/link_videoconferencia (+ ck_procedimento_modalidade) e o
#   escopo MODALIDADE das fichas. A regra de ficha aplicavel fica com 3 escopos (global, categoria,
#   procedimento) em FormularioAnamneseQuerySet.aplicaveis_a / FormularioAnamnese.aplica_a.
#   Ficha MODALIDADE PRESENCIAL/HIBRIDO -> GLOBAL (vale p/ todo procedimento, como ja valia na
#   pratica); ONLINE (ou outro valor, que nunca casou com procedimento nenhum) -> GLOBAL e inativa.
#   Procedimento ONLINE/HIBRIDO ou com link de videoconferencia passa a ser so presencial.
# - Notificacao: saem LEMBRETE_2H (nenhum job o envia) e PESQUISA (a pesquisa pos-atendimento e
#   oferecida no fim do NPS, sem notificacao propria). Guarda: aborta listando os ids se alguma
#   tiver resposta da cliente (CONFIRMOU/CANCELOU); senao apaga (a cascata leva o link_acesso).
#
# Ordem: RunPython (guarda -> conversoes; ids de tudo no LogAuditoria) -> CHECKs soltos ->
# RemoveField -> CHECKs recriados. Num banco novo (go-live: 0001 -> ultima) nada e encontrado.
# Reverso (dev/testes): colunas voltam com o default (PRESENCIAL / ''); conversoes ficam.
#
# Pre-checagem p/ o runbook:
#   SELECT id, tipo, resposta FROM notificacao WHERE tipo IN ('LEMBRETE_2H', 'PESQUISA');
#   SELECT id, modalidade, eh_ativo FROM formulario WHERE escopo = 'MODALIDADE';
#   SELECT id, modalidade, url_videoconferencia FROM procedimento
#    WHERE modalidade <> 'PRESENCIAL' OR url_videoconferencia <> '';
from django.db import migrations, models

APP = 'aranha_estetica'
TIPOS_REMOVIDOS = ('LEMBRETE_2H', 'PESQUISA')
MODALIDADES_PRESENCIAIS = ('PRESENCIAL', 'HIBRIDO')


def _pg(schema_editor):
    return schema_editor.connection.vendor == 'postgresql'


def guarda_notificacoes(apps, schema_editor):
    Notificacao = apps.get_model(APP, 'Notificacao')
    respondidas = list(
        Notificacao.objects.filter(tipo__in=TIPOS_REMOVIDOS, resposta__isnull=False)
        .order_by('pk').values_list('pk', 'tipo')
    )
    if respondidas:
        raise RuntimeError(
            '0067 abortada: os tipos de notificacao LEMBRETE_2H e PESQUISA deixaram de existir e estas '
            'tem resposta da cliente (decida o destino de cada uma e rode de novo): '
            + ', '.join(f'notificacao.id={pk} ({tipo})' for pk, tipo in respondidas)
        )


def converter(apps, schema_editor):
    Notificacao = apps.get_model(APP, 'Notificacao')
    Formulario = apps.get_model(APP, 'FormularioAnamnese')
    Procedimento = apps.get_model(APP, 'Procedimento')
    LogAuditoria = apps.get_model(APP, 'LogAuditoria')

    detalhes = {}
    notificacoes = Notificacao.objects.filter(tipo__in=TIPOS_REMOVIDOS)
    ids = list(notificacoes.order_by('pk').values_list('pk', flat=True))
    if ids:
        notificacoes.delete()
        detalhes['notificacao_apagada'] = ids

    por_modalidade = Formulario.objects.filter(escopo='MODALIDADE')
    globais = list(por_modalidade.filter(modalidade__in=MODALIDADES_PRESENCIAIS)
                   .order_by('pk').values_list('pk', flat=True))
    desativados = list(por_modalidade.exclude(modalidade__in=MODALIDADES_PRESENCIAIS)
                       .order_by('pk').values_list('pk', flat=True))
    if globais:
        Formulario.objects.filter(pk__in=globais).update(escopo='GLOBAL', modalidade='')
        detalhes['formulario_modalidade_vira_global'] = globais
    if desativados:
        Formulario.objects.filter(pk__in=desativados).update(escopo='GLOBAL', modalidade='', ativo=False)
        detalhes['formulario_online_desativado'] = desativados

    presenciais = list(
        Procedimento.objects.exclude(modalidade='PRESENCIAL', link_videoconferencia='')
        .order_by('pk').values_list('pk', flat=True)
    )
    if presenciais:
        detalhes['procedimento_vira_so_presencial'] = presenciais

    if not detalhes:
        return
    LogAuditoria.objects.create(
        acao='migration 0067: modalidade e tipos de notificacao sem produtor removidos',
        detalhes=detalhes,   # varias tabelas: notificacao, formulario e procedimento
    )
    if _pg(schema_editor):
        # FKs DEFERRABLE: dispara os checks agora, antes do ALTER TABLE (padrao 0034-0051)
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0066_papeis_e_links'),
    ]

    operations = [
        migrations.RunPython(guarda_notificacoes, migrations.RunPython.noop),
        migrations.RunPython(converter, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='formularioanamnese',
            name='ck_formulario_escopo',
        ),
        migrations.RemoveConstraint(
            model_name='formularioanamnese',
            name='ck_formulario_escopo_coerente',
        ),
        migrations.RemoveConstraint(
            model_name='notificacao',
            name='ck_notificacao_tipo',
        ),
        migrations.RemoveConstraint(
            model_name='procedimento',
            name='ck_procedimento_modalidade',
        ),
        migrations.RemoveField(
            model_name='formularioanamnese',
            name='modalidade',
        ),
        migrations.RemoveField(
            model_name='procedimento',
            name='link_videoconferencia',
        ),
        migrations.RemoveField(
            model_name='procedimento',
            name='modalidade',
        ),
        migrations.AlterField(
            model_name='formularioanamnese',
            name='escopo',
            field=models.CharField(choices=[('GLOBAL', 'Global (todo agendamento)'), ('CATEGORIA', 'Por categoria de procedimento'), ('PROCEDIMENTO', 'Por procedimento especifico')], db_default='GLOBAL', default='GLOBAL', max_length=20),
        ),
        migrations.AlterField(
            model_name='notificacao',
            name='tipo',
            field=models.CharField(choices=[('LEMBRETE', 'Lembrete D-1'), ('CONFIRMACAO', 'Confirmação'), ('CANCELAMENTO', 'Cancelamento'), ('NPS', 'Pesquisa NPS'), ('APROVACAO', 'Aprovação Profissional'), ('TERMO', 'Termo de consentimento')], db_default='LEMBRETE', default='LEMBRETE', max_length=30),
        ),
        migrations.AddConstraint(
            model_name='formularioanamnese',
            constraint=models.CheckConstraint(condition=models.Q(('escopo__in', ['GLOBAL', 'CATEGORIA', 'PROCEDIMENTO'])), name='ck_formulario_escopo'),
        ),
        migrations.AddConstraint(
            model_name='formularioanamnese',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(models.Q(('escopo', 'PROCEDIMENTO'), _negated=True), ('procedimento__isnull', False), _connector='OR'), models.Q(models.Q(('escopo', 'CATEGORIA'), _negated=True), models.Q(('categoria', ''), _negated=True), _connector='OR')), name='ck_formulario_escopo_coerente', violation_error_message='Escolha a categoria ou o procedimento do escopo.'),
        ),
        migrations.AddConstraint(
            model_name='notificacao',
            constraint=models.CheckConstraint(condition=models.Q(('tipo__in', ['LEMBRETE', 'CONFIRMACAO', 'CANCELAMENTO', 'NPS', 'APROVACAO', 'TERMO'])), name='ck_notificacao_tipo'),
        ),
    ]
