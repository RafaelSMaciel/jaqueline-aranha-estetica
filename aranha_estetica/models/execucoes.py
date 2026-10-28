"""ExecucaoJob: a trava "uma vez por dia" dos jobs de mensagem sem marca por destinatario."""
from django.db import models
from django.db.models.functions import Now


class ExecucaoJob(models.Model):
    """Um job diario (jobs.JOBS_UMA_VEZ_POR_DIA) ja rodou neste dia local.

    A linha nasce ANTES do job (UNIQUE job+dia: a 2a chamada do dia — cron HTTP repetido, comando
    no shell, dois agendadores — recebe 409 em vez de mandar o e-mail de novo). Vale entre
    processos, ao contrario do cache em memoria. Job que falhou no meio fica registrado com o
    erro: rodar de novo no mesmo dia e decisao da equipe (``rodar_job <job> --forcar``)."""
    job = models.CharField(max_length=40)
    dia = models.DateField(db_comment='Dia local (America/Sao_Paulo) da execucao.')
    iniciado_em = models.DateTimeField(auto_now_add=True, db_default=Now())
    concluido_em = models.DateTimeField(blank=True, null=True, db_comment='NULL = em andamento ou falhou.')
    resultado = models.TextField(blank=True, default='', db_default='',
                                 db_comment='Resumo devolvido pelo job ou o erro (tipo da excecao).')

    class Meta:
        managed = True
        db_table = 'execucao_job'
        db_table_comment = (
            'Execucao diaria dos jobs de mensagem sem marca por destinatario (aniversario, pacote '
            'expirando, NPS): UNIQUE job+dia impede o 2o envio no mesmo dia (jobs.executar).'
        )
        constraints = [
            models.UniqueConstraint(fields=['job', 'dia'], name='uq_execucao_job_job_dia'),
        ]

    def __str__(self):
        return f'{self.job} em {self.dia:%d/%m/%Y}'
