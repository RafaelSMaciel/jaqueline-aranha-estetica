"""Agenda do painel: agendamento pela recepcao, valor cobrado, select de status e desfazer
realizacao.

A recepcao agenda pelo telefone/WhatsApp/balcao (sem o OTP da cliente): cliente existente (busca)
ou nova (ClienteRapidoForm), procedimento, profissional e horario (AgendamentoPainelForm: o MESMO
SlotService do site). O caso de uso e services.agenda.agendar_pela_recepcao (preco do dia ou
combinado, AGENDADO, CONFIRMACAO a cliente, trilha); as views so traduzem HTTP.
"""
import json
import logging

from django.contrib import messages
from django.db import DatabaseError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from ...decorators import escrita_painel, staff_required
from ...excecoes import RegraDeNegocio, TermoPendente, TransicaoInvalida
from ...forms.agenda import AgendamentoPainelForm, ValorAtendimentoForm
from ...forms.cliente import ClienteRapidoForm
from ...forms.painel import avisar_erros
from ...models import Atendimento, Cliente
from ...models.agendamentos import StatusAtendimento
from ...services import agenda
from ...services import atendimento as casos_atendimento
from ...services.agendamento import AgendamentoService, avisar_cancelamento
from ...services.alertas import ids_com_ficha_pendente
from ...services.disponibilidade import SlotService
from ...utils.auditoria import registrar_log
from ...utils.busca import filtrar_clientes
from ...utils.datas import hoje
from ...utils.formatos import formatar_brl
from ...utils.parse import id_int
from ...utils.respostas import json_erro, json_ok, json_recusa
from .comum import voltar_para_origem

logger = logging.getLogger(__name__)

TEMPLATE_NOVO = 'painel/agendamento_novo.html'
MSG_FALHA_AGENDAR = 'Não foi possível salvar o agendamento. Tente novamente.'
MSG_FICHA_PENDENTE = (
    'Ficha de anamnese obrigatória pendente: use "Link da ficha" na agenda para a cliente '
    'responder antes do procedimento (ou confirme alergias e contraindicações no balcão).'
)


def _cliente_escolhida(request, dados):
    """(ativa, inativa) do radio da busca ou do atalho ?cliente= da ficha. Inativa nao agenda: o
    aviso explica e a tela leva a ficha p/ reativar (em vez de ignorar o atalho)."""
    pk = id_int(dados.get('cliente_id') or dados.get('cliente'))
    cliente = Cliente.objects.filter(pk=pk).first() if pk else None
    if cliente is not None and not cliente.ativo:
        messages.warning(request, f'{cliente.nome} está com o cadastro inativo: reative na ficha para agendar.')
        return None, cliente
    return cliente, None


def _buscar_clientes(q):
    """(ativas, inativas) da busca: inativas aparecem sem selecao, com o link p/ reativar na ficha."""
    if not q:
        return [], []
    ativas = list(filtrar_clientes(Cliente.objects.filter(ativo=True), q).order_by('nome')[:20])
    inativas = list(filtrar_clientes(Cliente.objects.filter(ativo=False), q).order_by('nome')[:5])
    return ativas, inativas


def _contexto_agendamento(request, dados, form) -> dict:
    """O que a tela mostra em qualquer etapa (busca, horarios livres do SlotService, preco do dia),
    sem validar nada: so 'agendar' valida."""
    q = (dados.get('q') or '').strip()[:100]
    resultados, inativas = _buscar_clientes(q)
    cliente, inativa = _cliente_escolhida(request, dados)
    procedimento, profissional, data = form.lido('procedimento'), form.lido('profissional'), form.lido('data')
    viu_horarios = bool(procedimento and profissional and data)
    return {
        'q': q, 'resultados': resultados, 'inativas': inativas,
        'cliente': cliente, 'cliente_inativa': inativa,
        'novo': {campo: (request.POST.get(f'novo_{campo}') or '').strip() for campo in ('nome', 'telefone', 'email')},
        'procedimentos': form.fields['procedimento'].queryset, 'procedimento': procedimento,
        'profissionais': form.fields['profissional'].queryset, 'profissional': profissional,
        'data': data, 'data_min': hoje(), 'hora': (dados.get('hora') or '').strip(),
        'valor': (dados.get('valor') or '').strip(),
        'slots': SlotService.slots_livres(profissional, data, procedimento) if viu_horarios else [],
        'preco': agenda.preco_do_dia(procedimento, profissional, data) if viu_horarios else None,
        'buscou': request.POST.get('acao') == 'buscar' or bool(q),
        'viu_horarios': viu_horarios,
    }


