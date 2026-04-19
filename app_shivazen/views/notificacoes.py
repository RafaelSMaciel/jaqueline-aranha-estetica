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
from ..utils.email import enviar_cancelamento_email

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
            logger.info(f'Cliente {atendimento.cliente.nome_completo} CONFIRMOU agendamento #{atendimento.pk}')

        elif acao == 'cancelar':
            atendimento.status = 'CANCELADO'
            atendimento.save()
            notif.resposta_cliente = 'CANCELOU'
            notif.respondido_em = timezone.now()
            notif.save()
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
    return render(request, 'painel/notificacoes.html', context)


@staff_required
def admin_cancelar_agendamento(request):
    """Admin cancela agendamento e notifica cliente via EMAIL."""
    if request.method == 'POST':
        atendimento_id = request.POST.get('atendimento_id')
        atendimento = get_object_or_404(Atendimento, pk=atendimento_id)

        status_anterior = atendimento.status
        atendimento.status = 'CANCELADO'
        atendimento.save()

        cliente = atendimento.cliente
        canal = 'nenhum canal'
        if cliente.email:
            from ..tasks import send_email_async
            dados_cancel = {
                'nome': cliente.nome_completo,
                'procedimento': atendimento.procedimento.nome,
                'data_hora': atendimento.data_hora_inicio.strftime('%d/%m/%Y as %H:%M'),
                'profissional': atendimento.profissional.nome,
            }
            try:
                send_email_async.delay('enviar_cancelamento_email', cliente.email, dados_cancel)
            except Exception as e:
                logger.warning('[EMAIL] Celery indisponivel, fallback sync: %s', e)
                enviar_cancelamento_email(cliente.email, dados_cancel)
            Notificacao.objects.create(
                atendimento=atendimento,
                tipo='CANCELAMENTO',
                canal='EMAIL',
                status_envio='ENVIADO',
                token=__import__('secrets').token_urlsafe(32),
                enviado_em=timezone.now(),
            )
            canal = 'email'
        else:
            logger.warning(
                'Cancelamento atendimento %s sem email do cliente — sem notificacao enviada',
                atendimento_id,
            )

        registrar_log(
            request.user,
            'Cancelou agendamento e notificou cliente',
            'atendimento',
            atendimento_id,
            {'status_anterior': status_anterior, 'cliente': cliente.nome_completo, 'canal': canal}
        )
        messages.success(
            request,
            f'Agendamento cancelado. Cliente {cliente.nome_completo} notificado via {canal}.'
        )

    return redirect('shivazen:painel_agendamentos')
