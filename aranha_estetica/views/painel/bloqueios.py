"""Bloqueios de agenda (AgendaExcecao BLOQUEIO) — lista, cria e exclui.

Feriados/recessos e folgas ficam em admin_excecoes."""
import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.shortcuts import get_object_or_404, redirect, render
from django_ratelimit.decorators import ratelimit

from ...decorators import staff_required
from ...forms.painel import BloqueioForm, avisar_erros
from ...models import AgendaExcecao, Profissional
from ...utils.auditoria import registrar_log

logger = logging.getLogger(__name__)


@staff_required
def admin_bloqueios(request):
    """Lista e cria bloqueios de agenda (AgendaExcecao tipo BLOQUEIO)."""
    bloqueios = AgendaExcecao.objects.filter(
        tipo=AgendaExcecao.TIPO_BLOQUEIO,
    ).select_related('profissional').order_by('-data_hora_inicio')

    profissionais = Profissional.objects.filter(ativo=True)

    paginator = Paginator(bloqueios, 30)
    page = request.GET.get('page', 1)
    bloqueios_page = paginator.get_page(page)

    context = {
        'bloqueios': bloqueios_page,
        'profissionais': profissionais,
    }
    return render(request, 'painel/bloqueios.html', context)


@staff_required
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_criar_bloqueio(request):
    """Cria bloqueio de agenda via POST (BloqueioForm)."""
    if request.method != 'POST':
        return redirect('aranha:admin_bloqueios')

    form = BloqueioForm(request.POST)
    if not form.is_valid():
        avisar_erros(request, form)
        return redirect('aranha:admin_bloqueios')
    try:
        bloqueio = form.criar(request.user)
    except (DatabaseError, ValidationError) as e:
        logger.error(f'Erro ao criar bloqueio: {e}', exc_info=True)
        messages.error(request, 'Erro ao criar bloqueio.')
        return redirect('aranha:admin_bloqueios')
    alvo = bloqueio.profissional.nome if bloqueio.profissional_id else 'todos os profissionais'
    registrar_log(request.user, f'Criou bloqueio para {alvo}', 'agenda_excecao', bloqueio.pk, request=request)
    messages.success(request, 'Bloqueio criado com sucesso!')
    return redirect('aranha:admin_bloqueios')


@staff_required
def admin_excluir_bloqueio(request, bloqueio_id):
    """Exclui bloqueio de agenda via POST."""
    if request.method != 'POST':
        return redirect('aranha:admin_bloqueios')

    bloqueio = get_object_or_404(AgendaExcecao, pk=bloqueio_id, tipo=AgendaExcecao.TIPO_BLOQUEIO)
    # profissional nulo = bloqueio global ('Todos os profissionais')
    alvo = bloqueio.profissional.nome if bloqueio.profissional_id else 'todos os profissionais'
    registrar_log(request.user, f'Excluiu bloqueio de {alvo}', 'agenda_excecao', bloqueio_id, request=request)
    bloqueio.delete()
    messages.success(request, 'Bloqueio excluído!')
    return redirect('aranha:admin_bloqueios')
