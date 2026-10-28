"""Ficha da cliente no painel: cadastro, indicacao e consentimentos."""
import logging

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache

from ...decorators import staff_required
from ...models import Atendimento, Carteira, Cliente, MovimentoCarteira
from ...models.agendamentos import StatusAtendimento
from ...services import consentimentos, retorno
from ...services.termos import aceita_assinatura
from ...utils.auditoria import registrar_log
from ...utils.datas import hoje

logger = logging.getLogger(__name__)


def _contexto_cliente(cliente):
    from django.db.models import Count, Max

    from ...models import CompraPacote, ConsumoSessao
    from ...services import financeiro
    from ...services.termos import ids_com_termo_procedimento_pendente
    from ...utils.saude import alertas_saude

    atendimentos = list(
        Atendimento.objects.filter(cliente=cliente)
        .select_related('profissional', 'procedimento').order_by('-data_hora_inicio')[:20]
    )
    sem_termo = ids_com_termo_procedimento_pendente(atendimentos)
    de_pacote = set(
        ConsumoSessao.objects.filter(atendimento_id__in=[a.pk for a in atendimentos])
        .values_list('atendimento_id', flat=True)
    )
    for at in atendimentos:
        at.termo_pendente = at.pk in sem_termo
        at.termo_link = at.termo_pendente and aceita_assinatura(at)
        at.de_pacote = at.pk in de_pacote

    # Compras de pacote: saldo por item CONGELADO na venda + validade (recepcao
    # responde "quantas sessoes me restam e ate quando" sem abrir o Django admin)
    hoje_local = hoje()
    pacotes = list(
        CompraPacote.objects.filter(cliente=cliente)
        .prefetch_related('itens__procedimento', 'sessoes_realizadas')
        .order_by('-criado_em')
    )
    for compra in pacotes:
        compra.saldo = compra.saldo_por_procedimento()
        compra.vencido = bool(
            compra.status == CompraPacote.STATUS_ATIVO and compra.data_expiracao and compra.data_expiracao < hoje_local
        )

    realizados_qs = Atendimento.objects.filter(cliente=cliente, status=StatusAtendimento.REALIZADO)
    agg = realizados_qs.aggregate(qtd=Count('pk'), ultima=Max('data_hora_inicio'))
    proc_top = realizados_qs.values(
        'procedimento__nome'
    ).annotate(c=Count('pk')).order_by('-c').first()
    # "LTV" = avulsos faturados + pacotes (liquidos do reembolso): a sessao de pacote
    # nao soma de novo a preco cheio (D20; mesma regra do dashboard/financeiro).
    gasto = financeiro.valor_gasto_cliente(cliente)

    cliente_stats = {
        'ltv': gasto['total'],
        'ltv_avulsos': gasto['avulsos'],
        'ltv_pacotes': gasto['pacotes'],
        'qtd_realizados': agg['qtd'] or 0,
        'ticket_medio': gasto['ticket_medio'],
        'ultima_visita': agg['ultima'],
        'procedimento_mais_frequente': proc_top['procedimento__nome'] if proc_top else None,
        'no_show_count': Atendimento.objects.filter(cliente=cliente, status=StatusAtendimento.FALTOU).count(),
        'cancelados_count': Atendimento.objects.filter(cliente=cliente, status=StatusAtendimento.CANCELADO).count(),
    }
    return {
        'cliente': cliente,
        'atendimentos': atendimentos,
        'cliente_stats': cliente_stats,
        'alertas': alertas_saude(cliente),
        'pacotes': pacotes,
        'indicacao_travada': _indicacao_gerou_cashback(cliente),
        # credito de indicacao: saldo, extrato e quem esta cliente indicou
        'carteira': Carteira.objects.filter(cliente=cliente).first(),
        'extrato_credito': list(MovimentoCarteira.objects.filter(carteira__cliente=cliente)
                                .select_related('atendimento').order_by('-criado_em')[:12]),
        'indicadas': list(cliente.indicacoes.order_by('nome').values('pk', 'nome')),
        'consentimentos': consentimentos.resumo_painel(cliente),
        'retornos': retorno.devidos(cliente_id=cliente.pk),
    }


