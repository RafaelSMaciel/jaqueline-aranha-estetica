"""Pesquisa NPS pelo link da mensagem (/nps/<token>/) — pagina publica.

POST valido -> services/nps.registrar_avaliacao -> redirect p/ /nps/<token>/obrigado/ (PRG: recarregar
a pagina de agradecimento nao reenvia a nota; clique duplo devolve a avaliacao que ja existe).
"""
import logging

from django.http import Http404
from django.shortcuts import redirect, render
from django_ratelimit.decorators import ratelimit

from ...forms.nps import NpsForm
from ...models import AvaliacaoNPS
from ...services import anamnese, links, nps

logger = logging.getLogger(__name__)


def _link_nps(token):
    """LinkAcesso NPS do token (vencido tambem: a pagina diz 'expirado'); 404 se nao existe."""
    link = links.resolver(
        links.NPS, token, aceitar_expirado=True,
        select_related=('notificacao__atendimento__cliente', 'notificacao__atendimento__procedimento',
                        'notificacao__atendimento__profissional'),
    )
    if link is None:
        raise Http404('Link inválido.')
    return link


def _expirado(request):
    """Link vencido (410): quem chega aqui NAO avaliou — sem 'obrigado pela avaliacao'."""
    return render(request, 'publico/nps_obrigado.html', {'expirado': True}, status=410)


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def avaliar(request, token):
    """Formulario da nota 0-10 (LinkAcesso NPS, 7 dias do envio)."""
    link = _link_nps(token)
    atendimento = link.notificacao.atendimento
    if link.expirado:
        return _expirado(request)

    ja_respondeu = AvaliacaoNPS.objects.filter(atendimento=atendimento).exists()
    form = NpsForm()
    if request.method == 'POST':
        if ja_respondeu:
            return redirect('aranha:nps_obrigado', token=token)
        form = NpsForm(request.POST)
        if form.is_valid():
            nps.registrar_avaliacao(
                atendimento, form.cleaned_data['nota'],
                comentario=form.cleaned_data['comentario'],
                autoriza_publicacao=form.cleaned_data['autoriza_publicacao'],
                link=link,
            )
            return redirect('aranha:nps_obrigado', token=token)

    erros = [erro for campo in ('nota', 'comentario') for erro in form.errors.get(campo, [])]
    return render(request, 'publico/nps_web.html', {
        'atendimento': atendimento,
        'ja_respondeu': ja_respondeu,
        'notas_range': range(11),  # 0..10 (escala NPS real)
        'erro': erros[0] if erros else None,
    })


def obrigado(request, token):
    """Agradecimento depois da nota (+ convite da pesquisa pos-atendimento, se houver)."""
    link = _link_nps(token)
    atendimento = link.notificacao.atendimento
    avaliacao = AvaliacaoNPS.objects.filter(atendimento=atendimento).first()
    if avaliacao is None:
        if link.expirado:
            return _expirado(request)
        return redirect('aranha:nps_web', token=token)
    return render(request, 'publico/nps_obrigado.html', {
        'nota': avaliacao.nota,
        # truncatewords:1 deixava 'Maria …' (o filtro marca o corte)
        'primeiro_nome': next(iter((atendimento.cliente.nome or '').split()), ''),
        # pesquisa pos-atendimento (opcional) no mesmo link, sem outra mensagem
        'link_pesquisa': anamnese.convite_pesquisa(atendimento),
    })
