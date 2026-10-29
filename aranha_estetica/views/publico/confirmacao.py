"""Link publico do lembrete: a cliente confirma ou cancela o agendamento (LinkAcesso CONFIRMAR_PRESENCA)."""
import logging

from django.db import transaction
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from ...models import Atendimento, Notificacao
from ...excecoes import RegraDeNegocio
from ...models.notificacoes import STATUS_ALTERAVEIS_PELO_LINK
from ...services import agenda, links
from ...utils.auditoria import registrar_log

logger = logging.getLogger(__name__)


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
    foi_tardio = False

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
                            resposta = Notificacao.Resposta.CONFIRMOU
                        else:
                            # decisao 10: com menos de 24 h e cancelamento tardio (conta como falta)
                            atendimento, foi_tardio = agenda.cancelar_pela_cliente(
                                atendimento.pk, origem='link', so_aprovados=True,
                            )
                            resposta = Notificacao.Resposta.CANCELOU
                    except RegraDeNegocio:   # inclui Atendimento.TransicaoInvalida
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
        # aviso antes de cancelar: com menos de 24 h o cancelamento conta como falta
        'cancelamento_tardio': not ja_respondeu and agenda.cancelamento_tardio(atendimento),
        'foi_tardio': foi_tardio,
    }
    return render(request, 'agendamento/confirmar_presenca.html', context)
