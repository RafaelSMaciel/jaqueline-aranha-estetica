"""Helpers de view do painel: voltar p/ a tela de origem e o aviso de uma transicao de status.

So traducao HTTP (redirect e messages); a regra fica nos services."""
from django.contrib import messages
from django.shortcuts import redirect

from ...utils.seguranca import safe_next


def voltar_para_origem(request, padrao: str = 'aranha:painel_agendamentos'):
    """Volta p/ a pagina de onde veio o POST (Referer local; host externo e ignorado) ou p/ `padrao`."""
    return redirect(safe_next(request, request.META.get('HTTP_REFERER'), padrao))


def avisar_transicao(request, ok: bool, atendimento, verbo: str) -> None:
    """'Agendamento de <cliente> <verbo>.' ou, se o service nao transitou (outra tela decidiu antes),
    o status em que o atendimento ja esta."""
    if ok:
        messages.success(request, f'Agendamento de {atendimento.cliente.nome} {verbo}.')
    else:
        messages.warning(request, f'Atendimento já está como {atendimento.get_status_display().lower()}.')
