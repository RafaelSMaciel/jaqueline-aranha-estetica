"""Promocoes no painel: lista, criar, editar, excluir e disparar por e-mail (services/marketing).

Criar e editar usam o mesmo PromocaoForm. Envio recusado nao redireciona: a lista volta (200) com o
modal reaberto, o que foi digitado e o erro no campo (achado 166)."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from ...decorators import staff_required
from ...excecoes import RegraDeNegocio
from ...forms.painel import erros_por_campo
from ...forms.promocoes import PromocaoForm
from ...models import Atendimento, Procedimento, Promocao
from ...services import marketing
from ...utils.auditoria import registrar_log
from ...utils.datas import hoje

POR_PAGINA = 30


def _valores_salvos(promo):
    return {
        'nome': promo.nome, 'descricao': promo.descricao or '', 'procedimento': promo.procedimento_id,
        'desconto': promo.desconto_percentual, 'preco_promocional': promo.preco_promocional,
        'ativa': promo.ativa, 'data_inicio': promo.data_inicio.isoformat() if promo.data_inicio else '',
        'data_fim': promo.data_fim.isoformat() if promo.data_fim else '',
    }


def _valores_digitados(post):
    """O que veio no POST, no formato do template (procedimento como int p/ comparar com o pk)."""
    proc = (post.get('procedimento') or '').strip()
    return {
        'nome': post.get('nome', ''), 'descricao': post.get('descricao', ''),
        'procedimento': int(proc) if proc.isascii() and proc.isdigit() else None,
        'desconto': post.get('desconto', ''), 'preco_promocional': post.get('preco_promocional', ''),
        'ativa': post.get('ativa') == '1', 'data_inicio': post.get('data_inicio', ''),
        'data_fim': post.get('data_fim', ''),
    }


def _contexto_lista(request, editar_pk=None):
    """Contexto da lista. editar_pk: abre a pagina onde esta essa promocao (reabrir o modal)."""
    promocoes = Promocao.objects.select_related('procedimento').order_by('-ativa', '-data_inicio', 'pk')

    paginator = Paginator(promocoes, POR_PAGINA)
    page = request.GET.get('page', 1)
    if editar_pk is not None:
        pks = list(promocoes.values_list('pk', flat=True))
        page = pks.index(editar_pk) // POR_PAGINA + 1 if editar_pk in pks else 1
    promocoes_page = paginator.get_page(page)
    for promo in promocoes_page:
        # o que o modal de edicao mostra (o salvo; no envio recusado, o digitado)
        promo.valores, promo.erros = _valores_salvos(promo), {}

    return {
        'promocoes': promocoes_page,
        'procedimentos': Procedimento.objects.filter(ativo=True),
        'hoje': hoje(),   # selo: "Comeca em dd/mm" (futura) x "Encerrada" (passada)
    }


def _devolver_com_erro(request, acao, form, editar=None):
    """Envio recusado: a lista volta SEM redirect, com o modal aberto, o digitado e o erro no campo.

    O resumo ('Erro ao <acao> promoção: ...') vai p/ o aviso do topo e, como o modal aberto deixa o
    resto da tela inerte, tambem p/ dentro dele (erros['geral']). editar = a promocao do modal de
    edicao; None = modal de nova promocao."""
    textos = list(dict.fromkeys(str(e) for erros in form.errors.values() for e in erros))
    mensagem = f'Erro ao {acao} promoção: {" ".join(textos)}'
    messages.error(request, mensagem)
    erros = {**erros_por_campo(form), 'geral': mensagem}
    contexto = _contexto_lista(request, editar_pk=editar.pk if editar else None)
    valores = _valores_digitados(request.POST)
    if editar is None:
        contexto['nova'] = {'valores': valores, 'erros': erros}
    else:
        for promo in contexto['promocoes']:
            if promo.pk == editar.pk:
                promo.valores, promo.erros, promo.reabrir = valores, erros, True
    return render(request, 'painel/promocoes.html', contexto)


def _detalhes(promo) -> dict:
    return {'desconto': str(promo.desconto_percentual),
            'preco_fixo': str(promo.preco_promocional) if promo.preco_promocional is not None else None}


@staff_required
def lista(request):
    """Lista todas as promocoes (vigentes primeiro)."""
    return render(request, 'painel/promocoes.html', _contexto_lista(request))


@staff_required
@require_POST
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def criar(request):
    """Cria promocao (REGRAS §13): geral = todos os procedimentos, so percentual; especifica =
    percentual OU preco fixo (PromocaoForm; o banco repete em CHECK)."""
    form = PromocaoForm(request.POST)
    if not form.is_valid():
        return _devolver_com_erro(request, 'criar', form)
    promo = form.save()
    registrar_log(request.user, f'Criou promoção: {promo.nome}', 'promocao', promo.pk, _detalhes(promo),
                  request=request)
    messages.success(request, 'Promoção criada com sucesso!')
    return redirect('aranha:admin_promocoes')


@staff_required
@require_POST
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def editar(request, pk):
    """Edita a promocao com o mesmo form da criacao (procedimento vazio = promocao geral)."""
    promo = get_object_or_404(Promocao, pk=pk)
    form = PromocaoForm(request.POST, instance=promo)
    if not form.is_valid():
        return _devolver_com_erro(request, 'atualizar', form, editar=promo)
    form.save()
    registrar_log(request.user, f'Editou promoção: {promo.nome}', 'promocao', promo.pk, _detalhes(promo),
                  request=request)
    messages.success(request, 'Promoção atualizada!')
    return redirect('aranha:admin_promocoes')


@staff_required
@require_POST
@ratelimit(key='user', rate='30/h', method='POST', block=True)
def disparar(request, pk):
    """Envia o proximo lote do e-mail da promocao (so a quem autorizou ofertas por e-mail).

    Lista grande vai em lotes (um por clique, sem reenvio); concluida, mandar de novo exige marcar
    'reenviar para todos' (reenviar_para_todos=1)."""
    promo = get_object_or_404(Promocao, pk=pk)
    reenviar = request.POST.get('reenviar_para_todos') == '1'
    try:
        enviados, falhas, restantes = marketing.enviar_promocao(promo, reenviar=reenviar)
    except RegraDeNegocio as exc:
        messages.error(request, exc.mensagem)
        return redirect('aranha:admin_promocoes')
    registrar_log(
        request.user, f'Disparou promoção: {promo.nome}', 'promocao', promo.pk,
        {'enviados': enviados, 'falhas': falhas, 'restantes': restantes, 'reenvio': reenviar},
        request=request,
    )
    if not (enviados or falhas or restantes):
        messages.info(request, 'Nenhum cliente autorizou receber ofertas por e-mail.')
        return redirect('aranha:admin_promocoes')
    msg = f'{enviados} e-mail(s) enviado(s)' + (f', {falhas} falha(s)' if falhas else '')
    if restantes:
        messages.info(request, f'{msg}. Faltam {restantes} cliente(s): clique em "Continuar envio" para o próximo lote.')
    else:
        messages.success(request, f'{msg}. Envio da promoção "{promo.nome}" concluído.')
    return redirect('aranha:admin_promocoes')


@staff_required
@require_POST
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def excluir(request, pk):
    """Exclui a promocao que nunca foi usada (a usada so desativa: o preco cobrado guarda o rastro)."""
    promo = get_object_or_404(Promocao, pk=pk)
    if Atendimento.objects.filter(promocao=promo).exists():
        messages.error(request, f'"{promo.nome}" já foi usada em atendimentos: desative em vez de excluir.')
        return redirect('aranha:admin_promocoes')
    nome = promo.nome
    promo.delete()
    registrar_log(request.user, f'Excluiu promoção: {nome}', 'promocao', pk, request=request)
    messages.success(request, 'Promoção excluída!')
    return redirect('aranha:admin_promocoes')
