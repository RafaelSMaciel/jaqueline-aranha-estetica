"""Views para gestao de pacotes (catalogo, venda, cancelamento e prorrogacao da compra).

Validacao nos forms (forms/financeiro) e regra nos casos de uso (CompraPacote.vender,
services/pacotes.cancelar_compra / prorrogar_validade): a view le, chama e mostra.
"""
import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ...decorators import staff_required
from ...excecoes import RegraDeNegocio
from ...forms.financeiro import CancelarCompraForm, PacoteForm, ProrrogarValidadeForm
from ...forms.painel import avisar_erros, parse_preco
from ...models import (
    Cliente,
    ItemPacote,
    Pacote,
    CompraPacote,
    Procedimento,
)
from ...services import pacotes as pacotes_service
from ...utils.auditoria import registrar_log
from ...utils.parse import id_int

logger = logging.getLogger(__name__)


def _itens_digitados(post):
    """Linhas de item como vieram (procedimento como int p/ comparar com o pk; quantidade como texto)."""
    linhas = []
    for proc_id, qtd in zip(post.getlist('procedimento_ids'), post.getlist('quantidades'), strict=False):
        linhas.append({'procedimento_id': id_int(proc_id), 'quantidade': qtd})
    return linhas or [{'procedimento_id': None, 'quantidade': '1'}]


def _valores_digitados(post):
    return {
        'nome': post.get('nome', ''), 'descricao': post.get('descricao', ''),
        'preco_total': post.get('preco_total', ''), 'validade_meses': post.get('validade_meses', ''),
        'ativo': post.get('ativo') in ('1', 'on'), 'itens': _itens_digitados(post),
    }


def _contexto_lista():
    pacotes = list(
        Pacote.objects.prefetch_related('itens__procedimento').annotate(
            total_vendas=Count('comprapacote'),
        ).order_by('-ativo', 'nome')
    )
    for pacote in pacotes:
        # o que o modal de edicao mostra (o salvo; no envio recusado, o digitado)
        pacote.valores = {
            'nome': pacote.nome, 'descricao': pacote.descricao or '', 'preco_total': pacote.preco_total,
            'validade_meses': pacote.validade_meses, 'ativo': pacote.ativo,
            'itens': [{'procedimento_id': i.procedimento_id, 'quantidade': i.quantidade_sessoes}
                      for i in pacote.itens.all()],
        }
        pacote.erros = {}

    return {
        'pacotes': pacotes,
        'pacotes_ativos': [p for p in pacotes if p.ativo],
        'procedimentos': Procedimento.objects.filter(ativo=True).order_by('nome'),
        # Edicao lista tambem inativos: item existente nao pode sumir do select
        'procedimentos_todos': Procedimento.objects.order_by('-ativo', 'nome'),
        # Renderizado UMA vez (modal unico de venda), nao por pacote.
        'clientes_ativos': Cliente.objects.filter(ativo=True).only('pk', 'nome', 'telefone').order_by('nome'),
        # modal de novo pacote: em branco (no envio recusado, o digitado + erros)
        'novo': {'valores': {'validade_meses': '12', 'itens': [{'procedimento_id': None, 'quantidade': '1'}]},
                 'erros': {}},
    }


def _erros_do_form(form) -> dict:
    """{campo: 1a mensagem} p/ o <c-campo erro=...>; erro geral do PacoteForm (clean) e dos itens."""
    erros = {campo: str(lista[0]) for campo, lista in form.errors.items() if campo != '__all__'}
    if form.non_field_errors():
        erros['itens'] = str(form.non_field_errors()[0])
    return erros


