"""CRUD das excecoes de agenda do profissional — folga e horario diferente.

Padrao Easy!Appointments / Cal.com working plan exceptions: override pontual
da grade semanal (AgendaHorario) sem mexer nela. FOLGA cobre um intervalo de
datas (ferias numa linha so); HORARIO_DIFERENTE substitui a grade num dia.
Bloqueios (intervalo exato) ficam em admin_bloqueios; feriados/recessos da
clinica (FOLGA sem profissional) no carregar_feriados / Django admin.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ..decorators import staff_required
from ..models import AgendaExcecao, Profissional
from ..utils.audit import registrar_log
from ..utils.datas import ler_data, ler_hora

TIPOS_PAINEL = [
    (AgendaExcecao.TIPO_FOLGA, 'Folga'),
    (AgendaExcecao.TIPO_HORARIO_DIFERENTE, 'Horário diferente'),
]


def _parse_hora(valor):
    """'HH:MM' ou 'HH:MM:SS' (input type=time) -> time. ValueError se invalido."""
    return ler_hora(valor)


def _parse_data(valor):
    return ler_data(valor)


def _mensagem(exc):
    if hasattr(exc, 'message_dict'):
        return ' '.join(m for msgs in exc.message_dict.values() for m in msgs)
    return ' '.join(exc.messages)


@staff_required
def admin_excecoes(request, prof_id):
    """Lista excecoes (folga/horario diferente) do profissional + form add."""
    prof = get_object_or_404(Profissional, pk=prof_id)
    hoje = timezone.localdate()
    base = AgendaExcecao.objects.ativas().filter(
        profissional=prof, tipo__in=AgendaExcecao.TIPOS_POR_DATA,
    )
    excecoes = base.filter(data_fim__gte=hoje).order_by('data_inicio', 'tipo')
    excecoes_passadas = base.filter(data_fim__lt=hoje).order_by('-data_inicio')[:30]

    return render(request, 'painel/excecoes.html', {
        'prof': prof,
        'excecoes': excecoes,
        'excecoes_passadas': excecoes_passadas,
        'TIPO_CHOICES': TIPOS_PAINEL,
    })


@staff_required
def admin_excecao_criar(request, prof_id):
    prof = get_object_or_404(Profissional, pk=prof_id)
    if request.method != 'POST':
        return redirect('aranha:admin_excecoes', prof_id=prof.pk)

    data_str = request.POST.get('data', '').strip()
    data_fim_str = request.POST.get('data_fim', '').strip()
    tipo = request.POST.get('tipo', '').strip()
    hora_inicio = request.POST.get('hora_inicio', '').strip() or None
    hora_fim = request.POST.get('hora_fim', '').strip() or None
    motivo = request.POST.get('motivo', '').strip()

    if not data_str or tipo not in dict(TIPOS_PAINEL):
        messages.error(request, 'Data e tipo são obrigatórios.')
        return redirect('aranha:admin_excecoes', prof_id=prof.pk)

    try:
        data = _parse_data(data_str)
        data_fim = _parse_data(data_fim_str) if data_fim_str else data
    except ValueError:
        messages.error(request, 'Data inválida.')
        return redirect('aranha:admin_excecoes', prof_id=prof.pk)

    if tipo == AgendaExcecao.TIPO_HORARIO_DIFERENTE:
        if not hora_inicio or not hora_fim:
            messages.error(request, 'Para horário diferente, informe a hora de início e a de fim.')
            return redirect('aranha:admin_excecoes', prof_id=prof.pk)
        try:
            hora_inicio = _parse_hora(hora_inicio)
            hora_fim = _parse_hora(hora_fim)
        except ValueError:
            messages.error(request, 'Hora inválida.')
            return redirect('aranha:admin_excecoes', prof_id=prof.pk)
        # Sem isso uma janela 14:00-09:00 zerava a agenda do dia sem aviso
        if hora_fim <= hora_inicio:
            messages.error(request, 'A hora de fim deve ser depois da hora de início.')
            return redirect('aranha:admin_excecoes', prof_id=prof.pk)
        data_fim = data  # horario diferente vale um dia
    else:
        hora_inicio = None
        hora_fim = None
        if data_fim < data:
            messages.error(request, 'A data final da folga deve ser igual ou posterior à inicial.')
            return redirect('aranha:admin_excecoes', prof_id=prof.pk)

    # Reenviar o mesmo dia/periodo atualiza (antes: update_or_create por dia+tipo)
    ativas = AgendaExcecao.objects.ativas().filter(profissional=prof, tipo=tipo)
    if tipo == AgendaExcecao.TIPO_HORARIO_DIFERENTE:
        excecao = ativas.filter(data_inicio=data).first()
    else:
        excecao = ativas.filter(data_inicio=data, data_fim=data_fim).first()
    created = excecao is None
    if created:
        excecao = AgendaExcecao(profissional=prof, tipo=tipo, origem=AgendaExcecao.ORIGEM_MANUAL)
    excecao.data_inicio = data
    excecao.data_fim = data_fim
    excecao.hora_inicio = hora_inicio
    excecao.hora_fim = hora_fim
    excecao.motivo = motivo
    if created and request.user.is_authenticated:
        excecao.usuario_autor = request.user

    try:
        excecao.full_clean()
        with transaction.atomic():  # EXCLUDE/UNIQUE do PG numa corrida
            excecao.save()
    except ValidationError as exc:
        messages.error(request, _mensagem(exc))
        return redirect('aranha:admin_excecoes', prof_id=prof.pk)
    except IntegrityError:
        messages.error(request, 'Não foi possível salvar a exceção. Verifique os dados.')
        return redirect('aranha:admin_excecoes', prof_id=prof.pk)
    registrar_log(
        request.user,
        f'{"Criou" if created else "Atualizou"} exceção {tipo} {excecao.periodo_display} prof={prof.nome}',
        'agenda_excecao', excecao.pk, request=request,
    )
    messages.success(request, f'Exceção {"criada" if created else "atualizada"}.')
    return redirect('aranha:admin_excecoes', prof_id=prof.pk)


@staff_required
def admin_excecao_excluir(request, pk):
    # so folga/horario diferente de profissional (bloqueio e feriado tem tela propria)
    excecao = get_object_or_404(
        AgendaExcecao, pk=pk, profissional__isnull=False, tipo__in=AgendaExcecao.TIPOS_POR_DATA,
    )
    prof_id = excecao.profissional_id
    if request.method == 'POST':
        info = f'{excecao.tipo} {excecao.periodo_display} prof={excecao.profissional.nome}'
        excecao.delete()
        registrar_log(request.user, f'Excluiu exceção: {info}', 'agenda_excecao', pk, request=request)
        messages.success(request, 'Exceção removida.')
    return redirect('aranha:admin_excecoes', prof_id=prof_id)
