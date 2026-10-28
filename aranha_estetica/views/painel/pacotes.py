"""Views para gestao de pacotes (CRUD admin + venda)."""
import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ...decorators import staff_required
from ...excecoes import RegraDeNegocio
from ...models import (
    Cliente,
    ItemPacote,
    Pacote,
    CompraPacote,
    Procedimento,
)
from ...utils.auditoria import registrar_log
from ...utils.parse import id_int
from ...forms.painel import parse_preco as _parse_preco

logger = logging.getLogger(__name__)


def _parse_valor(valor):
    """'1350,50' / '1350.50' -> Decimal em [0, VALOR_MAX]. ValueError se vazio/invalido.

    Mesmo parser dos precos do painel (forms.painel.parse_preco): teto
    dos campos conferido antes do quantize ('1e30' dava 500).
    """
    numero = _parse_preco(valor)
    if numero is None:
        raise ValueError('valor invalido')
    return numero


def _parse_validade(valor):
    meses = int(valor)
    if meses < 1 or meses > 120:
        raise ValueError('validade invalida')
    return meses


def _ler_itens(post):
    """[(procedimento_id, quantidade)] do POST; linha com procedimento vazio e ignorada.

    ValueError se quantidade invalida ou procedimento repetido
    (ItemPacote e unico por pacote+procedimento).
    """
    itens, vistos = [], set()
    proc_ids = post.getlist('procedimento_ids')
    qtds = post.getlist('quantidades')
    for proc_id, qtd in zip(proc_ids, qtds, strict=False):
        if not proc_id:
            continue
        pid, quantidade = id_int(proc_id), int(qtd)
        if pid is None:
            raise ValueError('procedimento invalido')
        if quantidade < 1:
            raise ValueError('quantidade invalida')
        if pid in vistos:
            raise ValueError('procedimento repetido')
        vistos.add(pid)
        itens.append((pid, quantidade))
    if itens:
        existentes = set(
            Procedimento.objects.filter(pk__in=vistos).values_list('pk', flat=True)
        )
        if existentes != vistos:
            raise ValueError('procedimento inexistente')
    return itens


MSG_DADOS_INVALIDOS = 'Dados inválidos: verifique preço, validade e itens (sem procedimento repetido).'


def _erros_dos_campos(post, valor_padrao=None, validade_padrao='12'):
    """Mesmos parsers da gravacao, campo a campo, so p/ dizer na tela QUAL campo foi recusado."""
    erros = {}
    try:
        _parse_valor(post.get('preco_total', valor_padrao))
    except (ValueError, TypeError):
        erros['preco_total'] = 'Informe o preço total, ex.: 1350,00.'
    try:
        _parse_validade(post.get('validade_meses', validade_padrao))
    except (ValueError, TypeError):
        erros['validade_meses'] = 'Validade de 1 a 120 meses.'
    try:
        _ler_itens(post)
    except (ValueError, TypeError):
        erros['itens'] = 'Confira os itens: quantidade de 1 sessão ou mais e sem procedimento repetido.'
    return erros


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


def _devolver_com_erro(request, mensagem, erros, editar=None):
    """Envio recusado: a lista volta SEM redirect, com o modal aberto, o digitado e o erro no campo.

    Antes o redirect fechava o modal e perdia tudo, inclusive as linhas de item. `mensagem` (o texto
    de sempre) vai p/ o aviso do topo e p/ dentro do modal (o resto da tela fica inerte com ele
    aberto). editar = o pacote do modal de edicao; None = modal de novo pacote."""
    messages.error(request, mensagem)
    erros = {**erros, 'geral': mensagem}
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


@staff_required
def admin_criar_pacote(request):
    """Cria novo pacote via POST."""
    if request.method != 'POST':
        return redirect('aranha:admin_pacotes')

    nome = request.POST.get('nome', '').strip()
    descricao = request.POST.get('descricao', '').strip()

    if not nome:
        return _devolver_com_erro(request, 'Nome do pacote é obrigatório.',
                                  {'nome': 'Informe o nome do pacote.'})

    try:
        preco_total = _parse_valor(request.POST.get('preco_total', ''))
        validade_meses = _parse_validade(request.POST.get('validade_meses', '12'))
        itens = _ler_itens(request.POST)
    except (ValueError, TypeError):
        return _devolver_com_erro(request, MSG_DADOS_INVALIDOS, _erros_dos_campos(request.POST, ''))
    if not itens:
        return _devolver_com_erro(request, 'Inclua ao menos um procedimento no pacote.',
                                  {'itens': 'Escolha ao menos um procedimento.'})

    try:
        with transaction.atomic():
            pacote = Pacote.objects.create(
                nome=nome,
                descricao=descricao,
                preco_total=preco_total,
                validade_meses=validade_meses,
                ativo=True,
            )
            ItemPacote.objects.bulk_create([
                ItemPacote(pacote=pacote, procedimento_id=pid, quantidade_sessoes=qtd)
                for pid, qtd in itens
            ])

        registrar_log(request.user, f'Criou pacote: {pacote.nome}', 'pacote', pacote.pk, request=request)
        messages.success(request, f'Pacote "{nome}" criado com sucesso!')
    except (DatabaseError, ValidationError) as e:
        logger.error(f'Erro ao criar pacote: {e}', exc_info=True)
        messages.error(request, 'Erro ao criar pacote.')

    return redirect('aranha:admin_pacotes')


