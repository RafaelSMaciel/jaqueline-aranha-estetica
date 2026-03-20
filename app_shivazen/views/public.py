import logging

from django.db import OperationalError, ProgrammingError
from django.shortcuts import render
from django.utils import timezone

from ..models import Procedimento, Promocao, Preco

logger = logging.getLogger(__name__)


def home(request):
    return render(request, 'inicio/home.html')


def termosUso(request):
    return render(request, 'inicio/termosUso.html')


def politicaPrivacidade(request):
    return render(request, 'inicio/politicaPrivacidade.html')


def quemsomos(request):
    return render(request, 'inicio/quemsomos.html')


def agendaContato(request):
    return render(request, 'agenda/contato.html')


def promocoes(request):
    """Lista de promoções ativas e vigentes"""
    promos = []
    try:
        hoje = timezone.now().date()
        promos = list(Promocao.objects.filter(
            ativa=True,
            data_inicio__lte=hoje,
            data_fim__gte=hoje
        ).select_related('procedimento').order_by('-data_inicio'))

        # Enriquecer com preço original
        for promo in promos:
            if promo.procedimento:
                preco_obj = Preco.objects.filter(
                    procedimento=promo.procedimento, profissional__isnull=True
                ).first()
                if not preco_obj:
                    preco_obj = Preco.objects.filter(procedimento=promo.procedimento).first()
                promo.preco_original = float(preco_obj.valor) if preco_obj else None
            else:
                promo.preco_original = None
    except (OperationalError, ProgrammingError):
        logger.warning('Tabela de promoções não encontrada — exibindo página sem promoções.')

    context = {'promocoes': promos}
    return render(request, 'inicio/promocoes.html', context)
