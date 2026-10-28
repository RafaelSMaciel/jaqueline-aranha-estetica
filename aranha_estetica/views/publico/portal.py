"""Portal "Meus agendamentos": listagem, reagendamento e cancelamento pela cliente.

Acesso unico: id do atendimento + sessao do OTP (telefone do cadastro), anti-IDOR. Nenhum
token e emitido (o link /reagendar/<token>/ antigo saiu com a 0066).
"""
import json
import logging
from datetime import datetime, timedelta

from django.contrib import messages
from django.db import DatabaseError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from ...constants import JANELA_MINIMA_REAGENDAMENTO
from ...models import Atendimento, Cliente, Notificacao, Profissional
from ...models.agendamentos import STATUS_FINALIZADOS
from ...excecoes import RegraDeNegocio, TransicaoInvalida
from ...services import agenda, comunicacao
from ...services.agendamento import formatar_data_hora
from ...utils.datas import ler_data
from ...utils.formatos import formatar_brl
from ...services.disponibilidade import profissionais_para
from ...utils.auditoria import registrar_log
from ...utils.captcha import turnstile_enabled, turnstile_site_key
from ...utils.datas import hoje
from ...utils.email import email_configurado
from ...utils.parse import id_int
from ...utils.pii import mask_telefone
from ...utils.sms import sms_disponivel
from .agendamento_api import agrupar_horarios
from .verificacao import SESSAO_MEUS_AGENDAMENTOS
from .agendamento import MSG_BLOQUEADO_ONLINE

logger = logging.getLogger(__name__)