def _form_cliente_nova(request, context):
    """ClienteRapidoForm validado (None se a tela nao tem cliente nova a cadastrar) + o erro de
    quando nao ha cliente nenhuma. Dono inativo do telefone/e-mail vai p/ o aviso com a ficha."""
    if context['cliente'] is not None:
        return None, None
    novo = context['novo']
    if not (novo['nome'] or novo['telefone']):
        if context['cliente_inativa'] is not None:   # o aviso de cadastro inativo ja foi dado
            return None, 'Reative a cliente na ficha antes de agendar.'
        return None, 'Selecione a cliente na busca ou preencha o cadastro da cliente nova.'
    form = ClienteRapidoForm(request.POST)
    if not form.is_valid() and form.dono_inativo is not None:
        context['cliente_inativa'] = form.dono_inativo
    return form, None


def _mensagem_falha(exc) -> str:
    if isinstance(exc, RegraDeNegocio):
        return exc.mensagem
    logger.error('agendamento_painel_falha', exc_info=True)
    return MSG_FALHA_AGENDAR


@staff_required
@ratelimit(key='user', rate='60/m', method='POST', block=True)
def admin_agendamento_novo(request):
    """Tela unica (POST com `acao`: buscar | horarios | agendar).

    Busca e horarios so re-renderizam (telefone/nome digitados nao vao p/ a URL); `agendar` valida
    tudo de novo no servidor (Forms) e chama agenda.agendar_pela_recepcao.
    GET aceita ?cliente=<pk> (atalho da ficha do cliente) e ?data=AAAA-MM-DD.
    """
    dados = request.POST if request.method == 'POST' else request.GET
    form = AgendamentoPainelForm(dados)
    context = _contexto_agendamento(request, dados, form)
    if request.method != 'POST' or request.POST.get('acao') != 'agendar':
        return render(request, TEMPLATE_NOVO, context)

    form_cliente, sem_cliente = _form_cliente_nova(request, context)
    cliente_ok = sem_cliente is None and (form_cliente is None or form_cliente.is_valid())
    form.checar_horario = cliente_ok   # um erro de cada vez: horario so com a cliente resolvida
    if sem_cliente:
        messages.error(request, sem_cliente)
    if form_cliente is not None:
        avisar_erros(request, form_cliente)
    if not form.is_valid() or not cliente_ok:
        avisar_erros(request, form)
        return render(request, TEMPLATE_NOVO, context)

    d = form.cleaned_data
    try:
        at = agenda.agendar_pela_recepcao(
            cliente=context['cliente'], dados_cliente_nova=form_cliente and form_cliente.cleaned_data,
            profissional=d['profissional'], procedimento=d['procedimento'], inicio=d['inicio'],
            valor_informado=d['valor'], por=request.user, request=request,
        )
    except (RegraDeNegocio, DatabaseError) as exc:
        messages.error(request, _mensagem_falha(exc))
        return render(request, TEMPLATE_NOVO, context)

    messages.success(
        request,
        f'Agendado: {at.cliente.nome} — {at.procedimento.nome} com {at.profissional.nome} em '
        f'{timezone.localtime(at.data_hora_inicio).strftime("%d/%m/%Y às %H:%M")}.',
    )
    if ids_com_ficha_pendente([at]):   # o agendamento pela recepcao nao coleta a ficha do site
        messages.warning(request, MSG_FICHA_PENDENTE)
    return redirect(f"{reverse('aranha:painel_agendamentos')}?data={d['data'].isoformat()}")


