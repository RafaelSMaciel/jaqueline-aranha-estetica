"""Visao de calendario (FullCalendar) para agendamentos — alternativa a lista."""
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from ..decorators import staff_required
from ..models import AgendaExcecao, Atendimento, Profissional
from ..services.disponibilidade import STATUS_OCUPAM_AGENDA, slot_disponivel
from ..utils.audit import registrar_log
from ..utils.datas import data_local, fmt_local
from ..utils.parse import id_int


def _parse_iso_aware(valor):
    """Parseia ISO 8601 do FullCalendar e garante datetime tz-aware.

    Com USE_TZ=True, um valor sem offset ficaria naive e deslocaria o
    horario salvo/comparado. Normaliza para aware no fuso default.
    """
    dt = datetime.fromisoformat(valor.replace('Z', '+00:00'))
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


STATUS_COLORS = {
    'PENDENTE': '#f9a825',
    'AGENDADO': '#1565c0',
    'CONFIRMADO': '#2e7d32',
    'REALIZADO': '#7b1fa2',
    'CANCELADO': '#c62828',
    'FALTOU': '#e65100',
    'REAGENDADO': '#999999',
}


@staff_required
def admin_calendar(request):
    """Renderiza pagina com FullCalendar — eventos carregados via AJAX."""
    profissionais = Profissional.objects.filter(ativo=True).order_by('nome')
    context = {
        'profissionais': profissionais,
        'status_colors': STATUS_COLORS,
    }
    return render(request, 'painel/calendar.html', context)


@staff_required
def admin_calendar_events(request):
    """Endpoint JSON compativel com FullCalendar — retorna agendamentos no range."""
    start = request.GET.get('start', '')
    end = request.GET.get('end', '')
    # filtro nao numerico/'²' (URL manipulada) e ignorado em vez de 500
    prof_filter = id_int(request.GET.get('profissional'))

    try:
        dt_start = _parse_iso_aware(start)
        dt_end = _parse_iso_aware(end)
    except (ValueError, AttributeError):
        return JsonResponse([], safe=False)

    qs = Atendimento.objects.select_related('cliente', 'profissional', 'procedimento').filter(
        data_hora_inicio__gte=dt_start,
        data_hora_inicio__lt=dt_end,
    )
    if prof_filter:
        qs = qs.filter(profissional_id=prof_filter)

    eventos = []
    for at in qs:
        eventos.append({
            'id': at.pk,
            'title': f'{at.cliente.nome} · {at.procedimento.nome}',
            'start': at.data_hora_inicio.isoformat(),
            'end': at.data_hora_fim.isoformat(),
            'backgroundColor': STATUS_COLORS.get(at.status, '#999'),
            'borderColor': STATUS_COLORS.get(at.status, '#999'),
            'extendedProps': {
                'profissional': at.profissional.nome,
                'procedimento': at.procedimento.nome,
                'cliente_nome': at.cliente.nome,
                'cliente_telefone': at.cliente.telefone or '',
                'status': at.status,
                'valor': float(at.valor_cobrado) if at.valor_cobrado else None,
            },
        })

    # Bloqueios (recorrentes expandidos) e folgas; sem filtro = de todos +
    # globais; com filtro = do profissional + globais (valem p/ ele tambem)
    excecoes = AgendaExcecao.objects.ativas().filter(
        (Q(tipo=AgendaExcecao.TIPO_BLOQUEIO, data_hora_inicio__lt=dt_end)
         & (Q(regra_recorrencia__isnull=False) | Q(data_hora_fim__gt=dt_start)))
        | Q(tipo=AgendaExcecao.TIPO_FOLGA,
            data_inicio__lt=data_local(dt_end), data_fim__gte=data_local(dt_start)),
    )
    if prof_filter:
        excecoes = excecoes.do_profissional_ou_global(prof_filter)
    for ex in excecoes:
        if ex.tipo == AgendaExcecao.TIPO_BLOQUEIO:
            ocorrencias = ex.ocorrencias(dt_start, dt_end)
            for n, (ini, fim) in enumerate(ocorrencias):
                eventos.append({
                    'id': f'bloq-{ex.pk}' if not ex.regra_recorrencia else f'bloq-{ex.pk}-{n}',
                    'title': f'Bloqueio: {ex.motivo or "—"}',
                    'start': ini.isoformat(),
                    'end': fim.isoformat(),
                    'display': 'background',
                    'backgroundColor': '#bdbdbd',
                    'editable': False,
                })
            continue
        if ex.profissional_id:
            rotulo = 'Folga'
        elif ex.origem == AgendaExcecao.ORIGEM_MANUAL:
            rotulo = 'Clínica fechada'
        else:
            rotulo = ex.get_origem_display()  # Feriado nacional/local, Recesso da clínica
        eventos.append({
            'id': f'folga-{ex.pk}',
            'title': f'{rotulo}: {ex.motivo or "—"}',
            'start': ex.data_inicio.isoformat(),
            # allDay: o fim do FullCalendar e exclusivo
            'end': (ex.data_fim + timedelta(days=1)).isoformat(),
            'allDay': True,
            'display': 'background',
            'backgroundColor': '#ffe0b2',
            'editable': False,
        })

    return JsonResponse(eventos, safe=False)


