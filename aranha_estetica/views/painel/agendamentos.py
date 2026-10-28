"""Agendamento interno pela recepcao (painel) — sem o OTP da cliente.

A recepcao agenda pelo telefone/WhatsApp/balcao: cliente existente (busca) ou
nova (nome + telefone validado), procedimento, profissional e horario. O
horario passa pelo MESMO SlotService do site (services.disponibilidade) e o
valor sai de utils.precos.preco_com_promocao na data do atendimento (a
recepcao pode informar outro valor combinado; fica registrado). Nasce
AGENDADO — a propria clinica confirmou — com auditoria.
"""
import json
import logging
from datetime import datetime

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import DatabaseError, IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from ...decorators import staff_required
from ...models import Atendimento, Cliente, Procedimento, Profissional
from ...models.agendamentos import StatusAtendimento
from ...excecoes import RegraDeNegocio
from ...services import agenda
from ...services.agendamento import avisar_cancelamento
from ...services.disponibilidade import SlotService, profissionais_para
from ...services.termos import termos_pendentes
from ...utils.auditoria import registrar_log
from ...utils.busca import filtrar_clientes
from ...utils.datas import hoje, ler_data, ler_hora
from ...utils.parse import id_int
from ...utils.precos import preco_com_promocao
from ...utils.seguranca import safe_next
from ...validators import normalizar_telefone, validate_telefone_br
from ...forms.painel import parse_preco as _parse_preco

logger = logging.getLogger(__name__)

ACOES = ('buscar', 'horarios', 'agendar')

def _int(valor):
    # id_int: isdigit() aceitava '²' e o int() dava 500
    return id_int(valor)


def _data(valor):
    try:
        return ler_data((valor or '').strip())
    except ValueError:
        return None


def _hora(valor):
    try:
        return ler_hora((valor or '').strip())
    except ValueError:
        return None


def _validar_cliente_novo(nome, telefone_raw, email):
    """(dados_limpos, erros, inativa) do cadastro rapido — mesmas regras da ficha do cliente.

    inativa: Cliente com cadastro INATIVO dono do telefone/e-mail — a busca so
    lista ativas, entao a recepcao precisa do caminho p/ reativar na ficha.
    """
    erros = []
    inativa = None
    nome = (nome or '').strip()
    if not nome:
        erros.append('Informe o nome da cliente nova.')
    elif len(nome) > 150:
        erros.append('O nome aceita no máximo 150 caracteres.')

    telefone = normalizar_telefone(telefone_raw)
    try:
        validate_telefone_br(telefone)
    except ValidationError:
        erros.append('Telefone inválido: informe DDD + número (10 ou 11 dígitos).')
    else:
        existente = Cliente.objects.filter(telefone=telefone).first()
        if existente is not None and not existente.ativo:
            inativa = existente
            erros.append(
                f'Este telefone é de {existente.nome}, com cadastro inativo: '
                'reative a cliente na ficha (link abaixo) para agendar.'
            )
        elif existente is not None:
            erros.append(
                f'Este telefone já é de {existente.nome}: busque e selecione a cliente cadastrada.'
            )

    email = (email or '').strip().lower() or None
    if email:
        try:
            validate_email(email)
        except ValidationError:
            erros.append('E-mail inválido.')
        else:
            dono = Cliente.objects.filter(email__iexact=email).first()
            if dono is not None and not dono.ativo:
                inativa = inativa or dono
                erros.append(
                    f'Este e-mail é de {dono.nome}, com cadastro inativo: '
                    'reative a cliente na ficha (link abaixo) para agendar.'
                )
            elif dono is not None:
                erros.append('Este e-mail já pertence a outra cliente: busque a cliente cadastrada.')
    return {'nome': nome, 'telefone': telefone, 'email': email}, erros, inativa


