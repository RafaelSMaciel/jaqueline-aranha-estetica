"""Feriados nacionais: a regra (fixos + moveis pela Pascoa) e a carga na agenda.

Feriado = AgendaExcecao FOLGA da clinica inteira (profissional NULL, origem FERIADO_NACIONAL).
``carregar`` e idempotente (get_or_create por origem+data, o mesmo par do
uq_agenda_excecao_feriado): o job ``feriados`` roda todo mes e a migration 0012 so semeou
2026-2027. Feriado em que a clinica abre fica com ativo=False (eh_ativo) e nao e recriado nem
reativado aqui.

Quem usa: o comando ``carregar_feriados`` (e o ``seed``) e o job ``feriados`` (jobs_manutencao).
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

from ..models import AgendaExcecao
from ..utils.datas import hoje

FERIADOS_FIXOS: tuple[tuple[tuple[int, int], str], ...] = (
    ((1, 1), 'Confraternização Universal'),
    ((4, 21), 'Tiradentes'),
    ((5, 1), 'Dia do Trabalho'),
    ((9, 7), 'Independência do Brasil'),
    ((10, 12), 'Nossa Senhora Aparecida'),
    ((11, 2), 'Finados'),
    ((11, 15), 'Proclamação da República'),
    ((11, 20), 'Consciência Negra'),
    ((12, 25), 'Natal'),
)


def pascoa(ano: int) -> date:
    """Domingo de Pascoa (algoritmo de Meeus/Jones/Butcher, calendario gregoriano)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    dia_semana = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * dia_semana) // 451
    mes, dia = divmod(h + dia_semana - 7 * m + 114, 31)
    return date(ano, mes, dia + 1)


def feriados_moveis(ano: int) -> list[tuple[date, str]]:
    """Carnaval (segunda e terca), Sexta-feira Santa e Corpus Christi do ano."""
    p = pascoa(ano)
    return [
        (p - timedelta(days=48), 'Carnaval (segunda)'),
        (p - timedelta(days=47), 'Carnaval (terça)'),
        (p - timedelta(days=2), 'Sexta-feira Santa'),
        (p + timedelta(days=60), 'Corpus Christi'),
    ]


def feriados_do_ano(ano: int) -> list[tuple[date, str]]:
    """Os 13 feriados nacionais do ano (9 fixos + 4 moveis), em ordem de data."""
    fixos = [(date(ano, mes, dia), nome) for (mes, dia), nome in FERIADOS_FIXOS]
    return sorted(fixos + feriados_moveis(ano))


def anos_padrao() -> list[int]:
    """Ano atual e o seguinte (fuso da clinica: 31/12 a noite ja e outro ano em UTC)."""
    ano = hoje().year
    return [ano, ano + 1]


def carregar(anos: Iterable[int] | None = None) -> int:
    """Cria os feriados que faltam nos ``anos`` (padrao: atual + seguinte). Devolve quantos criou."""
    criados = 0
    for ano in (anos or anos_padrao()):
        for dia, nome in feriados_do_ano(ano):
            _feriado, criado = AgendaExcecao.objects.get_or_create(
                origem=AgendaExcecao.ORIGEM_FERIADO_NACIONAL,
                data_inicio=dia,
                defaults={
                    'tipo': AgendaExcecao.TIPO_FOLGA,
                    'profissional': None,
                    'data_fim': dia,
                    'motivo': nome,
                    'ativo': True,
                },
            )
            criados += criado
    return criados
