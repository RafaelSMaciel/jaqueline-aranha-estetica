"""Reagendamento publico via token + listagem 'Meus Agendamentos'."""
import logging
from datetime import datetime, timedelta

from django.contrib import messages
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from ..constants import JANELA_MINIMA_REAGENDAMENTO
from ..models import Atendimento, Cliente, Feriado, Profissional, RespostaAnamnese
from ..services.agendamento_service import formatar_brl, formatar_data_hora
from ..services.disponibilidade import profissionais_para, profissional_habilitado, slot_disponivel
from ..services.retorno_service import DURACAO_RETORNO_PADRAO_MINUTOS
from ..utils.audit import registrar_log
from ..utils.captcha import turnstile_enabled, turnstile_site_key
from ..utils.datas import hoje
from ..utils.email import email_configurado
from ..utils.parse import id_int
from ..utils.pii import mask_telefone
from ..utils.sms import sms_disponivel
from .booking_api import agrupar_horarios
from .booking_otp import SESSAO_MEUS_AGENDAMENTOS
from .booking_public import (
    MSG_BLOQUEADO_ONLINE,
    _eh_sobreposicao,
    _enfileirar_email,
    link_revisar_agenda,
)

logger = logging.getLogger(__name__)

STATUS_FINALIZADOS = ('CANCELADO', 'REALIZADO', 'FALTOU', 'REAGENDADO')


def meus_agendamentos(request):
    """Listagem autenticada via OTP (sessao presa ao telefone do cadastro)."""
    telefone = request.session.get(SESSAO_MEUS_AGENDAMENTOS)
    if not telefone:
        return render(request, 'agenda/meus_agendamentos.html', {
            'step': '1',
            'turnstile_site_key': turnstile_site_key(),
            'turnstile_enabled': turnstile_enabled(),
            # Sem SMS o login do portal e impossivel: avisa antes do envio
            'sms_disponivel': sms_disponivel(),
        })

    agora = timezone.now()
    clientes = Cliente.objects.filter(telefone=telefone, ativo=True)
    agendamentos = Atendimento.objects.filter(
        cliente__in=clientes
    ).select_related('profissional', 'procedimento').order_by('-data_hora_inicio')

    agendamentos_futuros = agendamentos.filter(
        data_hora_inicio__gte=agora,
        status__in=Atendimento.STATUS_ATIVOS,
    ).order_by('data_hora_inicio')
    agendamentos_passados = agendamentos.filter(
        Q(data_hora_inicio__lt=agora)
        | Q(status__in=STATUS_FINALIZADOS)
    )

    return render(request, 'agenda/meus_agendamentos.html', {
        'step': '3',
        'telefone_mascarado': mask_telefone(telefone),
        'agendamentos_futuros': agendamentos_futuros[:20],
        'agendamentos_passados': agendamentos_passados.distinct()[:20],
        'status_editaveis': Atendimento.STATUS_ATIVOS,
        'limite_reagendar': agora + JANELA_MINIMA_REAGENDAMENTO,
    })


def _motivo_sem_reagendamento(atendimento, agora):
    """Por que o atendimento nao pode ser reagendado online ('' = pode)."""
    if atendimento.data_hora_inicio <= agora:
        return 'Não é possível reagendar atendimentos passados.'
    if atendimento.status in STATUS_FINALIZADOS:
        return f'Este atendimento está {atendimento.get_status_display().lower()} e não pode ser reagendado.'
    if (atendimento.data_hora_inicio - agora) < JANELA_MINIMA_REAGENDAMENTO:
        return (
            'O reagendamento online requer no mínimo 24h de antecedência. '
            'Fale conosco pelo WhatsApp para ajustes de última hora.'
        )
    if atendimento.cliente.bloqueado_online or not atendimento.cliente.ativo:
        return MSG_BLOQUEADO_ONLINE
    return ''


