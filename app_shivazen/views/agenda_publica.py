"""Views publicas por profissional: pagina agendar/<slug>/ + ICS feed.

ICS feed requer query param ?token=<ics_token> p/ nao vazar agenda.
"""
from datetime import datetime, timedelta

from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.cache import never_cache

from ..models import Atendimento, Profissional


def agendar_por_profissional(request, slug):
    """Atalho: redireciona p/ booking publico c/ profissional fixado via query."""
    prof = get_object_or_404(Profissional, slug=slug, ativo=True)
    base = f"/agendamento/?profissional={prof.pk}"
    proc = request.GET.get('procedimento')
    if proc:
        base += f"&procedimento={proc}"
    return redirect(base)


def _ics_escape(s):
    return (
        (s or '')
        .replace('\\', '\\\\')
        .replace(';', '\\;')
        .replace(',', '\\,')
        .replace('\n', '\\n')
    )


def _ics_format_dt(dt):
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


@never_cache
def ics_feed_profissional(request, slug):
    """Retorna agenda do profissional como text/calendar.

    URL: /agenda/<slug>/feed.ics?token=<ics_token>
    Atendimentos AGENDADO/CONFIRMADO/REALIZADO no range -30d..+90d.
    """
    prof = get_object_or_404(Profissional, slug=slug, ativo=True)
    token = (request.GET.get('token') or '').strip()
    if not token or token != (prof.ics_token or ''):
        raise Http404('Token invalido')

    agora = timezone.now()
    inicio = agora - timedelta(days=30)
    fim = agora + timedelta(days=90)

    atendimentos = Atendimento.objects.filter(
        profissional=prof,
        data_hora_inicio__gte=inicio,
        data_hora_inicio__lte=fim,
        status__in=['AGENDADO', 'CONFIRMADO', 'REALIZADO'],
    ).select_related('cliente', 'procedimento')

    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//shivazen//agenda profissional//PT-BR',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        f'X-WR-CALNAME:{_ics_escape(prof.nome)} - Agenda',
        'X-WR-TIMEZONE:America/Sao_Paulo',
    ]
    for at in atendimentos:
        cliente_nome = at.cliente.nome_completo if at.cliente_id else 'Paciente'
        proc_nome = at.procedimento.nome if at.procedimento_id else 'Atendimento'
        summary = f'{cliente_nome} - {proc_nome}'
        descricao = (
            f'Status: {at.get_status_display()}\\n'
            f'Procedimento: {proc_nome}\\n'
            f'Profissional: {prof.nome}'
        )
        lines.extend([
            'BEGIN:VEVENT',
            f'UID:atend-{at.pk}@shivazen',
            f'DTSTAMP:{_ics_format_dt(at.atualizado_em or at.criado_em or agora)}',
            f'DTSTART:{_ics_format_dt(at.data_hora_inicio)}',
            f'DTEND:{_ics_format_dt(at.data_hora_fim)}',
            f'SUMMARY:{_ics_escape(summary)}',
            f'DESCRIPTION:{_ics_escape(descricao)}',
            f'STATUS:{"CONFIRMED" if at.status in ("CONFIRMADO","REALIZADO") else "TENTATIVE"}',
            'END:VEVENT',
        ])
    lines.append('END:VCALENDAR')

    body = '\r\n'.join(lines) + '\r\n'
    resp = HttpResponse(body, content_type='text/calendar; charset=utf-8')
    resp['Content-Disposition'] = f'inline; filename="{prof.slug}-agenda.ics"'
    return resp
