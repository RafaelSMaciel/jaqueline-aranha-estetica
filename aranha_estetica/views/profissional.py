import logging
from datetime import datetime, timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..decorators import profissional_required
from ..models import AnotacaoSessao, Atendimento
from ..utils.security import safe_next

logger = logging.getLogger(__name__)


def _redirect_seguro(request, destino, fallback='aranha:profissional_agenda'):
    """Redirect so p/ URL local (anti open-redirect no campo 'next')."""
    return redirect(safe_next(request, destino, fallback))


def _profissional_do_usuario(user):
    """Retorna o Profissional vinculado ao Usuario, ou None se for staff puro."""
    return getattr(user, 'profissional', None)


def _atendimento_do_profissional(user, pk, *, select_related=None):
    """Resolve Atendimento aplicando ownership.

    - Staff: vê qualquer atendimento.
    - Profissional: SOMENTE atendimentos seus (404 caso contrário — anti-BOLA).
    """
    qs = Atendimento.objects.all()
    if select_related:
        qs = qs.select_related(*select_related)
    if user.is_staff:
        return get_object_or_404(qs, pk=pk)
    prof = _profissional_do_usuario(user)
    if not prof:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    return get_object_or_404(qs, pk=pk, profissional=prof)


@profissional_required
def agenda(request):
    """Agenda do profissional logado — dia e semana."""
    prof = _profissional_do_usuario(request.user)

    # Staff sem profissional vinculado cai no painel admin
    if not prof:
        return redirect('aranha:painel_overview')

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
        # Indexa pela data LOCAL (consistente com dias_semana, derivado de localdate)
        # — evita KeyError perto da meia-noite quando o datetime aware esta em UTC.
        dia_local = timezone.localtime(at.data_hora_inicio).date()
        if dia_local in agenda_por_dia:
            agenda_por_dia[dia_local].append(at)

    # Agendamentos pendentes de aprovação
    pendentes = Atendimento.objects.filter(
        profissional=prof,
        status='PENDENTE',
        data_hora_inicio__gte=timezone.now(),
    ).select_related('cliente', 'procedimento').order_by('data_hora_inicio')

    context = {
        'profissional': prof,
        'dia': dia,
        'dia_anterior': dia - timedelta(days=1),
        'dia_seguinte': dia + timedelta(days=1),
        'hoje': timezone.localdate(),
        'atendimentos_dia': atendimentos_dia,
        'agenda_por_dia': agenda_por_dia,
        'dias_semana': dias_semana,
        'pendentes': pendentes,
    }
    return render(request, 'profissional/agenda.html', context)


@profissional_required
@require_POST
def marcar_realizado(request, pk):
    """Profissional marca atendimento como realizado (via FSM).

    marcar_realizado() publica AtendimentoRealizado: comissao, cashback,
    retorno e NPS dependem do evento (status direto os pulava).
    """
    atendimento = _atendimento_do_profissional(
        request.user, pk, select_related=['cliente'],
    )
    try:
        atendimento.marcar_realizado(by_user=request.user)
    except Atendimento.TransicaoInvalida:
        messages.warning(
            request,
            f'Não é possível marcar como realizado um atendimento '
            f'{atendimento.get_status_display().lower()}.'
        )
    else:
        messages.success(request, f'Atendimento de {atendimento.cliente.nome} marcado como realizado.')

    return _redirect_seguro(request, request.POST.get('next'))


@profissional_required
def anotar(request, pk):
    """Formulario para adicionar anotacao de sessao ao atendimento."""
    atendimento = _atendimento_do_profissional(
        request.user, pk, select_related=['cliente', 'procedimento'],
    )

    anotacoes = AnotacaoSessao.objects.filter(
        atendimento=atendimento
    ).select_related('autor').order_by('-criado_em')

    if request.method == 'POST':
        texto = request.POST.get('texto', '').strip()
        if not texto:
            messages.error(request, 'Digite o conteúdo da anotação.')
            return redirect('aranha:profissional_anotar', pk=pk)

        AnotacaoSessao.objects.create(
            atendimento=atendimento,
            autor=request.user,
            texto=texto,
        )
        messages.success(request, 'Anotação salva.')
        return redirect('aranha:profissional_agenda')

    context = {
        'atendimento': atendimento,
        'anotacoes': anotacoes,
    }
    return render(request, 'profissional/anotar.html', context)


@profissional_required
@require_POST
def aprovar_agendamento(request, pk):
    """Profissional aprova agendamento pendente -> AGENDADO.

    Delega ao AgendamentoService (FSM + auditoria + e-mail com hora local),
    o mesmo caminho da aprovacao pelo painel.
    """
    from ..services.agendamento_service import AgendamentoService

    atendimento = _atendimento_do_profissional(
        request.user, pk,
        select_related=['cliente', 'procedimento', 'profissional'],
    )
    try:
        transitou = AgendamentoService().aprovar(atendimento, by_user=request.user)
    except Atendimento.TransicaoInvalida:
        transitou = False
    if not transitou:
        messages.warning(request, f'Atendimento já está como {atendimento.get_status_display().lower()}.')
    else:
        messages.success(request, f'Agendamento de {atendimento.cliente.nome} aprovado.')
    return _redirect_seguro(request, request.POST.get('next'))


@profissional_required
@require_POST
def rejeitar_agendamento(request, pk):
    """Profissional rejeita agendamento pendente -> CANCELADO (via service/FSM)."""
    from ..services.agendamento_service import AgendamentoService

    atendimento = _atendimento_do_profissional(
        request.user, pk,
        select_related=['cliente', 'procedimento', 'profissional'],
    )
    try:
        transitou = AgendamentoService().rejeitar(
            atendimento, motivo='Rejeitado pelo profissional', by_user=request.user,
        )
    except Atendimento.TransicaoInvalida:
        transitou = False
    if not transitou:
        messages.warning(request, f'Atendimento já está como {atendimento.get_status_display().lower()}.')
    else:
        messages.success(request, f'Agendamento de {atendimento.cliente.nome} rejeitado.')
    return _redirect_seguro(request, request.POST.get('next'))
