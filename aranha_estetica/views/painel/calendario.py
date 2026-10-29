"""Visao de calendario (FullCalendar) para agendamentos — alternativa a lista.

O JS da tela mora em static/src/js/painel-calendario.js (entrada do Vite); as URLs vem do
template por data-* e a resposta do arrasto segue o contrato de utils/respostas.
"""
import json
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render

from ...decorators import escrita_painel, staff_required
from ...excecoes import RegraDeNegocio, TransicaoInvalida
from ...models import AgendaExcecao, Atendimento, Profissional
from ...models.agendamentos import StatusAtendimento
from ...services import agenda
from ...utils.auditoria import registrar_log
from ...utils.datas import data_local, fmt_local, ler_instante_iso
from ...utils.parse import id_int
from ...utils.respostas import json_erro, json_ok, json_recusa

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


def _evento_atendimento(at) -> dict:
    cor = STATUS_COLORS.get(at.status, 'var(--cor-texto-suave)')
    return {
        'id': at.pk,
        'title': f'{at.cliente.nome} · {at.procedimento.nome}',
        'start': at.data_hora_inicio.isoformat(),
        'end': at.data_hora_fim.isoformat(),
        'backgroundColor': cor,
        'borderColor': cor,
        'textColor': 'var(--cor-superficie)',   # >= 7:1 nos dois temas
        'extendedProps': {
            'profissional': at.profissional.nome,
            'procedimento': at.procedimento.nome,
            'cliente_nome': at.cliente.nome,
            'cliente_telefone': at.cliente.telefone or '',
            'status': at.status,
            'valor': float(at.valor_cobrado) if at.valor_cobrado else None,
        },
    }


def _eventos_excecao(ex, dt_start, dt_end) -> list[dict]:
    """Bloqueio (recorrente expandido) ou folga/feriado como evento de fundo."""
    if ex.tipo == AgendaExcecao.TIPO_BLOQUEIO:
        return [{
            'id': f'bloq-{ex.pk}' if not ex.regra_recorrencia else f'bloq-{ex.pk}-{n}',
            'title': f'Bloqueio: {ex.motivo or "—"}',
            'start': ini.isoformat(),
            'end': fim.isoformat(),
            'display': 'background',
            'backgroundColor': 'var(--cor-borda-campo)',   # bloqueio (tokens: claro/escuro)
            'editable': False,
        } for n, (ini, fim) in enumerate(ex.ocorrencias(dt_start, dt_end))]
    if ex.profissional_id:
        rotulo = 'Folga'
    elif ex.origem == AgendaExcecao.ORIGEM_MANUAL:
        rotulo = 'Clínica fechada'
    else:
        rotulo = ex.get_origem_display()  # Feriado nacional/local, Recesso da clinica
    return [{
        'id': f'folga-{ex.pk}',
        'title': f'{rotulo}: {ex.motivo or "—"}',
        'start': ex.data_inicio.isoformat(),
        # allDay: o fim do FullCalendar e exclusivo
        'end': (ex.data_fim + timedelta(days=1)).isoformat(),
        'allDay': True,
        'display': 'background',
        'backgroundColor': 'var(--cor-aviso-fundo)',   # folga/feriado
        'editable': False,
    }]


@staff_required
def admin_calendar_events(request):
    """Eventos do range no formato do FullCalendar (lista crua, fora do envelope de
    utils/respostas: e o que a biblioteca le). Range invalido -> lista vazia."""
    # filtro nao numerico/'²' (URL manipulada) e ignorado em vez de 500
    prof_filter = id_int(request.GET.get('profissional'))
    try:
        dt_start = ler_instante_iso(request.GET.get('start', ''))
        dt_end = ler_instante_iso(request.GET.get('end', ''))
    except ValueError:
        return JsonResponse([], safe=False)

    qs = Atendimento.objects.select_related('cliente', 'profissional', 'procedimento').filter(
        data_hora_inicio__gte=dt_start,
        data_hora_inicio__lt=dt_end,
    )
    if prof_filter:
        qs = qs.filter(profissional_id=prof_filter)
    eventos = [_evento_atendimento(at) for at in qs]

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
        eventos.extend(_eventos_excecao(ex, dt_start, dt_end))
    return JsonResponse(eventos, safe=False)


def _ler_movimento(corpo: bytes):
    """(pk, inicio, fim) do arrasto do calendario. ValueError se o corpo nao for o esperado."""
    payload = json.loads(corpo)   # JSONDecodeError e corpo que nao e UTF-8 sao ValueError
    if not isinstance(payload, dict):
        raise ValueError('corpo nao e um objeto JSON')
    pk = id_int(payload.get('id'))
    if pk is None:
        raise ValueError('id invalido')
    return pk, ler_instante_iso(payload.get('start')), ler_instante_iso(payload.get('end'))


def _status_da_recusa(recusa: RegraDeNegocio) -> int:
    if recusa.codigo == 'nao_encontrado':
        return 404
    return 400 if isinstance(recusa, TransicaoInvalida) else 409


@escrita_painel
def admin_calendar_mover(request):
    """Reagenda atendimento via drag-drop no calendario (services.agenda.mover).

    Mesma regra de horario do site, do painel e do portal (SlotService), com o motivo da recusa
    p/ a equipe (conflito, bloqueio, folga, feriado, fora do expediente). Preco/promocao e
    duracao combinados ficam (eventDurationEditable=false).
    """
    try:
        pk, novo_inicio, novo_fim = _ler_movimento(request.body)
    except ValueError:
        return json_erro('dados_invalidos', 'Dados inválidos.')
    if novo_fim <= novo_inicio:
        return json_erro('fim_antes_do_inicio', 'O fim deve ser depois do início.')

    try:
        with transaction.atomic():
            at, antigo = agenda.mover(pk, novo_inicio)
            registrar_log(
                request.user, 'Moveu agendamento via calendario',
                'atendimento', at.pk,
                detalhes={'de': antigo.isoformat(), 'para': novo_inicio.isoformat()}, request=request,
            )
    except RegraDeNegocio as recusa:
        return json_recusa(recusa, status=_status_da_recusa(recusa))

    # FullCalendar envia ISO em UTC ('Z'): formata no fuso da clinica
    return json_ok(nova_data=fmt_local(novo_inicio))