def _indicacao_gerou_cashback(cliente):
    """Indicacao que ja creditou cashback nao muda mais (o credito ficaria
    com a indicadora errada)."""
    return bool(cliente.indicado_por_id) and MovimentoCarteira.objects.filter(
        origem=MovimentoCarteira.Origem.CASHBACK_INDICACAO, tipo=MovimentoCarteira.Tipo.CREDITO, atendimento__cliente=cliente,
    ).exists()


def _buscar_indicadora(termo, cliente):
    """(indicadora|None, erro|None) pelo codigo de indicacao ou pelo nome."""
    termo = (termo or '').strip()[:150]
    if not termo:
        return None, 'Informe o código de indicação ou o nome de quem indicou.'
    # Codigo (gerado em maiusculas) tem prioridade; senao busca pelo nome.
    candidatas = list(Cliente.objects.filter(codigo_indicacao=termo.upper())[:1])
    if not candidatas:
        por_nome = Cliente.objects.filter(nome__icontains=termo)
        candidatas = list(por_nome.filter(nome__iexact=termo)[:2]) or list(por_nome[:2])
    if not candidatas:
        return None, 'Nenhuma cliente encontrada com esse código ou nome.'
    if len(candidatas) > 1:
        return None, 'Mais de uma cliente com esse nome: use o código de indicação de quem indicou.'
    if candidatas[0].pk == cliente.pk:
        return None, 'Uma cliente não pode indicar a si mesma.'
    return candidatas[0], None


def _salvar_indicacao(request, cliente):
    """Grava/remove Cliente.indicado_por (cashback de indicacao) + auditoria."""
    destino = redirect('aranha:admin_cliente_detalhe', pk=cliente.pk)
    if _indicacao_gerou_cashback(cliente):
        messages.error(request, 'Essa indicação já gerou cashback para quem indicou e não pode ser alterada.')
        return destino
    anterior = cliente.indicado_por_id
    if request.POST.get('remover') == '1':
        indicadora = None
    else:
        indicadora, erro = _buscar_indicadora(request.POST.get('indicado_por_busca'), cliente)
        if erro:
            messages.error(request, erro)
            return destino
    novo = indicadora.pk if indicadora is not None else None
    if novo == anterior:
        messages.info(request, 'Indicação sem alteração.')
        return destino
    try:
        with transaction.atomic():
            # update(): nao passa pelo save() do cadastro nem por concorrencia do form
            Cliente.objects.filter(pk=cliente.pk).update(indicado_por=indicadora, atualizado_em=timezone.now())
    except IntegrityError:  # ck_cliente_nao_indica_a_si
        messages.error(request, 'Indicação inválida.')
        return destino
    # sem nomes no texto: o log sobrevive ao esquecimento (LGPD); os pks bastam
    registrar_log(
        request.user, 'Removeu indicacao' if indicadora is None else 'Registrou indicacao',
        'cliente', cliente.pk, detalhes={'indicado_por': novo, 'indicado_por_anterior': anterior},
        request=request,
    )
    if indicadora is None:
        messages.success(request, 'Indicação removida.')
        return destino
    messages.success(request, f'Indicação registrada: {indicadora.nome}.')
    ja_pagou = Atendimento.objects.filter(
        cliente=cliente, status=Atendimento.STATUS_REALIZADO, eh_retorno=False, valor_cobrado__gt=0,
    ).exists()
    if ja_pagou:
        messages.warning(
            request,
            'Esta cliente já tem atendimento pago: o cashback de indicação só é gerado no '
            '1º atendimento pago, então não haverá crédito para quem indicou.',
        )
    return destino


