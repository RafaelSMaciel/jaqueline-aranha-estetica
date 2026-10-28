"""
Views de notificacao — confirmacao/cancelamento via link (lembrete WhatsApp)
e historico no painel.
"""
import logging

from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from ...models import Atendimento, Notificacao
from ...models.agendamentos import StatusAtendimento
from ...services import links
from ...utils.auditoria import registrar_log

logger = logging.getLogger(__name__)

# Notificacoes que pedem resposta pelo link /confirmar/ (LinkAcesso
# CONFIRMAR_PRESENCA, emitido no lembrete WhatsApp). Link de termo, NPS e
# reagendar NAO valem aqui (finalidade diferente -> 404).
TIPOS_LINK_CONFIRMACAO = ('LEMBRETE', 'LEMBRETE_2H', 'CONFIRMACAO')
STATUS_ALTERAVEIS_PELO_LINK = (StatusAtendimento.AGENDADO, StatusAtendimento.CONFIRMADO)
# "Sem resposta" so faz sentido p/ o que pede resposta (link de confirmacao
# enviado): termo, NPS, cancelamento e falhas de envio nao contam.
AGUARDA_RESPOSTA = Q(
    tipo__in=TIPOS_LINK_CONFIRMACAO, canal=Notificacao.Canal.WHATSAPP, status=Notificacao.Status.ENVIADO, resposta__isnull=True,
)


def _alteravel_pelo_link(atendimento) -> bool:
    """Cliente so mexe em agendamento futuro ja aprovado (PENDENTE segue p/ a profissional)."""
    return (
        atendimento.status in STATUS_ALTERAVEIS_PELO_LINK
        and atendimento.data_hora_inicio > timezone.now()
    )


@require_http_methods(['GET', 'POST'])
@ratelimit(key='ip', rate='30/m', block=True)
def confirmar_presenca(request, token):
    """
    Link publico (sem login) para cliente confirmar/cancelar agendamento.
    Recebe o token do LinkAcesso CONFIRMAR_PRESENCA enviado no lembrete WhatsApp.

    Transicoes pela FSM do Atendimento (auditoria + eventos); status terminal,
    PENDENTE ou horario passado ficam "nao editavel" (o link vencido abre so
    para leitura: a regra de horario e a do atendimento atual).
    """
    link = links.resolver(
        links.CONFIRMAR_PRESENCA, token, aceitar_expirado=True,
        select_related=('notificacao__atendimento__procedimento', 'notificacao__atendimento__profissional'),
    )
    if link is None:
        raise Http404('Link inválido.')
    notif = link.notificacao
    acao = request.GET.get('acao', '')
    if acao not in ('confirmar', 'cancelar'):
        acao = ''
    atendimento = notif.atendimento

    if request.method == 'POST' and notif.resposta is None:
        acao_post = request.POST.get('acao', '')
        if acao_post in ('confirmar', 'cancelar'):
            acao = acao_post
            # Trava notif + atendimento: duplo-clique/retry nao transiciona 2x.
            with transaction.atomic():
                notif = Notificacao.objects.select_for_update().get(pk=notif.pk)
                atendimento = (
                    Atendimento.objects.select_for_update()
                    .select_related('procedimento', 'profissional')
                    .get(pk=notif.atendimento_id)
                )
                if notif.resposta is None and _alteravel_pelo_link(atendimento):
                    try:
                        if acao == 'confirmar':
                            if atendimento.status == Atendimento.STATUS_AGENDADO:
                                atendimento.confirmar()
                            resposta = 'CONFIRMOU'
                        else:
                            atendimento.cancelar(motivo='Cancelado pelo cliente via link', pela_cliente=True)
                            resposta = 'CANCELOU'
                    except Atendimento.TransicaoInvalida:
                        resposta = None
                    if resposta:
                        notif.resposta = resposta
                        notif.respondido_em = timezone.now()
                        notif.save(update_fields=['resposta', 'respondido_em'])
                        links.marcar_usado(link)
                        logger.info('confirmar_presenca_%s', resposta.lower(),
                                    extra={'atendimento_id': atendimento.pk})
                        # IP de origem da resposta (a FSM audita so a transicao)
                        registrar_log(
                            None, f'Cliente respondeu pelo link: {resposta}',
                            'atendimento', atendimento.pk,
                            detalhes={'notificacao_id': notif.pk}, request=request,
                        )

    ja_respondeu = notif.resposta is not None
    context = {
        'atendimento': atendimento,
        'notif': notif,
        'acao': acao,
        'ja_respondeu': ja_respondeu,
        'resposta': notif.resposta,
        'nao_editavel': not ja_respondeu and not _alteravel_pelo_link(atendimento),
    }
    return render(request, 'agenda/confirmar_presenca.html', context)