@escrita_painel
def admin_atendimento_valor(request, pk):
    """'Registrar valor' da agenda (desconto combinado, 'a consultar'): services.atendimento.
    registrar_valor_cobrado decide (status final e retorno recusam; realizado recalcula a comissao
    e libera o cashback); aqui o parse (ValorAtendimentoForm), a trilha e o aviso."""
    at = get_object_or_404(Atendimento.objects.select_related('cliente', 'procedimento'), pk=pk)
    form = ValorAtendimentoForm(request.POST)
    if not form.is_valid():
        avisar_erros(request, form)
        return voltar_para_origem(request)
    valor = form.cleaned_data['valor']
    try:
        r = casos_atendimento.registrar_valor_cobrado(
            at, valor, usar_credito=form.cleaned_data['usar_credito'], por=request.user)
    except RegraDeNegocio as recusa:
        messages.error(request, recusa.mensagem)
        return voltar_para_origem(request)
    except DatabaseError:
        logger.error('atendimento_valor_falha', exc_info=True, extra={'atendimento_id': at.pk})
        messages.error(request, 'Não foi possível registrar o valor. Confira o número e tente novamente.')
        return voltar_para_origem(request)

    registrar_log(
        request.user, 'Registrou valor cobrado', 'atendimento', at.pk,
        {'de': str(r.antigo) if r.antigo is not None else None, 'para': str(valor),
         'comissao': r.comissao.pk if r.comissao else None,
         'cashback': r.cashback.pk if r.cashback else None,
         'credito_usado': str(r.credito_usado.valor) if r.credito_usado else None},
        request=request,
    )
    abatido = f' (crédito de indicação abatido: {formatar_brl(r.credito_usado.valor)}).' if r.credito_usado else '.'
    messages.success(request, f'Valor de {at.cliente.nome} registrado: {formatar_brl(valor)}{abatido}')
    return voltar_para_origem(request)


# Select de status (partials/_select_status.html) -> metodo da FSM. AGENDADO nao e opcao do select:
# so chega nele a aprovacao de um pedido (AgendamentoService); Realizado tem caso de uso proprio.
ACOES_DO_SELECT = {
    StatusAtendimento.CONFIRMADO: 'confirmar',
    StatusAtendimento.CANCELADO: 'cancelar',
    StatusAtendimento.FALTOU: 'marcar_falta',
}
DECISOES_DE_PEDIDO = (StatusAtendimento.AGENDADO, StatusAtendimento.CONFIRMADO, StatusAtendimento.CANCELADO)


def _ler_pedido_status(corpo: bytes) -> tuple[int, str, bool]:
    """(atendimento_id, status, sem_termo) do JSON do select. RegraDeNegocio se invalido.

    id_int: isdigit() aceitava '²' e o int() dava 500; bool/float/lista -> invalido. O override do
    termo so vale com o booleano true (nao 'true' nem 1)."""
    try:
        dados = json.loads(corpo)   # JSONDecodeError e corpo que nao e UTF-8 sao ValueError
    except ValueError:
        raise RegraDeNegocio('Dados inválidos.', codigo='dados_invalidos') from None
    if not isinstance(dados, dict):
        raise RegraDeNegocio('Dados inválidos.', codigo='dados_invalidos')
    bruto = dados.get('atendimento_id')
    pk = id_int(bruto) if isinstance(bruto, (int, str)) and not isinstance(bruto, bool) else None
    if pk is None:
        raise RegraDeNegocio('Agendamento inválido.', codigo='atendimento_invalido')
    status = dados.get('status')
    if not isinstance(status, str) or not status.strip():
        raise RegraDeNegocio('Status inválido.', codigo='status_invalido')
    return pk, status.strip().upper(), dados.get('sem_termo') is True


