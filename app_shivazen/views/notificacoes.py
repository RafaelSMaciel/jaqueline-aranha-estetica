"""
Views de notificacao — confirmacao/cancelamento via link e painel admin.
"""
import logging

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ..decorators import staff_required
from ..models import Atendimento, Notificacao
from ..utils.audit import registrar_log
from ..utils.whatsapp import enviar_cancelamento_cliente, enviar_confirmacao_admin

logger = logging.getLogger(__name__)


def confirmar_presenca(request, token):
    """
    Link publico (sem login) para cliente confirmar/cancelar agendamento.
    Recebe token unico enviado via WhatsApp.
    """
    notif = get_object_or_404(Notificacao, token=token)
    atendimento = notif.atendimento
    acao = request.GET.get('acao', '')

    # Verifica se ja respondeu
    ja_respondeu = notif.resposta_cliente is not None

    if request.method == 'POST' and not ja_respondeu:
        acao = request.POST.get('acao', '')

        if acao == 'confirmar':
            atendimento.status = 'CONFIRMADO'
            atendimento.save()
            notif.resposta_cliente = 'CONFIRMOU'
            notif.respondido_em = timezone.now()
            notif.save()
            enviar_confirmacao_admin(atendimento, 'CONFIRMOU')
            logger.info(f'Cliente {atendimento.cliente.nome_completo} CONFIRMOU agendamento #{atendimento.pk}')

        elif acao == 'cancelar':
            atendimento.status = 'CANCELADO'
            atendimento.save()
            notif.resposta_cliente = 'CANCELOU'
            notif.respondido_em = timezone.now()
            notif.save()
            enviar_confirmacao_admin(atendimento, 'CANCELOU')
            logger.info(f'Cliente {atendimento.cliente.nome_completo} CANCELOU agendamento #{atendimento.pk}')

    context = {
        'atendimento': atendimento,
        'notif': notif,
        'acao': acao,
        'ja_respondeu': ja_respondeu or request.method == 'POST',
        'resposta': notif.resposta_cliente,
    }
    return render(request, 'agenda/confirmar_presenca.html', context)


@staff_required
def painel_notificacoes(request):
    """Painel de notificacoes do admin — historico de envios e respostas."""
    tipo_filter = request.GET.get('tipo', 'all')
    status_filter = request.GET.get('status', 'all')

    notifs = Notificacao.objects.select_related(
        'atendimento__cliente', 'atendimento__profissional', 'atendimento__procedimento'
    ).order_by('-enviado_em')

    if tipo_filter != 'all':
        notifs = notifs.filter(tipo=tipo_filter)
    if status_filter != 'all':
        if status_filter == 'respondido':
            notifs = notifs.exclude(resposta_cliente__isnull=True).exclude(resposta_cliente='')
        elif status_filter == 'pendente':
            notifs = notifs.filter(resposta_cliente__isnull=True)

    # Stats
    total = notifs.count()
    confirmados = notifs.filter(resposta_cliente='CONFIRMOU').count()
    cancelados = notifs.filter(resposta_cliente='CANCELOU').count()
    sem_resposta = notifs.filter(resposta_cliente__isnull=True).count()

    paginator = Paginator(notifs, 50)
    page = request.GET.get('page', 1)
    notifs_page = paginator.get_page(page)

    context = {
        'notificacoes': notifs_page,
        'total': total,
        'confirmados': confirmados,
        'cancelados': cancelados,
        'sem_resposta': sem_resposta,
        'tipo_filter': tipo_filter,
        'status_filter': status_filter,
    }
    return render(request, 'painel/painel_notificacoes.html', context)


@staff_required
def admin_cancelar_agendamento(request):
    """Admin cancela agendamento e notifica cliente via WhatsApp."""
    if request.method == 'POST':
        atendimento_id = request.POST.get('atendimento_id')
        atendimento = get_object_or_404(Atendimento, pk=atendimento_id)

        status_anterior = atendimento.status
        atendimento.status = 'CANCELADO'
        atendimento.save()

        # Notifica cliente via WhatsApp
        enviar_cancelamento_cliente(atendimento)

        registrar_log(
            request.user,
            f'Cancelou agendamento e notificou cliente',
            'atendimento',
            atendimento_id,
            {'status_anterior': status_anterior, 'cliente': atendimento.cliente.nome_completo}
        )
        messages.success(request, f'Agendamento cancelado. Cliente {atendimento.cliente.nome_completo} notificado via WhatsApp.')

    return redirect('shivazen:painel_agendamentos')
