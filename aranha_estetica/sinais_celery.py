"""Contexto do banco nas tasks do Celery (docs/specs/padrao-sql.md §5.3).

Worker real (CELERY_WORKER_ENABLED): cada task grava ``app.origem='celery:<task>'``
antes de rodar e limpa depois — a trilha ``historico_alteracao`` mostra que a
mudanca veio de um job, sem autor. Task EAGER (producao sem worker, testes) roda
dentro do request/cron que a disparou e herda o contexto dele (``web`` com o
usuario, ou ``cron:<job>``): aqui e no-op.
"""
import logging

from celery.signals import task_postrun, task_prerun
from django.db import DatabaseError

from .utils.banco import definir_contexto_banco

logger = logging.getLogger(__name__)


def _eager(task):
    return bool(getattr(getattr(task, 'request', None), 'is_eager', False))


@task_prerun.connect(dispatch_uid='aranha_estetica_celery_contexto_banco')
def definir_contexto_task(sender=None, task=None, **kwargs):
    if task is None or _eager(task):
        return
    try:
        definir_contexto_banco('', '', f'celery:{task.name}')
    except DatabaseError:  # a task segue; a trilha fica sem origem
        logger.warning('contexto_banco_celery_falhou', exc_info=True)


@task_postrun.connect(dispatch_uid='aranha_estetica_celery_limpa_contexto_banco')
def limpar_contexto_task(sender=None, task=None, **kwargs):
    if task is None or _eager(task):
        return
    try:
        definir_contexto_banco()
    except DatabaseError:
        from django.db import connections
        connections['default'].close()