def _aplicar_status(request, atendimento, novo_status: str, sem_termo: bool) -> None:
    """Uma porta por destino: pedido PENDENTE -> AgendamentoService (auditoria + e-mail a cliente);
    Realizado -> services.atendimento.marcar_realizado (regra do termo, igual ao portal); o resto ->
    FSM + trilha, e o cancelamento pela clinica avisa a cliente."""
    anterior = atendimento.status
    if anterior == StatusAtendimento.PENDENTE and novo_status in DECISOES_DE_PEDIDO:
        _decidir_pedido(atendimento, novo_status, by_user=request.user, request=request)
        return   # aprovar/recusar ja auditou e avisou a cliente
    if novo_status == StatusAtendimento.REALIZADO:
        casos_atendimento.marcar_realizado(atendimento, por=request.user, aceitar_sem_termo=sem_termo,
                                           request=request)
    elif novo_status in ACOES_DO_SELECT:
        getattr(atendimento, ACOES_DO_SELECT[novo_status])(by_user=request.user)
    else:
        raise TransicaoInvalida(f'Transição para "{novo_status}" não suportada.', codigo='status_nao_suportado')
    registrar_log(
        request.user, f'Status alterado: {anterior} → {novo_status}', 'atendimento', atendimento.pk,
        {'status_anterior': anterior, 'status_novo': novo_status, 'cliente': atendimento.cliente_id},
        request=request,
    )
    if novo_status == StatusAtendimento.CANCELADO:
        avisar_cancelamento(atendimento)


@escrita_painel
def admin_atualizar_status(request):
    """Select de status das listas do painel (AJAX, contrato de utils/respostas).

    Entrada invalida -> 400/404. Termo do procedimento nao aceito -> 409 'termo_pendente' (o front
    pede a confirmacao e reenvia com sem_termo=true, que fica na trilha). Realizado/Faltou antes do
    horario -> 409 com a mensagem da FSM; outra transicao recusada -> 400.
    """
    try:
        pk, novo_status, sem_termo = _ler_pedido_status(request.body)
    except RegraDeNegocio as invalido:
        return json_recusa(invalido)
    atendimento = Atendimento.objects.select_related('cliente', 'procedimento').filter(pk=pk).first()
    if atendimento is None:
        return json_erro('nao_encontrado', 'Agendamento não encontrado.', status=404)
    anterior = atendimento.status
    try:
        _aplicar_status(request, atendimento, novo_status, sem_termo)
    except TermoPendente as exc:
        return json_erro(exc.codigo, exc.mensagem, status=409, termos=[t.pk for t in exc.termos])
    except RegraDeNegocio as recusa:   # TransicaoInvalida da FSM, pedido ja decidido, efeito recusado
        return json_recusa(recusa, status=409 if recusa.codigo == 'desfecho_antes_do_inicio' else 400)
    except DatabaseError:
        logger.exception('atualizar_status_falhou', extra={'atendimento_id': atendimento.pk})
        return json_erro('erro_interno', 'Ocorreu um erro interno. Tente novamente.', status=500)
    return json_ok(status_anterior=anterior, status_novo=novo_status)


def _decidir_pedido(atendimento, novo_status, by_user=None, request=None) -> None:
    """Pedido PENDENTE pelo seletor de status = mesmo caso de uso dos botoes Aprovar/Recusar
    (AgendamentoService: auditoria + e-mail de confirmacao ou de cancelamento a cliente). Antes o
    seletor chamava a FSM direto e a cliente nunca sabia que o pedido foi aceito."""
    servico = AgendamentoService()
    with transaction.atomic():
        if novo_status == StatusAtendimento.CANCELADO:
            decidido = servico.rejeitar(atendimento, by_user=by_user, request=request)
        else:
            decidido = servico.aprovar(atendimento, by_user=by_user, request=request)
            if decidido and novo_status == StatusAtendimento.CONFIRMADO:
                atendimento.confirmar(by_user=by_user)
        if not decidido:
            raise Atendimento.TransicaoInvalida('Este pedido já foi decidido em outra tela.')


@escrita_painel
def admin_desfazer_realizacao(request, pk):
    """Realizado marcado por engano -> Agendado, Faltou ou Cancelado, com motivo obrigatorio.
    Estorna comissao pendente e cashback, devolve a sessao do pacote e cancela o retorno sugerido
    (services/atendimento); comissao ja paga recusa."""
    at = get_object_or_404(Atendimento.objects.select_related('cliente'), pk=pk)
    voltar = voltar_para_origem(request)
    bruto = (request.POST.get('destino') or '').strip().upper()
    destino = next((s for s in Atendimento.DESFAZER_REALIZACAO if s == bruto), None)
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
