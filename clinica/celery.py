import os
from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'clinica.settings')

app = Celery('clinica')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps (tasks.py). Os jobs de
# manutencao (aranha_estetica.tasks_manutencao) entram via CELERY_IMPORTS.
app.autodiscover_tasks()
