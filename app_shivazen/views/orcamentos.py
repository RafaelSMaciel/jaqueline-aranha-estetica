from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Q
from datetime import datetime
import logging

from ..models import Orcamento, Procedimento
from ..decorators import staff_required
from ..utils.audit import registrar_log

logger = logging.getLogger(__name__)


@staff_required
def painel_orcamentos(request):
    """Lista de orçamentos com filtros."""
    status_filter = request.GET.get('status', 'all')
    search = request.GET.get('search', '')

    orcamentos = Orcamento.objects.select_related('procedimento').order_by('-data_criacao')

    if status_filter != 'all':
        orcamentos = orcamentos.filter(status=status_filter.upper())
    if search:
        orcamentos = orcamentos.filter(
            Q(nome_completo__icontains=search) |
            Q(cpf__icontains=search) |
            Q(procedimento__nome__icontains=search)
        )

    procedimentos = Procedimento.objects.filter(ativo=True)

    context = {
        'orcamentos': orcamentos[:100],
        'procedimentos': procedimentos,
        'status_filter': status_filter,
        'search': search,
    }
    return render(request, 'painel/painel_orcamentos.html', context)


@staff_required
def criar_orcamento(request):
    """Cria um novo orçamento via POST."""
    if request.method == 'POST':
        try:
            procedimento = get_object_or_404(Procedimento, pk=request.POST.get('procedimento'))

            orcamento = Orcamento.objects.create(
                nome_completo=request.POST.get('nome_completo', '').strip(),
                data_nascimento=request.POST.get('data_nascimento') or None,
                profissao=request.POST.get('profissao', '').strip(),
                endereco_cep=request.POST.get('endereco_cep', '').strip(),
                email=request.POST.get('email', '').strip(),
                rg=request.POST.get('rg', '').strip(),
                cpf=request.POST.get('cpf', '').strip(),
                telefone=request.POST.get('telefone', '').strip(),
                procedimento=procedimento,
                sessoes=int(request.POST.get('sessoes', 1)),
                valor=request.POST.get('valor'),
                data=request.POST.get('data'),
                status=request.POST.get('status', 'PENDENTE'),
                observacoes=request.POST.get('observacoes', '').strip(),
                # Questionário pré-procedimento
                tratamento_estetico_anterior=request.POST.get('tratamento_estetico_anterior', '').strip(),
                doenca_pele=request.POST.get('doenca_pele', '').strip(),
                tratamento_cancer=request.POST.get('tratamento_cancer', '').strip(),
                melasma_pintas=request.POST.get('melasma_pintas', '').strip(),
                uso_acido=request.POST.get('uso_acido', '').strip(),
                medicacao_continua=request.POST.get('medicacao_continua', '').strip(),
                gravida_amamentando=request.POST.get('gravida_amamentando', '').strip(),
                alergia=request.POST.get('alergia', '').strip(),
                implante_marcapasso=request.POST.get('implante_marcapasso', '').strip(),
            )
            registrar_log(request.user, f'Criou orçamento #{orcamento.pk}', 'orcamento', orcamento.pk)
            messages.success(request, 'Orçamento criado com sucesso!')
        except Exception as e:
            logger.error(f'Erro ao criar orçamento: {e}', exc_info=True)
            messages.error(request, 'Erro ao criar orçamento. Verifique os dados.')
    return redirect('shivazen:painel_orcamentos')


@staff_required
def editar_orcamento(request, pk):
    """Edita um orçamento existente via POST."""
    orcamento = get_object_or_404(Orcamento, pk=pk)
    if request.method == 'POST':
        try:
            orcamento.nome_completo = request.POST.get('nome_completo', orcamento.nome_completo).strip()
            orcamento.data_nascimento = request.POST.get('data_nascimento') or orcamento.data_nascimento
            orcamento.profissao = request.POST.get('profissao', '').strip()
            orcamento.endereco_cep = request.POST.get('endereco_cep', '').strip()
            orcamento.email = request.POST.get('email', '').strip()
            orcamento.rg = request.POST.get('rg', '').strip()
            orcamento.cpf = request.POST.get('cpf', '').strip()
            orcamento.telefone = request.POST.get('telefone', '').strip()
            orcamento.procedimento = get_object_or_404(Procedimento, pk=request.POST.get('procedimento'))
            orcamento.sessoes = int(request.POST.get('sessoes', 1))
            orcamento.valor = request.POST.get('valor')
            orcamento.data = request.POST.get('data')
            orcamento.status = request.POST.get('status', orcamento.status)
            orcamento.observacoes = request.POST.get('observacoes', '').strip()
            # Questionário
            orcamento.tratamento_estetico_anterior = request.POST.get('tratamento_estetico_anterior', '').strip()
            orcamento.doenca_pele = request.POST.get('doenca_pele', '').strip()
            orcamento.tratamento_cancer = request.POST.get('tratamento_cancer', '').strip()
            orcamento.melasma_pintas = request.POST.get('melasma_pintas', '').strip()
            orcamento.uso_acido = request.POST.get('uso_acido', '').strip()
            orcamento.medicacao_continua = request.POST.get('medicacao_continua', '').strip()
            orcamento.gravida_amamentando = request.POST.get('gravida_amamentando', '').strip()
            orcamento.alergia = request.POST.get('alergia', '').strip()
            orcamento.implante_marcapasso = request.POST.get('implante_marcapasso', '').strip()
            orcamento.save()
            registrar_log(request.user, f'Editou orçamento #{orcamento.pk}', 'orcamento', orcamento.pk)
            messages.success(request, 'Orçamento atualizado!')
        except Exception as e:
            logger.error(f'Erro ao editar orçamento: {e}', exc_info=True)
            messages.error(request, 'Erro ao atualizar orçamento.')
    return redirect('shivazen:painel_orcamentos')
