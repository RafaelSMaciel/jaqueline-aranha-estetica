"""Popula dados de demonstracao (dev/staging).

Substitui a antiga view setup_seed acessivel via URL, que expunha o token
em query params e logs. Uso:

    python manage.py seed            # roda contra ambiente nao-producao
    python manage.py seed --force    # permite rodar em producao (cuidado)
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Popula o banco com dados de demonstracao (dev/staging).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Permite rodar mesmo com RAILWAY_ENVIRONMENT_NAME definido (producao).',
        )

    def handle(self, *args, **options):
        import os

        em_producao = bool(os.environ.get('RAILWAY_ENVIRONMENT_NAME'))
        if em_producao and not options['force']:
            raise CommandError(
                'Recusando rodar seed em producao. Use --force se tem certeza.'
            )

        seed_path = settings.BASE_DIR / 'seed.py'
        if not seed_path.exists():
            self.stdout.write(self.style.WARNING(
                'seed.py nao existe. Crie o arquivo na raiz do projeto com '
                'uma funcao seed() que popula os dados.'
            ))
            return

        import importlib.util
        spec = importlib.util.spec_from_file_location('seed', seed_path)
        seed_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(seed_module)
        seed_module.seed()
        self.stdout.write(self.style.SUCCESS('Seed executado com sucesso.'))
