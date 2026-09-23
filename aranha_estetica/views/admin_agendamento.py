"""Agendamento interno pela recepcao (painel) — sem o OTP da cliente.

A recepcao agenda pelo telefone/WhatsApp/balcao: cliente existente (busca) ou
nova (nome + telefone validado), procedimento, profissional e horario. O
horario passa pelo MESMO SlotService do site (services.disponibilidade) e o
valor sai de utils.precos.preco_com_promocao na data do atendimento (a
recepcao pode informar outro valor combinado; fica registrado). Nasce
AGENDADO — a propria clinica confirmou — com auditoria.
"""
import logging
from datetime import datetime, timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import DatabaseError, IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from ..decorators import staff_required
from ..models import Atendimento, Cliente, Procedimento, Profissional
from ..services.disponibilidade import SlotService, profissionais_para, slot_disponivel
from ..utils.audit import registrar_log
from ..utils.busca import q_busca_cliente
from ..utils.datas import hoje
from ..utils.precos import preco_com_promocao
from ..utils.security import safe_next
from ..validators import validate_telefone_br
from .admin_management import _normalizar_telefone_br, _parse_preco

logger = logging.getLogger(__name__)

ACOES = ('buscar', 'horarios', 'agendar')


def _eh_sobreposicao(exc) -> bool:
    """IntegrityError da exclusion constraint do PG (23P01) = horario ja ocupado."""
    causa = getattr(exc, '__cause__', None)
    return getattr(causa, 'pgcode', None) == '23P01' or 'excl_atendimento_sobreposicao' in str(exc)


def _int(valor):
    valor = str(valor or '').strip()
    return int(valor) if valor.isdigit() else None


