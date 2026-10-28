"""Aprovar/rejeitar pedidos de agendamento pelo painel (um a um e em lote)."""
import logging

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from ...decorators import staff_required
from ...models import Atendimento
from ...models.agendamentos import StatusAtendimento
from ...utils.seguranca import safe_next

logger = logging.getLogger(__name__)


def _voltar(request):
    """Volta p/ a pagina de origem (Referer local) ou p/ a lista de agendamentos."""
    return redirect(safe_next(request, request.META.get('HTTP_REFERER'), 'aranha:painel_agendamentos'))


@staff_required
@require_POST
@ratelimit(key='user', rate='60/m', method='POST', block=True)
def admin_aprovar_agendamento(request, pk):
    """Gerente/recepção aprova agendamento PENDENTE → AGENDADO.

    View thin: delega regras + email + audit ao AgendamentoService.
    """
    atendimento = get_object_or_404(
        Atendimento.objects.select_related('cliente', 'procedimento', 'profissional'),
        pk=pk,
    )
    from ...services.agendamento import AgendamentoService
    try:
        transitou = AgendamentoService().aprovar(atendimento, by_user=request.user, request=request)
    except Atendimento.TransicaoInvalida:
        transitou = False
    if not transitou:
        messages.warning(request, f'Atendimento já está como {atendimento.get_status_display().lower()}.')
    else:
        messages.success(request, f'Agendamento de {atendimento.cliente.nome} aprovado.')
    return _voltar(request)


@staff_required
@require_POST
@ratelimit(key='user', rate='60/m', method='POST', block=True)
def admin_rejeitar_agendamento(request, pk):
    """Gerente/recepção rejeita agendamento PENDENTE → CANCELADO.

    View thin: delega regras + email + audit ao AgendamentoService.
    """
    atendimento = get_object_or_404(
        Atendimento.objects.select_related('cliente', 'procedimento', 'profissional'),
        pk=pk,
    )
    from ...services.agendamento import AgendamentoService
    try:
        transitou = AgendamentoService().rejeitar(atendimento, by_user=request.user, request=request)
    except Atendimento.TransicaoInvalida:
        transitou = False
    if not transitou:
        messages.warning(request, f'Atendimento já está como {atendimento.get_status_display().lower()}.')
    else:
        messages.success(request, f'Agendamento de {atendimento.cliente.nome} rejeitado.')
    return _voltar(request)


@staff_required
@require_POST
@ratelimit(key='user', rate='10/m', method='POST', block=True)
def admin_bulk_agendamentos(request):
    """Aprovacao/rejeicao em massa de agendamentos PENDENTES.

    POST: ids=[...] + acao=aprovar|rejeitar. Cada item passa pelo
    AgendamentoService (FSM + auditoria + e-mail pos-commit com hora local);
    item que mudou de estado no meio do caminho e ignorado sem abortar o lote.
    """
    ids_raw = request.POST.getlist('ids') or []
    acao = request.POST.get('acao', '').strip().lower()

    if acao not in ('aprovar', 'rejeitar'):
        messages.error(request, 'Ação inválida.')
        return _voltar(request)

    try:
        ids = [int(x) for x in ids_raw]
    except (TypeError, ValueError):
        messages.error(request, 'Seleção inválida.')
        return _voltar(request)

    if not ids:
        messages.warning(request, 'Nenhum agendamento selecionado.')
        return _voltar(request)

    from ...services.agendamento import AgendamentoService
    service = AgendamentoService()

    processados = 0
    with transaction.atomic():
        atendimentos = list(
            Atendimento.objects.select_related('cliente', 'procedimento', 'profissional')
            .filter(pk__in=ids, status=StatusAtendimento.PENDENTE)
        )
        for at in atendimentos:
            try:
                if acao == 'aprovar':
                    ok = service.aprovar(at, by_user=request.user, request=request)
                else:
                    ok = service.rejeitar(at, motivo='Rejeitado em lote', by_user=request.user, request=request)
            except Atendimento.TransicaoInvalida:
                ok = False
            if ok:
                processados += 1

    ignorados = len(ids) - processados
    msg_acao = 'aprovado(s)' if acao == 'aprovar' else 'rejeitado(s)'
    msg = f'{processados} agendamento(s) {msg_acao}.'
    if ignorados:
        msg += f' {ignorados} ignorado(s) (não estavam pendentes).'
    messages.success(request, msg)
    return _voltar(request)
