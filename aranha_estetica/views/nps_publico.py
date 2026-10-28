"""Pesquisa NPS pelo link da mensagem (/nps/<token>/) — pagina publica."""
import logging
import re

from django.http import Http404
from django.shortcuts import render
from django_ratelimit.decorators import ratelimit

from ..models import AvaliacaoNPS
from ..services import links

logger = logging.getLogger(__name__)


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def nps_web(request, token):
    """Pagina publica para coletar NPS via link (LinkAcesso NPS, 7 dias)."""
    link = links.resolver(
        links.NPS, token, aceitar_expirado=True,
        select_related=('notificacao__atendimento__cliente',),
    )
    if link is None:
        raise Http404('Link inválido.')
    atendimento = link.notificacao.atendimento

    if link.expirado:
        return render(request, 'publico/nps_obrigado.html', {
            'expirado': True,
            'cliente': atendimento.cliente,
        }, status=410)

    ja_respondeu = AvaliacaoNPS.objects.filter(atendimento=atendimento).exists()

    erro = None
    if request.method == 'POST' and not ja_respondeu:
        nota_str = request.POST.get('nota', '').strip()
        comentario = request.POST.get('comentario', '').strip()

        # fullmatch: isdigit() aceitava '²' (int() -> 500) e '07'
        if re.fullmatch(r'10|[0-9]', nota_str):
            nota = int(nota_str)

            AvaliacaoNPS.objects.create(
                atendimento=atendimento,
                nota=nota,
                comentario=comentario,
                # opt-in explicito p/ virar depoimento publico (moderado no painel)
                autoriza_publicacao=request.POST.get('autoriza_publicacao') in ('1', 'on', 'true'),
            )
            links.marcar_usado(link)
            return render(request, 'publico/nps_obrigado.html', {
                'nota': nota,
                'cliente': atendimento.cliente,
            })
        erro = 'Escolha uma nota de 0 a 10.'

    context = {
        'atendimento': atendimento,
        'ja_respondeu': ja_respondeu,
        'notas_range': range(11),  # 0..10 (escala NPS real)
        'erro': erro,
    }
    return render(request, 'publico/nps_web.html', context)