@staff_required
@require_POST
@ratelimit(key='user', rate='60/m', method='POST', block=True)
def admin_calendar_mover(request):
    """Reagenda atendimento via drag-drop no calendario.

    Mesma regra de horario do site, do agendamento pelo painel e do reagendamento pelo portal
    (SlotService): expediente/horario diferente do dia, folga do profissional OU feriado/recesso
    da clinica, bloqueios, buffer, antecedencia e limite de dias. Os 409 especificos (conflito,
    bloqueio, folga) vem antes so p/ a mensagem dizer o motivo. O preco/promocao combinados
    seguem com o atendimento (como no portal). A duracao nao muda (eventDurationEditable=false).
    """
    import json
    try:
        payload = json.loads(request.body)
        pk = int(payload.get('id'))
        novo_inicio = _parse_iso_aware(payload.get('start'))
        novo_fim = _parse_iso_aware(payload.get('end'))
    except (ValueError, TypeError, AttributeError, KeyError):
        return JsonResponse({'sucesso': False, 'erro': 'Dados inválidos.'}, status=400)

    if novo_fim <= novo_inicio:
        return JsonResponse({'sucesso': False, 'erro': 'O fim deve ser depois do início.'}, status=400)

    with transaction.atomic():
        try:
            at = Atendimento.objects.select_for_update().get(pk=pk)
        except Atendimento.DoesNotExist:
            return JsonResponse({'sucesso': False, 'erro': 'Atendimento não encontrado.'}, status=404)

        if at.status in ('REALIZADO', 'CANCELADO', 'FALTOU'):
            return JsonResponse({
                'sucesso': False,
                'erro': f'Não é possível mover um atendimento {at.get_status_display().lower()}.',
            }, status=400)

        novo_fim = novo_inicio + (at.data_hora_fim - at.data_hora_inicio)
        # Bloqueia double-booking: nao mover para janela que sobrepoe outro
        # atendimento ativo do mesmo profissional, um bloqueio ou uma folga.
        conflito_atendimento = Atendimento.objects.filter(
            profissional_id=at.profissional_id,
            data_hora_inicio__lt=novo_fim,
            data_hora_fim__gt=novo_inicio,
            status__in=STATUS_OCUPAM_AGENDA,
        ).exclude(pk=at.pk).exists()
        if conflito_atendimento:
            return JsonResponse({
                'sucesso': False,
                'erro': 'Conflito: já existe um atendimento nesse horário para o profissional.',
            }, status=409)

        # Inclui bloqueios globais (profissional nulo = vale p/ todos) e
        # expande os recorrentes (mesma regra do SlotService).
        conflito_bloqueio = any(
            bl.ocorrencias(novo_inicio, novo_fim)
            for bl in AgendaExcecao.objects.ativas()
            .do_profissional_ou_global(at.profissional_id)
            .bloqueios_no_intervalo(novo_inicio, novo_fim)
        )
        if conflito_bloqueio:
            return JsonResponse({
                'sucesso': False,
                'erro': 'Conflito: o horário cai dentro de um bloqueio de agenda.',
            }, status=409)

        # folga do profissional OU feriado/recesso/fechamento da clinica (profissional nulo)
        folga = (
            AgendaExcecao.objects.ativas().do_profissional_ou_global(at.profissional_id)
            .filter(tipo=AgendaExcecao.TIPO_FOLGA)
            .cobrindo(timezone.localtime(novo_inicio).date()).first()
        )
        if folga is not None:
            return JsonResponse({
                'sucesso': False,
                'erro': ('Conflito: o profissional está de folga nesse dia.' if folga.profissional_id
                         else 'Conflito: a clínica não atende nesse dia (feriado ou recesso).'),
            }, status=409)

        if not slot_disponivel(at.profissional, at.procedimento, novo_inicio, ignorar_atendimento_id=at.pk):
            return JsonResponse({
                'sucesso': False,
                'erro': ('Fora do horário de atendimento do profissional (expediente, intervalo entre '
                         'sessões, antecedência mínima ou dias liberados para agendar).'),
            }, status=409)

        antigo = at.data_hora_inicio.isoformat()
        at.data_hora_inicio = novo_inicio
        at.data_hora_fim = novo_fim
        try:
            # savepoint: a EXCLUDE ex_atendimento_sobreposicao (PG) pode
            # disparar numa corrida com o booking entre a checagem e o save
            with transaction.atomic():
                at.save(update_fields=['data_hora_inicio', 'data_hora_fim', 'atualizado_em'])
        except IntegrityError:
            return JsonResponse({
                'sucesso': False,
                'erro': 'Conflito: o horário acabou de ser ocupado. Atualize o calendário.',
            }, status=409)

        registrar_log(
            request.user, 'Moveu agendamento via calendario',
            'atendimento', at.pk,
            detalhes={'de': antigo, 'para': novo_inicio.isoformat()}, request=request,
        )

    return JsonResponse({
        'sucesso': True,
        # FullCalendar envia ISO em UTC ('Z'): formata no fuso da clinica
        'nova_data': fmt_local(novo_inicio),
    })
