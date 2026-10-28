# Habilitacao passa a ser obrigatoria: so atende quem esta habilitado (services/disponibilidade).
# Antes, procedimento SEM nenhuma linha em habilitacao ficava liberado p/ qualquer profissional
# ativo; tirar a ultima habilitacao (p/ suspender um injetavel) liberava a equipe inteira.
# Para nada mudar no dia da troca, cada procedimento ativo sem habilitacao recebe a de todos os
# profissionais ativos (o mesmo conjunto que o atendia ate aqui). Reverso: nao apaga nada (as
# linhas criadas sao indistinguiveis das cadastradas depois; com a regra antiga elas so repetem o
# que ja valia).
from django.db import migrations


def habilitar_quem_ja_atendia(apps, schema_editor):
    Procedimento = apps.get_model('aranha_estetica', 'Procedimento')
    Profissional = apps.get_model('aranha_estetica', 'Profissional')
    Habilitacao = apps.get_model('aranha_estetica', 'Habilitacao')
    ativos = list(Profissional.objects.filter(ativo=True).values_list('pk', flat=True))
    sem_habilitacao = Procedimento.objects.filter(ativo=True).exclude(
        pk__in=Habilitacao.objects.values('procedimento_id'))
    Habilitacao.objects.bulk_create([
        Habilitacao(procedimento_id=proc_id, profissional_id=prof_id)
        for proc_id in sem_habilitacao.values_list('pk', flat=True) for prof_id in ativos
    ])


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0061_fila_email_notificacao'),
    ]

    operations = [
        migrations.RunPython(habilitar_quem_ja_atendia, migrations.RunPython.noop),
        migrations.AlterModelTableComment(
            name='habilitacao',
            table_comment='Profissional x procedimento que ele realiza. So atende quem tem linha aqui: procedimento sem habilitacao nao aparece no agendamento (services/disponibilidade.profissionais_para).',
        ),
    ]
