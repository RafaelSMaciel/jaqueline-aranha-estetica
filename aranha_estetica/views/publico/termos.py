"""Termos de consentimento: versoes no painel, link de assinatura e a pagina publica de aceite."""
import logging

from django.db import transaction
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from ...models import AceiteTermo, Atendimento, Notificacao
from ...services import links
from ...services.termos import aceita_assinatura, termos_pendentes
from ...utils.auditoria import registrar_log

logger = logging.getLogger(__name__)


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def termo_assinatura(request, token):
    """Pagina publica p/ a cliente aceitar os termos pendentes do atendimento.

    - So LinkAcesso TERMO: link de NPS/lembrete WhatsApp nao abre esta pagina
      (revogado ou desconhecido -> 404).
    - Vale ate o fim do atendimento (regra de dominio aceita_assinatura sobre o
      horario ATUAL — atendimento movido no calendario continua valendo);
      cancelado/realizado/faltou/reagendado ou ja encerrado -> 410 sem gravar nada.
    - POST exige o aceite de TODOS os termos pendentes (validado no servidor;
      o `required` do checkbox e so conveniencia) e grava pela
      AceiteTermo.registrar (IP, user-agent, SHA-256 do texto) + auditoria.
    """
    link = links.resolver(
        links.TERMO, token, aceitar_expirado=True,
        select_related=(
            'notificacao__atendimento__cliente', 'notificacao__atendimento__procedimento',
            'notificacao__atendimento__profissional',
        ),
    )
    if link is None:
        raise Http404('Link inválido.')
    notif = link.notificacao
    atendimento = notif.atendimento
    cliente = atendimento.cliente

    if not aceita_assinatura(atendimento):
        ativo = atendimento.status in Atendimento.STATUS_ATIVOS
        return render(request, 'publico/termo_obrigado.html', {
            'cliente': cliente,
            # ativo mas ja encerrado = link vencido; senao o atendimento nao existe mais
            'expirado': ativo,
            'indisponivel': not ativo,
        }, status=410)

    pendentes = termos_pendentes(cliente, atendimento.procedimento)
    context = {
        'atendimento': atendimento,
        'cliente': cliente,
        'termos_pendentes': pendentes,
    }

    if request.method == 'POST':
        # Versao publicada entre o GET e o POST tambem entra em `pendentes`
        # e, sem o aceite dela no POST, cai aqui (nada e gravado).
        faltando = [t for t in pendentes if request.POST.get(f'aceite_{t.pk}') != '1']
        if faltando:
            context['erro'] = 'Para concluir, marque "Li e concordo" em todos os termos abaixo.'
            return render(request, 'publico/termo_assinatura.html', context, status=400)

        if pendentes:
            with transaction.atomic():
                for termo in pendentes:
                    AceiteTermo.registrar(cliente, termo, request=request, atendimento=atendimento)
                Notificacao.objects.filter(pk=notif.pk).update(respondido_em=timezone.now())
                links.marcar_usado(link)
            registrar_log(
                None, 'Cliente aceitou termos', 'aceite_termo', cliente.pk,
                {'versoes': [t.pk for t in pendentes], 'atendimento': atendimento.pk,
                 'notificacao': notif.pk},
                request=request,
            )
        return render(request, 'publico/termo_obrigado.html', {'cliente': cliente})

    return render(request, 'publico/termo_assinatura.html', context)
