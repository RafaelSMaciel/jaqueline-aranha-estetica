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
- `SET LOCAL lock_timeout = '5s'`: o deploy antigo continua no ar durante o
  pre-deploy; uma query longa/"idle in transaction" dele deixava o ALTER
  esperando sem fim, com as tabelas ja alteradas travadas (requests enfileiram
  atras). Com o timeout o migrate falha (LockNotAvailable), tudo volta e o
  deploy e refeito depois.
- Falhou: escreve em stderr 'migrate_atomico: FALHOU - transacao desfeita;
  banco continua em <app.ultima_migration>' (lida de django_migrations apos o
  rollback) e re-levanta a excecao (pre-deploy sai != 0, deploy antigo no ar).
- Depois do COMMIT o codigo antigo roda contra o schema novo ate o container
  novo passar no healthcheck: upgrades com rename/delete (ex.: 0026 -> atual)
  em horario sem movimento.
- Nao use com migrations `atomic = False`/CONCURRENTLY (nao ha nenhuma hoje).
- Fora do Postgres (SQLite em dev) cai no `migrate` normal: o SQLite desliga
  checagem de FK por migration e isso nao funciona dentro de transacao.
- Rollback de deploy = restaurar o dump, nao `migrate <app> <anterior>`.
"""
from django.core.management.commands.migrate import Command as MigrateCommand
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.db.migrations.recorder import MigrationRecorder


class Command(MigrateCommand):
    help = 'Aplica as migrations numa transacao unica no Postgres (tudo ou nada).'

    # SET LOCAL: vale so ate o fim da transacao do upgrade
    LOCK_TIMEOUT_SQL = "SET LOCAL lock_timeout = '5s'"

    # App cujo estado o log de falha informa (a unica com migrations proprias)
    APP_PRINCIPAL = 'aranha_estetica'

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
                with connection.cursor() as cursor:
                    cursor.execute(self.LOCK_TIMEOUT_SQL)
                return super().handle(*args, **options)
        except Exception:
            # Log do pre-deploy: deixa explicito que nada ficou pela metade
            self.stderr.write(
                'migrate_atomico: FALHOU - transacao desfeita; banco continua em '
                f'{self._ultima_migration_aplicada(options.get("app_label"))}'
            )
            raise
        finally:
            self._atomico = False

    def _ultima_migration_aplicada(self, app_label=None):
        """'<app>.<migration>' mais recente em django_migrations (apos o rollback)."""
        app = app_label or self.APP_PRINCIPAL
        try:
            aplicadas = MigrationRecorder(connections[self._alias]).applied_migrations()
        except Exception as exc:  # noqa: BLE001 — conexao caiu: nao mascara o erro original
            return f'<desconhecida: {type(exc).__name__} ao ler django_migrations>'
        nomes = sorted(nome for (label, nome) in aplicadas if label == app)
        return f'{app}.{nomes[-1]}' if nomes else f'{app} sem nenhuma migration aplicada'

    def migration_progress_callback(self, action, migration=None, fake=False):
        # Antes do " OK": violacao de FK aparece atribuida a migration certa
        if self._atomico and action == 'apply_success' and not fake:
            with connections[self._alias].cursor() as cursor:
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
                cursor.execute('SET CONSTRAINTS ALL DEFERRED')
        super().migration_progress_callback(action, migration, fake)
