"""CRUD ExcecaoDisponibilidade — folgas e horarios diferentes em data especifica.

Padrao Easy!Appointments / Cal.com working plan exceptions: override pontual
da regra semanal sem precisar mexer DisponibilidadeProfissional.
"""
from datetime import datetime

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ..decorators import staff_required
from ..models import ExcecaoDisponibilidade, Profissional
from ..utils.audit import registrar_log


@staff_required
def admin_excecoes(request, prof_id):
    """Lista excecoes do profissional + form add."""
    prof = get_object_or_404(Profissional, pk=prof_id)
    hoje = timezone.localdate()
    excecoes = ExcecaoDisponibilidade.objects.filter(
        profissional=prof, data__gte=hoje
    ).order_by('data')
    excecoes_passadas = ExcecaoDisponibilidade.objects.filter(
        profissional=prof, data__lt=hoje
    ).order_by('-data')[:30]

    return render(request, 'painel/excecoes.html', {
        'prof': prof,
        'excecoes': excecoes,
        'excecoes_passadas': excecoes_passadas,
        'TIPO_CHOICES': ExcecaoDisponibilidade.TIPO_CHOICES,
    })


@staff_required
def admin_excecao_criar(request, prof_id):
    prof = get_object_or_404(Profissional, pk=prof_id)
    if request.method != 'POST':
        return redirect('aranha:admin_excecoes', prof_id=prof.pk)

    data_str = request.POST.get('data', '').strip()
    tipo = request.POST.get('tipo', '').strip()
    hora_inicio = request.POST.get('hora_inicio', '').strip() or None
    hora_fim = request.POST.get('hora_fim', '').strip() or None
    motivo = request.POST.get('motivo', '').strip() or None

    if not data_str or tipo not in dict(ExcecaoDisponibilidade.TIPO_CHOICES):
        messages.error(request, 'Data e tipo são obrigatórios.')
        return redirect('aranha:admin_excecoes', prof_id=prof.pk)

    try:
        data = datetime.strptime(data_str, '%Y-%m-%d').date()
    except ValueError:
        messages.error(request, 'Data inválida.')
        return redirect('aranha:admin_excecoes', prof_id=prof.pk)

    if tipo == 'HORARIO_DIFERENTE' and (not hora_inicio or not hora_fim):
        messages.error(request, 'Para HORARIO_DIFERENTE informe hora início e fim.')
        return redirect('aranha:admin_excecoes', prof_id=prof.pk)

    if tipo == 'FOLGA':
        hora_inicio = None
        hora_fim = None

    excecao, created = ExcecaoDisponibilidade.objects.update_or_create(
        profissional=prof, data=data, tipo=tipo,
        defaults={'hora_inicio': hora_inicio, 'hora_fim': hora_fim, 'motivo': motivo},
    )
    registrar_log(
        request.user,
        f'{"Criou" if created else "Atualizou"} exceção {tipo} {data} prof={prof.nome}',
        'excecao_disponibilidade', excecao.pk,
    )
    messages.success(request, f'Exceção {"criada" if created else "atualizada"}.')
    return redirect('aranha:admin_excecoes', prof_id=prof.pk)


@staff_required
def admin_excecao_excluir(request, pk):
    excecao = get_object_or_404(ExcecaoDisponibilidade, pk=pk)
    prof_id = excecao.profissional_id
    if request.method == 'POST':
        info = f'{excecao.tipo} {excecao.data} prof={excecao.profissional.nome}'
        excecao.delete()
        registrar_log(request.user, f'Excluiu exceção: {info}', 'excecao_disponibilidade', pk)
        messages.success(request, 'Exceção removida.')
    return redirect('aranha:admin_excecoes', prof_id=prof_id)
