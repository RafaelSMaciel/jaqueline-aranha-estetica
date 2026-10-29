"""Views publicas por profissional: pagina agendar/<slug>/ e o widget embutivel.

O feed ICS da agenda do profissional fica em views/infra/feed_ics.py.
"""
from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_exempt

from ...models import Profissional
from ...services.disponibilidade import procedimentos_agendaveis
from ...services.precos import precos_de_vitrine


def agendar_por_profissional(request, slug):
    """Atalho: redireciona p/ booking publico c/ profissional fixado via query."""
    prof = get_object_or_404(Profissional, slug=slug, ativo=True)
    query = {'profissional': prof.pk}
    proc = request.GET.get('procedimento', '')
    if proc.isdigit():
        query['procedimento'] = proc
    return redirect(f"{reverse('aranha:agendamento_publico')}?{urlencode(query)}")


@xframe_options_exempt
def embed_agendar(request):
    """Widget standalone p/ iframe (Linktree, Instagram bio, site externo).

    Lista procedimentos ativos com link p/ booking publico c/ ?procedimento=X.
    Permite iframe (X-Frame-Options OFF).
    """
    # "A partir de" = a vitrine do wizard (services/precos, promocao vigente hoje inclusa)
    procs = list(procedimentos_agendaveis())
    vitrine = precos_de_vitrine(procs)
    procedimentos = []
    for p in procs:
        preco = vitrine.get(p.pk)
        procedimentos.append({
            'id': p.pk,
            'nome': p.nome,
            'duracao_minutos': p.duracao_minutos,
            'preco': float(preco.final) if preco else 0,
            'promocao': preco.promocao.nome if preco and preco.promocao else '',
        })

    # URL absoluta publica (iframe em outro dominio precisa do host canonico)
    booking_url = f"{settings.SITE_URL}{reverse('aranha:agendamento_publico')}"
    context = {
        'procedimentos': procedimentos,
        'booking_url': booking_url,
    }
    return render(request, 'agendamento/embed.html', context)
