"""Views para gestao de pacotes (CRUD admin + venda)."""
import logging
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from ..decorators import staff_required
from ..models import (
    Cliente,
    ItemPacote,
    Pacote,
    CompraPacote,
    Procedimento,
)
from ..utils.audit import registrar_log

logger = logging.getLogger(__name__)


def _parse_valor(valor):
    """'1350,50' / '1350.50' -> Decimal >= 0. ValueError se vazio/invalido."""
    try:
        numero = Decimal((valor or '').strip().replace(',', '.'))
    except InvalidOperation as exc:
        raise ValueError('valor invalido') from exc
    if not numero.is_finite() or numero < 0:
        raise ValueError('valor invalido')
    return numero.quantize(Decimal('0.01'))


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
        pid, quantidade = int(proc_id), int(qtd)
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


@staff_required
def admin_pacotes(request):
    """Lista todos os pacotes com itens e vendas."""
    pacotes = list(
        Pacote.objects.prefetch_related('itens__procedimento').annotate(
            total_vendas=Count('comprapacote'),
        ).order_by('-ativo', 'nome')
    )

    context = {
        'pacotes': pacotes,
        'pacotes_ativos': [p for p in pacotes if p.ativo],
        'procedimentos': Procedimento.objects.filter(ativo=True).order_by('nome'),
        # Edicao lista tambem inativos: item existente nao pode sumir do select
        'procedimentos_todos': Procedimento.objects.order_by('-ativo', 'nome'),
        # Renderizado UMA vez (modal unico de venda), nao por pacote.
        'clientes_ativos': Cliente.objects.filter(ativo=True).only('pk', 'nome', 'telefone').order_by('nome'),
    }
    return render(request, 'painel/pacotes.html', context)


@staff_required
def admin_criar_pacote(request):
    """Cria novo pacote via POST."""
    if request.method != 'POST':
        return redirect('aranha:admin_pacotes')

    nome = request.POST.get('nome', '').strip()
    descricao = request.POST.get('descricao', '').strip()

    if not nome:
        messages.error(request, 'Nome do pacote é obrigatório.')
        return redirect('aranha:admin_pacotes')

    try:
        preco_total = _parse_valor(request.POST.get('preco_total', ''))
        validade_meses = _parse_validade(request.POST.get('validade_meses', '12'))
        itens = _ler_itens(request.POST)
    except (ValueError, TypeError):
        messages.error(request, 'Dados inválidos: verifique preço, validade e itens (sem procedimento repetido).')
        return redirect('aranha:admin_pacotes')

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

        registrar_log(request.user, f'Criou pacote: {pacote.nome}', 'pacote', pacote.pk)
        messages.success(request, f'Pacote "{nome}" criado com sucesso!')
    except (DatabaseError, ValidationError) as e:
        logger.error(f'Erro ao criar pacote: {e}', exc_info=True)
        messages.error(request, 'Erro ao criar pacote.')

    return redirect('aranha:admin_pacotes')


@staff_required
def admin_editar_pacote(request, pk):
    """Edita pacote existente (dados, itens e ativo/inativo)."""
    pacote = get_object_or_404(Pacote, pk=pk)

    if request.method != 'POST':
        return redirect('aranha:admin_pacotes')

    nome = request.POST.get('nome', pacote.nome).strip()
    if not nome:
        messages.error(request, 'Nome do pacote é obrigatório.')
        return redirect('aranha:admin_pacotes')

    try:
        preco_total = _parse_valor(request.POST.get('preco_total', str(pacote.preco_total)))
        validade_meses = _parse_validade(request.POST.get('validade_meses', pacote.validade_meses))
        itens = _ler_itens(request.POST)
    except (ValueError, TypeError):
        messages.error(request, 'Dados inválidos: verifique preço, validade e itens (sem procedimento repetido).')
        return redirect('aranha:admin_pacotes')

    try:
        with transaction.atomic():
            pacote.nome = nome
            pacote.descricao = request.POST.get('descricao', '').strip()
            pacote.preco_total = preco_total
            pacote.validade_meses = validade_meses
            pacote.ativo = request.POST.get('ativo') in ('1', 'on')
            pacote.save()

            # Itens: substitui o conjunto (ItemPacote nao e referenciado por
            # consumos — ConsumoSessao aponta p/ a compra e o atendimento).
            ItemPacote.objects.filter(pacote=pacote).delete()
            ItemPacote.objects.bulk_create([
                ItemPacote(pacote=pacote, procedimento_id=pid, quantidade_sessoes=qtd)
                for pid, qtd in itens
            ])

        registrar_log(request.user, f'Editou pacote: {pacote.nome}', 'pacote', pacote.pk)
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

    pacote_id = request.POST.get('pacote_id', '')
    cliente_id = request.POST.get('cliente_id', '')
    if not (pacote_id.isdigit() and cliente_id.isdigit()):
        messages.error(request, 'Selecione o pacote e o cliente.')
        return redirect('aranha:admin_pacotes')

    # 404 real (pacote/cliente inexistente) fica fora do try de escrita.
    pacote = get_object_or_404(Pacote, pk=int(pacote_id))
    cliente = get_object_or_404(Cliente, pk=int(cliente_id))

    if not pacote.ativo:
        messages.error(request, f'O pacote "{pacote.nome}" está inativo e não pode ser vendido.')
        return redirect('aranha:admin_pacotes')

    try:
        valor_pago = _parse_valor(request.POST.get('valor_pago', ''))
    except ValueError:
        messages.error(request, 'Valor pago inválido.')
        return redirect('aranha:admin_pacotes')

    try:
        # data_expiracao fica a cargo de CompraPacote.save() (localdate +
        # validade em meses civis) — calcular aqui em UTC errava o dia a noite.
        pc = CompraPacote.objects.create(
            cliente=cliente,
            pacote=pacote,
            valor_pago=valor_pago,
            status='ATIVO',
        )

        registrar_log(
            request.user,
            f'Vendeu pacote "{pacote.nome}" para {cliente.nome}',
            'compra_pacote', pc.pk,
        )
        messages.success(request, f'Pacote vendido para {cliente.nome}!')
    except (DatabaseError, ValidationError) as e:
        logger.error(f'Erro ao vender pacote: {e}', exc_info=True)
        messages.error(request, 'Erro ao vender pacote.')

    return redirect('aranha:admin_pacotes')
