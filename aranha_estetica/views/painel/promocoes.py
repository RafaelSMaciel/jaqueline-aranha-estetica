"""Promocoes no painel: lista, criar, editar, excluir e disparar por e-mail (services/marketing)."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from ...decorators import staff_required
from ...excecoes import RegraDeNegocio
from ...forms.painel import avisar_erros
from ...forms.promocoes import PromocaoForm
from ...models import Atendimento, Procedimento, Promocao
from ...services import marketing
from ...utils.auditoria import registrar_log


@staff_required
def lista(request):
    """Lista todas as promocoes (vigentes primeiro)."""
    promocoes = Promocao.objects.select_related('procedimento').order_by('-ativa', '-data_inicio')
    return render(request, 'painel/promocoes.html', {
        'promocoes': Paginator(promocoes, 30).get_page(request.GET.get('page', 1)),
        'procedimentos': Procedimento.objects.filter(ativo=True),
    })


def _detalhes(promo) -> dict:
    return {'desconto': str(promo.desconto_percentual),
            'preco_fixo': str(promo.preco_promocional) if promo.preco_promocional is not None else None}


@staff_required
@require_POST
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def criar(request):
    """Cria promocao (REGRAS §13): geral = todos os procedimentos, so percentual; especifica =
    percentual OU preco fixo (PromocaoForm; o banco repete em CHECK)."""
    form = PromocaoForm(request.POST)
    if not form.is_valid():
        avisar_erros(request, form)
        return redirect('aranha:admin_promocoes')
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
        avisar_erros(request, form)
        return redirect('aranha:admin_promocoes')
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
