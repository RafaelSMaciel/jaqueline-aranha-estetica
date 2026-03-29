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

        proc_ids = list(procedimentos.values_list('id_procedimento', flat=True))

        # Fetch all prices in one query, prefer profissional=NULL (generic price)
        precos = Preco.objects.filter(procedimento_id__in=proc_ids).order_by('profissional')
        preco_map = {}
        for p in precos:
            # First match wins; profissional=NULL sorts first (NULL < value)
            if p.procedimento_id not in preco_map:
                preco_map[p.procedimento_id] = p.valor

        for proc in procedimentos:
            procedimentos_com_preco.append({
                'id': proc.id_procedimento,
                'nome': proc.nome,
                'descricao': proc.descricao or '',
                'duracao_minutos': proc.duracao_minutos,
                'preco': float(preco_map.get(proc.id_procedimento, 0)),
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
    """Pagina de produtos/dermocosmeticos."""
    return render(request, 'servicos/produtos.html', {})
