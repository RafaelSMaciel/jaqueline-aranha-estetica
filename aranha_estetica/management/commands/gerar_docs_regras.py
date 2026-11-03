"""Gera a tabela "Valores-chave" (§24) de docs/REGRAS-DE-NEGOCIO.md a partir do catalogo de regras
(aranha_estetica/regras.py), que le cada valor de onde o codigo o aplica.

Uso:
  python manage.py gerar_docs_regras            # reescreve a tabela entre os marcadores
  python manage.py gerar_docs_regras --check    # so confere; exit 1 se o documento ficou para tras
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from aranha_estetica import regras

DOCUMENTO = Path(settings.BASE_DIR) / 'docs' / 'REGRAS-DE-NEGOCIO.md'


class Command(BaseCommand):
    help = 'Gera a tabela de valores das regras de negocio (docs/REGRAS-DE-NEGOCIO.md §24).'

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true', help='so confere se o documento esta em dia')

    def handle(self, *args, check=False, **options):
        atual = DOCUMENTO.read_text(encoding='utf-8')
        try:
            novo = regras.aplicar_no_documento(atual)
        except ValueError as exc:
            raise CommandError(f'marcadores da tabela nao encontrados em {DOCUMENTO.name}') from exc
        if novo == atual:
            self.stdout.write('REGRAS-DE-NEGOCIO.md em dia.')
            return
        if check:
            raise CommandError('REGRAS-DE-NEGOCIO.md desatualizado: rode `python manage.py gerar_docs_regras`.')
        DOCUMENTO.write_bytes(novo.encode('utf-8'))
        self.stdout.write(self.style.SUCCESS('Tabela de valores atualizada em REGRAS-DE-NEGOCIO.md.'))
