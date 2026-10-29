"""Area da cliente "Meus agendamentos" (decisao 39): agendamentos, reagendar e cancelar, preferencias
de comunicacao, revogacao do consentimento de saude, pedido de correcao/exclusao e "baixar meus dados".

Acesso unico: sessao do OTP (do portal ou do fim do wizard) presa ao telefone do cadastro
(services/portal_cliente), e cada atendimento so pelo id dentro dessa sessao (anti-IDOR). Nenhum
token e emitido (o link /reagendar/<token>/ antigo saiu com a 0072).
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
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from ...constants import JANELA_MINIMA_ALTERACAO
from ...models import Atendimento, Consentimento, Notificacao, Profissional
from ...models.agendamentos import STATUS_FINALIZADOS
from ...excecoes import RegraDeNegocio, TransicaoInvalida
from ...services import agenda, agendamento_online, comunicacao, consentimentos, portal_cliente
from ...services.agendamento_online import MSG_BLOQUEADO_ONLINE
from ...utils.datas import ler_data
from ...services.disponibilidade import horarios_agrupados, profissionais_para
from ...utils.auditoria import registrar_log
from ...utils.captcha import turnstile_enabled, turnstile_site_key
from ...utils.datas import hoje
from ...utils.parse import id_int
from ...utils.pii import mask_telefone
from ...utils.respostas import json_erro, json_ok, json_recusa
from ...utils.sms import sms_disponivel
from .agendamento import SESSAO_SUCESSO
from .lgpd import resposta_export

logger = logging.getLogger(__name__)




def meus_agendamentos(request):
    """Area da cliente autenticada via OTP (sessao presa ao telefone do cadastro)."""
    telefone = portal_cliente.telefone_da_sessao(request)
    if not telefone:
        return render(request, 'agendamento/meus_agendamentos.html', {
            'step': '1',
            'turnstile_site_key': turnstile_site_key(),
            'turnstile_enabled': turnstile_enabled(),
            # Sem SMS o login do portal e impossivel: avisa antes do envio
            'sms_disponivel': sms_disponivel(),
        })

    agora = timezone.now()
    clientes = portal_cliente.clientes_da_sessao(request)
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
    titular = portal_cliente.cliente_da_sessao(request)
    cliente = clientes.exclude(codigo_indicacao__isnull=True).exclude(codigo_indicacao='').first()
    indicacao = {
        'codigo': cliente.codigo_indicacao, 'valor': VALOR_CASHBACK_INDICACAO,
        'saldo': sum(FidelidadeService.saldos(clientes.values_list('pk', flat=True)).values()),
    } if cliente else None
    from ...services.pacotes import sessoes_previstas
    from ...services.retorno import devidos
    retornos = [r for c in clientes for r in devidos(cliente_id=c.pk)]
    futuros = list(agendamentos_futuros[:20])

    return render(request, 'agendamento/meus_agendamentos.html', {
        'step': '3',
        'indicacao': indicacao,
        'retornos': retornos,   # retorno sem custo a combinar com a clinica (services/retorno)
        'telefone_mascarado': mask_telefone(telefone),
        'agendamentos_futuros': futuros,
        'pacotes_previstos': sessoes_previstas(futuros),   # "Sessão do pacote X" no lugar do preco
        'agendamentos_passados': agendamentos_passados.distinct()[:20],
        'status_editaveis': Atendimento.STATUS_ATIVOS,
        'limite_reagendar': agora + JANELA_MINIMA_ALTERACAO,
        # cancelar antes deste instante e sem falta; depois (aprovado) e cancelamento tardio
        'limite_cancelar_sem_falta': agora + JANELA_MINIMA_ALTERACAO,
        'status_aprovados': agenda.STATUS_APROVADOS,
        'preferencias': consentimentos.preferencias_portal(titular) if titular else [],
        'saude_consentida': bool(titular) and consentimentos.saude_consentida(titular),
        'texto_revogacao_saude': consentimentos.TEXTO_REVOGACAO_SAUDE,
        'tipos_pedido': portal_cliente.TIPOS_PEDIDO,
    })


atendimento_da_sessao = portal_cliente.atendimento_da_sessao


def _motivo_sem_reagendamento(atendimento, agora):
    """Por que o atendimento nao pode ser reagendado online ('' = pode)."""
    if atendimento.data_hora_inicio <= agora:
        return 'Não é possível reagendar atendimentos passados.'
    if atendimento.status in STATUS_FINALIZADOS:
        return f'Este atendimento está {atendimento.get_status_display().lower()} e não pode ser reagendado.'
    if (atendimento.data_hora_inicio - agora) < JANELA_MINIMA_ALTERACAO:
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
        'horarios': horarios_agrupados(
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
    if not portal_cliente.telefone_da_sessao(request):
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
    # Mesmo resumo do pedido pelo site (valor do pacote/retorno/promocao numa regra so)
    request.session[SESSAO_SUCESSO] = agendamento_online.resumo_confirmacao(novo, reagendamento=True)
    return redirect('aranha:agendamento_sucesso')


@require_POST
@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def cancelar_agendamento(request):
    """Cancela pelo portal (POST JSON {"atendimento_id": N}) — agenda.cancelar_pela_cliente.

    Anti-IDOR: so atendimento de cliente com o telefone da sessao do OTP. Com menos de 24 h e
    cancelamento tardio (conta como falta; o modal avisa antes). Resposta: utils/respostas.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return json_erro('dados_invalidos', 'Dados inválidos.')
    if not isinstance(data, dict):
        return json_erro('dados_invalidos', 'Dados inválidos.')
    atendimento_id = id_int(data.get('atendimento_id'))
    if atendimento_id is None:
        return json_erro('sem_atendimento', 'Agendamento não informado.')
    alvo = atendimento_da_sessao(request, atendimento_id)
    if alvo is None:
        return json_erro('nao_encontrado', 'Agendamento não encontrado.', status=404)
    try:
        atendimento, tardio = agenda.cancelar_pela_cliente(alvo.pk, origem='portal')
    except RegraDeNegocio as recusa:
        return json_recusa(recusa)
    except DatabaseError:
        logger.error('cancelar_agendamento_falha', exc_info=True)
        return json_erro('erro_interno', 'Ocorreu um erro interno. Tente novamente.', status=500)
    mensagem = f'Agendamento de {atendimento.procedimento.nome} cancelado.'
    if tardio:
        mensagem += ' Como faltavam menos de 24 h, o cancelamento conta como falta.'
    return json_ok(mensagem=mensagem, tardio=tardio)


