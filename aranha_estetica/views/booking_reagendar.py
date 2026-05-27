"""Reagendamento publico via token + listagem 'Meus Agendamentos'."""
import json
import logging
import os
from datetime import datetime, timedelta

from django.contrib import messages
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from ..models import Atendimento, Cliente, Feriado, Profissional
from ..utils.captcha import turnstile_enabled, turnstile_site_key

logger = logging.getLogger(__name__)

WHATSAPP_NUMERO = os.environ.get('WHATSAPP_NUMERO', '5517999990000')

JANELA_MINIMA_REAGENDAMENTO = timedelta(hours=24)


def meus_agendamentos(request):
    """Listagem autenticada via OTP por email."""
    email = request.session.get('meus_agendamentos_email')
    if not email:
        return render(request, 'agenda/meus_agendamentos.html', {
            'step': '1',
            'turnstile_site_key': turnstile_site_key(),
            'turnstile_enabled': turnstile_enabled(),
        })

    clientes = Cliente.objects.filter(email__iexact=email, ativo=True)
    agendamentos = Atendimento.objects.filter(
        cliente__in=clientes
    ).select_related('profissional', 'procedimento').order_by('-data_hora_inicio')

    agendamentos_futuros = agendamentos.filter(
        data_hora_inicio__gte=timezone.now(),
        status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO']
    )
    agendamentos_passados = agendamentos.filter(
        Q(data_hora_inicio__lt=timezone.now())
        | Q(status__in=['REALIZADO', 'CANCELADO', 'FALTOU', 'REAGENDADO'])
    )

    return render(request, 'agenda/meus_agendamentos.html', {
        'step': '3',
        'email': email,
        'agendamentos_futuros': agendamentos_futuros[:20],
        'agendamentos_passados': agendamentos_passados.distinct()[:20],
        'whatsapp_numero': WHATSAPP_NUMERO,
    })


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def reagendar_agendamento(request, token):
    """Fluxo publico de reagendamento via token seguro."""
    try:
        atendimento = Atendimento.objects.select_related(
            'cliente', 'profissional', 'procedimento'
        ).get(token_cancelamento=token)
    except Atendimento.DoesNotExist:
        messages.error(request, 'Agendamento nao encontrado.')
        return redirect('aranha:agendamento_publico')

    agora = timezone.now()
    if atendimento.data_hora_inicio <= agora:
        messages.error(request, 'Nao e possivel reagendar atendimentos passados.')
        return redirect('aranha:meus_agendamentos')

    if atendimento.status in ['CANCELADO', 'REALIZADO', 'FALTOU', 'REAGENDADO']:
        messages.error(
            request,
            f'Este atendimento esta {atendimento.get_status_display().lower()} e nao pode ser reagendado.'
        )
        return redirect('aranha:meus_agendamentos')

    if (atendimento.data_hora_inicio - agora) < JANELA_MINIMA_REAGENDAMENTO:
        messages.error(
            request,
            'Reagendamento requer no minimo 24h de antecedencia. '
            'Entre em contato pelo WhatsApp para ajustes de ultima hora.'
        )
        return redirect('aranha:meus_agendamentos')

    if request.method == 'GET':
        procedimentos_json = json.dumps([{
            'id': atendimento.procedimento.pk,
            'nome': atendimento.procedimento.nome,
            'duracao_minutos': atendimento.procedimento.duracao_minutos,
        }])
        context = {
            'atendimento': atendimento,
            'procedimentos_json': procedimentos_json,
            'whatsapp_numero': WHATSAPP_NUMERO,
        }
        return render(request, 'agenda/reagendar.html', context)

    datetime_str = request.POST.get('datetime', '').strip()
    profissional_id = request.POST.get('profissional') or atendimento.profissional_id

    if not datetime_str:
        messages.error(request, 'Selecione uma nova data e horario.')
        return redirect('aranha:reagendar_agendamento', token=token)

    try:
        nova_data = datetime.fromisoformat(datetime_str)
    except ValueError:
        messages.error(request, 'Data/horario invalidos.')
        return redirect('aranha:reagendar_agendamento', token=token)

    if nova_data <= agora:
        messages.error(request, 'Escolha uma data futura.')
        return redirect('aranha:reagendar_agendamento', token=token)

    try:
        profissional = Profissional.objects.get(pk=profissional_id, ativo=True)
    except Profissional.DoesNotExist:
        messages.error(request, 'Profissional indisponivel.')
        return redirect('aranha:reagendar_agendamento', token=token)

    nova_data_fim = nova_data + timedelta(minutes=atendimento.procedimento.duracao_minutos)

    if Feriado.objects.filter(data=nova_data.date(), bloqueia_agendamento=True).exists():
        messages.error(request, 'A data escolhida e um feriado/recesso. Escolha outro dia.')
        return redirect('aranha:reagendar_agendamento', token=token)

    try:
        with transaction.atomic():
            antigo = Atendimento.objects.select_for_update().get(pk=atendimento.pk)

            if antigo.status in ['CANCELADO', 'REALIZADO', 'FALTOU', 'REAGENDADO']:
                messages.error(
                    request,
                    'Este atendimento ja foi processado em outra operacao.'
                )
                return redirect('aranha:meus_agendamentos')

            conflito = Atendimento.objects.select_for_update().filter(
                profissional=profissional,
                data_hora_inicio__lt=nova_data_fim,
                data_hora_fim__gt=nova_data,
                status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO']
            ).exclude(pk=antigo.pk).first() is not None

            if conflito:
                messages.error(request, 'Este horario acabou de ser reservado. Escolha outro.')
                return redirect('aranha:reagendar_agendamento', token=token)

            novo = Atendimento.objects.create(
                cliente=antigo.cliente,
                profissional=profissional,
                procedimento=antigo.procedimento,
                promocao=antigo.promocao,
                reagendado_de=antigo,
                data_hora_inicio=nova_data,
                data_hora_fim=nova_data_fim,
                valor_cobrado=antigo.valor_cobrado,
                valor_original=antigo.valor_original,
                descricao_preco=antigo.descricao_preco,
                status='AGENDADO',
            )

            antigo.status = 'REAGENDADO'
            antigo.save()

        data_fmt = nova_data.strftime('%d/%m/%Y as %H:%M')
        request.session['agendamento_sucesso'] = {
            'nome': antigo.cliente.nome_completo,
            'procedimento': antigo.procedimento.nome,
            'profissional': profissional.nome,
            'data_hora': data_fmt,
            'valor': f'R$ {float(novo.valor_cobrado):.2f}' if novo.valor_cobrado else 'A consultar',
            'pendente': True,
            'reagendamento': True,
        }
        return redirect('aranha:agendamento_sucesso')

    except (DatabaseError, IntegrityError) as exc:
        logger.error(
            'reagendamento_falha',
            extra={'atendimento_id': atendimento.pk, 'erro': str(exc)},
            exc_info=True,
        )
        messages.error(request, 'Ocorreu um erro ao reagendar. Tente novamente.')
        return redirect('aranha:reagendar_agendamento', token=token)
