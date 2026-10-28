"""Indicadores do painel com definicao explicita (o que o numero quer dizer fica no codigo).

Ocupacao da semana = horas de atendimento marcadas/realizadas ÷ horas de expediente da semana.
  - expediente: grade semanal (AgendaHorario) de cada profissional ativo, dia a dia, sem os dias de
    folga dele nem os feriados/recessos da clinica (AgendaExcecao FOLGA);
  - ocupado: duracao dos atendimentos ativos ou realizados que comecam na semana (cancelado,
    faltou e reagendado nao ocupam).
Antes: quantidade de REALIZADOS ÷ horarios de 30 min da grade — um atendimento de 90 min contava
como 1 horario, a segunda-feira mostrava ~0% por definicao e semana com feriado parecia ociosa.
Aproximacao conhecida: horario diferente e bloqueio pontual nao descontam o expediente.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from django.db.models import Q

from ..models import AgendaExcecao, AgendaHorario, Atendimento
from ..models.agendamentos import STATUS_ATIVOS, StatusAtendimento
from ..utils.datas import dias_locais


def _minutos(inicio, fim) -> int:
    base = date(2000, 1, 3)
    return max(0, int((datetime.combine(base, fim) - datetime.combine(base, inicio)).total_seconds() // 60))


def ocupacao_semana(inicio: date, fim: date) -> dict:
    """{'percentual', 'horas_ocupadas', 'horas_expediente'} de [inicio, fim] (datas locais)."""
    grade: dict[tuple[int, int], int] = {}
    for h in AgendaHorario.objects.filter(profissional__ativo=True):
        chave = (h.profissional_id, h.dia_semana)
        grade[chave] = grade.get(chave, 0) + _minutos(h.hora_inicio, h.hora_fim)
    folgas = list(AgendaExcecao.objects.ativas().filter(
        tipo=AgendaExcecao.TIPO_FOLGA, data_inicio__lte=fim, data_fim__gte=inicio,
    ).values_list('profissional_id', 'data_inicio', 'data_fim'))

    def de_folga(prof_id, dia):
        return any((p is None or p == prof_id) and ini <= dia <= f for p, ini, f in folgas)

    expediente = 0
    dia = inicio
    while dia <= fim:
        for (prof_id, dia_semana), minutos in grade.items():
            if dia_semana == dia.isoweekday() and not de_folga(prof_id, dia):
                expediente += minutos
        dia += timedelta(days=1)

    ocupados = Atendimento.objects.filter(
        Q(status__in=STATUS_ATIVOS) | Q(status=StatusAtendimento.REALIZADO),
        **dias_locais('data_hora_inicio', inicio, fim),
    ).values_list('data_hora_inicio', 'data_hora_fim')
    ocupado = sum(int((f - i).total_seconds() // 60) for i, f in ocupados)
    return {
        'percentual': round(ocupado * 100 / expediente, 1) if expediente else 0,
        'horas_ocupadas': round(ocupado / 60, 1),
        'horas_expediente': round(expediente / 60, 1),
    }