@ratelimit(key='ip', rate='30/m', method='GET', block=True)
def reagendar_horarios(request, token):
    """AJAX da tela de reagendamento: horarios livres do dia (GET ?data=).

    Mesmo token e mesmas regras do POST de reagendar_agendamento; o proprio
    horario atual NAO conta como ocupado (ignorar_atendimento_id), senao a
    tela esconderia horarios que o servidor aceita (ex.: empurrar 30 min).
    """
    try:
        atendimento = Atendimento.objects.select_related('cliente', 'procedimento').get(
            token_cancelamento=token,
        )
    except Atendimento.DoesNotExist:
        return JsonResponse({'error': 'Agendamento não encontrado'}, status=404)
    motivo = _motivo_sem_reagendamento(atendimento, timezone.now())
    if motivo:
        return JsonResponse({'error': motivo}, status=400)
    try:
        dia = datetime.strptime(request.GET.get('data', ''), '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Data inválida'}, status=400)

    procedimento = atendimento.procedimento
    return JsonResponse({
        'data': dia.isoformat(),
        'horarios': agrupar_horarios(
            procedimento, profissionais_para(procedimento), dia,
            ignorar_atendimento_id=atendimento.pk, com_preco=False,
        ),
    })


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def reagendar_agendamento(request, token):
    """Fluxo publico de reagendamento via token seguro."""
    try:
        atendimento = Atendimento.objects.select_related(
            'cliente', 'profissional', 'procedimento'
        ).get(token_cancelamento=token)
    except Atendimento.DoesNotExist:
        messages.error(request, 'Agendamento não encontrado.')
        return redirect('aranha:agendamento_publico')

    agora = timezone.now()
    motivo = _motivo_sem_reagendamento(atendimento, agora)
    if motivo:
        messages.error(request, motivo)
        return redirect('aranha:meus_agendamentos')

    if request.method == 'GET':
        context = {
            'atendimento': atendimento,
            # Data local (nao UTC): apos as 21h o "amanha" em UTC pularia um dia.
            'data_min': (hoje() + timedelta(days=1)).isoformat(),
        }
        return render(request, 'agenda/reagendar.html', context)

    datetime_str = (request.POST.get('datetime') or '').strip()
    profissional_id = id_int(request.POST.get('profissional') or atendimento.profissional_id)

    if not datetime_str:
        messages.error(request, 'Selecione uma nova data e horário.')
        return redirect('aranha:reagendar_agendamento', token=token)

    try:
        nova_data = datetime.fromisoformat(datetime_str)
        if timezone.is_naive(nova_data):
            nova_data = timezone.make_aware(nova_data)
        nova_data = timezone.localtime(nova_data)
    except (ValueError, TypeError, OverflowError):
        messages.error(request, 'Data/horário inválidos.')
        return redirect('aranha:reagendar_agendamento', token=token)

    if nova_data <= agora:
        messages.error(request, 'Escolha uma data futura.')
        return redirect('aranha:reagendar_agendamento', token=token)

    if profissional_id is None:
        messages.error(request, 'Profissional indisponível.')
        return redirect('aranha:reagendar_agendamento', token=token)
    try:
        profissional = Profissional.objects.get(pk=profissional_id, ativo=True)
    except Profissional.DoesNotExist:
        messages.error(request, 'Profissional indisponível.')
        return redirect('aranha:reagendar_agendamento', token=token)

    procedimento = atendimento.procedimento
    if not profissional_habilitado(profissional, procedimento):
        messages.error(request, 'Este profissional não realiza este procedimento.')
        return redirect('aranha:reagendar_agendamento', token=token)

    if Feriado.objects.filter(data=nova_data.date(), bloqueia_agendamento=True).exists():
        messages.error(request, 'A data escolhida é feriado/recesso. Escolha outro dia.')
        return redirect('aranha:reagendar_agendamento', token=token)

    # O proprio horario antigo nao bloqueia o novo (ignorar_atendimento_id).
    if not slot_disponivel(profissional, procedimento, nova_data,
                           ignorar_atendimento_id=atendimento.pk):
        messages.error(request, 'Este horário não está disponível. Escolha outro.')
        return redirect('aranha:reagendar_agendamento', token=token)

    # Retorno gratuito ocupa so a duracao de retorno (igual ao RetornoService).
    duracao = (
        procedimento.duracao_retorno_minutos or DURACAO_RETORNO_PADRAO_MINUTOS
        if atendimento.eh_retorno else procedimento.duracao_minutos
    )
    nova_data_fim = nova_data + timedelta(minutes=duracao)

    try:
        with transaction.atomic():
            antigo = Atendimento.objects.select_for_update().select_related('cliente').get(pk=atendimento.pk)

            if antigo.status in STATUS_FINALIZADOS:
                messages.error(request, 'Este atendimento já foi processado em outra operação.')
                return redirect('aranha:meus_agendamentos')

            conflito = Atendimento.objects.select_for_update().filter(
                profissional=profissional,
                data_hora_inicio__lt=nova_data_fim,
                data_hora_fim__gt=nova_data,
                status__in=Atendimento.STATUS_ATIVOS,
            ).exclude(pk=antigo.pk).exists()

            if conflito:
                messages.error(request, 'Este horário acabou de ser reservado. Escolha outro.')
                return redirect('aranha:reagendar_agendamento', token=token)

            # Aprovacao continua valendo so se o original ja estava aprovado e
            # a profissional e a mesma; senao volta p/ a fila (PENDENTE).
            mesmo_profissional = profissional.pk == antigo.profissional_id
            novo_status = (
                Atendimento.STATUS_AGENDADO
                if antigo.status in (Atendimento.STATUS_AGENDADO, Atendimento.STATUS_CONFIRMADO)
                and mesmo_profissional
                else Atendimento.STATUS_PENDENTE
            )

            # Antigo sai da agenda ANTES do novo entrar (FSM + auditoria) — senao
            # a excl_atendimento_sobreposicao (Postgres) bloqueia mover o horario
            # p/ janela que sobrepoe o proprio slot antigo. Rollback do atomic
            # restaura tudo se o INSERT falhar.
            antigo.marcar_reagendado()

            # Preco/promocao combinados e o vinculo de retorno seguem no novo
            # (sem eh_retorno o retorno reagendado viraria sessao normal e
            # geraria outro retorno gratuito ao ser realizado).
            novo = Atendimento.objects.create(
                cliente=antigo.cliente,
                profissional=profissional,
                procedimento=procedimento,
                promocao=antigo.promocao,
                reagendado_de=antigo,
                eh_retorno=antigo.eh_retorno,
                atendimento_origem=antigo.atendimento_origem,
                data_hora_inicio=nova_data,
                data_hora_fim=nova_data_fim,
                valor_cobrado=antigo.valor_cobrado,
                valor_original=antigo.valor_original,
                descricao_preco=antigo.descricao_preco,
                status=novo_status,
            )
            # Ficha de anamnese (alergias etc.) acompanha o atendimento vivo.
            RespostaAnamnese.objects.filter(atendimento=antigo).update(atendimento=novo)
            registrar_log(
                None, 'Cliente reagendou pelo link', 'atendimento', novo.pk,
                detalhes={'reagendado_de': antigo.pk}, request=request,
            )
    except Atendimento.TransicaoInvalida:
        messages.error(request, 'Este atendimento não pode mais ser reagendado.')
        return redirect('aranha:meus_agendamentos')
    except IntegrityError as exc:
        if _eh_sobreposicao(exc):
            messages.error(request, 'Este horário acabou de ser reservado. Escolha outro.')
            return redirect('aranha:reagendar_agendamento', token=token)
        logger.error(
            'reagendamento_falha',
            extra={'atendimento_id': atendimento.pk, 'erro': str(exc)},
            exc_info=True,
        )
        messages.error(request, 'Ocorreu um erro ao reagendar. Tente novamente.')
        return redirect('aranha:reagendar_agendamento', token=token)
    except DatabaseError as exc:
        logger.error(
            'reagendamento_falha',
            extra={'atendimento_id': atendimento.pk, 'erro': str(exc)},
            exc_info=True,
        )
        messages.error(request, 'Ocorreu um erro ao reagendar. Tente novamente.')
        return redirect('aranha:reagendar_agendamento', token=token)

    data_fmt = formatar_data_hora(nova_data)
    pendente = novo.status == Atendimento.STATUS_PENDENTE

    if pendente:
        usuario_prof = getattr(profissional, 'usuario', None)
        prof_email = getattr(usuario_prof, 'email', None)
        if prof_email:
            _enfileirar_email('enviar_aprovacao_profissional_email', prof_email, {
                'profissional': profissional.nome,
                'cliente': antigo.cliente.nome,
                'procedimento': procedimento.nome,
                'data_hora': data_fmt,
                'link_revisar': link_revisar_agenda(nova_data),
            })

    if novo.eh_retorno:
        valor_txt = 'Sem custo (retorno)'
    elif novo.valor_cobrado is not None:
        valor_txt = formatar_brl(novo.valor_cobrado)
    else:
        valor_txt = 'A consultar'
    em_promocao = bool(
        novo.promocao_id and novo.valor_original is not None
        and novo.valor_cobrado is not None and novo.valor_original > novo.valor_cobrado
    )
    request.session['agendamento_sucesso'] = {
        'nome': antigo.cliente.nome,
        'procedimento': procedimento.nome,
        'profissional': profissional.nome,
        'data_hora': data_fmt,
        'valor': valor_txt,
        'valor_cheio': formatar_brl(novo.valor_original) if em_promocao else '',
        'promocao': novo.promocao.nome if em_promocao else '',
        'pendente': pendente,
        'reagendamento': True,
        # So promete acompanhamento por e-mail se o backend entrega de fato
        'email': bool(antigo.cliente.email) and email_configurado(),
    }
    return redirect('aranha:agendamento_sucesso')
