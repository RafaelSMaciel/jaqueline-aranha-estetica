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
            'shivazen:inicio',
            'shivazen:quemsomos',
            'shivazen:agendaContato',
            'shivazen:promocoes',
            'shivazen:equipe',
            'shivazen:especialidades',
            'shivazen:depoimentos',
            'shivazen:galeria',
            'shivazen:agendamento_publico',
            'shivazen:lista_espera_publica',
            'shivazen:servicos_faciais',
            'shivazen:servicos_corporais',
            'shivazen:servicos_produtos',
            'shivazen:termosUso',
            'shivazen:politicaPrivacidade',
        ]

    def location(self, item):
        return reverse(item)


class ProcedimentoSitemap(Sitemap):
    """Paginas de detalhe de procedimentos (com slug)."""
    priority = 0.7
    changefreq = 'monthly'

    def items(self):
        return Procedimento.objects.filter(ativo=True, slug__isnull=False)

    def location(self, obj):
        return reverse('shivazen:servico_detalhe', kwargs={'slug': obj.slug})
