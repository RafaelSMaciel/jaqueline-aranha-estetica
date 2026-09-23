"""
Views de notificacao — confirmacao/cancelamento via link (lembrete WhatsApp)
e historico no painel.
"""
import logging

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from ..decorators import staff_required
from ..models import Atendimento, Notificacao
from ..utils.audit import registrar_log

logger = logging.getLogger(__name__)

# Unicos tokens que carregam link /confirmar/ (lembrete WhatsApp). Token de
# termo (canal EMAIL), NPS e cancelamento NAO valem aqui.
TIPOS_LINK_CONFIRMACAO = ('LEMBRETE', 'LEMBRETE_2H', 'CONFIRMACAO')
STATUS_ALTERAVEIS_PELO_LINK = ('AGENDADO', 'CONFIRMADO')
# "Sem resposta" so faz sentido p/ o que pede resposta (link de confirmacao
# enviado): termo, NPS, cancelamento e falhas de envio nao contam.
AGUARDA_RESPOSTA = Q(
    tipo__in=TIPOS_LINK_CONFIRMACAO, canal='WHATSAPP', status='ENVIADO', resposta__isnull=True,
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
    Recebe token unico enviado no lembrete via WhatsApp.

    Transicoes pela FSM do Atendimento (auditoria + eventos); status terminal,
    PENDENTE ou horario passado ficam "nao editavel".
    """
    notif = get_object_or_404(
        Notificacao.objects.select_related(
            'atendimento__procedimento', 'atendimento__profissional',
        ),
        token=token, canal='WHATSAPP', tipo__in=TIPOS_LINK_CONFIRMACAO,
    )
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
                            atendimento.cancelar(motivo='Cancelado pelo cliente via link')
                            resposta = 'CANCELOU'
                    except Atendimento.TransicaoInvalida:
                        resposta = None
                    if resposta:
                        notif.resposta = resposta
                        notif.respondido_em = timezone.now()
                        notif.save(update_fields=['resposta', 'respondido_em'])
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


@staff_required
def painel_notificacoes(request):
    """Painel de notificacoes do admin — historico de envios e respostas."""
    tipo_filter = request.GET.get('tipo', 'all')
    status_filter = request.GET.get('status', 'all')

    # enviado_em NULL (nunca enviada) no fim tambem no Postgres (DESC = NULLS FIRST)
    notifs = Notificacao.objects.select_related(
        'atendimento__cliente', 'atendimento__profissional', 'atendimento__procedimento'
    ).order_by(F('enviado_em').desc(nulls_last=True), '-criado_em')

    if tipo_filter != 'all':
        notifs = notifs.filter(tipo=tipo_filter)
    if status_filter != 'all':
        if status_filter == 'respondido':
            notifs = notifs.exclude(resposta__isnull=True).exclude(resposta='')
        elif status_filter == 'pendente':
            notifs = notifs.filter(AGUARDA_RESPOSTA)

    # Stats — agregacao condicional unica.
    stats = notifs.aggregate(
        total=Count('id'),
        enviadas=Count('id', filter=Q(status='ENVIADO')),
        falhas=Count('id', filter=Q(status='FALHOU')),
        confirmados=Count('id', filter=Q(resposta='CONFIRMOU')),
        cancelados=Count('id', filter=Q(resposta='CANCELOU')),
        sem_resposta=Count('id', filter=AGUARDA_RESPOSTA),
    )

    paginator = Paginator(notifs, 50)
    page = request.GET.get('page', 1)
    notifs_page = paginator.get_page(page)

    context = {
        'notificacoes': notifs_page,
        'total': stats['total'],
        'enviadas': stats['enviadas'],
        'falhas': stats['falhas'],
        'confirmados': stats['confirmados'],
        'cancelados': stats['cancelados'],
        'sem_resposta': stats['sem_resposta'],
        'tipos': Notificacao.TIPO_CHOICES,
        'tipo_filter': tipo_filter,
        'status_filter': status_filter,
    }
    return render(request, 'painel/notificacoes.html', context)
