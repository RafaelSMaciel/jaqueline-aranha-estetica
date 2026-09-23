"""SlotService — calculo de horarios disponiveis de um profissional.

Fonte UNICA de disponibilidade: o wizard publico (api_horarios_disponiveis),
o reagendamento e a validacao server-side de confirmar/reagendar usam
slots_livres/slot_disponivel — nada de recalcular agenda em outra view.
"""
from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone

from ..models import (
    Atendimento,
    BloqueioAgenda,
    DisponibilidadeProfissional,
    ExcecaoDisponibilidade,
    Feriado,
    Habilitacao,
    Profissional,
)

STATUS_OCUPAM_AGENDA = ('PENDENTE', 'AGENDADO', 'CONFIRMADO')


def _aware(dt):
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


class SlotService:
    """Slots livres no dia considerando: feriado bloqueador, ExcecaoDisponibilidade
    (folga / horario diferente), regra semanal (todos os turnos do dia),
    atendimentos ativos (sobreposicao no intervalo inteiro + buffers),
    BloqueioAgenda (pontual + recorrente, do profissional ou global),
    min_notice_horas e max_advance_dias do profissional."""

    INTERVALO = timedelta(minutes=30)

    @classmethod
    def slots_livres(cls, profissional, data_selecionada, procedimento=None,
                     ignorar_atendimento_id=None):
        """Lista 'HH:MM' (hora local) em que o procedimento INTEIRO cabe.

        ignorar_atendimento_id: atendimento que nao conta como ocupado
        (reagendamento: o proprio horario antigo nao bloqueia o novo).
        """
        if Feriado.objects.filter(data=data_selecionada, bloqueia_agendamento=True).exists():
            return []

        agora = timezone.localtime()
        if data_selecionada < agora.date():
            return []
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

        if excecao_horario:
            janelas = [(excecao_horario.hora_inicio, excecao_horario.hora_fim)]
        else:
            dia_semana = data_selecionada.isoweekday() % 7 + 1
            janelas = [
                (d.hora_inicio, d.hora_fim)
                for d in DisponibilidadeProfissional.objects.filter(
                    profissional=profissional, dia_semana=dia_semana
                )
            ]
            if not janelas:
                return []

        dia_inicio = _aware(datetime.combine(data_selecionada, time.min))
        dia_fim = dia_inicio + timedelta(days=1)

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

        # Bloqueios do profissional OU globais (profissional nulo = 'Todos')
        filtro_prof = Q(profissional=profissional) | Q(profissional__isnull=True)
        bloqueios = [
            (b.data_hora_inicio, b.data_hora_fim)
            for b in BloqueioAgenda.objects.filter(filtro_prof, regra_recorrencia='').filter(
                data_hora_inicio__lt=dia_fim, data_hora_fim__gt=dia_inicio,
            )
        ]
        for b in BloqueioAgenda.objects.filter(filtro_prof).exclude(regra_recorrencia=''):
            bloqueios.extend(b.expandir_ocorrencias(dia_inicio, dia_fim))

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
