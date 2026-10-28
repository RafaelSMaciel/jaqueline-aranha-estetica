"""
Views de notificacao — confirmacao/cancelamento via link (lembrete WhatsApp)
e historico no painel.
"""
import logging

from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.shortcuts import render

from ...decorators import staff_required
from ...models import Notificacao
from ...models.agendamentos import StatusAtendimento

logger = logging.getLogger(__name__)

# Notificacoes que pedem resposta pelo link /confirmar/ (LinkAcesso
# CONFIRMAR_PRESENCA, emitido no lembrete WhatsApp). Link de termo e NPS NAO
# valem aqui (finalidade diferente -> 404).
TIPOS_LINK_CONFIRMACAO = ('LEMBRETE', 'LEMBRETE_2H', 'CONFIRMACAO')
STATUS_ALTERAVEIS_PELO_LINK = (StatusAtendimento.AGENDADO, StatusAtendimento.CONFIRMADO)
# "Sem resposta" so faz sentido p/ o que pede resposta (link de confirmacao
# enviado): termo, NPS, cancelamento e falhas de envio nao contam.
AGUARDA_RESPOSTA = Q(
    tipo__in=TIPOS_LINK_CONFIRMACAO, canal=Notificacao.Canal.WHATSAPP, status=Notificacao.Status.ENVIADO, resposta__isnull=True,
)


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
        enviadas=Count('id', filter=Q(status=Notificacao.Status.ENVIADO)),
        falhas=Count('id', filter=Q(status=Notificacao.Status.FALHOU)),
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