def _salvar_consentimento(request, cliente):
    """Ficha do cliente: revogar (1 clique) ou registrar autorizacao (exige justificativa).

    Consentimento e da titular: a equipe so revoga a pedido dela; conceder pelo
    painel exige o texto do que foi autorizado e como (ex.: "pediu por telefone
    em 10/11 para receber promocoes por e-mail") — fica no historico com o autor.
    """
    from ...models import Consentimento

    destino = redirect('aranha:admin_cliente_detalhe', pk=cliente.pk)
    par = consentimentos.par_da_chave(request.POST.get('par'))
    operacao = request.POST.get('operacao')
    if par is None or operacao not in ('revogar', 'conceder'):
        messages.error(request, 'Pedido de consentimento inválido.')
        return destino
    rotulo = consentimentos.ROTULOS_PAR[par]
    if operacao == 'revogar':
        linha = consentimentos.revogar(
            cliente, *par, origem=Consentimento.ORIGEM_PAINEL,
            texto='Revogado pela equipe a pedido da cliente', usuario=request.user,
        )
    else:
        justificativa = (request.POST.get('justificativa') or '').strip()[:1000]
        if len(justificativa) < consentimentos.MIN_JUSTIFICATIVA:
            messages.error(
                request,
                'Para registrar a autorização, descreva o que a cliente autorizou e como '
                '(ex.: "pediu por telefone em 10/11 para receber promoções por e-mail").',
            )
            return destino
        linha = consentimentos.registrar(
            cliente, *par, True, origem=Consentimento.ORIGEM_PAINEL,
            texto=justificativa, usuario=request.user,
        )
    if linha is None:
        messages.info(request, f'{rotulo}: sem alteração.')
        return destino
    registrar_log(
        request.user,
        'Revogou consentimento' if operacao == 'revogar' else 'Registrou consentimento',
        'cliente', cliente.pk,
        detalhes={'finalidade': par[0], 'canal': par[1], 'consentimento_id': linha.pk},
        request=request,
    )
    messages.success(
        request, f'{rotulo}: {"revogado" if operacao == "revogar" else "autorização registrada"}.',
    )
    return destino


@never_cache  # alertas de saude + CPF/RG/endereco: fora do cache do navegador
@staff_required
def admin_cliente_detalhe(request, pk):
    """Detalhe e edicao de cliente."""
    cliente = get_object_or_404(Cliente, pk=pk)

    if request.method == 'POST' and request.POST.get('acao') == 'indicacao':
        return _salvar_indicacao(request, cliente)
    if request.method == 'POST' and request.POST.get('acao') == 'consentimento':
        return _salvar_consentimento(request, cliente)

    if request.method == 'POST':
        # ClientePainelForm: normaliza telefone (+55)/CPF com mascara, valida e
        # checa unicidade entre ativos como erro de formulario; nao toca nos
        # consents LGPD (campo ausente no POST nao zera opt-in).
        from ...forms import ClientePainelForm
        form = ClientePainelForm(request.POST, instance=cliente)
        erros = []
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save()
            except IntegrityError:
                # corrida com outro cadastro entre a checagem e o save
                erros = ['Telefone, e-mail ou CPF já cadastrado para outro cliente.']
        else:
            rotulos = {'nome': 'Nome', 'telefone': 'Telefone', 'email': 'E-mail', 'cpf': 'CPF',
                       'data_nascimento': 'Data de nascimento'}
            for campo, lista in form.errors.items():
                prefixo = f'{rotulos.get(campo, campo.replace("_", " ").capitalize())}: ' if campo != '__all__' else ''
                erros.extend(f'{prefixo}{erro}' for erro in lista)
        if erros:
            # Nada gravado: re-renderiza com o que foi digitado + erros
            for erro in erros:
                messages.error(request, erro)
            return render(request, 'painel/cliente_detalhe.html', _contexto_cliente(cliente))
        # sem o nome no texto: o log sobrevive ao esquecimento (LGPD); o pk basta
        registrar_log(request.user, 'Editou cliente', 'cliente', cliente.pk, request=request)
        messages.success(request, 'Cliente atualizado!')
        return redirect('aranha:admin_cliente_detalhe', pk=pk)

    return render(request, 'painel/cliente_detalhe.html', _contexto_cliente(cliente))
