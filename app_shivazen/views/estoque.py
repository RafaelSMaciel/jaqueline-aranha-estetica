from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q, Sum, F
from django.utils import timezone
from django.core.paginator import Paginator
from datetime import date, timedelta
import json
import logging

from ..models import Produto, CategoriaProduto, MovimentacaoEstoque
from ..decorators import staff_required
from ..utils.audit import registrar_log

logger = logging.getLogger(__name__)


@staff_required
def painel_estoque(request):
    """Dashboard de estoque com overview e lista de produtos."""
    search = request.GET.get('search', '')
    categoria_filter = request.GET.get('categoria', '')
    estoque_filter = request.GET.get('estoque', '')  # baixo, normal, all
    vencimento_filter = request.GET.get('vencimento', '')  # vencido, proximo, all

    produtos = Produto.objects.select_related('categoria').filter(ativo=True)

    if search:
        produtos = produtos.filter(
            Q(nome__icontains=search) |
            Q(marca__icontains=search) |
            Q(codigo_barras__icontains=search)
        )
    if categoria_filter:
        produtos = produtos.filter(categoria_id=categoria_filter)
    if estoque_filter == 'baixo':
        produtos = produtos.filter(quantidade_estoque__lte=F('estoque_minimo'))

    hoje = date.today()
    limite_vencimento = hoje + timedelta(days=30)

    if vencimento_filter == 'vencido':
        produtos = produtos.filter(data_validade__lt=hoje)
    elif vencimento_filter == 'proximo':
        produtos = produtos.filter(data_validade__gte=hoje, data_validade__lte=limite_vencimento)

    # Estatísticas
    total_produtos = Produto.objects.filter(ativo=True).count()
    produtos_estoque_baixo = Produto.objects.filter(
        ativo=True, quantidade_estoque__lte=F('estoque_minimo')
    ).count()
    valor_total_estoque = Produto.objects.filter(ativo=True).aggregate(
        total=Sum(F('quantidade_estoque') * F('preco_custo'))
    )['total'] or 0
    total_categorias = CategoriaProduto.objects.filter(ativo=True).count()

    # Novas estatísticas de vencimento e compra
    produtos_vencidos = Produto.objects.filter(
        ativo=True, data_validade__lt=hoje
    ).count()
    produtos_proximo_vencer = Produto.objects.filter(
        ativo=True, data_validade__gte=hoje, data_validade__lte=limite_vencimento
    ).count()
    produtos_comprar = produtos_estoque_baixo  # mesma lógica: estoque baixo = precisa comprar

    # Lista de produtos que precisam ser comprados (para alerta)
    lista_comprar = Produto.objects.select_related('categoria').filter(
        ativo=True, quantidade_estoque__lte=F('estoque_minimo')
    ).order_by('quantidade_estoque')[:10]

    categorias = CategoriaProduto.objects.filter(ativo=True).order_by('nome')

    # Paginação
    paginator = Paginator(produtos, 20)
    page = request.GET.get('page', 1)
    produtos_page = paginator.get_page(page)

    context = {
        'produtos': produtos_page,
        'categorias': categorias,
        'total_produtos': total_produtos,
        'produtos_estoque_baixo': produtos_estoque_baixo,
        'valor_total_estoque': valor_total_estoque,
        'total_categorias': total_categorias,
        'produtos_vencidos': produtos_vencidos,
        'produtos_proximo_vencer': produtos_proximo_vencer,
        'produtos_comprar': produtos_comprar,
        'lista_comprar': lista_comprar,
        'search': search,
        'categoria_filter': categoria_filter,
        'estoque_filter': estoque_filter,
        'vencimento_filter': vencimento_filter,
    }
    return render(request, 'painel/painel_estoque.html', context)


@staff_required
def criar_produto(request):
    """Cria um novo produto via POST."""
    if request.method == 'POST':
        try:
            categoria = None
            cat_id = request.POST.get('categoria')
            if cat_id:
                categoria = get_object_or_404(CategoriaProduto, pk=cat_id)

            data_validade_str = request.POST.get('data_validade', '').strip()
            data_validade = None
            if data_validade_str:
                data_validade = data_validade_str

            produto = Produto.objects.create(
                nome=request.POST.get('nome', '').strip(),
                descricao=request.POST.get('descricao', '').strip(),
                marca=request.POST.get('marca', '').strip(),
                codigo_barras=request.POST.get('codigo_barras', '').strip() or None,
                categoria=categoria,
                preco_custo=request.POST.get('preco_custo', 0),
                preco_venda=request.POST.get('preco_venda', 0),
                quantidade_estoque=int(request.POST.get('quantidade_estoque', 0)),
                estoque_minimo=int(request.POST.get('estoque_minimo', 5)),
                unidade=request.POST.get('unidade', 'UN'),
                data_validade=data_validade,
                lote=request.POST.get('lote', '').strip() or None,
                ativo=True,
            )
            registrar_log(request.user, f'Criou produto: {produto.nome}', 'produto', produto.pk)
            messages.success(request, f'Produto "{produto.nome}" cadastrado com sucesso!')
        except Exception as e:
            logger.error(f'Erro ao criar produto: {e}', exc_info=True)
            messages.error(request, 'Erro ao cadastrar produto. Verifique os dados.')
    return redirect('shivazen:painel_estoque')