def meus_agendamentos(request):
    """Listagem autenticada via OTP (sessao presa ao telefone do cadastro)."""
    telefone = request.session.get(SESSAO_MEUS_AGENDAMENTOS)
    if not telefone:
        return render(request, 'agendamento/meus_agendamentos.html', {
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

    # programa de indicacao: o codigo da cliente e o credito dela (antes so a recepcao via)
    from ...constants import VALOR_CASHBACK_INDICACAO
    from ...services.fidelidade import FidelidadeService
    cliente = clientes.exclude(codigo_indicacao__isnull=True).exclude(codigo_indicacao='').first()
    indicacao = {
        'codigo': cliente.codigo_indicacao, 'valor': VALOR_CASHBACK_INDICACAO,
        'saldo': sum(FidelidadeService.saldos(clientes.values_list('pk', flat=True)).values()),
    } if cliente else None
    from ...services.retorno import devidos
    retornos = [r for c in clientes for r in devidos(cliente_id=c.pk)]

    return render(request, 'agendamento/meus_agendamentos.html', {
        'step': '3',
        'indicacao': indicacao,
        'retornos': retornos,   # retorno sem custo a combinar com a clinica (services/retorno)
        'telefone_mascarado': mask_telefone(telefone),
        'agendamentos_futuros': agendamentos_futuros[:20],
        'agendamentos_passados': agendamentos_passados.distinct()[:20],
        'status_editaveis': Atendimento.STATUS_ATIVOS,
        'limite_reagendar': agora + JANELA_MINIMA_REAGENDAMENTO,
    })


def atendimento_da_sessao(request, pk, *select_related):
    """Atendimento `pk` de um cliente ativo com o telefone da sessao do OTP (None = nao e dela)."""
    telefone = request.session.get(SESSAO_MEUS_AGENDAMENTOS)
    if not telefone or pk is None:
        return None
    return (
        Atendimento.objects.select_related(*select_related)
        .filter(pk=pk, cliente__in=Cliente.objects.filter(telefone=telefone, ativo=True))
        .first()
    )


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


def _horarios(request, atendimento):
    """Horarios livres do dia (GET ?data=) p/ a tela de reagendamento.

    Mesmas regras do POST; o proprio horario atual NAO conta como ocupado
    (ignorar_atendimento_id), senao a tela esconderia horarios que o servidor
    aceita (ex.: empurrar 30 min).
    """
    if atendimento is None:
        return JsonResponse({'erro': 'Agendamento não encontrado'}, status=404)
    motivo = _motivo_sem_reagendamento(atendimento, timezone.now())
    if motivo:
        return JsonResponse({'erro': motivo}, status=400)
    try:
        dia = ler_data(request.GET.get('data', ''))
    except ValueError:
        return JsonResponse({'erro': 'Data inválida'}, status=400)

    procedimento = atendimento.procedimento
    return JsonResponse({
        'data': dia.isoformat(),
        'horarios': agrupar_horarios(
            procedimento, profissionais_para(procedimento), dia,
            ignorar_atendimento_id=atendimento.pk, com_preco=False,
        ),
    })


@ratelimit(key='ip', rate='30/m', method='GET', block=True)
def reagendar_horarios_portal(request, pk):
    """AJAX da tela de reagendamento do portal (id + sessao do OTP)."""
    return _horarios(request, atendimento_da_sessao(request, pk, 'cliente', 'procedimento'))


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def reagendar_agendamento_portal(request, pk):
    """Reagendamento pelo portal "Meus agendamentos" (id + sessao do OTP, anti-IDOR)."""
    if not request.session.get(SESSAO_MEUS_AGENDAMENTOS):
        return redirect('aranha:meus_agendamentos')
    atendimento = atendimento_da_sessao(request, pk, 'cliente', 'profissional', 'procedimento')
    if atendimento is None:
        messages.error(request, 'Agendamento não encontrado.')
        return redirect('aranha:meus_agendamentos')
    return _reagendar(
        request, atendimento,
        url_voltar=reverse('aranha:reagendar_agendamento_portal', args=[pk]),
        url_horarios=reverse('aranha:reagendar_horarios_portal', args=[pk]),
    )


def _reagendar(request, atendimento, *, url_voltar, url_horarios):
    """GET: tela de escolha; POST: move o atendimento (antigo REAGENDADO + novo criado)."""
    agora = timezone.now()
    motivo = _motivo_sem_reagendamento(atendimento, agora)
    if motivo:
        messages.error(request, motivo)
        return redirect('aranha:meus_agendamentos')

    if request.method == 'GET':
        context = {
            'atendimento': atendimento,
            'url_horarios': url_horarios,
            # Data local (nao UTC): apos as 21h o "amanha" em UTC pularia um dia.
            'data_min': (hoje() + timedelta(days=1)).isoformat(),
        }
        return render(request, 'agendamento/reagendar.html', context)

    datetime_str = (request.POST.get('datetime') or '').strip()
    profissional_id = id_int(request.POST.get('profissional') or atendimento.profissional_id)

    if not datetime_str:
        messages.error(request, 'Selecione uma nova data e horário.')
        return redirect(url_voltar)

    try:
        nova_data = datetime.fromisoformat(datetime_str)
        if timezone.is_naive(nova_data):
            nova_data = timezone.make_aware(nova_data)
        nova_data = timezone.localtime(nova_data)
    except (ValueError, TypeError, OverflowError):
        messages.error(request, 'Data/horário inválidos.')
        return redirect(url_voltar)

    if nova_data <= agora:
        messages.error(request, 'Escolha uma data futura.')
        return redirect(url_voltar)

    if profissional_id is None:
        messages.error(request, 'Profissional indisponível.')
        return redirect(url_voltar)
    try:
        profissional = Profissional.objects.get(pk=profissional_id, ativo=True)
    except Profissional.DoesNotExist:
        messages.error(request, 'Profissional indisponível.')
        return redirect(url_voltar)

    procedimento = atendimento.procedimento
    try:
        with transaction.atomic():
            # Mesma regra de horario das outras portas; antigo -> REAGENDADO + novo com o preco
            # combinado (services.agenda). O proprio horario antigo nao bloqueia o novo.
            novo = agenda.reagendar(atendimento, nova_data, profissional)
            if novo.status == Atendimento.STATUS_PENDENTE:   # volta p/ a profissional aprovar
                comunicacao.enfileirar_email(novo, Notificacao.Tipo.APROVACAO)
            registrar_log(
                None, 'Cliente reagendou pelo portal',
                'atendimento', novo.pk, detalhes={'reagendado_de': atendimento.pk}, request=request,
            )
    except TransicaoInvalida as recusa:
        messages.error(request, recusa.mensagem)
        return redirect('aranha:meus_agendamentos')
    except RegraDeNegocio as recusa:
        messages.error(request, recusa.mensagem)
        return redirect(url_voltar)
    except DatabaseError as exc:
        logger.error(
            'reagendamento_falha',
            extra={'atendimento_id': atendimento.pk, 'erro': str(exc)},
            exc_info=True,
        )
        messages.error(request, 'Ocorreu um erro ao reagendar. Tente novamente.')
        return redirect(url_voltar)
    antigo = novo.reagendado_de

    data_fmt = formatar_data_hora(nova_data)
    pendente = novo.status == Atendimento.STATUS_PENDENTE

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


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def cancelar_agendamento(request):
    """Cancela pelo portal (POST JSON {"atendimento_id": N}) pela FSM, anti-IDOR: so agendamento
    de cliente com o telefone da sessao do OTP."""
    if request.method != 'POST':
        return JsonResponse({'erro': 'Método não permitido'}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'erro': 'Dados inválidos'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'erro': 'Dados inválidos'}, status=400)
    atendimento_id = id_int(data.get('atendimento_id'))
    if atendimento_id is None:
        return JsonResponse({'erro': 'Agendamento não informado'}, status=400)
    alvo = atendimento_da_sessao(request, atendimento_id)
    if alvo is None:
        return JsonResponse({'erro': 'Agendamento não encontrado'}, status=404)

    try:
        with transaction.atomic():
            atendimento = (
                Atendimento.objects.select_for_update()
                .select_related('procedimento')
                .get(pk=alvo.pk)
            )

            if atendimento.data_hora_inicio <= timezone.now():
                return JsonResponse(
                    {'erro': 'Não é possível cancelar agendamentos passados'}, status=400
                )
            if atendimento.status not in Atendimento.STATUS_ATIVOS:
                return JsonResponse({'erro': 'Este agendamento não pode mais ser cancelado'}, status=400)
            try:
                # FSM: auditoria + efeitos (lista de espera, aviso a profissional)
                atendimento.cancelar(motivo='Cancelado pelo cliente (portal)', pela_cliente=True)
            except Atendimento.TransicaoInvalida:
                return JsonResponse({'erro': 'Este agendamento não pode mais ser cancelado'}, status=400)
            procedimento_nome = atendimento.procedimento.nome
    except DatabaseError:
        logger.error('cancelar_agendamento_falha', exc_info=True)
        return JsonResponse({'erro': 'Ocorreu um erro interno. Tente novamente.'}, status=500)

    return JsonResponse({
        'sucesso': True,
        'mensagem': f'Agendamento de {procedimento_nome} cancelado com sucesso.',
    })
