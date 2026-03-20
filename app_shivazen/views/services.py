from django.shortcuts import render
from django.db import OperationalError, ProgrammingError
import logging

from ..models import Procedimento, Preco, Promocao

logger = logging.getLogger(__name__)


def _get_procedimentos_com_preco(tipo='facial'):
    """Retorna procedimentos enriquecidos com preços."""
    procedimentos_com_preco = []
    try:
        # Define quais procedimentos são faciais
        nomes_faciais = [
            'Limpeza de Pele Profunda', 'Preenchimento Facial', 'Harmonização Facial',
            'Bioestimulador de Colágeno', 'Toxina Botulínica (Botox)', 'Peeling Químico',
            'Fototerapia LED', 'Microagulhamento', 'Skinbooster', 'Laser Fracionado'
        ]

        if tipo == 'facial':
            procedimentos = Procedimento.objects.filter(ativo=True, nome__in=nomes_faciais)
        else:
            procedimentos = Procedimento.objects.filter(ativo=True).exclude(nome__in=nomes_faciais)

        for proc in procedimentos:
            preco_obj = Preco.objects.filter(procedimento=proc, profissional__isnull=True).first()
            if not preco_obj:
                preco_obj = Preco.objects.filter(procedimento=proc).first()
            procedimentos_com_preco.append({
                'id': proc.id_procedimento,
                'nome': proc.nome,
                'descricao': proc.descricao or '',
                'duracao_minutos': proc.duracao_minutos,
                'preco': float(preco_obj.valor) if preco_obj else 0,
            })
    except (OperationalError, ProgrammingError):
        logger.warning('Tabelas não encontradas para procedimentos.')

    return procedimentos_com_preco


def servicos_faciais(request):
    """Página de serviços faciais com dados reais."""
    context = {
        'procedimentos': _get_procedimentos_com_preco('facial'),
    }
    return render(request, 'servicos/faciais.html', context)


def servicos_corporais(request):
    """Página de serviços corporais com dados reais."""
    context = {
        'procedimentos': _get_procedimentos_com_preco('corporal'),
    }
    return render(request, 'servicos/corporais.html', context)


def servicos_produtos(request):
    """Página de produtos/dermocosméticos."""
    from ..models import Produto, CategoriaProduto
    produtos = []
    categorias = []
    try:
        produtos = list(Produto.objects.filter(
            ativo=True, preco_venda__gt=0
        ).select_related('categoria').order_by('nome'))
        categorias = list(CategoriaProduto.objects.filter(ativo=True).order_by('nome'))
    except (OperationalError, ProgrammingError):
        logger.warning('Tabelas de produto não encontradas.')

    context = {
        'produtos': produtos,
        'categorias': categorias,
    }
    return render(request, 'servicos/produtos.html', context)
