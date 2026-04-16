import logging
from datetime import datetime, timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..decorators import profissional_required
from ..models import AnotacaoSessao, Atendimento

logger = logging.getLogger(__name__)


def _profissional_do_usuario(user):
    """Retorna o Profissional vinculado ao Usuario, ou None se for staff puro."""
    return getattr(user, 'profissional', None)


@profissional_required
def agenda(request):
    """Agenda do profissional logado — dia e semana."""
    prof = _profissional_do_usuario(request.user)

    # Staff sem profissional vinculado cai no painel admin
    if not prof:
        return redirect('shivazen:painel_overview')

    data_str = request.GET.get('data', '')
    try:
        dia = datetime.strptime(data_str, '%Y-%m-%d').date() if data_str else timezone.localdate()
    except ValueError:
        dia = timezone.localdate()

    inicio_semana = dia - timedelta(days=dia.weekday())
    fim_semana = inicio_semana + timedelta(days=7)

    atendimentos_dia = Atendimento.objects.filter(
        profissional=prof,
        data_hora_inicio__date=dia,
    ).select_related(
        'cliente', 'cliente__prontuario', 'procedimento'
    ).order_by('data_hora_inicio')

    atendimentos_semana = Atendimento.objects.filter(
        profissional=prof,
        data_hora_inicio__date__gte=inicio_semana,
        data_hora_inicio__date__lt=fim_semana,
    ).select_related(
        'cliente', 'procedimento'
    ).order_by('data_hora_inicio')

    dias_semana = [inicio_semana + timedelta(days=i) for i in range(7)]
    agenda_por_dia = {d: [] for d in dias_semana}
    for at in atendimentos_semana:
        agenda_por_dia[at.data_hora_inicio.date()].append(at)

    context = {
        'profissional': prof,
        'dia': dia,
        'dia_anterior': dia - timedelta(days=1),
        'dia_seguinte': dia + timedelta(days=1),
        'hoje': timezone.localdate(),
        'atendimentos_dia': atendimentos_dia,
        'agenda_por_dia': agenda_por_dia,
        'dias_semana': dias_semana,
    }
    return render(request, 'profissional/agenda.html', context)


@profissional_required
@require_POST
def marcar_realizado(request, pk):
    """Profissional marca atendimento como realizado."""
    prof = _profissional_do_usuario(request.user)
    atendimento = get_object_or_404(Atendimento, pk=pk)

    if prof and atendimento.profissional_id != prof.pk:
        messages.error(request, 'Este atendimento nao e seu.')
        return redirect('shivazen:profissional_agenda')

    if atendimento.status in ['REALIZADO', 'CANCELADO', 'FALTOU', 'REAGENDADO']:
        messages.warning(
            request,
            f'Atendimento ja esta como {atendimento.get_status_display().lower()}.'
        )
    else:
        atendimento.status = 'REALIZADO'
        atendimento.save()
        messages.success(request, f'Atendimento de {atendimento.cliente.nome_completo} marcado como realizado.')

    return redirect(request.POST.get('next') or 'shivazen:profissional_agenda')


@profissional_required
def anotar(request, pk):
    """Formulario para adicionar anotacao de sessao ao atendimento."""
    prof = _profissional_do_usuario(request.user)
    atendimento = get_object_or_404(
        Atendimento.objects.select_related('cliente', 'procedimento'),
        pk=pk,
    )

    if prof and atendimento.profissional_id != prof.pk:
        messages.error(request, 'Este atendimento nao e seu.')
        return redirect('shivazen:profissional_agenda')

    anotacoes = AnotacaoSessao.objects.filter(
        atendimento=atendimento
    ).select_related('usuario').order_by('-criado_em')

    if request.method == 'POST':
        texto = request.POST.get('texto', '').strip()
        if not texto:
            messages.error(request, 'Digite o conteudo da anotacao.')
            return redirect('shivazen:profissional_anotar', pk=pk)

        AnotacaoSessao.objects.create(
            atendimento=atendimento,
            usuario=request.user,
            texto=texto,
        )
        messages.success(request, 'Anotacao salva.')
        return redirect('shivazen:profissional_agenda')

    context = {
        'atendimento': atendimento,
        'anotacoes': anotacoes,
    }
    return render(request, 'profissional/anotar.html', context)