def _data(valor):
    try:
        return datetime.strptime((valor or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def _hora(valor):
    try:
        return datetime.strptime((valor or '').strip(), '%H:%M').time()
    except ValueError:
        return None


def _validar_cliente_novo(nome, telefone_raw, email):
    """(dados_limpos, erros) do cadastro rapido — mesmas regras da ficha do cliente."""
    erros = []
    nome = (nome or '').strip()
    if not nome:
        erros.append('Informe o nome da cliente nova.')
    elif len(nome) > 150:
        erros.append('O nome aceita no máximo 150 caracteres.')

    telefone = _normalizar_telefone_br(telefone_raw)
    try:
        validate_telefone_br(telefone)
    except ValidationError:
        erros.append('Telefone inválido: informe DDD + número (10 ou 11 dígitos).')
    else:
        existente = Cliente.objects.filter(telefone=telefone).first()
        if existente is not None:
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
            if Cliente.objects.filter(email__iexact=email).exists():
                erros.append('Este e-mail já pertence a outra cliente: busque a cliente cadastrada.')
    return {'nome': nome, 'telefone': telefone, 'email': email}, erros


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
    cliente_id = _int(dados.get('cliente_id') or dados.get('cliente'))
    if cliente_id:
        cliente = Cliente.objects.filter(pk=cliente_id, ativo=True).first()
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

    resultados = []
    if q:
        resultados = list(
            Cliente.objects.filter(ativo=True).filter(q_busca_cliente(q)).order_by('nome')[:20]
        )

    slots, preco = [], None
    if procedimento and profissional and data:
        slots = SlotService.slots_livres(profissional, data, procedimento)
        final, promo, cheio = preco_com_promocao(procedimento, profissional, data)
        preco = {'final': final, 'promocao': promo, 'cheio': cheio}

    context = {
        'q': q,
        'resultados': resultados,
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
            dados_novo, erros_novo = _validar_cliente_novo(novo['nome'], novo['telefone'], novo['email'])
            erros.extend(erros_novo)
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
        # Mesmo SlotService do site: expediente, folga, bloqueio, buffer, antecedencia
        if not slot_disponivel(profissional, procedimento, inicio):
            erros.append('Este horário não está livre para o profissional. Escolha outro da lista.')

    if erros:
        for erro in erros:
            messages.error(request, erro)
        return render(request, 'painel/agendamento_novo.html', context)

    final, promo, cheio = preco['final'], preco['promocao'], preco['cheio']
    valor_original, descricao_preco = None, None
    if valor_informado is not None and valor_informado != final:
        # Valor combinado com a cliente (desconto negociado, cortesia...)
        valor_cobrado, promo = valor_informado, None
        valor_original = cheio
        descricao_preco = 'Valor combinado pela recepção'
    else:
        valor_cobrado = final
        if promo is not None:
            valor_original = cheio
            descricao_preco = f'Promoção {promo.nome}'

    cliente_novo = cliente is None
    try:
        with transaction.atomic():
            if cliente_novo:
                cliente = Cliente.objects.create(**dados_novo)
            atendimento = Atendimento.objects.create(
                cliente=cliente,
                profissional=profissional,
                procedimento=procedimento,
                data_hora_inicio=inicio,
                data_hora_fim=inicio + timedelta(minutes=procedimento.duracao_minutos),
                valor_cobrado=valor_cobrado,
                valor_original=valor_original,
                promocao=promo,
                descricao_preco=descricao_preco,
                status=Atendimento.STATUS_AGENDADO,
            )
    except IntegrityError as exc:
        if _eh_sobreposicao(exc):
            messages.error(request, 'Este horário acabou de ser ocupado. Escolha outro.')
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
        f'Agendou pelo painel: {cliente.nome} — {procedimento.nome}',
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
    return redirect(f"{reverse('aranha:painel_agendamentos')}?data={data.isoformat()}")


@staff_required
@require_POST
@ratelimit(key='user', rate='60/m', method='POST', block=True)
def admin_atendimento_valor(request, pk):
    """Registra o valor cobrado de um atendimento (desconto combinado, 'a consultar').

    Antes de realizado: so grava. Realizado sem comissao lancada: grava e
    calcula a comissao (valor vazio nao gerava nenhuma). Realizado com
    comissao ja lancada, cancelado/faltou/reagendado e retorno gratuito
    (sempre 0): recusado com a explicacao — nada muda em silencio.
    """
    from ..models import MovimentoComissao
    from ..services.comissao_service import ComissaoService

    at = get_object_or_404(Atendimento.objects.select_related('cliente', 'procedimento'), pk=pk)
    voltar = redirect(safe_next(request, request.META.get('HTTP_REFERER'), 'aranha:painel_agendamentos'))
    try:
        valor = _parse_preco(request.POST.get('valor'))
    except ValueError:
        valor = None
    if valor is None:
        messages.error(request, 'Valor inválido: use números, ex.: 150,00.')
        return voltar
    if at.status in ('CANCELADO', 'FALTOU', 'REAGENDADO'):
        messages.error(request, f'Atendimento {at.get_status_display().lower()}: o valor não pode ser alterado.')
        return voltar
    if at.eh_retorno and valor != 0:
        messages.error(request, 'Retorno gratuito fica sempre com valor zero.')
        return voltar
    if at.status == Atendimento.STATUS_REALIZADO and MovimentoComissao.objects.filter(atendimento=at).exists():
        messages.error(
            request,
            'Este atendimento já tem comissão lançada: o valor não pode mudar por aqui '
            '(ajuste a comissão no financeiro).',
        )
        return voltar

    antigo = at.valor_cobrado
    with transaction.atomic():
        if at.valor_original is None and antigo is not None and antigo != valor:
            at.valor_original = antigo
        at.valor_cobrado = valor
        at.descricao_preco = 'Valor combinado pela recepção'
        at.save(update_fields=['valor_cobrado', 'valor_original', 'descricao_preco', 'atualizado_em'])
        comissao = None
        if at.status == Atendimento.STATUS_REALIZADO:
            comissao = ComissaoService.calcular_comissao(at)

    registrar_log(
        request.user, 'Registrou valor cobrado', 'atendimento', at.pk,
        {'de': str(antigo) if antigo is not None else None, 'para': str(valor),
         'comissao': comissao.pk if comissao else None},
        request=request,
    )
    from ..services.agendamento_service import formatar_brl
    messages.success(request, f'Valor de {at.cliente.nome} registrado: {formatar_brl(valor)}.')
    return voltar