@staff_required
@ratelimit(key='user', rate='60/m', method='POST', block=True)
def admin_agendamento_novo(request):
    """Tela unica (POST com `acao`: buscar | horarios | agendar).

    Busca e horarios so re-renderizam (telefone/nome digitados nao vao p/ a
    URL); `agendar` valida tudo de novo no servidor e cria o atendimento.
    GET aceita ?cliente=<pk> (atalho da ficha do cliente) e ?data=AAAA-MM-DD.
    """
    post = request.method == 'POST'
    dados = request.POST if post else request.GET
    acao = request.POST.get('acao', '') if post else ''
    if acao not in ACOES:
        acao = ''

    q = (dados.get('q') or '').strip()[:100]
    cliente = None
    cliente_inativa = None
    cliente_id = _int(dados.get('cliente_id') or dados.get('cliente'))
    if cliente_id:
        cliente = Cliente.objects.filter(pk=cliente_id).first()
        if cliente is not None and not cliente.ativo:
            # atalho da ficha / selecao de inativa: explica em vez de ignorar
            cliente_inativa, cliente = cliente, None
            messages.warning(
                request,
                f'{cliente_inativa.nome} está com o cadastro inativo: reative na ficha para agendar.',
            )
    novo = {
        'nome': (request.POST.get('novo_nome') or '').strip() if post else '',
        'telefone': (request.POST.get('novo_telefone') or '').strip() if post else '',
        'email': (request.POST.get('novo_email') or '').strip() if post else '',
    }

    procedimentos = Procedimento.objects.filter(ativo=True).order_by('nome')
    procedimento = procedimentos.filter(pk=_int(dados.get('procedimento_id'))).first()
    profissionais = (
        profissionais_para(procedimento) if procedimento
        else Profissional.objects.filter(ativo=True).order_by('nome')
    )
    # So profissional habilitado p/ o procedimento (profissionais_para)
    profissional = profissionais.filter(pk=_int(dados.get('profissional_id'))).first()
    data = _data(dados.get('data'))
    hora_str = (dados.get('hora') or '').strip()
    valor_str = (dados.get('valor') or '').strip()

    resultados, inativas = [], []
    if q:
        resultados = list(
            filtrar_clientes(Cliente.objects.filter(ativo=True), q).order_by('nome')[:20]
        )
        # inativas aparecem (nao selecionaveis) com o link p/ reativar na ficha
        inativas = list(
            filtrar_clientes(Cliente.objects.filter(ativo=False), q).order_by('nome')[:5]
        )

    slots, preco = [], None
    if procedimento and profissional and data:
        slots = SlotService.slots_livres(profissional, data, procedimento)
        final, promo, cheio = preco_com_promocao(procedimento, profissional, data)
        preco = {'final': final, 'promocao': promo, 'cheio': cheio}

    context = {
        'q': q,
        'resultados': resultados,
        'inativas': inativas,
        'cliente_inativa': cliente_inativa,
        'cliente': cliente,
        'novo': novo,
        'procedimentos': procedimentos,
        'procedimento': procedimento,
        'profissionais': profissionais,
        'profissional': profissional,
        'data': data,
        'data_min': hoje(),
        'hora': hora_str,
        'slots': slots,
        'preco': preco,
        'valor': valor_str,
        'buscou': acao == 'buscar' or bool(q),
        'viu_horarios': bool(procedimento and profissional and data),
    }

    if acao != 'agendar':
        return render(request, 'painel/agendamento_novo.html', context)

    # ── agendar: valida tudo no servidor ──
    erros = []
    dados_novo = None
    if cliente is None:
        if novo['nome'] or novo['telefone']:
            dados_novo, erros_novo, inativa = _validar_cliente_novo(
                novo['nome'], novo['telefone'], novo['email'],
            )
            erros.extend(erros_novo)
            if inativa is not None:
                context['cliente_inativa'] = inativa
        elif cliente_inativa is not None:
            # o aviso de cadastro inativo ja foi dado acima; nada e gravado
            erros.append('Reative a cliente na ficha antes de agendar.')
        else:
            erros.append('Selecione a cliente na busca ou preencha o cadastro da cliente nova.')
    if procedimento is None:
        erros.append('Selecione o procedimento.')
    if profissional is None:
        erros.append('Selecione um profissional que realize o procedimento.')
    hora = _hora(hora_str)
    if data is None or hora is None:
        erros.append('Escolha a data e um dos horários livres.')

    valor_informado = None
    try:
        valor_informado = _parse_preco(valor_str)
    except ValueError:
        erros.append('Valor inválido: use números, ex.: 150,00.')

    inicio = None
    if not erros:
        inicio = timezone.make_aware(datetime.combine(data, hora))
        # Mesma regra do site (SlotService: expediente, folga, bloqueio, buffer, antecedencia),
        # com o motivo da recusa p/ a recepcao. Habilitacao ja veio do profissionais_para.
        try:
            agenda.validar_horario(profissional, procedimento, inicio, checar_habilitacao=False, detalhado=True)
        except RegraDeNegocio as recusa:
            erros.append(recusa.mensagem)

    if erros:
        for erro in erros:
            messages.error(request, erro)
        return render(request, 'painel/agendamento_novo.html', context)

    precos = agenda.preco_do_dia(procedimento, profissional, inicio)
    if valor_informado is not None and valor_informado != precos['valor_cobrado']:
        # Valor combinado com a cliente (desconto negociado, cortesia...)
        precos = {
            'valor_cobrado': valor_informado, 'valor_original': preco['cheio'],
            'promocao': None, 'descricao_preco': 'Valor combinado pela recepção',
        }
    valor_cobrado, promo = precos['valor_cobrado'], precos['promocao']

    cliente_novo = cliente is None
    try:
        with transaction.atomic():
            if cliente_novo:
                cliente = Cliente.objects.create(**dados_novo)
            atendimento = agenda.reservar(
                cliente=cliente, profissional=profissional, procedimento=procedimento,
                inicio=inicio, status=Atendimento.STATUS_AGENDADO, preco=precos,
            )
    except (RegraDeNegocio, IntegrityError) as exc:
        if isinstance(exc, RegraDeNegocio):
            messages.error(request, exc.mensagem)
        else:
            logger.warning('agendamento_painel_integridade', exc_info=True)
            messages.error(request, 'Telefone, e-mail ou CPF já cadastrado para outra cliente. Busque a cliente.')
        if cliente_novo:
            context['cliente'] = None
        return render(request, 'painel/agendamento_novo.html', context)
    except DatabaseError:
        logger.error('agendamento_painel_falha', exc_info=True)
        messages.error(request, 'Não foi possível salvar o agendamento. Tente novamente.')
        if cliente_novo:
            context['cliente'] = None
        return render(request, 'painel/agendamento_novo.html', context)

    registrar_log(
        request.user,
        f'Agendou pelo painel: {procedimento.nome}',
        'atendimento', atendimento.pk,
        {
            'cliente': cliente.pk, 'cliente_novo': cliente_novo,
            'profissional': profissional.pk,
            'valor_cobrado': str(valor_cobrado) if valor_cobrado is not None else None,
            'promocao': promo.pk if promo else None,
        },
        request=request,
    )
    messages.success(
        request,
        f'Agendado: {cliente.nome} — {procedimento.nome} com {profissional.nome} em '
        f'{timezone.localtime(inicio).strftime("%d/%m/%Y às %H:%M")}.',
    )
    # O agendamento pela recepcao nao coleta a ficha de anamnese do site
    from ...services.alertas import ids_com_ficha_pendente
    if ids_com_ficha_pendente([atendimento]):
        messages.warning(
            request,
            'Ficha de anamnese obrigatória pendente: confirme alergias e contraindicações '
            'com a cliente antes do procedimento e registre no prontuário.',
        )
    return redirect(f"{reverse('aranha:painel_agendamentos')}?data={data.isoformat()}")


