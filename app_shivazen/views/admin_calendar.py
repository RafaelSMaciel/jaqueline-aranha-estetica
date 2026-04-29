"""Visao de calendario (FullCalendar) para agendamentos — alternativa a lista."""
from datetime import datetime

from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from ..decorators import staff_required
from ..models import Atendimento, Profissional
from ..utils.audit import registrar_log


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
    prof_filter = request.GET.get('profissional', '')

    try:
        dt_start = datetime.fromisoformat(start.replace('Z', '+00:00'))
        dt_end = datetime.fromisoformat(end.replace('Z', '+00:00'))
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
            'title': f'{at.cliente.nome_completo} · {at.procedimento.nome}',
            'start': at.data_hora_inicio.isoformat(),
            'end': at.data_hora_fim.isoformat(),
            'backgroundColor': STATUS_COLORS.get(at.status, '#999'),
            'borderColor': STATUS_COLORS.get(at.status, '#999'),
            'extendedProps': {
                'profissional': at.profissional.nome,
                'procedimento': at.procedimento.nome,
                'cliente_nome': at.cliente.nome_completo,
                'cliente_telefone': at.cliente.telefone or '',
                'status': at.status,
                'valor': float(at.valor_cobrado) if at.valor_cobrado else None,
            },
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
        novo_inicio = datetime.fromisoformat(payload.get('start').replace('Z', '+00:00'))
        novo_fim = datetime.fromisoformat(payload.get('end').replace('Z', '+00:00'))
    except (ValueError, TypeError, AttributeError, KeyError):
        return JsonResponse({'sucesso': False, 'erro': 'Payload invalido'}, status=400)

    try:
        at = Atendimento.objects.get(pk=pk)
    except Atendimento.DoesNotExist:
        return JsonResponse({'sucesso': False, 'erro': 'Atendimento nao encontrado'}, status=404)

    if at.status in ('REALIZADO', 'CANCELADO', 'FALTOU'):
        return JsonResponse({
            'sucesso': False,
            'erro': f'Nao e possivel mover atendimento {at.get_status_display().lower()}.',
        }, status=400)

    antigo = at.data_hora_inicio.isoformat()
    at.data_hora_inicio = novo_inicio
    at.data_hora_fim = novo_fim
    at.save(update_fields=['data_hora_inicio', 'data_hora_fim', 'atualizado_em'])

    registrar_log(
        request.user, 'Moveu agendamento via calendario',
        'atendimento', at.pk,
        detalhes={'de': antigo, 'para': novo_inicio.isoformat()},
    )

    return JsonResponse({
        'sucesso': True,
        'nova_data': novo_inicio.strftime('%d/%m/%Y %H:%M'),
    })
