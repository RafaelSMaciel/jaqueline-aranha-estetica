"""Consultas que mais pesam no Postgres (pg_stat_statements) — para medir antes de criar indice.

    python manage.py consultas_lentas                  # top 15 por tempo total
    python manage.py consultas_lentas --ordem media --top 30
    python manage.py consultas_lentas --zerar          # recomeca a contagem (depois de um indice)

So leitura (fora o --zerar, que limpa so as estatisticas). O texto vem normalizado pelo Postgres
($1, $2... no lugar dos valores): nenhum dado de cliente sai na tela. Sem a extensao, explica
como ligar (PROJECT.md §14 h).
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection

ORDENS = {
    'total': 'total_exec_time',
    'media': 'mean_exec_time',
    'chamadas': 'calls',
    'linhas': 'rows',
}

COMO_LIGAR = (
    'pg_stat_statements indisponivel. Para ligar (uma vez, superusuario):\n'
    "  ALTER SYSTEM SET shared_preload_libraries = 'pg_stat_statements';   -- e reinicie o Postgres\n"
    '  CREATE EXTENSION IF NOT EXISTS pg_stat_statements;'
)


class Command(BaseCommand):
    help = 'Lista as consultas que mais pesam no Postgres (pg_stat_statements).'

    def add_arguments(self, parser):
        parser.add_argument('--top', type=int, default=15)
        parser.add_argument('--ordem', choices=sorted(ORDENS), default='total')
        parser.add_argument('--zerar', action='store_true', help='pg_stat_statements_reset()')

    def handle(self, *args, top, ordem, zerar, **options):
        if connection.vendor != 'postgresql':
            raise CommandError('consultas_lentas: so no Postgres (pg_stat_statements).')
        try:
            with connection.cursor() as cursor:
                if zerar:
                    cursor.execute('SELECT pg_stat_statements_reset()')
                    self.stdout.write('Estatisticas zeradas.')
                    return
                cursor.execute(
                    f'SELECT calls, total_exec_time, mean_exec_time, rows, query '
                    f'FROM pg_stat_statements WHERE dbid = (SELECT oid FROM pg_database '
                    f'WHERE datname = current_database()) '
                    f'ORDER BY {ORDENS[ordem]} DESC LIMIT %s',
                    [max(1, top)],
                )
                linhas = cursor.fetchall()
        except DatabaseError as exc:
            raise CommandError(f'{COMO_LIGAR}\n({exc.__class__.__name__}: {str(exc).strip()[:200]})') from exc

        self.stdout.write(f'{"chamadas":>9} {"total ms":>11} {"media ms":>9} {"linhas":>9}  consulta')
        for calls, total, media, rows, query in linhas:
            texto = ' '.join(str(query).split())[:140]
            self.stdout.write(f'{calls:>9} {total:>11.1f} {media:>9.2f} {rows:>9}  {texto}')