@staff_required
@require_POST
@ratelimit(key='user', rate='60/m', method='POST', block=True)
def admin_atendimento_valor(request, pk):
    """Registra o valor cobrado de um atendimento (desconto combinado, 'a consultar').

    Antes de realizado: so grava. Realizado: grava e (re)calcula a comissao — com credito
    PENDENTE, estorna e lanca o novo com o valor corrigido; comissao ja PAGA recusa (o repasse
    saiu). Cancelado/faltou/reagendado e retorno gratuito (sempre 0): recusado com a explicacao.
    """
    from ...services.comissao import ComissaoService
    from ...services.fidelidade import FidelidadeService

    at = get_object_or_404(Atendimento.objects.select_related('cliente', 'procedimento'), pk=pk)
    voltar = redirect(safe_next(request, request.META.get('HTTP_REFERER'), 'aranha:painel_agendamentos'))
    try:
        valor = _parse_preco(request.POST.get('valor'))
    except ValueError:
        valor = None
    if valor is None:
        messages.error(request, 'Valor inválido: use números, ex.: 150,00.')
        return voltar
    if at.status in (StatusAtendimento.CANCELADO, StatusAtendimento.FALTOU, StatusAtendimento.REAGENDADO):
        messages.error(request, f'Atendimento {at.get_status_display().lower()}: o valor não pode ser alterado.')
        return voltar
    if at.eh_retorno and valor != 0:
        messages.error(request, 'Retorno gratuito fica sempre com valor zero.')
        return voltar

    antigo = at.valor_cobrado
    comissao = cashback = credito_usado = None
    try:
        with transaction.atomic():
            if at.valor_original is None and antigo is not None and antigo != valor:
                at.valor_original = antigo
            at.valor_cobrado = valor
            at.descricao_preco = 'Valor combinado pela recepção'
            at.save(update_fields=['valor_cobrado', 'valor_original', 'descricao_preco', 'atualizado_em'])
            if request.POST.get('usar_credito') == '1':
                # credito de indicacao da propria cliente: abate ate o saldo e ate o valor
                saldo = FidelidadeService.saldos([at.cliente_id]).get(at.cliente_id, 0)
                if saldo:
                    credito_usado = FidelidadeService.usar_credito(at, min(saldo, valor), request.user)
            if at.status == Atendimento.STATUS_REALIZADO:
                comissao = ComissaoService.recalcular(at, request.user)
                # Mesmo gatilho do REALIZADO: sem valor o cashback da indicadora
                # tinha sido pulado e o evento nao roda de novo. Idempotente
                # (UNIQUE carteira+atendimento+origem; 1o pago exclui o proprio pk).
                cashback = FidelidadeService.liberar_cashback_indicacao(at)
    except RegraDeNegocio as e:
        messages.error(request, e.mensagem)
        return voltar
    except DatabaseError:
        logger.error('atendimento_valor_falha', exc_info=True, extra={'atendimento_id': at.pk})
        messages.error(request, 'Não foi possível registrar o valor. Confira o número e tente novamente.')
        return voltar

    registrar_log(
        request.user, 'Registrou valor cobrado', 'atendimento', at.pk,
        {'de': str(antigo) if antigo is not None else None, 'para': str(valor),
         'comissao': comissao.pk if comissao else None,
         'cashback': cashback.pk if cashback else None,
         'credito_usado': str(credito_usado.valor) if credito_usado else None},
        request=request,
    )
    from ...utils.formatos import formatar_brl
    messages.success(request, f'Valor de {at.cliente.nome} registrado: {formatar_brl(valor)}'
                     + (f' (crédito de indicação abatido: {formatar_brl(credito_usado.valor)}).' if credito_usado
                        else '.'))
    return voltar