def _devolver_com_erro(request, form, editar=None):
    """Envio recusado: a lista volta SEM redirect, com o modal aberto, o digitado e o erro no campo.

    Antes o redirect fechava o modal e perdia tudo, inclusive as linhas de item. Os erros do form
    vao p/ o aviso do topo (avisar_erros, com o rotulo do campo) e p/ dentro do modal (o resto da
    tela fica inerte com ele aberto). editar = o pacote do modal de edicao; None = novo pacote."""
    avisar_erros(request, form)
    erros = _erros_do_form(form)
    erros['geral'] = next(iter(erros.values()), 'Confira os dados do pacote.')
    contexto = _contexto_lista()
    valores = _valores_digitados(request.POST)
    if editar is None:
        contexto['novo'] = {'valores': valores, 'erros': erros}
    else:
        for pacote in contexto['pacotes']:
            if pacote.pk == editar.pk:
                if 'procedimento_ids' not in request.POST:   # form sem a lista: itens intocados
                    valores['itens'] = pacote.valores['itens']
                pacote.valores, pacote.erros, pacote.reabrir = valores, erros, True
    return render(request, 'painel/pacotes.html', contexto)


@staff_required
def admin_pacotes(request):
    """Lista todos os pacotes com itens e vendas."""
    return render(request, 'painel/pacotes.html', _contexto_lista())


def _gravar_itens(pacote, itens):
    """Substitui os itens do CATALOGO (compras ja feitas tem os proprios itens congelados)."""
    ItemPacote.objects.filter(pacote=pacote).delete()
    ItemPacote.objects.bulk_create([
        ItemPacote(pacote=pacote, procedimento_id=pid, quantidade_sessoes=qtd) for pid, qtd in itens
    ])


@staff_required
@require_POST
def admin_criar_pacote(request):
    """Cria pacote do catalogo (ao menos 1 procedimento)."""
    form = PacoteForm(request.POST)
    if not form.is_valid():
        return _devolver_com_erro(request, form)
    dados = form.cleaned_data
    try:
        with transaction.atomic():
            pacote = Pacote.objects.create(
                nome=dados['nome'], descricao=dados['descricao'], preco_total=dados['preco_total'],
                validade_meses=dados['validade_meses'], ativo=True,
            )
            _gravar_itens(pacote, dados['itens'])
        registrar_log(request.user, f'Criou pacote: {pacote.nome}', 'pacote', pacote.pk, request=request)
        messages.success(request, f'Pacote "{pacote.nome}" criado com sucesso!')
    except (DatabaseError, ValidationError):
        logger.exception('pacote_criar_falhou')
        messages.error(request, 'Erro ao criar pacote.')
    return redirect('aranha:admin_pacotes')


@staff_required
@require_POST
def admin_editar_pacote(request, pk):
    """Edita pacote do catalogo (dados, itens e ativo/inativo) — sempre.

    A venda congela nome e itens (CompraPacote.pacote_nome + CompraPacoteItem,
    migration 0052): editar aqui so vale p/ vendas futuras; quem ja comprou
    mantem o que comprou (saldo, debito e comissao leem os itens congelados) e
    a validade gravada em data_expiracao.
    """
    pacote = get_object_or_404(Pacote, pk=pk)
    form = PacoteForm(request.POST, editando=True)
    if not form.is_valid():
        return _devolver_com_erro(request, form, editar=pacote)
    dados = form.cleaned_data
    try:
        with transaction.atomic():
            pacote.nome = dados['nome']
            pacote.descricao = dados['descricao']
            pacote.preco_total = dados['preco_total']
            pacote.validade_meses = dados['validade_meses']
            pacote.ativo = dados['ativo']
            pacote.save()
            if dados['itens'] is not None:
                _gravar_itens(pacote, dados['itens'])
        registrar_log(request.user, f'Editou pacote: {pacote.nome}', 'pacote', pacote.pk, request=request)
        messages.success(request, f'Pacote "{pacote.nome}" atualizado!')
    except (DatabaseError, ValidationError):
        logger.exception('pacote_editar_falhou', extra={'pacote_id': pacote.pk})
        messages.error(request, 'Erro ao editar pacote.')
    return redirect('aranha:admin_pacotes')


