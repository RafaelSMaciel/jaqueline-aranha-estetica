"""Aprovar/rejeitar pedidos de agendamento pelo painel (um a um e em lote).

Views finas: o AgendamentoService decide (FSM + auditoria + e-mail a cliente) e devolve se
transitou; aqui so o aviso e o retorno p/ a tela de origem."""
from django.contrib import messages
from django.shortcuts import get_object_or_404

from ...decorators import escrita_painel
from ...models import Atendimento
from ...services.agendamento import AgendamentoService
from ...utils.parse import id_int
from .comum import avisar_transicao, voltar_para_origem

ACOES_EM_LOTE = {'aprovar': 'aprovado(s)', 'rejeitar': 'rejeitado(s)'}


def _pedido(pk):
    return get_object_or_404(Atendimento.objects.select_related('cliente', 'procedimento', 'profissional'), pk=pk)


@escrita_painel
def admin_aprovar_agendamento(request, pk):
    """Gerente/recepção aprova agendamento PENDENTE → AGENDADO (e-mail de confirmacao a cliente)."""
    atendimento = _pedido(pk)
    ok = AgendamentoService().aprovar(atendimento, by_user=request.user, request=request)
    avisar_transicao(request, ok, atendimento, 'aprovado')
    return voltar_para_origem(request)


@escrita_painel
def admin_rejeitar_agendamento(request, pk):
    """Gerente/recepção rejeita agendamento PENDENTE → CANCELADO (e-mail de cancelamento)."""
    atendimento = _pedido(pk)
    ok = AgendamentoService().rejeitar(atendimento, by_user=request.user, request=request)
    avisar_transicao(request, ok, atendimento, 'rejeitado')
    return voltar_para_origem(request)


@escrita_painel
def admin_bulk_agendamentos(request):
    """Aprovacao/rejeicao em massa: POST ids=[...] + acao=aprovar|rejeitar.

    Cada item passa pelo AgendamentoService na sua propria transacao: o que mudou de estado no
    meio do caminho e ignorado sem abortar o lote."""
    acao = request.POST.get('acao', '').strip().lower()
    if acao not in ACOES_EM_LOTE:
        messages.error(request, 'Ação inválida.')
        return voltar_para_origem(request)
    ids = [id_int(bruto) for bruto in request.POST.getlist('ids')]
    if None in ids:
        messages.error(request, 'Seleção inválida.')
        return voltar_para_origem(request)
    if not ids:
        messages.warning(request, 'Nenhum agendamento selecionado.')
        return voltar_para_origem(request)

    processados = AgendamentoService().decidir_em_lote(ids, acao, by_user=request.user, request=request)
    ignorados = len(ids) - processados
    msg = f'{processados} agendamento(s) {ACOES_EM_LOTE[acao]}.'
    if ignorados:
        msg += f' {ignorados} ignorado(s) (não estavam pendentes).'
    messages.success(request, msg)
    return voltar_para_origem(request)