@staff_required
@ratelimit(key='user', rate='60/m', method='POST', block=True)
def admin_atualizar_status(request):
    """Atualiza status de um agendamento via AJAX.

    Entrada invalida -> 400/404 (antes virava 500 + traceback no Sentry).
    REALIZADO com termo de PROCEDIMENTO nao aceito: nao bloqueia duro (a
    cliente pode ter assinado em papel), mas exige o override explicito
    'sem_termo': true e deixa 'Realizado sem termo aceito' na trilha — mesma
    regra do portal do profissional.
    """
    if request.method != 'POST':
        return JsonResponse({'erro': 'Método não permitido'}, status=405)

    try:
        data = json.loads(request.body)
    except ValueError:  # JSONDecodeError e corpo que nao e UTF-8
        return JsonResponse({'erro': 'Dados inválidos'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'erro': 'Dados inválidos'}, status=400)

    # id_int: isdigit() aceitava '²' e o int() dava 500; bool/float/lista -> 400
    bruto_id = data.get('atendimento_id')
    atendimento_id = (
        id_int(bruto_id) if isinstance(bruto_id, (int, str)) and not isinstance(bruto_id, bool) else None
    )
    if atendimento_id is None:
        return JsonResponse({'erro': 'Agendamento inválido'}, status=400)
    novo_status = data.get('status')
    if not isinstance(novo_status, str) or not novo_status.strip():
        return JsonResponse({'erro': 'Status inválido'}, status=400)
    novo_status = novo_status.strip().upper()

    atendimento = (
        Atendimento.objects.select_related('cliente', 'procedimento')
        .filter(pk=atendimento_id).first()
    )
    if atendimento is None:
        return JsonResponse({'erro': 'Agendamento não encontrado'}, status=404)
    status_anterior = atendimento.status

    # Usa a FSM do model: valida a transicao e publica os eventos colaterais
    # (no-show, etc.). Evita transicoes invalidas como REALIZADO -> PENDENTE.
    metodos = {
        StatusAtendimento.CONFIRMADO: atendimento.confirmar,
        StatusAtendimento.REALIZADO: atendimento.marcar_realizado,
        StatusAtendimento.CANCELADO: atendimento.cancelar,
        StatusAtendimento.FALTOU: atendimento.marcar_falta,
        StatusAtendimento.AGENDADO: atendimento.aprovar,
    }
    acao = metodos.get(novo_status)
    if acao is None:
        return JsonResponse(
            {'erro': f'Transição para "{novo_status}" não suportada.'}, status=400
        )

    sem_termo = []
    if novo_status == StatusAtendimento.REALIZADO and status_anterior in (StatusAtendimento.AGENDADO, StatusAtendimento.CONFIRMADO):
        sem_termo = termos_pendentes(
            atendimento.cliente, atendimento.procedimento, so_procedimento=True,
        )
    if sem_termo and data.get('sem_termo') is not True:
        return JsonResponse({
            'erro': 'A cliente ainda não aceitou o termo do procedimento.',
            'termo_pendente': True,
            'codigo': 'termo_pendente',
        }, status=409)

    try:
        acao(by_user=request.user)
    except Atendimento.TransicaoInvalida as exc:
        return JsonResponse({'erro': str(exc)}, status=400)
    except DatabaseError:
        logger.exception('atualizar_status_falhou', extra={'atendimento_id': atendimento.pk})
        return JsonResponse({'erro': 'Ocorreu um erro interno. Tente novamente.'}, status=500)

    registrar_log(
        request.user,
        f'Status alterado: {status_anterior} → {novo_status}',
        'atendimento',
        atendimento.pk,
        {'status_anterior': status_anterior, 'status_novo': novo_status,
         'cliente': atendimento.cliente_id},
        request=request,
    )
    if sem_termo:
        registrar_log(
            request.user, 'Realizado sem termo aceito', 'atendimento', atendimento.pk,
            {'termos': [t.pk for t in sem_termo]}, request=request,
        )

    if novo_status == StatusAtendimento.CANCELADO:
        avisar_cancelamento(atendimento)

    return JsonResponse({
        'sucesso': True,
        'status_anterior': status_anterior,
        'status_novo': novo_status,
    })


