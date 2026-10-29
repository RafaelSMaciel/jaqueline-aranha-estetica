"""SlotService — calculo de horarios disponiveis de um profissional.

Fonte UNICA de disponibilidade: o wizard publico (api_horarios_disponiveis),
o reagendamento e a validacao server-side de confirmar/reagendar usam
slots_livres/slot_disponivel — nada de recalcular agenda em outra view.
``horarios_agrupados`` (horarios do dia por profissional) e ``dias_com_expediente`` (calendario
do mes) tambem moram aqui: as views so leem os parametros e devolvem o JSON.
"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone

from ..models import (
    AgendaExcecao,
    AgendaHorario,
    Atendimento,
    Habilitacao,
    Profissional,
)
from ..models.agendamentos import STATUS_ATIVOS
from ..utils.precos import preco_com_promocao

STATUS_OCUPAM_AGENDA = STATUS_ATIVOS


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
    """Profissionais ativos HABILITADOS no procedimento (so atende quem esta habilitado).

    Procedimento sem habilitacao nao tem quem atenda: some do agendamento online e o painel avisa.
    Antes, sem nenhuma linha, qualquer ativo atendia — tirar a ultima habilitacao (p/ suspender um
    injetavel, por ex.) liberava o procedimento p/ a equipe inteira.
    """
    habilitados = Habilitacao.objects.filter(procedimento=procedimento).values('profissional_id')
    return Profissional.objects.filter(ativo=True, pk__in=habilitados).order_by('nome')


def procedimentos_agendaveis(qs=None):
    """Procedimentos ativos com ao menos 1 profissional ativo habilitado (os que o wizard oferece)."""
    from ..models import Procedimento

    qs = qs if qs is not None else Procedimento.objects.all()
    return qs.filter(ativo=True, habilitacao__profissional__ativo=True).distinct()


def profissional_habilitado(profissional, procedimento) -> bool:
    return profissionais_para(procedimento).filter(pk=profissional.pk).exists()


# ─── Wizard: horarios do dia e calendario do mes ────────────────────────────

def _preco_json(procedimento, profissional, dia) -> dict:
    """{'valor', 'valor_cheio', 'promocao'} (strings decimais; None = a consultar)."""
    valor, promocao, cheio = preco_com_promocao(procedimento, profissional, dia)
    return {
        'valor': str(valor) if valor is not None else None,
        'valor_cheio': str(cheio) if promocao is not None else None,
        'promocao': promocao.nome if promocao is not None else '',
    }


def horarios_agrupados(procedimento, profissionais, dia, ignorar_atendimento_id=None, com_preco=True) -> list[dict]:
    """[{'horario', 'datetime_iso', 'profissionais': [...]}] ordenado por horario (SlotService).

    ignorar_atendimento_id: o atendimento sendo reagendado nao ocupa o proprio horario (mesma
    regra do POST do reagendamento). com_preco: preco NA DATA p/ cada profissional (promocao
    inclusa) — o resumo do wizard mostra o valor que o booking grava; o reagendamento omite
    (mantem o valor combinado).
    """
    agrupados: dict[str, dict] = {}
    for prof in profissionais:
        slots = SlotService.slots_livres(prof, dia, procedimento, ignorar_atendimento_id=ignorar_atendimento_id)
        if not slots:
            continue
        info = {'id': prof.pk, 'nome': prof.nome}
        if com_preco:
            info.update(_preco_json(procedimento, prof, dia))
        for hhmm in slots:
            if hhmm not in agrupados:
                inicio = timezone.make_aware(datetime.combine(dia, time.fromisoformat(hhmm)))
                agrupados[hhmm] = {'horario': hhmm, 'datetime_iso': inicio.isoformat(), 'profissionais': []}
            agrupados[hhmm]['profissionais'].append(info)
    return [agrupados[k] for k in sorted(agrupados)]


def _coberta(inicio, fim, bloqueios) -> bool:
    """[inicio, fim) inteiro dentro da uniao dos intervalos bloqueados."""
    cursor = inicio
    for b_ini, b_fim in sorted(bloqueios):
        if b_ini > cursor:
            return False
        cursor = max(cursor, b_fim)
        if cursor >= fim:
            return True
    return cursor >= fim


def dias_com_expediente(profissionais, inicio: date, fim: date) -> list[date]:
    """Dias de [inicio, fim] (a partir de hoje) com expediente de ao menos uma das profissionais.

    Calendario do wizard, em 2 consultas p/ o mes: fora feriado/recesso da clinica, folga dela e
    o max_advance; vale a grade semanal ou o horario diferente do dia, desde que alguma janela
    nao esteja inteira coberta por bloqueio (pontual ou recorrente, dela ou da clinica). Os
    horarios livres de cada dia (ocupacao, buffer, antecedencia) vem do SlotService quando a
    cliente clica no dia.
    """
    hoje = timezone.localdate()
    inicio = max(inicio, hoje)
    profissionais = list(profissionais)
    if not profissionais or inicio > fim:
        return []
    ids = [p.pk for p in profissionais]
    grade = defaultdict(list)
    for prof_id, dia_semana, hora_ini, hora_fim in AgendaHorario.objects.filter(profissional_id__in=ids).values_list(
            'profissional_id', 'dia_semana', 'hora_inicio', 'hora_fim'):
        grade[(prof_id, dia_semana)].append((hora_ini, hora_fim))

    periodo_ini = _aware(datetime.combine(inicio, time.min))
    periodo_fim = _aware(datetime.combine(fim + timedelta(days=1), time.min))
    excecoes = list(
        AgendaExcecao.objects.ativas().filter(Q(profissional_id__in=ids) | Q(profissional__isnull=True)).filter(
            Q(tipo__in=AgendaExcecao.TIPOS_POR_DATA, data_inicio__lte=fim, data_fim__gte=inicio)
            | (Q(tipo=AgendaExcecao.TIPO_BLOQUEIO, data_hora_inicio__lt=periodo_fim)
               & (Q(regra_recorrencia__isnull=False) | Q(data_hora_fim__gt=periodo_ini)))
        )
    )
    # profissional NULL = feriado/recesso/fechamento da clinica (vale p/ todas)
    feriados, folgas, diferente = set(), set(), {}
    for exc in excecoes:
        if exc.tipo not in AgendaExcecao.TIPOS_POR_DATA:
            continue
        dia = max(exc.data_inicio, inicio)
        while dia <= min(exc.data_fim, fim):
            if exc.profissional_id is None:
                feriados.add(dia)
            elif exc.tipo == AgendaExcecao.TIPO_FOLGA:
                folgas.add((exc.profissional_id, dia))
            else:
                diferente[(exc.profissional_id, dia)] = (exc.hora_inicio, exc.hora_fim)
            dia += timedelta(days=1)
    bloqueios = [exc for exc in excecoes if exc.tipo == AgendaExcecao.TIPO_BLOQUEIO]

    def atende(prof, dia) -> bool:
        if dia > hoje + timedelta(days=prof.max_advance_dias) or (prof.pk, dia) in folgas:
            return False
        janelas = [diferente[(prof.pk, dia)]] if (prof.pk, dia) in diferente else grade.get((prof.pk, dia.isoweekday()), [])
        if not janelas:
            return False
        dia_ini = _aware(datetime.combine(dia, time.min))
        bloqueado = [
            intervalo for exc in bloqueios if exc.profissional_id in (None, prof.pk)
            for intervalo in exc.ocorrencias(dia_ini, dia_ini + timedelta(days=1))
        ]
        return any(
            not _coberta(_aware(datetime.combine(dia, hora_ini)), _aware(datetime.combine(dia, hora_fim)), bloqueado)
            for hora_ini, hora_fim in janelas
        )

    dias = []
    dia = inicio
    while dia <= fim:
        if dia not in feriados and any(atende(prof, dia) for prof in profissionais):
            dias.append(dia)
        dia += timedelta(days=1)
    return dias