# ─── Preferencias, consentimento de saude, pedidos e dados (LGPD) ───────────────────────────

def _titular_ou_login(request):
    """(cliente, None) ou (None, redirect p/ o login do portal)."""
    cliente = portal_cliente.cliente_da_sessao(request)
    if cliente is None:
        portal_cliente.encerrar_sessao(request)
        return None, redirect('aranha:meus_agendamentos')
    return cliente, None


@require_POST
@ratelimit(key='ip', rate='20/h', method='POST', block=True)
def portal_preferencias(request):
    """"Minhas preferencias": os 3 pares de comunicacao, origem PORTAL (IP e user-agent como prova)."""
    cliente, fora = _titular_ou_login(request)
    if fora:
        return fora
    novas = consentimentos.registrar_do_portal(
        cliente, {campo: campo in request.POST for campo in consentimentos.PARES}, request=request,
    )
    messages.success(request, 'Preferências atualizadas.' if novas else 'Nenhuma preferência mudou.')
    return redirect(reverse('aranha:meus_agendamentos') + '#preferencias')


@require_POST
@ratelimit(key='ip', rate='10/h', method='POST', block=True)
def portal_revogar_saude(request):
    """Revoga o consentimento de dado de saude (art. 8 §5): consentimentos.revogar_saude."""
    cliente, fora = _titular_ou_login(request)
    if fora:
        return fora
    if consentimentos.revogar_saude(cliente, origem=Consentimento.ORIGEM_PORTAL, request=request):
        messages.success(
            request, 'Consentimento de saúde revogado. A ficha já preenchida fica guardada pelo prazo legal; '
                     'um novo atendimento vai pedir uma nova ficha.',
        )
    else:
        messages.info(request, 'Não há consentimento de saúde vigente para revogar.')
    return redirect(reverse('aranha:meus_agendamentos') + '#privacidade')


@require_POST
@ratelimit(key='ip', rate='5/h', method='POST', block=True)
def portal_pedido(request):
    """Pedido de correcao ou exclusao: trilha + e-mail a clinica (services/portal_cliente)."""
    cliente, fora = _titular_ou_login(request)
    if fora:
        return fora
    tipo = request.POST.get('tipo', '')
    if tipo not in portal_cliente.TIPOS_PEDIDO:
        messages.error(request, 'Escolha o tipo de pedido.')
        return redirect(reverse('aranha:meus_agendamentos') + '#privacidade')
    prazo = portal_cliente.registrar_pedido(cliente, tipo, request.POST.get('descricao', ''), request=request)
    messages.success(
        request, f'Pedido de {portal_cliente.TIPOS_PEDIDO[tipo]} registrado. A clínica responde até '
                 f'{prazo.strftime("%d/%m/%Y")}.',
    )
    return redirect(reverse('aranha:meus_agendamentos') + '#privacidade')


@require_POST
@ratelimit(key='ip', rate='10/h', method='POST', block=True)
def portal_meus_dados(request):
    """"Baixar meus dados" sem a ficha de saude; exige codigo confirmado ha ate 10 min (o mesmo
    export e trilha do "Meus dados"). Sessao mais antiga: pede um codigo novo."""
    cliente, fora = _titular_ou_login(request)
    if fora:
        return fora
    if not portal_cliente.sessao_recente(request):
        portal_cliente.encerrar_sessao(request)
        messages.info(
            request, 'Por segurança, confirme o código por SMS de novo para baixar os seus dados '
                     '(vale por 10 minutos).',
        )
        return redirect('aranha:meus_agendamentos')
    return resposta_export(request, cliente)