@staff_required
@require_POST
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_desfazer_realizacao(request, pk):
    """Realizado marcado por engano -> Agendado, Faltou ou Cancelado, com motivo obrigatorio.
    Estorna comissao pendente e cashback, devolve a sessao do pacote e cancela o retorno sugerido
    (services/atendimento); comissao ja paga recusa."""
    at = get_object_or_404(Atendimento.objects.select_related('cliente'), pk=pk)
    voltar = redirect(safe_next(request, request.META.get('HTTP_REFERER'), 'aranha:painel_agendamentos'))
    destinos = {'AGENDADO': StatusAtendimento.AGENDADO, 'FALTOU': StatusAtendimento.FALTOU,
                'CANCELADO': StatusAtendimento.CANCELADO}
    destino = destinos.get((request.POST.get('destino') or '').upper())
    if destino is None:
        messages.error(request, 'Escolha para onde o atendimento volta: agendado, faltou ou cancelado.')
        return voltar
    try:
        at.desfazer_realizacao(destino, request.POST.get('motivo', ''), by_user=request.user)
    except RegraDeNegocio as e:
        messages.error(request, e.mensagem)
        return voltar
    messages.success(request, f'Realização de {at.cliente.nome} desfeita: agora {destino.label.lower()}.')
    return voltar