@staff_required
@require_POST
def admin_vender_pacote(request):
    """Vende pacote para um cliente."""
    pacote_id = id_int(request.POST.get('pacote_id'))
    cliente_id = id_int(request.POST.get('cliente_id'))
    if pacote_id is None or cliente_id is None:
        messages.error(request, 'Selecione o pacote e o cliente.')
        return redirect('aranha:admin_pacotes')

    # 404 real (pacote/cliente inexistente) fica fora do try de escrita.
    pacote = get_object_or_404(Pacote, pk=pacote_id)
    cliente = get_object_or_404(Cliente, pk=cliente_id)

    if not pacote.ativo:
        messages.error(request, f'O pacote "{pacote.nome}" está inativo e não pode ser vendido.')
        return redirect('aranha:admin_pacotes')

    try:
        valor_pago = parse_preco(request.POST.get('valor_pago', ''))
    except ValueError:
        valor_pago = None
    if valor_pago is None:
        messages.error(request, 'Valor pago inválido.')
        return redirect('aranha:admin_pacotes')

    try:
        # vender() congela nome e itens do catalogo (CompraPacoteItem) e o valor
        # de cada sessao (rateio pelo preco de tabela); data_expiracao fica a cargo de
        # CompraPacote.save() (localdate + validade em meses civis).
        pc = CompraPacote.vender(pacote, cliente, valor_pago)
        # sem o nome da cliente no texto (o log sobrevive ao esquecimento LGPD)
        registrar_log(
            request.user,
            f'Vendeu pacote "{pacote.nome}"',
            'compra_pacote', pc.pk, {'cliente': cliente.pk}, request=request,
        )
        messages.success(request, f'Pacote vendido para {cliente.nome}!')
    except RegraDeNegocio as e:
        messages.error(request, e.mensagem)
    except (DatabaseError, ValidationError):
        logger.exception('pacote_vender_falhou', extra={'pacote_id': pacote.pk})
        messages.error(request, 'Erro ao vender pacote.')

    return redirect('aranha:admin_pacotes')


@staff_required
@require_POST
def admin_cancelar_compra_pacote(request, pk):
    """Cancela uma compra de pacote ATIVA (desistencia) com o valor devolvido em dinheiro e o motivo
    (services/pacotes.cancelar_compra). O reembolso deduz a receita do mes do cancelamento."""
    compra = get_object_or_404(CompraPacote.objects.select_related('cliente'), pk=pk)
    destino = redirect('aranha:admin_cliente_detalhe', pk=compra.cliente_id)
    form = CancelarCompraForm(request.POST, compra=compra)
    if not form.is_valid():
        avisar_erros(request, form)
        return destino
    try:
        pacotes_service.cancelar_compra(
            compra, form.cleaned_data['valor_reembolsado'], motivo=form.cleaned_data['observacao'],
            por=request.user, request=request,
        )
    except RegraDeNegocio as exc:
        messages.warning(request, exc.mensagem)
        return destino
    messages.success(request, f'Pacote "{compra.pacote_nome}" cancelado.')
    return destino


@staff_required
@require_POST
def admin_prorrogar_compra_pacote(request, pk):
    """Prorroga a validade da compra (ATIVA ou EXPIRADA; EXPIRADA volta a ATIVA), com motivo e
    auditoria de/para (services/pacotes.prorrogar_validade). Painel = so ADMIN (staff_required)."""
    compra = get_object_or_404(CompraPacote, pk=pk)
    destino = redirect('aranha:admin_cliente_detalhe', pk=compra.cliente_id)
    form = ProrrogarValidadeForm(request.POST)
    if not form.is_valid():
        avisar_erros(request, form)
        return destino
    try:
        pacotes_service.prorrogar_validade(
            compra, form.cleaned_data['nova_data'], motivo=form.cleaned_data['motivo'],
            por=request.user, request=request,
        )
    except RegraDeNegocio as exc:
        messages.error(request, exc.mensagem)
        return destino
    messages.success(request, f'Validade do pacote "{compra.pacote_nome}" prorrogada até '
                              f'{compra.data_expiracao:%d/%m/%Y}.')
    return destino
