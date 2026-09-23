"""Views publicas por profissional: pagina agendar/<slug>/ + ICS feed.

ICS feed requer query param ?token=<ics_token> p/ nao vazar agenda.
"""
import hmac
from datetime import timedelta, timezone as dt_timezone
from urllib.parse import urlencode

from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.clickjacking import xframe_options_exempt

from ..models import Atendimento, Procedimento, Profissional


def agendar_por_profissional(request, slug):
    """Atalho: redireciona p/ booking publico c/ profissional fixado via query."""
    prof = get_object_or_404(Profissional, slug=slug, ativo=True)
    query = {'profissional': prof.pk}
    proc = request.GET.get('procedimento', '')
    if proc.isdigit():
        query['procedimento'] = proc
    return redirect(f"{reverse('aranha:agendamento_publico')}?{urlencode(query)}")


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
def ics_feed_profissional(request, slug):
    """Retorna agenda do profissional como text/calendar.

    URL: /agenda/<slug>/feed.ics?token=<ics_token>
    Atendimentos AGENDADO/CONFIRMADO/REALIZADO no range -30d..+90d.
    """
    prof = get_object_or_404(Profissional, slug=slug, ativo=True)
    token = (request.GET.get('token') or '').strip()
    # Comparacao em tempo constante (token e segredo de assinatura do calendario)
    if not token or not hmac.compare_digest(token.encode(), (prof.ics_token or '').encode()):
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
        'PRODID:-//aranha-estetica//agenda profissional//PT-BR',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        f'X-WR-CALNAME:{_ics_escape(prof.nome)} - Agenda',
        'X-WR-TIMEZONE:America/Sao_Paulo',
    ]
    for at in atendimentos:
        cliente_nome = at.cliente.nome if at.cliente_id else 'Cliente'
        proc_nome = at.procedimento.nome if at.procedimento_id else 'Atendimento'
        summary = f'{cliente_nome} - {proc_nome}'
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


@xframe_options_exempt
def embed_agendar(request):
    """Widget standalone p/ iframe (Linktree, Instagram bio, site externo).

    Lista procedimentos ativos com link p/ booking publico c/ ?procedimento=X.
    Permite iframe (X-Frame-Options OFF).
    """
    procedimentos = []
    try:
        # "A partir de" = mesma conta do wizard (promocao vigente hoje inclusa)
        from .booking_public import _precos_card
        procs_qs = list(Procedimento.objects.filter(ativo=True))
        precos = _precos_card(procs_qs)
        for p in procs_qs:
            valor, promo, _cheio = precos.get(p.pk, (None, None, None))
            procedimentos.append({
                'id': p.pk,
                'nome': p.nome,
                'duracao_minutos': p.duracao_minutos,
                'preco': float(valor) if valor is not None else 0,
                'promocao': promo.nome if promo is not None else '',
            })
    except (OperationalError, ProgrammingError, ImportError):
        pass

    # URL absoluta publica (iframe em outro dominio precisa do host canonico)
    booking_url = f"{settings.SITE_URL}{reverse('aranha:agendamento_publico')}"
    context = {
        'procedimentos': procedimentos,
        'booking_url': booking_url,
    }
    return render(request, 'agenda/embed.html', context)
