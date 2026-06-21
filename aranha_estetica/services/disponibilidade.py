"""SlotService — calculo de horarios disponiveis de um profissional.

Extraido de Profissional.get_horarios_disponiveis (fat-model: ~110 linhas no
model misturando dominio + 5 querysets + aritmetica de timezone) para uma
camada de service testavel. O metodo do model passou a delegar aqui, com
comportamento preservado (ver tests/test_slots_disponibilidade.py).
"""
from datetime import datetime, timedelta

from django.db.models import Q
from django.utils import timezone

from ..models import (
    Atendimento,
    BloqueioAgenda,
    DisponibilidadeProfissional,
    ExcecaoDisponibilidade,
    Feriado,
)


class SlotService:
    """Slots livres no dia considerando: feriado bloqueador, ExcecaoDisponibilidade
    (folga / horario diferente), regra semanal (DisponibilidadeProfissional),
    atendimentos ativos (com buffer do procedimento), BloqueioAgenda (pontual +
    recorrente), min_notice_horas e max_advance_dias do profissional."""

    INTERVALO = timedelta(minutes=30)

    @classmethod
    def slots_livres(cls, profissional, data_selecionada, procedimento=None):
        if Feriado.objects.filter(data=data_selecionada, bloqueia_agendamento=True).exists():
            return []

        agora = timezone.localtime()
        limite_min_notice = agora + timedelta(hours=profissional.min_notice_horas)
        limite_max_advance = (agora + timedelta(days=profissional.max_advance_dias)).date()
        if data_selecionada > limite_max_advance:
            return []

        excecoes = ExcecaoDisponibilidade.objects.filter(
            profissional=profissional, data=data_selecionada
        )
        excecao_horario = None
        for ex in excecoes:
            if ex.tipo == 'FOLGA':
                return []
            if ex.tipo == 'HORARIO_DIFERENTE' and ex.hora_inicio and ex.hora_fim:
                excecao_horario = ex
                break

        if excecao_horario:
            janelas = [(excecao_horario.hora_inicio, excecao_horario.hora_fim)]
        else:
            dia_semana = data_selecionada.isoweekday() % 7 + 1
            disponibilidades = DisponibilidadeProfissional.objects.filter(
                profissional=profissional, dia_semana=dia_semana
            )
            if not disponibilidades.exists():
                return []
            janelas = [(d.hora_inicio, d.hora_fim) for d in disponibilidades]

        agendamentos = list(Atendimento.objects.filter(
            profissional=profissional,
            data_hora_inicio__date=data_selecionada,
            status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO']
        ).select_related('procedimento'))

        dia_inicio = datetime.combine(data_selecionada, datetime.min.time())
        dia_fim = datetime.combine(data_selecionada, datetime.max.time())
        if timezone.is_naive(dia_inicio):
            tz = timezone.get_current_timezone()
            dia_inicio = timezone.make_aware(dia_inicio, tz)
            dia_fim = timezone.make_aware(dia_fim, tz)

        # Bloqueios pontuais (sem recorrencia) intersectando o dia
        bloqueios_raw = list(BloqueioAgenda.objects.filter(
            profissional=profissional,
        ).filter(
            Q(regra_recorrencia='') &
            Q(data_hora_inicio__date__lte=data_selecionada) &
            Q(data_hora_fim__date__gte=data_selecionada)
        ))
        # Bloqueios recorrentes — expande p/ a janela do dia
        recorrentes = BloqueioAgenda.objects.filter(profissional=profissional).exclude(regra_recorrencia='')
        bloqueios_intervalos = [(b.data_hora_inicio, b.data_hora_fim) for b in bloqueios_raw]
        for b in recorrentes:
            bloqueios_intervalos.extend(b.expandir_ocorrencias(dia_inicio, dia_fim))

        buffer_proc = timedelta(minutes=procedimento.buffer_minutos) if procedimento else timedelta(0)

        horarios_disponiveis = []
        for hora_ini, hora_fim in janelas:
            hora_atual = datetime.combine(data_selecionada, hora_ini)
            hora_fim_expediente = datetime.combine(data_selecionada, hora_fim)
            if timezone.is_naive(hora_atual):
                tz = timezone.get_current_timezone()
                hora_atual = timezone.make_aware(hora_atual, tz)
                hora_fim_expediente = timezone.make_aware(hora_fim_expediente, tz)

            while hora_atual < hora_fim_expediente:
                if hora_atual < limite_min_notice:
                    hora_atual += cls.INTERVALO
                    continue

                horario_ocupado = False
                for ag in agendamentos:
                    buf_ag = timedelta(minutes=ag.procedimento.buffer_minutos) if ag.procedimento_id else timedelta(0)
                    bloqueio_fim = ag.data_hora_fim + max(buf_ag, buffer_proc)
                    if ag.data_hora_inicio <= hora_atual < bloqueio_fim:
                        horario_ocupado = True
                        break
                if not horario_ocupado:
                    for bl_ini, bl_fim in bloqueios_intervalos:
                        if bl_ini <= hora_atual < bl_fim:
                            horario_ocupado = True
                            break
                if not horario_ocupado:
                    horario_str = hora_atual.strftime('%H:%M')
                    if horario_str not in horarios_disponiveis:
                        horarios_disponiveis.append(horario_str)
                hora_atual += cls.INTERVALO

        return sorted(horarios_disponiveis)
