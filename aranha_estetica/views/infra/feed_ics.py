"""Feed ICS da agenda do profissional (/agenda/<slug>/feed.ics?token=...).

O calendario externo (Google, Apple, Outlook) assinado pelo profissional copia cada evento para um
operador fora da clinica: o evento leva so o primeiro nome e a inicial da cliente (o mesmo nome
publico do site) e o procedimento — nunca o nome completo.
"""
from datetime import timedelta, timezone as dt_timezone

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache

from ...models import Atendimento, Profissional
from ...models.agendamentos import StatusAtendimento
from ...services import links
from ...utils.pii import nome_publico

JANELA_PASSADO = timedelta(days=30)
JANELA_FUTURO = timedelta(days=90)
STATUS_NO_FEED = (StatusAtendimento.AGENDADO, StatusAtendimento.CONFIRMADO, StatusAtendimento.REALIZADO)


def _ics_escape(s):
    """Escapa TEXT (RFC 5545 3.3.11): cada quebra de linha REAL vira barra+n (uma vez)."""
    return (
        (s or '')
        .replace('\r\n', '\n')
        .replace('\r', '\n')
        .replace('\\', '\\\\')
        .replace(';', '\\;')
        .replace(',', '\\,')
        .replace('\n', '\\n')
    )


def _ics_format_dt(dt):
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt.astimezone(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')


@never_cache
def feed_profissional(request, slug):
    """Agenda do profissional como text/calendar (atendimentos de -30 a +90 dias).

    Busca pelo sha256 do token (indice unico; sem comparacao de segredo em Python); link revogado
    (novo link gerado no painel) ou de outro profissional -> 404.
    """
    prof = get_object_or_404(Profissional, slug=slug, ativo=True)
    link = links.resolver(links.FEED_ICS, request.GET.get('token'))
    if link is None or link.profissional_id != prof.pk:
        raise Http404('Token invalido')
    if link.usado_em is None:
        links.marcar_usado(link)  # 1a assinatura do calendario (o painel mostra)

    agora = timezone.now()
    atendimentos = Atendimento.objects.filter(
        profissional=prof,
        data_hora_inicio__gte=agora - JANELA_PASSADO,
        data_hora_inicio__lte=agora + JANELA_FUTURO,
        status__in=STATUS_NO_FEED,
    ).select_related('cliente', 'procedimento')

    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//aranha-estetica//agenda profissional//PT-BR',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        f'X-WR-CALNAME:{_ics_escape(prof.nome)} - Agenda',
        'X-WR-TIMEZONE:America/Sao_Paulo',
    ]
    for at in atendimentos:
        cliente = nome_publico(at.cliente.nome if at.cliente_id else '')
        proc_nome = at.procedimento.nome if at.procedimento_id else 'Atendimento'
        # Quebras REAIS: _ics_escape gera o escape do ICS uma unica vez (barra+n
        # literal aqui era escapada de novo e o calendario exibia a barra).
        descricao = '\n'.join([
            f'Status: {at.get_status_display()}',
            f'Procedimento: {proc_nome}',
            f'Profissional: {prof.nome}',
        ])
        lines.extend([
            'BEGIN:VEVENT',
            f'UID:atend-{at.pk}@aranha-estetica',
            f'DTSTAMP:{_ics_format_dt(at.atualizado_em or at.criado_em or agora)}',
            f'DTSTART:{_ics_format_dt(at.data_hora_inicio)}',
            f'DTEND:{_ics_format_dt(at.data_hora_fim)}',
            f'SUMMARY:{_ics_escape(f"{cliente} - {proc_nome}")}',
            f'DESCRIPTION:{_ics_escape(descricao)}',
            f'STATUS:{"CONFIRMED" if at.status in (StatusAtendimento.CONFIRMADO, StatusAtendimento.REALIZADO) else "TENTATIVE"}',
            'END:VEVENT',
        ])
    lines.append('END:VCALENDAR')

    body = '\r\n'.join(lines) + '\r\n'
    resp = HttpResponse(body, content_type='text/calendar; charset=utf-8')
    resp['Content-Disposition'] = f'inline; filename="{prof.slug}-agenda.ics"'
    return resp
