"""Carrega feriados nacionais brasileiros (regra em services/feriados).

Feriado = AgendaExcecao FOLGA da clinica inteira (profissional NULL, origem FERIADO_NACIONAL).
Idempotente: pode rodar a qualquer hora (o job ``feriados`` roda todo mes). Feriado em que a
clinica abre fica com ativo=False e nao e recriado nem reativado.

Uso:
  python manage.py carregar_feriados          # ano atual + proximo (fuso local)
  python manage.py carregar_feriados --ano 2027
"""
from django.core.management.base import BaseCommand

from aranha_estetica.services import feriados


class Command(BaseCommand):
    help = 'Carrega feriados nacionais brasileiros'

    def add_arguments(self, parser):
        parser.add_argument('--ano', type=int, help='Ano a carregar (default: atual + proximo)')

    def handle(self, *args, **options):
        anos = [options['ano']] if options.get('ano') else feriados.anos_padrao()
        criados = feriados.carregar(anos)
        existentes = sum(len(feriados.feriados_do_ano(ano)) for ano in anos) - criados
        self.stdout.write(self.style.SUCCESS(f'Feriados: {criados} criados, {existentes} ja existiam.'))