@staff_required
def admin_editar_pacote(request, pk):
    """Edita pacote do catalogo (dados, itens e ativo/inativo) — sempre.

    A venda congela nome e itens (CompraPacote.pacote_nome + CompraPacoteItem,
    migration 0052): editar aqui so vale p/ vendas futuras; quem ja comprou
    mantem o que comprou (saldo, debito e comissao leem os itens congelados) e
    a validade gravada em data_expiracao.
    """
    pacote = get_object_or_404(Pacote, pk=pk)

    if request.method != 'POST':
        return redirect('aranha:admin_pacotes')

    nome = request.POST.get('nome', pacote.nome).strip()
    if not nome:
        return _devolver_com_erro(request, 'Nome do pacote é obrigatório.',
                                  {'nome': 'Informe o nome do pacote.'}, editar=pacote)

    try:
        preco_total = _parse_valor(request.POST.get('preco_total', str(pacote.preco_total)))
        validade_meses = _parse_validade(request.POST.get('validade_meses', pacote.validade_meses))
        itens = _ler_itens(request.POST)
    except (ValueError, TypeError):
        erros = _erros_dos_campos(request.POST, str(pacote.preco_total), pacote.validade_meses)
        return _devolver_com_erro(request, MSG_DADOS_INVALIDOS, erros, editar=pacote)
    if 'procedimento_ids' in request.POST and not itens:
        return _devolver_com_erro(request, 'O pacote precisa de ao menos um procedimento.',
                                  {'itens': 'Escolha ao menos um procedimento.'}, editar=pacote)

    try:
        with transaction.atomic():
            pacote.nome = nome
            pacote.descricao = request.POST.get('descricao', '').strip()
            pacote.preco_total = preco_total
            pacote.validade_meses = validade_meses
            pacote.ativo = request.POST.get('ativo') in ('1', 'on')
            pacote.save()

            # Itens do catalogo: substitui o conjunto (form sem a lista de itens =
            # mantem os atuais). Compras ja feitas tem os proprios itens congelados.
            if 'procedimento_ids' in request.POST:
                ItemPacote.objects.filter(pacote=pacote).delete()
                ItemPacote.objects.bulk_create([
                    ItemPacote(pacote=pacote, procedimento_id=pid, quantidade_sessoes=qtd)
                    for pid, qtd in itens
                ])

        registrar_log(request.user, f'Editou pacote: {pacote.nome}', 'pacote', pacote.pk, request=request)
        messages.success(request, f'Pacote "{pacote.nome}" atualizado!')
    except (DatabaseError, ValidationError) as e:
        logger.error(f'Erro ao editar pacote: {e}', exc_info=True)
        messages.error(request, 'Erro ao editar pacote.')

    return redirect('aranha:admin_pacotes')


@staff_required
def admin_vender_pacote(request):
    """Vende pacote para um cliente."""
    if request.method != 'POST':
        return redirect('aranha:admin_pacotes')

    # id_int: isdigit() aceitava '²' e o int() dava 500
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
        valor_pago = _parse_valor(request.POST.get('valor_pago', ''))
    except ValueError:
        messages.error(request, 'Valor pago inválido.')
        return redirect('aranha:admin_pacotes')

    try:
        # vender() congela nome e itens do catalogo (CompraPacoteItem) e o valor
        # de cada sessao; data_expiracao fica a cargo de CompraPacote.save()
        # (localdate + validade em meses civis).
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
    except (DatabaseError, ValidationError) as e:
        logger.error(f'Erro ao vender pacote: {e}', exc_info=True)
        messages.error(request, 'Erro ao vender pacote.')

    return redirect('aranha:admin_pacotes')


@staff_required
@require_POST
def admin_cancelar_compra_pacote(request, pk):
    """Cancela uma compra de pacote ATIVA (ex.: desistencia com reembolso).

    Exige o valor devolvido (0 a valor_pago) e a observacao do acordo, que vao
    para a auditoria. Sessoes ja usadas continuam registradas; so compra ATIVA
    pode ser cancelada.
    """
    compra = get_object_or_404(CompraPacote.objects.select_related('cliente'), pk=pk)
    destino = redirect('aranha:admin_cliente_detalhe', pk=compra.cliente_id)
    observacao = (request.POST.get('observacao') or '').strip()[:500]
    if not observacao:
        messages.error(request, 'Descreva o motivo e o reembolso/acordo para cancelar o pacote.')
        return destino
    try:
        reembolso = _parse_valor(request.POST.get('valor_reembolsado'))
    except ValueError:
        reembolso = None
    if reembolso is None or reembolso > compra.valor_pago:
        from ...utils.formatos import formatar_brl
        messages.error(
            request,
            'Informe o valor devolvido à cliente (0 se nada foi devolvido), '
            f'até o valor pago de {formatar_brl(compra.valor_pago)}.',
        )
        return destino

    # valor_reembolsado gravado na compra (CHECK 0 <= reembolso <= valor_pago):
    # a receita dos dashboards e valor_pago - valor_reembolsado.
    with transaction.atomic():
        atualizadas = CompraPacote.objects.filter(pk=compra.pk, status=CompraPacote.STATUS_ATIVO).update(
            status=CompraPacote.STATUS_CANCELADO, valor_reembolsado=reembolso,
        )
    if not atualizadas:
        messages.warning(
            request, f'O pacote "{compra.pacote_nome}" não está ativo ({compra.get_status_display().lower()}).',
        )
        return destino

    # Reembolso tambem na auditoria (quem cancelou e o acordo). Sem o nome da
    # cliente no texto (LGPD).
    registrar_log(
        request.user,
        f'Cancelou pacote "{compra.pacote_nome}"',
        'compra_pacote', compra.pk,
        {'cliente': compra.cliente_id, 'observacao': observacao,
         'valor_pago': str(compra.valor_pago), 'valor_reembolsado': str(reembolso),
         'sessoes_restantes': compra.sessoes_restantes()},
        request=request,
    )
    messages.success(request, f'Pacote "{compra.pacote_nome}" cancelado.')
    return destino
