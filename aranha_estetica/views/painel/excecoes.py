"""CRUD das excecoes de agenda do profissional — folga e horario diferente.

Padrao Easy!Appointments / Cal.com working plan exceptions: override pontual
da grade semanal (AgendaHorario) sem mexer nela. FOLGA cobre um intervalo de
datas (ferias numa linha so); HORARIO_DIFERENTE substitui a grade num dia.
Bloqueios (intervalo exato) ficam em admin_bloqueios; feriados/recessos da
clinica (FOLGA sem profissional) no carregar_feriados / Django admin.
A validacao e o upsert ficam no AgendaExcecaoForm (forms/agenda) + AgendaExcecao.clean.
"""

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ...decorators import escrita_painel, staff_required
from ...forms.agenda import TIPOS_PAINEL, AgendaExcecaoForm
from ...forms.painel import avisar_erros
from ...models import AgendaExcecao, Profissional
from ...utils.auditoria import registrar_log


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


@escrita_painel
def admin_excecao_criar(request, prof_id):
    """Cria ou atualiza (mesmo dia/periodo) a folga ou o horario diferente do profissional."""
    prof = get_object_or_404(Profissional, pk=prof_id)
    voltar = redirect('aranha:admin_excecoes', prof_id=prof.pk)
    form = AgendaExcecaoForm(request.POST, profissional=prof, autor=request.user)
    if not form.is_valid():
        avisar_erros(request, form)
        return voltar
    try:
        with transaction.atomic():  # EXCLUDE/UNIQUE do PG numa corrida
            excecao = form.save()
    except IntegrityError:
        messages.error(request, 'Não foi possível salvar a exceção. Verifique os dados.')
        return voltar
    registrar_log(
        request.user,
        f'{"Criou" if form.criada else "Atualizou"} exceção {excecao.tipo} {excecao.periodo_display} prof={prof.nome}',
        'agenda_excecao', excecao.pk, request=request,
    )
    messages.success(request, f'Exceção {"criada" if form.criada else "atualizada"}.')
    return voltar


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
