"""Visao de calendario (FullCalendar) para agendamentos — alternativa a lista."""
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from ..decorators import staff_required
from ..excecoes import RegraDeNegocio, TransicaoInvalida
from ..models import AgendaExcecao, Atendimento, Profissional
from ..models.agendamentos import StatusAtendimento
from ..services import agenda
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
    # tokens --st-<status>-texto (tokens.css): texto branco no evento passa 7:1 no claro
    StatusAtendimento.PENDENTE: 'var(--st-pendente-texto)',
    StatusAtendimento.AGENDADO: 'var(--st-agendado-texto)',
    StatusAtendimento.CONFIRMADO: 'var(--st-confirmado-texto)',
    StatusAtendimento.REALIZADO: 'var(--st-realizado-texto)',
    StatusAtendimento.CANCELADO: 'var(--st-cancelado-texto)',
    StatusAtendimento.FALTOU: 'var(--st-faltou-texto)',
    StatusAtendimento.REAGENDADO: 'var(--st-reagendado-texto)',
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
            'backgroundColor': STATUS_COLORS.get(at.status, 'var(--cor-texto-suave)'),
            'borderColor': STATUS_COLORS.get(at.status, 'var(--cor-texto-suave)'),
            'textColor': 'var(--cor-superficie)',   # >= 7:1 nos dois temas
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
                    'backgroundColor': 'var(--cor-borda-campo)',   # bloqueio (tokens: claro/escuro)
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
            'backgroundColor': 'var(--cor-aviso-fundo)',   # folga/feriado
            'editable': False,
        })

    return JsonResponse(eventos, safe=False)


@staff_required
@require_POST
@ratelimit(key='user', rate='60/m', method='POST', block=True)
def admin_calendar_mover(request):
    """Reagenda atendimento via drag-drop no calendario (services.agenda.mover).

    Mesma regra de horario do site, do painel e do portal (SlotService), com o motivo da recusa
    p/ a equipe (conflito, bloqueio, folga, feriado, fora do expediente). Preco/promocao e
    duracao combinados ficam (eventDurationEditable=false).
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

    try:
        with transaction.atomic():
            at, antigo = agenda.mover(pk, novo_inicio)
            registrar_log(
                request.user, 'Moveu agendamento via calendario',
                'atendimento', at.pk,
                detalhes={'de': antigo.isoformat(), 'para': novo_inicio.isoformat()}, request=request,
            )
    except RegraDeNegocio as recusa:
        status = (404 if recusa.codigo == 'nao_encontrado'
                  else 400 if isinstance(recusa, TransicaoInvalida) else 409)
        return JsonResponse({'sucesso': False, 'erro': recusa.mensagem}, status=status)

    return JsonResponse({
        'sucesso': True,
        # FullCalendar envia ISO em UTC ('Z'): formata no fuso da clinica
        'nova_data': fmt_local(novo_inicio),
    })
