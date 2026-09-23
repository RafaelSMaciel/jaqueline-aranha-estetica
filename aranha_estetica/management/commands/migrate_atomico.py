"""`migrate` em UMA transacao no Postgres (tudo ou nada) — usado no pre-deploy.

Por que: cada migration commita sozinha. Se a N-esima falha (dado legado), o
banco fica meio migrado e o deploy anterior, que continua no ar, passa a dar
500 contra o schema novo. Numa transacao unica, a falha desfaz tudo e o banco
volta intacto ao estado anterior.

Detalhes:
- Aceita as mesmas opcoes do `migrate` (subclasse do comando nativo).
- Apos cada migration aplicada roda `SET CONSTRAINTS ALL IMMEDIATE` (checa ja
  as FKs deferidas daquela migration e esvazia os "pending trigger events"
  que fariam o ALTER TABLE da proxima falhar) e volta p/ DEFERRED (padrao das
  FKs do Django).
- Lock ACCESS EXCLUSIVE nas tabelas alteradas dura o upgrade inteiro (segundos).
- Nao use com migrations `atomic = False`/CONCURRENTLY (nao ha nenhuma hoje).
- Fora do Postgres (SQLite em dev) cai no `migrate` normal: o SQLite desliga
  checagem de FK por migration e isso nao funciona dentro de transacao.
- Rollback de deploy = restaurar o dump, nao `migrate <app> <anterior>`.
"""
from django.core.management.commands.migrate import Command as MigrateCommand
from django.db import DEFAULT_DB_ALIAS, connections, transaction


class Command(MigrateCommand):
    help = 'Aplica as migrations numa transacao unica no Postgres (tudo ou nada).'

    _alias = DEFAULT_DB_ALIAS
    _atomico = False

    def handle(self, *args, **options):
        self._alias = options.get('database') or DEFAULT_DB_ALIAS
        connection = connections[self._alias]
        if connection.vendor != 'postgresql':
            if options.get('verbosity', 1) >= 1:
                self.stdout.write(f'migrate_atomico: {connection.vendor} -> migrate normal.')
            return super().handle(*args, **options)

        self._atomico = True
        try:
            with transaction.atomic(using=self._alias):
                return super().handle(*args, **options)
        finally:
            self._atomico = False

    def migration_progress_callback(self, action, migration=None, fake=False):
        # Antes do " OK": violacao de FK aparece atribuida a migration certa
        if self._atomico and action == 'apply_success' and not fake:
            with connections[self._alias].cursor() as cursor:
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
                cursor.execute('SET CONSTRAINTS ALL DEFERRED')
        super().migration_progress_callback(action, migration, fake)
