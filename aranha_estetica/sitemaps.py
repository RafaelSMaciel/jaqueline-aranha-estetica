"""Sitemaps para SEO — paginas publicas e detalhe de procedimentos."""
from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .models import Procedimento


class StaticViewSitemap(Sitemap):
    """Paginas publicas estaticas."""
    priority = 0.8
    changefreq = 'weekly'

    def items(self):
        return [
            'aranha:inicio',
            'aranha:quem_somos',
            'aranha:agenda_contato',
            'aranha:promocoes',
            'aranha:equipe',
            'aranha:especialidades',
            # depoimentos: so exibe NPS com opt-in do cliente + aprovacao da clinica
            'aranha:depoimentos',
            'aranha:galeria',
            'aranha:agendamento_publico',
            'aranha:lista_espera_publica',
            'aranha:servicos_faciais',
            'aranha:servicos_corporais',
            'aranha:termos_uso',
            'aranha:politica_privacidade',
        ]

    def location(self, item):
        return reverse(item)


class ProcedimentoSitemap(Sitemap):
    """Paginas de detalhe de procedimentos (com slug)."""
    priority = 0.7
    changefreq = 'monthly'

    def items(self):
        # order_by explicito: paginacao do sitemap exige ordem estavel
        return (
            Procedimento.objects.filter(ativo=True, slug__isnull=False)
            .exclude(slug='')
            .order_by('nome', 'pk')
        )

    def location(self, obj):
        return reverse('aranha:servico_detalhe', kwargs={'slug': obj.slug})
