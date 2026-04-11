import logging

from django.db import OperationalError, ProgrammingError
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from ..models import (
    AvaliacaoNPS,
    Preco,
    Procedimento,
    Profissional,
    Promocao,
)

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


def equipe(request):
    """Pagina publica com a equipe de profissionais ativos."""
    profissionais = []
    try:
        profissionais = list(
            Profissional.objects.filter(ativo=True).order_by('nome')
        )
    except (OperationalError, ProgrammingError):
        logger.warning('Tabela de profissionais nao encontrada.')

    return render(request, 'publico/equipe.html', {'profissionais': profissionais})


def especialidades(request):
    """Pagina publica com procedimentos agrupados por categoria em tabs."""
    categorias = [
        ('FACIAL', 'Tratamentos Faciais', 'bi-sparkles'),
        ('CORPORAL', 'Tratamentos Corporais', 'bi-heart-pulse'),
        ('CAPILAR', 'Tratamentos Capilares', 'bi-droplet-half'),
        ('OUTRO', 'Outros Servicos', 'bi-stars'),
    ]

    grupos = []
    try:
        procedimentos = list(Procedimento.objects.filter(ativo=True).order_by('nome'))

        preco_map = {}
        proc_ids = [p.pk for p in procedimentos]
        if proc_ids:
            precos = Preco.objects.filter(
                procedimento_id__in=proc_ids
            ).order_by('profissional')
            for preco in precos:
                if preco.procedimento_id not in preco_map:
                    preco_map[preco.procedimento_id] = float(preco.valor)

        for cat_key, cat_label, cat_icon in categorias:
            itens = [
                {
                    'id': p.pk,
                    'nome': p.nome,
                    'descricao': p.descricao or '',
                    'duracao_minutos': p.duracao_minutos,
                    'preco': preco_map.get(p.pk, 0),
                }
                for p in procedimentos
                if p.categoria == cat_key
            ]
            if itens:
                grupos.append({
                    'key': cat_key,
                    'label': cat_label,
                    'icon': cat_icon,
                    'itens': itens,
                })
    except (OperationalError, ProgrammingError):
        logger.warning('Tabelas de procedimentos nao encontradas.')

    return render(request, 'publico/especialidades.html', {'grupos': grupos})


def depoimentos(request):
    """Pagina publica com avaliacoes NPS (promotores) em carousel."""
    avaliacoes = []
    try:
        avaliacoes = list(
            AvaliacaoNPS.objects
            .filter(nota__gte=9)
            .filter(~Q(comentario__isnull=True))
            .exclude(comentario__exact='')
            .select_related('atendimento__cliente')
            .order_by('-criado_em')[:30]
        )
    except (OperationalError, ProgrammingError):
        logger.warning('Tabela AvaliacaoNPS nao encontrada.')

    return render(request, 'publico/depoimentos.html', {'avaliacoes': avaliacoes})


def galeria(request):
    """Pagina publica com galeria estatica da clinica (GLightbox)."""
    # Imagens usam o template_img ja presente em static/assets/
    fotos = [
        {'src': 'assets/template_img/health/facilities-6.webp', 'titulo': 'Recepcao'},
        {'src': 'assets/template_img/health/facilities-9.webp', 'titulo': 'Sala de Espera'},
        {'src': 'assets/template_img/health/dermatology-1.webp', 'titulo': 'Sala de Tratamento Facial'},
        {'src': 'assets/template_img/health/dermatology-4.webp', 'titulo': 'Procedimento Facial'},
        {'src': 'assets/template_img/health/maternal-2.webp', 'titulo': 'Ambiente Relaxante'},
        {'src': 'assets/template_img/health/consultation-4.webp', 'titulo': 'Sala de Avaliacao'},
        {'src': 'assets/template_img/health/laboratory-3.webp', 'titulo': 'Equipamentos'},
        {'src': 'assets/template_img/health/vaccination-3.webp', 'titulo': 'Atendimento Personalizado'},
        {'src': 'assets/template_img/health/cardiology-1.webp', 'titulo': 'Tecnologia Avancada'},
    ]
    return render(request, 'publico/galeria.html', {'fotos': fotos})


def servico_detalhe(request, slug):
    """Pagina de detalhe individual de um procedimento pelo slug."""
    procedimento = get_object_or_404(Procedimento, slug=slug, ativo=True)

    # Buscar preco generico (profissional=NULL) ou primeiro disponivel
    preco_obj = (
        Preco.objects.filter(procedimento=procedimento, profissional__isnull=True).first()
        or Preco.objects.filter(procedimento=procedimento).first()
    )
    preco = float(preco_obj.valor) if preco_obj else None

    # Profissionais que executam esse procedimento
    profissionais = list(
        procedimento.profissionais.filter(ativo=True).order_by('nome')
    )

    # Outros procedimentos da mesma categoria (sugestao)
    relacionados = list(
        Procedimento.objects
        .filter(ativo=True, categoria=procedimento.categoria)
        .exclude(pk=procedimento.pk)
        .order_by('nome')[:4]
    )

    context = {
        'procedimento': procedimento,
        'preco': preco,
        'profissionais': profissionais,
        'relacionados': relacionados,
    }
    return render(request, 'publico/servico_detalhe.html', context)
