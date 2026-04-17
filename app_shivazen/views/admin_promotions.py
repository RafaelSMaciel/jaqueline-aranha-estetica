"""Views de CRUD de promocoes — listagem, criacao, edicao, exclusao."""
import logging

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django_ratelimit.decorators import ratelimit

from ..decorators import staff_required
from ..models import Procedimento, Promocao
from ..utils.audit import registrar_log

logger = logging.getLogger(__name__)


@staff_required
def admin_promocoes(request):
    """Lista todas as promoções"""
    promocoes = Promocao.objects.select_related('procedimento').order_by('-ativa', '-data_inicio')

    paginator = Paginator(promocoes, 30)
    page = request.GET.get('page', 1)
    promocoes_page = paginator.get_page(page)

    procedimentos = Procedimento.objects.filter(ativo=True)
    context = {
        'promocoes': promocoes_page,
        'procedimentos': procedimentos,
    }
    return render(request, 'painel/promocoes.html', context)


@staff_required
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_criar_promocao(request):
    """Cria nova promoção via POST"""
    if request.method == 'POST':
        try:
            procedimento = get_object_or_404(Procedimento, pk=request.POST.get('procedimento'))
            promo = Promocao.objects.create(
                nome=request.POST.get('nome', '').strip(),
                descricao=request.POST.get('descricao', '').strip(),
                desconto_percentual=int(request.POST.get('desconto', 10)),
                procedimento=procedimento,
                data_inicio=request.POST.get('data_inicio'),
                data_fim=request.POST.get('data_fim'),
                ativa=request.POST.get('ativa') == '1',
            )
            registrar_log(request.user, f'Criou promoção: {promo.nome}', 'promocao', promo.pk, {'desconto': str(promo.desconto_percentual)})
            messages.success(request, 'Promoção criada com sucesso!')
        except Exception as e:
            logger.error(f'Erro ao criar promoção: {e}', exc_info=True)
            messages.error(request, 'Erro ao criar promoção. Verifique os dados e tente novamente.')
    return redirect('shivazen:admin_promocoes')


@staff_required
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_editar_promocao(request, pk):
    """Edita promoção existente via POST"""
    promo = get_object_or_404(Promocao, pk=pk)
    if request.method == 'POST':
        try:
            promo.nome = request.POST.get('nome', promo.nome).strip()
            promo.descricao = request.POST.get('descricao', promo.descricao).strip()
            promo.desconto_percentual = int(request.POST.get('desconto', promo.desconto_percentual))
            promo.procedimento = get_object_or_404(Procedimento, pk=request.POST.get('procedimento'))
            promo.data_inicio = request.POST.get('data_inicio')
            promo.data_fim = request.POST.get('data_fim')
            promo.ativa = request.POST.get('ativa') == '1'
            promo.save()
            registrar_log(request.user, f'Editou promoção: {promo.nome}', 'promocao', promo.pk)
            messages.success(request, 'Promoção atualizada!')
        except Exception as e:
            logger.error(f'Erro ao atualizar promoção: {e}', exc_info=True)
            messages.error(request, 'Erro ao atualizar promoção. Verifique os dados e tente novamente.')
    return redirect('shivazen:admin_promocoes')


@staff_required
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_excluir_promocao(request, pk):
    """Exclui promoção via POST"""
    if request.method == 'POST':
        try:
            promo = get_object_or_404(Promocao, pk=pk)
            nome = promo.nome
            promo.delete()
            registrar_log(request.user, f'Excluiu promoção: {nome}', 'promocao', pk)
            messages.success(request, 'Promoção excluída!')
        except Exception as e:
            logger.error(f'Erro ao excluir promoção: {e}', exc_info=True)
            messages.error(request, 'Erro ao excluir promoção.')
    return redirect('shivazen:admin_promocoes')
