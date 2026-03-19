from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q, Sum
from datetime import datetime
import logging

from ..models import Venda, Cliente, Procedimento, Profissional
from ..decorators import staff_required
from ..utils.audit import registrar_log

logger = logging.getLogger(__name__)


@staff_required
def painel_vendas(request):
    """Lista de vendas com filtros."""
    status_filter = request.GET.get('status', 'all')
    search = request.GET.get('search', '')
    data_filter = request.GET.get('data', '')

    vendas = Venda.objects.select_related(
        'cliente', 'procedimento', 'profissional'
    ).order_by('-data')

    if status_filter != 'all':
        vendas = vendas.filter(status=status_filter.upper())
    if search:
        vendas = vendas.filter(
            Q(cliente__nome_completo__icontains=search) |
            Q(procedimento__nome__icontains=search)
        )
    if data_filter:
        try:
            data = datetime.strptime(data_filter, '%Y-%m-%d').date()
            vendas = vendas.filter(data=data)
        except ValueError:
            pass

    # Totais
    total_vendas = vendas.count()
    total_valor = vendas.filter(status='PAGO').aggregate(total=Sum('valor'))['total'] or 0

    clientes = Cliente.objects.filter(ativo=True).order_by('nome_completo')
    procedimentos = Procedimento.objects.filter(ativo=True)
    profissionais = Profissional.objects.filter(ativo=True)

    context = {
        'vendas': vendas[:100],
        'total_vendas': total_vendas,
        'total_valor': total_valor,
        'clientes': clientes,
        'procedimentos': procedimentos,
        'profissionais': profissionais,
        'status_filter': status_filter,
        'search': search,
    }
    return render(request, 'painel/painel_vendas.html', context)


@staff_required
def criar_venda(request):
    """Cria uma nova venda via POST."""
    if request.method == 'POST':
        try:
            cliente = get_object_or_404(Cliente, pk=request.POST.get('cliente'))
            procedimento = get_object_or_404(Procedimento, pk=request.POST.get('procedimento'))
            profissional = None
            prof_id = request.POST.get('profissional')
            if prof_id:
                profissional = get_object_or_404(Profissional, pk=prof_id)

            venda = Venda.objects.create(
                cliente=cliente,
                procedimento=procedimento,
                profissional=profissional,
                data=request.POST.get('data'),
                sessoes=int(request.POST.get('sessoes', 1)),
                valor=request.POST.get('valor'),
                status=request.POST.get('status', 'PENDENTE'),
                observacoes=request.POST.get('observacoes', '').strip(),
            )
            registrar_log(request.user, f'Criou venda #{venda.pk}', 'venda', venda.pk)
            messages.success(request, 'Venda registrada com sucesso!')
        except Exception as e:
            logger.error(f'Erro ao criar venda: {e}', exc_info=True)
            messages.error(request, 'Erro ao registrar venda. Verifique os dados.')
    return redirect('shivazen:painel_vendas')


@staff_required
def editar_venda(request, pk):
    """Edita uma venda existente via POST."""
    venda = get_object_or_404(Venda, pk=pk)
    if request.method == 'POST':
        try:
            venda.cliente = get_object_or_404(Cliente, pk=request.POST.get('cliente'))
            venda.procedimento = get_object_or_404(Procedimento, pk=request.POST.get('procedimento'))
            prof_id = request.POST.get('profissional')
            venda.profissional = get_object_or_404(Profissional, pk=prof_id) if prof_id else None
            venda.data = request.POST.get('data')
            venda.sessoes = int(request.POST.get('sessoes', 1))
            venda.valor = request.POST.get('valor')
            venda.status = request.POST.get('status', venda.status)
            venda.observacoes = request.POST.get('observacoes', '').strip()
            venda.save()
            registrar_log(request.user, f'Editou venda #{venda.pk}', 'venda', venda.pk)
            messages.success(request, 'Venda atualizada!')
        except Exception as e:
            logger.error(f'Erro ao editar venda: {e}', exc_info=True)
            messages.error(request, 'Erro ao atualizar venda.')
    return redirect('shivazen:painel_vendas')