@staff_required
def editar_produto(request, pk):
    """Edita um produto existente via POST."""
    produto = get_object_or_404(Produto, pk=pk)
    if request.method == 'POST':
        try:
            produto.nome = request.POST.get('nome', produto.nome).strip()
            produto.descricao = request.POST.get('descricao', '').strip()
            produto.marca = request.POST.get('marca', '').strip()
            produto.codigo_barras = request.POST.get('codigo_barras', '').strip() or None

            cat_id = request.POST.get('categoria')
            produto.categoria = get_object_or_404(CategoriaProduto, pk=cat_id) if cat_id else None

            produto.preco_custo = request.POST.get('preco_custo', produto.preco_custo)
            produto.preco_venda = request.POST.get('preco_venda', produto.preco_venda)
            produto.estoque_minimo = int(request.POST.get('estoque_minimo', produto.estoque_minimo))
            produto.unidade = request.POST.get('unidade', produto.unidade)

            data_validade_str = request.POST.get('data_validade', '').strip()
            produto.data_validade = data_validade_str if data_validade_str else None
            produto.lote = request.POST.get('lote', '').strip() or None

            produto.ativo = request.POST.get('ativo') != '0'
            produto.save()

            registrar_log(request.user, f'Editou produto: {produto.nome}', 'produto', produto.pk)
            messages.success(request, f'Produto "{produto.nome}" atualizado!')
        except Exception as e:
            logger.error(f'Erro ao editar produto: {e}', exc_info=True)
            messages.error(request, 'Erro ao atualizar produto.')
    return redirect('shivazen:painel_estoque')


@staff_required
def movimentar_estoque(request):
    """Registra uma movimentação de estoque via POST."""
    if request.method == 'POST':
        try:
            produto = get_object_or_404(Produto, pk=request.POST.get('produto'))
            tipo = request.POST.get('tipo', 'ENTRADA')
            quantidade = int(request.POST.get('quantidade', 0))
            motivo = request.POST.get('motivo', '').strip()

            if quantidade <= 0:
                messages.error(request, 'A quantidade deve ser maior que zero.')
                return redirect('shivazen:painel_estoque')

            quantidade_anterior = produto.quantidade_estoque

            if tipo in ('SAIDA', 'PERDA'):
                if quantidade > produto.quantidade_estoque:
                    messages.error(request, f'Estoque insuficiente. Disponível: {produto.quantidade_estoque}')
                    return redirect('shivazen:painel_estoque')
                produto.quantidade_estoque -= quantidade
            elif tipo == 'ENTRADA':
                produto.quantidade_estoque += quantidade
            elif tipo == 'AJUSTE':
                produto.quantidade_estoque = quantidade
                quantidade = abs(quantidade - quantidade_anterior)

            produto.save()

            MovimentacaoEstoque.objects.create(
                produto=produto,
                tipo=tipo,
                quantidade=quantidade,
                quantidade_anterior=quantidade_anterior,
                quantidade_posterior=produto.quantidade_estoque,
                motivo=motivo,
                usuario=request.user,
            )

            registrar_log(
                request.user,
                f'Movimentação {tipo}: {produto.nome} ({quantidade})',
                'movimentacao_estoque',
                produto.pk,
                {'tipo': tipo, 'quantidade': quantidade, 'motivo': motivo}
            )
            messages.success(request, f'Movimentação registrada: {tipo} de {quantidade}x {produto.nome}')
        except Exception as e:
            logger.error(f'Erro ao movimentar estoque: {e}', exc_info=True)
            messages.error(request, 'Erro ao registrar movimentação.')
    return redirect('shivazen:painel_estoque')


@staff_required
def historico_movimentacoes(request):
    """Lista o histórico de movimentações de estoque."""
    produto_filter = request.GET.get('produto', '')
    tipo_filter = request.GET.get('tipo', '')

    movimentacoes = MovimentacaoEstoque.objects.select_related(
        'produto', 'usuario'
    ).order_by('-data_movimentacao')

    if produto_filter:
        movimentacoes = movimentacoes.filter(produto_id=produto_filter)
    if tipo_filter:
        movimentacoes = movimentacoes.filter(tipo=tipo_filter)

    paginator = Paginator(movimentacoes, 30)
    page = request.GET.get('page', 1)
    movimentacoes_page = paginator.get_page(page)

    produtos = Produto.objects.filter(ativo=True).order_by('nome')

    context = {
        'movimentacoes': movimentacoes_page,
        'produtos': produtos,
        'produto_filter': produto_filter,
        'tipo_filter': tipo_filter,
    }
    return render(request, 'painel/painel_movimentacoes.html', context)


@staff_required
def criar_categoria(request):
    """Cria uma nova categoria de produto via POST."""
    if request.method == 'POST':
        try:
            nome = request.POST.get('nome', '').strip()
            descricao = request.POST.get('descricao', '').strip()
            if nome:
                CategoriaProduto.objects.create(nome=nome, descricao=descricao, ativo=True)
                messages.success(request, f'Categoria "{nome}" criada!')
            else:
                messages.error(request, 'Nome da categoria é obrigatório.')
        except Exception as e:
            logger.error(f'Erro ao criar categoria: {e}', exc_info=True)
            messages.error(request, 'Erro ao criar categoria.')
    return redirect('shivazen:painel_estoque')
