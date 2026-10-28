# P11 (jobs): trava diaria dos jobs de mensagem sem marca por destinatario (aniversario, pacote
# expirando, NPS) — UNIQUE job+dia: a 2a chamada do dia recebe 409 (jobs.executar) em vez de mandar
# tudo de novo. Vale entre processos (o cache em memoria nao: o servico Cron roda num processo novo
# a cada 15 min). Tabela SISTEMA (sem dado pessoal: inventario_lgpd).
import django.db.models.functions.datetime
from django.db import migrations, models

from aranha_estetica.db.operacoes import PadronizarNomes


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0066_consentimento_origem_whatsapp'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExecucaoJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('job', models.CharField(max_length=40)),
                ('dia', models.DateField(db_comment='Dia local (America/Sao_Paulo) da execucao.')),
                ('iniciado_em', models.DateTimeField(
                    auto_now_add=True, db_default=django.db.models.functions.datetime.Now())),
                ('concluido_em', models.DateTimeField(blank=True, db_comment='NULL = em andamento ou falhou.', null=True)),
                ('resultado', models.TextField(
                    blank=True, db_comment='Resumo devolvido pelo job ou o erro (tipo da excecao).', db_default='',
                    default='')),
            ],
            options={
                'db_table': 'execucao_job',
                'db_table_comment': (
                    'Execucao diaria dos jobs de mensagem sem marca por destinatario (aniversario, pacote '
                    'expirando, NPS): UNIQUE job+dia impede o 2o envio no mesmo dia (jobs.executar).'
                ),
                'managed': True,
            },
        ),
        migrations.AddConstraint(
            model_name='execucaojob',
            constraint=models.UniqueConstraint(fields=('job', 'dia'), name='uq_execucao_job_job_dia'),
        ),
        PadronizarNomes(tabelas=['execucao_job']),
    ]
