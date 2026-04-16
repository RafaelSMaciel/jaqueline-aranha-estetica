import logging

from django.db import OperationalError, ProgrammingError
from django.shortcuts import render

from ..models import Preco, Procedimento

logger = logging.getLogger(__name__)


def _get_procedimentos_com_preco(categoria='FACIAL'):
    """Retorna procedimentos de uma categoria enriquecidos com precos."""
    procedimentos_com_preco = []
    try:
        procedimentos = Procedimento.objects.filter(ativo=True, categoria=categoria)

        proc_ids = list(procedimentos.values_list('pk', flat=True))

        # Fetch all prices in one query, prefer profissional=NULL (generic price)
        precos = Preco.objects.filter(procedimento_id__in=proc_ids).order_by('profissional')
        preco_map = {}
        for p in precos:
            if p.procedimento_id not in preco_map:
                preco_map[p.procedimento_id] = p.valor

        for proc in procedimentos:
            procedimentos_com_preco.append({
                'id': proc.pk,
                'nome': proc.nome,
                'descricao': proc.descricao or '',
                'duracao_minutos': proc.duracao_minutos,
                'preco': float(preco_map.get(proc.pk, 0)),
            })
    except (OperationalError, ProgrammingError):
        logger.warning('Tabelas nao encontradas para procedimentos.')

    return procedimentos_com_preco


def servicos_faciais(request):
    """Pagina de servicos faciais com dados reais."""
    context = {
        'procedimentos': _get_procedimentos_com_preco('FACIAL'),
    }
    return render(request, 'servicos/faciais.html', context)


def servicos_corporais(request):
    """Pagina de servicos corporais com dados reais."""
    context = {
        'procedimentos': _get_procedimentos_com_preco('CORPORAL'),
    }
    return render(request, 'servicos/corporais.html', context)


def servicos_produtos(request):
    """Pagina de produtos/dermocosmeticos."""
    return render(request, 'servicos/produtos.html', {})
