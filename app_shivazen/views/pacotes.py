"""Views para gestao de pacotes (CRUD admin + venda)."""
import logging
from datetime import timedelta

from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ..decorators import staff_required
from ..models import (
    Cliente,
    ItemPacote,
    Pacote,
    PacoteCliente,
    Procedimento,
)
from ..utils.audit import registrar_log

logger = logging.getLogger(__name__)


@staff_required
def admin_pacotes(request):
    """Lista todos os pacotes com itens e vendas."""
    pacotes = Pacote.objects.prefetch_related('itens__procedimento').annotate(
        total_vendas=Count('pacotecliente'),
    ).order_by('-ativo', 'nome')

    context = {
        'pacotes': pacotes,
        'procedimentos': Procedimento.objects.filter(ativo=True),
    }
    return render(request, 'painel/pacotes.html', context)


@staff_required
def admin_criar_pacote(request):
    """Cria novo pacote via POST."""
    if request.method != 'POST':
        return redirect('shivazen:admin_pacotes')

    nome = request.POST.get('nome', '').strip()
    descricao = request.POST.get('descricao', '').strip()
    preco_total = request.POST.get('preco_total', '0')
    validade_meses = request.POST.get('validade_meses', '12')

    if not nome:
        messages.error(request, 'Nome do pacote e obrigatorio.')
        return redirect('shivazen:admin_pacotes')

    try:
        pacote = Pacote.objects.create(
            nome=nome,
            descricao=descricao,
            preco_total=preco_total,
            validade_meses=int(validade_meses),
            ativo=True,
        )

        # Itens do pacote
        proc_ids = request.POST.getlist('procedimento_ids')
        qtds = request.POST.getlist('quantidades')
        for proc_id, qtd in zip(proc_ids, qtds):
            if proc_id and qtd:
                ItemPacote.objects.create(
                    pacote=pacote,
                    procedimento_id=int(proc_id),
                    quantidade_sessoes=int(qtd),
                )

        registrar_log(request.user, f'Criou pacote: {pacote.nome}', 'pacote', pacote.pk)
        messages.success(request, f'Pacote "{nome}" criado com sucesso!')
    except Exception as e:
        logger.error(f'Erro ao criar pacote: {e}', exc_info=True)
        messages.error(request, 'Erro ao criar pacote.')

    return redirect('shivazen:admin_pacotes')


@staff_required
def admin_editar_pacote(request, pk):
    """Edita pacote existente."""
    pacote = get_object_or_404(Pacote, pk=pk)

    if request.method != 'POST':
        return redirect('shivazen:admin_pacotes')

    try:
        pacote.nome = request.POST.get('nome', pacote.nome).strip()
        pacote.descricao = request.POST.get('descricao', '').strip()
        pacote.preco_total = request.POST.get('preco_total', pacote.preco_total)
        pacote.validade_meses = int(request.POST.get('validade_meses', pacote.validade_meses))
        pacote.ativo = request.POST.get('ativo') == '1'
        pacote.save()

        # Atualiza itens
        ItemPacote.objects.filter(pacote=pacote).delete()
        proc_ids = request.POST.getlist('procedimento_ids')
        qtds = request.POST.getlist('quantidades')
        for proc_id, qtd in zip(proc_ids, qtds):
            if proc_id and qtd:
                ItemPacote.objects.create(
                    pacote=pacote,
                    procedimento_id=int(proc_id),
                    quantidade_sessoes=int(qtd),
                )

        registrar_log(request.user, f'Editou pacote: {pacote.nome}', 'pacote', pacote.pk)
        messages.success(request, f'Pacote "{pacote.nome}" atualizado!')
    except Exception as e:
        logger.error(f'Erro ao editar pacote: {e}', exc_info=True)
        messages.error(request, 'Erro ao editar pacote.')

    return redirect('shivazen:admin_pacotes')


@staff_required
def admin_vender_pacote(request):
    """Vende pacote para um cliente."""
    if request.method != 'POST':
        return redirect('shivazen:admin_pacotes')

    pacote_id = request.POST.get('pacote_id')
    cliente_id = request.POST.get('cliente_id')
    valor_pago = request.POST.get('valor_pago', '0')

    try:
        pacote = get_object_or_404(Pacote, pk=pacote_id)
        cliente = get_object_or_404(Cliente, pk=cliente_id)

        data_expiracao = (timezone.now() + timedelta(days=30 * pacote.validade_meses)).date()

        pc = PacoteCliente.objects.create(
            cliente=cliente,
            pacote=pacote,
            valor_pago=valor_pago,
            status='ATIVO',
            data_expiracao=data_expiracao,
        )

        registrar_log(
            request.user,
            f'Vendeu pacote "{pacote.nome}" para {cliente.nome_completo}',
            'pacote_cliente', pc.pk,
        )
        messages.success(request, f'Pacote vendido para {cliente.nome_completo}!')
    except Exception as e:
        logger.error(f'Erro ao vender pacote: {e}', exc_info=True)
        messages.error(request, 'Erro ao vender pacote.')

    return redirect('shivazen:admin_pacotes')
