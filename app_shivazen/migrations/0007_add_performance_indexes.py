from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app_shivazen', '0006_add_status_reagendado'),
    ]

    operations = [
        # Atendimento: profissional + data_hora_inicio — usado em dashboard, disponibilidade e painel
        migrations.AddIndex(
            model_name='atendimento',
            index=models.Index(
                fields=['profissional', 'data_hora_inicio'],
                name='idx_atn_prof_data',
            ),
        ),
        # Notificacao: atendimento + status_envio — usado em tarefas Celery e webhook
        migrations.AddIndex(
            model_name='notificacao',
            index=models.Index(
                fields=['atendimento', 'status_envio'],
                name='idx_notif_atn_status',
            ),
        ),
        # Procedimento: ativo + categoria — usado nas paginas publicas de especialidades
        migrations.AddIndex(
            model_name='procedimento',
            index=models.Index(
                fields=['ativo', 'categoria'],
                name='idx_proc_ativo_cat',
            ),
        ),
    ]
