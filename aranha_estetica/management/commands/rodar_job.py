"""Roda um job periodico (jobs.py) pela linha de comando — o mesmo do cron HTTP.

Uso:
  python manage.py rodar_job emails
  python manage.py rodar_job housekeeping
  python manage.py rodar_job aniversario --forcar   # de novo no mesmo dia (depois de uma falha)
  python manage.py rodar_job --lista

As trilhas saem com origem 'comando:<job>' (sem autor). Erro = exit code 1 (o agendador do
servidor enxerga a falha, como o 500 do /cron/run/). Job de mensagem que so roda 1x por dia
(jobs.JOBS_UMA_VEZ_POR_DIA) e ja rodou hoje: avisa e nao roda (exit 0), salvo --forcar.
"""
from django.core.management.base import BaseCommand, CommandError

from aranha_estetica.jobs import JOB_MAP, JobJaExecutado, executar


class Command(BaseCommand):
    help = 'Roda um job periodico (o mesmo do /cron/run/<job>/).'

    def add_arguments(self, parser):
        parser.add_argument('job', nargs='?', help='nome do job (veja --lista)')
        parser.add_argument('--lista', action='store_true', help='lista os jobs e sai')
        parser.add_argument('--forcar', action='store_true',
                            help='roda mesmo se o job de mensagem ja rodou hoje (pode reenviar)')

    def handle(self, *args, job=None, lista=False, forcar=False, **options):
        if lista or not job:
            self.stdout.write('\n'.join(sorted(JOB_MAP)))
            return
        if job not in JOB_MAP:
            raise CommandError(f'job desconhecido: {job} (disponiveis: {", ".join(sorted(JOB_MAP))})')
        try:
            resultado = executar(job, origem=f'comando:{job}', forcar=forcar)
        except JobJaExecutado:
            self.stdout.write(self.style.WARNING(f'{job}: ja rodou hoje (use --forcar para rodar de novo)'))
            return
        except Exception as exc:
            raise CommandError(f'{job} falhou: {exc}') from exc
        self.stdout.write(self.style.SUCCESS(f'{job}: {resultado or "ok"}'))
