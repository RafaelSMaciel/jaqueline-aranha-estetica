"""Roda os jobs cuja hora (jobs.JOB_AGENDA, horario de Brasilia) caiu nos ultimos 15 minutos.

E o comando do servico Cron do Railway (railway.cron.json, agenda */15 * * * *): um servico so, no
lugar de um servico por horario. Cada job passa pelo mesmo jobs.executar do cron HTTP (trilha com
origem comando:<job>; job de mensagem que so roda 1x por dia nunca roda 2x) e avisa o monitor
(CRON_MONITOR_URL). Um job que falha nao impede os outros; no fim, exit 1 se algum falhou (o
Railway marca a execucao como falha).

Uso:
  python manage.py rodar_jobs_devidos                         # janela (agora - 15 min, agora]
  python manage.py rodar_jobs_devidos --somente emails,housekeeping   # dev: so estes, se devidos
  python manage.py rodar_jobs_devidos --agora 2026-11-02T09:00         # simula o relogio (local)
"""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from aranha_estetica.jobs import JOB_MAP, JobJaExecutado, avisar_monitor, executar, jobs_devidos

JANELA = timedelta(minutes=15)


class Command(BaseCommand):
    help = 'Roda os jobs da JOB_AGENDA cujo horario caiu na janela anterior (servico Cron, a cada 15 min).'

    def add_arguments(self, parser):
        parser.add_argument('--somente', default='', help='lista de jobs separados por virgula')
        parser.add_argument('--agora', default='', help='instante de referencia (ISO, fuso da clinica)')

    def _agora(self, valor):
        if not valor:
            return timezone.now()
        momento = parse_datetime(valor)
        if momento is None:
            raise CommandError(f'--agora invalido: {valor!r} (use AAAA-MM-DDTHH:MM)')
        return timezone.make_aware(momento) if timezone.is_naive(momento) else momento

    def handle(self, *args, somente='', agora='', **options):
        filtro = {nome.strip() for nome in somente.split(',') if nome.strip()}
        desconhecidos = filtro - set(JOB_MAP)
        if desconhecidos:
            raise CommandError(f'job desconhecido: {", ".join(sorted(desconhecidos))}')
        fim = self._agora(agora)
        devidos = [job for job in jobs_devidos(fim - JANELA, fim) if not filtro or job in filtro]
        falhas = []
        for job in devidos:
            try:
                resultado = executar(job, origem=f'comando:{job}')
            except JobJaExecutado:
                self.stdout.write(f'{job}: ja rodou hoje')
                continue
            except Exception as exc:  # noqa: BLE001 — isola o job; o exit code no fim mostra a falha
                falhas.append(job)
                avisar_monitor(job, ok=False)
                self.stderr.write(f'{job}: FALHOU ({type(exc).__name__}: {exc})')
                continue
            avisar_monitor(job, ok=True)
            self.stdout.write(self.style.SUCCESS(f'{job}: {resultado or "ok"}'))
        if not devidos:
            self.stdout.write('nenhum job devido')
        if falhas:
            raise CommandError(f'falharam: {", ".join(falhas)}')
