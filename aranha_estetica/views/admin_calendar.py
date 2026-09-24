"""Visao de calendario (FullCalendar) para agendamentos — alternativa a lista."""
from datetime import datetime

from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from ..decorators import staff_required
from ..models import Atendimento, BloqueioAgenda, ExcecaoDisponibilidade, Profissional
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

    bloq_qs = BloqueioAgenda.objects.filter(
        data_hora_inicio__lt=dt_end,
        data_hora_fim__gt=dt_start,
    )
    if prof_filter:
        bloq_qs = bloq_qs.filter(profissional_id=prof_filter)
    for bl in bloq_qs:
        eventos.append({
            'id': f'bloq-{bl.pk}',
            'title': f'Bloqueio: {bl.motivo or "—"}',
            'start': bl.data_hora_inicio.isoformat(),
            'end': bl.data_hora_fim.isoformat(),
            'display': 'background',
            'backgroundColor': '#bdbdbd',
            'editable': False,
        })

    folgas_qs = ExcecaoDisponibilidade.objects.filter(
        tipo='FOLGA',
        data__gte=data_local(dt_start),
        data__lt=data_local(dt_end),
    )
    if prof_filter:
        folgas_qs = folgas_qs.filter(profissional_id=prof_filter)
    for ex in folgas_qs:
        eventos.append({
            'id': f'folga-{ex.pk}',
            'title': f'Folga: {ex.motivo or "—"}',
            'start': ex.data.isoformat(),
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
    """Reagenda atendimento via drag-drop no calendario."""
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

        # Bloqueia double-booking: nao mover para janela que sobrepoe outro
        # atendimento ativo do mesmo profissional, um bloqueio ou uma folga.
        conflito_atendimento = Atendimento.objects.filter(
            profissional_id=at.profissional_id,
            data_hora_inicio__lt=novo_fim,
            data_hora_fim__gt=novo_inicio,
            status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO', 'REALIZADO'],
        ).exclude(pk=at.pk).exists()
        if conflito_atendimento:
            return JsonResponse({
                'sucesso': False,
                'erro': 'Conflito: já existe um atendimento nesse horário para o profissional.',
            }, status=409)

        # Inclui bloqueios globais (profissional nulo = vale p/ todos).
        # Checagem de sobreposicao direta; nao expande recorrencia (debito menor).
        from django.db.models import Q
        conflito_bloqueio = BloqueioAgenda.objects.filter(
            Q(profissional_id=at.profissional_id) | Q(profissional__isnull=True),
            data_hora_inicio__lt=novo_fim,
            data_hora_fim__gt=novo_inicio,
        ).exists()
        if conflito_bloqueio:
            return JsonResponse({
                'sucesso': False,
                'erro': 'Conflito: o horário cai dentro de um bloqueio de agenda.',
            }, status=409)

        conflito_folga = ExcecaoDisponibilidade.objects.filter(
            profissional_id=at.profissional_id,
            tipo='FOLGA',
            data=timezone.localtime(novo_inicio).date(),
        ).exists()
        if conflito_folga:
            return JsonResponse({
                'sucesso': False,
                'erro': 'Conflito: o profissional está de folga nesse dia.',
            }, status=409)

        antigo = at.data_hora_inicio.isoformat()
        at.data_hora_inicio = novo_inicio
        at.data_hora_fim = novo_fim
        try:
            # savepoint: a EXCLUDE excl_atendimento_sobreposicao (PG) pode
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
