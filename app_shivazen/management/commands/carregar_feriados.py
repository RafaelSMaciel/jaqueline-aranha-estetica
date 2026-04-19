"""Carrega feriados nacionais brasileiros para o ano informado.

Uso:
  python manage.py carregar_feriados          # ano atual + proximo
  python manage.py carregar_feriados --ano 2027
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from app_shivazen.models import Feriado


FERIADOS_FIXOS = [
    ((1, 1), 'Confraternizacao Universal'),
    ((4, 21), 'Tiradentes'),
    ((5, 1), 'Dia do Trabalho'),
    ((9, 7), 'Independencia do Brasil'),
    ((10, 12), 'Nossa Senhora Aparecida'),
    ((11, 2), 'Finados'),
    ((11, 15), 'Proclamacao da Republica'),
    ((11, 20), 'Consciencia Negra'),
    ((12, 25), 'Natal'),
]


def pascoa(ano: int) -> date:
    """Algoritmo de Meeus/Jones/Butcher para domingo de Pascoa."""
    a = ano % 19
    b = ano // 100
    c = ano % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def feriados_moveis(ano: int):
    p = pascoa(ano)
    return [
        (p - timedelta(days=48), 'Carnaval (segunda)'),
        (p - timedelta(days=47), 'Carnaval (terca)'),
        (p - timedelta(days=2), 'Sexta-feira Santa'),
        (p + timedelta(days=60), 'Corpus Christi'),
    ]


class Command(BaseCommand):
    help = 'Carrega feriados nacionais brasileiros'

    def add_arguments(self, parser):
        parser.add_argument('--ano', type=int, help='Ano a carregar (default: atual + proximo)')

    def handle(self, *args, **options):
        ano_alvo = options.get('ano')
        anos = [ano_alvo] if ano_alvo else [timezone.now().year, timezone.now().year + 1]

        criados, existentes = 0, 0
        for ano in anos:
            entradas = [
                (date(ano, m, d), nome) for (m, d), nome in FERIADOS_FIXOS
            ] + feriados_moveis(ano)

            for dt, nome in entradas:
                _obj, created = Feriado.objects.get_or_create(
                    data=dt,
                    escopo=Feriado.ESCOPO_NACIONAL,
                    defaults={
                        'nome': nome,
                        'bloqueia_agendamento': True,
                    },
                )
                if created:
                    criados += 1
                else:
                    existentes += 1
                    self.stdout.write(f'  ja existe: {dt} {nome}')

        self.stdout.write(self.style.SUCCESS(
            f'Feriados: {criados} criados, {existentes} ja existiam.'
        ))
