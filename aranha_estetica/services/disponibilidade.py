"""SlotService — calculo de horarios disponiveis de um profissional.

Fonte UNICA de disponibilidade: o wizard publico (api_horarios_disponiveis),
o reagendamento e a validacao server-side de confirmar/reagendar usam
slots_livres/slot_disponivel — nada de recalcular agenda em outra view.
"""
from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone

from ..models import (
    AgendaExcecao,
    AgendaHorario,
    Atendimento,
    Habilitacao,
    Profissional,
)

STATUS_OCUPAM_AGENDA = ('PENDENTE', 'AGENDADO', 'CONFIRMADO')


def _aware(dt):
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


class SlotService:
    """Slots livres no dia considerando: folga da clinica (feriado/recesso) ou
    do profissional, horario diferente (substitui a grade do dia), grade
    semanal (todas as janelas do dia), atendimentos ativos (sobreposicao no
    intervalo inteiro + buffers), bloqueios (pontuais + recorrentes, do
    profissional ou globais), min_notice_horas e max_advance_dias.

    Tudo vem de AgendaHorario + AgendaExcecao (ativas) — 3 consultas: excecoes
    do dia, grade (so sem horario diferente) e atendimentos. Espelho SQL:
    fn_horarios_livres (spec padrao-sql §5.5), com teste de paridade."""

    INTERVALO = timedelta(minutes=30)

    @classmethod
    def slots_livres(cls, profissional, data_selecionada, procedimento=None,
                     ignorar_atendimento_id=None):
        """Lista 'HH:MM' (hora local) em que o procedimento INTEIRO cabe.

        ignorar_atendimento_id: atendimento que nao conta como ocupado
        (reagendamento: o proprio horario antigo nao bloqueia o novo).
        """
        agora = timezone.localtime()
        if data_selecionada < agora.date():
            return []
        limite_min_notice = agora + timedelta(hours=profissional.min_notice_horas)
        limite_max_advance = (agora + timedelta(days=profissional.max_advance_dias)).date()
        if data_selecionada > limite_max_advance:
            return []

        dia_inicio = _aware(datetime.combine(data_selecionada, time.min))
        dia_fim = dia_inicio + timedelta(days=1)

        excecoes = list(
            AgendaExcecao.objects.ativas().do_profissional_ou_global(profissional).filter(
                Q(tipo__in=AgendaExcecao.TIPOS_POR_DATA,
                  data_inicio__lte=data_selecionada, data_fim__gte=data_selecionada)
                | (Q(tipo=AgendaExcecao.TIPO_BLOQUEIO, data_hora_inicio__lt=dia_fim)
                   & (Q(regra_recorrencia__isnull=False) | Q(data_hora_fim__gt=dia_inicio)))
            )
        )
        # folga do profissional OU feriado/recesso/fechamento da clinica
        if any(e.tipo == AgendaExcecao.TIPO_FOLGA for e in excecoes):
            return []

        # horario diferente so existe com profissional (CHECK) e 1 por dia (UNIQUE)
        horario_diferente = next(
            (e for e in excecoes if e.tipo == AgendaExcecao.TIPO_HORARIO_DIFERENTE), None,
        )
        if horario_diferente:
            janelas = [(horario_diferente.hora_inicio, horario_diferente.hora_fim)]
        else:
            janelas = list(
                AgendaHorario.objects.filter(
                    profissional=profissional, dia_semana=data_selecionada.isoweekday(),
                ).values_list('hora_inicio', 'hora_fim')
            )
            if not janelas:
                return []

        # Atendimentos que intersectam o dia (intervalo, nao __date em UTC)
        agendamentos_qs = Atendimento.objects.filter(
            profissional=profissional,
            data_hora_inicio__lt=dia_fim,
            data_hora_fim__gt=dia_inicio,
            status__in=STATUS_OCUPAM_AGENDA,
        ).select_related('procedimento')
        if ignorar_atendimento_id:
            agendamentos_qs = agendamentos_qs.exclude(pk=ignorar_atendimento_id)
        ocupados = []
        for ag in agendamentos_qs:
            buf_ag = timedelta(minutes=ag.procedimento.buffer_minutos or 0) if ag.procedimento_id else timedelta(0)
            ocupados.append((ag.data_hora_inicio, ag.data_hora_fim + buf_ag))

        # Bloqueios do profissional OU globais (pontuais + ocorrencias das recorrentes)
        bloqueios = [
            intervalo
            for e in excecoes if e.tipo == AgendaExcecao.TIPO_BLOQUEIO
            for intervalo in e.ocorrencias(dia_inicio, dia_fim)
        ]

        duracao = (
            timedelta(minutes=procedimento.duracao_minutos)
            if procedimento and procedimento.duracao_minutos else cls.INTERVALO
        )
        buffer_proc = (
            timedelta(minutes=procedimento.buffer_minutos or 0) if procedimento else timedelta(0)
        )

        horarios_disponiveis = set()
        for hora_ini, hora_fim in janelas:
            hora_atual = _aware(datetime.combine(data_selecionada, hora_ini))
            fim_expediente = _aware(datetime.combine(data_selecionada, hora_fim))

            # O procedimento inteiro precisa caber antes do fim do expediente.
            while hora_atual + duracao <= fim_expediente:
                if hora_atual >= limite_min_notice:
                    fim_slot = hora_atual + duracao
                    livre = not any(
                        hora_atual < oc_fim and fim_slot + buffer_proc > oc_ini
                        for oc_ini, oc_fim in ocupados
                    ) and not any(
                        hora_atual < bl_fim and fim_slot > bl_ini
                        for bl_ini, bl_fim in bloqueios
                    )
                    if livre:
                        horarios_disponiveis.add(timezone.localtime(hora_atual).strftime('%H:%M'))
                hora_atual += cls.INTERVALO

        return sorted(horarios_disponiveis)


def slot_disponivel(profissional, procedimento, data_hora, ignorar_atendimento_id=None) -> bool:
    """True se data_hora (aware) e um slot oferecido pelo SlotService."""
    local = timezone.localtime(_aware(data_hora))
    if local.second or local.microsecond:
        return False
    return local.strftime('%H:%M') in SlotService.slots_livres(
        profissional, local.date(), procedimento,
        ignorar_atendimento_id=ignorar_atendimento_id,
    )


def profissionais_para(procedimento):
    """Profissionais ativos que realizam o procedimento.

    Sem nenhuma Habilitacao cadastrada p/ o procedimento, qualquer ativo atende
    (compat com clinica que ainda nao configurou habilitacoes).
    """
    habilitados = Habilitacao.objects.filter(procedimento=procedimento).values_list(
        'profissional_id', flat=True
    )
    qs = Profissional.objects.filter(ativo=True)
    if Habilitacao.objects.filter(procedimento=procedimento).exists():
        qs = qs.filter(pk__in=habilitados)
    return qs.order_by('nome')


def profissional_habilitado(profissional, procedimento) -> bool:
    return profissionais_para(procedimento).filter(pk=profissional.pk).exists()
