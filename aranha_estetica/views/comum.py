"""Helpers de view compartilhados entre areas (fora do contrato "Areas de views independentes" do
pyproject.toml): o que mais de uma area renderiza igual. Regra de negocio nao mora aqui (services/)."""
from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse

from ..services import links
from ..utils.auditoria import registrar_log


def responder_link_ics(request, profissional, *, template, voltar, voltar_rotulo):
    """Gera um link novo do feed ICS (revoga o anterior) e RENDERIZA a URL — painel (dona, por
    profissional) e portal do profissional (o proprio).

    Renderiza em vez de redirecionar: o token nao pode ir p/ URL de redirect
    nem p/ log, e so existe nesta resposta (o banco guarda o hash; a pagina e
    no-store como todo /painel/ e /profissional/).
    """
    if not profissional.slug:
        profissional.save()  # Profissional.save() gera o slug
    token = links.gerar_link_feed_ics(profissional)
    url = request.build_absolute_uri(
        reverse('aranha:ics_feed_profissional', args=[profissional.slug])
    ) + '?' + urlencode({'token': token})
    registrar_log(
        request.user, 'Gerou novo link do feed ICS (o anterior foi revogado)', 'profissional',
        profissional.pk, request=request,
    )
    return render(request, template, {
        'profissional': profissional, 'link': url, 'voltar': voltar, 'voltar_rotulo': voltar_rotulo,
    })
