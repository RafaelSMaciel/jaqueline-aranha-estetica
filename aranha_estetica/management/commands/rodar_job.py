"""Roda um job periodico (jobs.py) pela linha de comando — o mesmo do cron HTTP.

Uso:
  python manage.py rodar_job emails
  python manage.py rodar_job housekeeping
  python manage.py rodar_job --lista

As trilhas saem com origem 'comando:<job>' (sem autor). Erro = exit code 1 (o agendador do
servidor enxerga a falha, como o 500 do /cron/run/).
"""
from django.core.management.base import BaseCommand, CommandError

from aranha_estetica.views.cron import JOB_MAP, executar


class Command(BaseCommand):
    help = 'Roda um job periodico (o mesmo do /cron/run/<job>/).'

    def add_arguments(self, parser):
        parser.add_argument('job', nargs='?', help='nome do job (veja --lista)')
        parser.add_argument('--lista', action='store_true', help='lista os jobs e sai')

    def handle(self, *args, job=None, lista=False, **options):
        if lista or not job:
            self.stdout.write('\n'.join(sorted(JOB_MAP)))
            return
        if job not in JOB_MAP:
            raise CommandError(f'job desconhecido: {job} (disponiveis: {", ".join(sorted(JOB_MAP))})')
        try:
            resultado = executar(job, origem=f'comando:{job}')
        except Exception as exc:
            raise CommandError(f'{job} falhou: {exc}') from exc
        self.stdout.write(self.style.SUCCESS(f'{job}: {resultado or "ok"}'))
