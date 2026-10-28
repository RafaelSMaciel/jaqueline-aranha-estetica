"""Views de CRUD de promocoes — listagem, criacao, edicao, exclusao e disparo."""
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django_ratelimit.decorators import ratelimit

from ...decorators import staff_required
from ...models import Atendimento, Cliente, Configuracao, Procedimento, Promocao
from ...utils.auditoria import registrar_log
from ...utils.datas import hoje
from ...utils.parse import id_int
from ...forms.painel import parse_preco as _parse_preco

logger = logging.getLogger(__name__)

# O envio roda DENTRO do request (sem fila de tarefas): limita cada clique a um lote pequeno
# p/ nao estourar o timeout do gunicorn.
LOTE_PROMOCAO = 25


def _parse_desconto(valor):
    """'15', '15,5' ou '15.50' -> Decimal em (0, 100]. ValueError se invalido."""
    try:
        desconto = Decimal((valor or '').strip().replace(',', '.'))
    except InvalidOperation as exc:
        raise ValueError('desconto invalido') from exc
    if not desconto.is_finite() or desconto <= 0 or desconto > 100:
        raise ValueError('desconto invalido')
    return desconto.quantize(Decimal('0.01'))


def _fmt_desconto(valor):
    """20.00 -> '20'; 15.50 -> '15,5' (texto ao cliente)."""
    texto = f'{Decimal(valor).normalize():f}'
    return texto.replace('.', ',')


def _assunto_promocao(promo):
    """Assunto do e-mail: preco fixo mostra o preco (nunca '0% OFF')."""
    if promo.preco_promocional is not None:
        from ...utils.formatos import formatar_brl
        return f'{promo.nome} — por {formatar_brl(promo.preco_promocional)}'
    return f'{promo.nome} — {_fmt_desconto(promo.desconto_percentual)}% OFF'


POR_PAGINA = 30
# campo do model -> campo do formulario (o resto tem o mesmo nome)
_CAMPO_DO_FORM = {'desconto_percentual': 'desconto'}


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


MSG_PERIODO = 'A data de fim deve ser igual ou posterior à de início.'


def _erros_por_campo(exc):
    """Erro do model/banco -> {campo do formulario: mensagem}, p/ marcar o campo na tela.

    ValidationError do full_clean vem por campo; a CHECK do periodo (ck_promocao_periodo) vem
    como erro geral (full_clean) ou IntegrityError (save) e e marcada na data de fim."""
    erros = {}
    if hasattr(exc, 'message_dict'):
        erros = {_CAMPO_DO_FORM.get(campo, campo): msgs[0]
                 for campo, msgs in exc.message_dict.items() if campo != '__all__' and msgs}
    if 'ck_promocao_periodo' in str(exc):
        erros['data_fim'] = MSG_PERIODO
    return erros


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
    }


def _devolver_com_erro(request, mensagem, erros, editar=None):
    """Envio recusado: a lista volta SEM redirect, com o modal aberto, o digitado e o erro no campo.

    Antes o redirect fechava o modal e perdia o que foi digitado. `mensagem` (o texto de sempre)
    vai p/ o aviso do topo e, como o modal aberto deixa o resto da tela inerte, tambem p/ dentro
    dele (erros['geral']). editar = a promocao do modal de edicao; None = modal de nova promocao."""
    messages.error(request, mensagem)
    erros = {**erros, 'geral': mensagem}
    contexto = _contexto_lista(request, editar_pk=editar.pk if editar else None)
    valores = _valores_digitados(request.POST)
    if editar is None:
        contexto['nova'] = {'valores': valores, 'erros': erros}
    else:
        for promo in contexto['promocoes']:
            if promo.pk == editar.pk:
                promo.valores, promo.erros, promo.reabrir = valores, erros, True
    return render(request, 'painel/promocoes.html', contexto)


@staff_required
def admin_promocoes(request):
    """Lista todas as promoções"""
    return render(request, 'painel/promocoes.html', _contexto_lista(request))


@staff_required
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_criar_promocao(request):
    """Cria promocao (REGRAS §13): geral = todos os procedimentos, so percentual; especifica =
    percentual OU preco fixo (XOR, validado pelo model e pelo CHECK do banco)."""
    if request.method == 'POST':
        proc_id = (request.POST.get('procedimento') or '').strip()
        # 404 real (procedimento inexistente) nao deve ser mascarado como erro interno
        procedimento = get_object_or_404(Procedimento, pk=proc_id) if proc_id else None
        preco_raw = (request.POST.get('preco_promocional') or '').strip()
        try:
            promo = Promocao(
                nome=request.POST.get('nome', '').strip(),
                descricao=request.POST.get('descricao', '').strip(),
                procedimento=procedimento,
                data_inicio=request.POST.get('data_inicio'),
                data_fim=request.POST.get('data_fim'),
                ativa=request.POST.get('ativa') == '1',
            )
            if preco_raw:
                promo.preco_promocional = _parse_preco(preco_raw)
                promo.desconto_percentual = Decimal('0')
            else:
                promo.desconto_percentual = _parse_desconto(request.POST.get('desconto', ''))
            promo.full_clean()
            with transaction.atomic():   # CHECK recusada nao deixa a conexao quebrada p/ devolver a tela
                promo.save()
            registrar_log(request.user, f'Criou promoção: {promo.nome}', 'promocao', promo.pk,
                          {'desconto': str(promo.desconto_percentual),
                           'preco_fixo': str(promo.preco_promocional) if promo.preco_promocional else None},
                          request=request)
            messages.success(request, 'Promoção criada com sucesso!')
        except ValidationError as e:
            mensagem = (' '.join(m for msgs in e.message_dict.values() for m in msgs)
                        if hasattr(e, 'message_dict') else 'Promoção inválida.')
            return _devolver_com_erro(request, mensagem, _erros_por_campo(e))
        except (ValueError, IntegrityError) as e:
            logger.error(f'Erro ao criar promoção: {e}', exc_info=True)
            mensagem = ('Erro ao criar promoção. Informe desconto (até 100%) ou preço fixo e datas '
                        '(fim depois do início).')
            # ValueError = parse do valor digitado (preco fixo, se veio; senao o desconto)
            erros = ({'preco_promocional' if preco_raw else 'desconto': 'Valor inválido.'}
                     if isinstance(e, ValueError) else _erros_por_campo(e))
            return _devolver_com_erro(request, mensagem, erros)
    return redirect('aranha:admin_promocoes')


@staff_required
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_editar_promocao(request, pk):
    """Edita promoção existente via POST.

    Procedimento vazio = promocao global (todos os procedimentos). Promocao de
    preco fixo (preco_promocional) mantem desconto 0 (XOR no banco): o
    desconto nao e lido e o preco so muda se vier no POST.
    """
    promo = get_object_or_404(Promocao, pk=pk)
    if request.method == 'POST':
        pid = (request.POST.get('procedimento') or '').strip()
        proc_id = id_int(pid)
        if pid and proc_id is None:
            return _devolver_com_erro(request, 'Procedimento inválido.',
                                      {'procedimento': 'Procedimento inválido.'}, editar=promo)
        # 404 real (procedimento inexistente) nao deve ser mascarado como erro interno
        procedimento = get_object_or_404(Procedimento, pk=proc_id) if pid else None
        preco_fixo = promo.preco_promocional is not None
        try:
            promo.nome = request.POST.get('nome', promo.nome).strip()
            promo.descricao = request.POST.get('descricao', promo.descricao or '').strip()
            if promo.preco_promocional is not None:
                preco_raw = request.POST.get('preco_promocional')
                if preco_raw not in (None, ''):
                    promo.preco_promocional = _parse_preco(preco_raw)
            else:
                promo.desconto_percentual = _parse_desconto(
                    request.POST.get('desconto', str(promo.desconto_percentual))
                )
            promo.procedimento = procedimento
            promo.data_inicio = request.POST.get('data_inicio')
            promo.data_fim = request.POST.get('data_fim')
            promo.ativa = request.POST.get('ativa') == '1'
            with transaction.atomic():   # CHECK recusada nao deixa a conexao quebrada p/ devolver a tela
                promo.save()
            registrar_log(request.user, f'Editou promoção: {promo.nome}', 'promocao', promo.pk, request=request)
            messages.success(request, 'Promoção atualizada!')
        except (ValueError, ValidationError, IntegrityError) as e:
            logger.error(f'Erro ao atualizar promoção: {e}', exc_info=True)
            if isinstance(e, ValueError):   # parse do valor digitado (preco fixo ou desconto)
                erros = {'preco_promocional' if preco_fixo else 'desconto': 'Valor inválido.'}
            else:
                erros = _erros_por_campo(e)
            return _devolver_com_erro(
                request, 'Erro ao atualizar promoção. Verifique desconto (até 100%) e datas (fim depois do início).',
                erros, editar=promo)
    return redirect('aranha:admin_promocoes')


def _destinatarios_promocao():
    """Mesmo publico do job_promocao_mensal: MARKETING/EMAIL vigente no historico."""
    from ...services import consentimentos

    return consentimentos.com_consentimento(
        Cliente.objects.filter(ativo=True, email__isnull=False).exclude(email=''),
        *consentimentos.MARKETING_EMAIL, caminho='pk',
    ).order_by('pk')


def _enviar_lote_promocao(promo, assunto, corpo, validade_dias):
    """Envia o proximo lote (LOTE_PROMOCAO) a partir do cursor persistido.

    O cursor (ultimo pk processado) fica em Configuracao para sobreviver a
    restart e evitar reenvio a quem ja recebeu. Retorna (enviados, falhas, restantes).
    """
    from ...utils.email import enviar_marketing_com_descadastro, enviar_promocao_email

    chave = f'promocao_{promo.pk}_envio_cursor'
    cfg, _ = Configuracao.objects.get_or_create(
        chave=chave,
        defaults={'valor': '0', 'descricao': f'Controle interno do envio em lotes da promoção "{promo.nome}".'},
    )
    cursor = int(cfg.valor) if (cfg.valor or '').isdigit() else 0
    lote = list(_destinatarios_promocao().filter(pk__gt=cursor)[:LOTE_PROMOCAO])

    validade = (hoje() + timedelta(days=validade_dias)).strftime('%d/%m/%Y')
    enviados = falhas = 0
    for cliente in lote:
        dados = {'nome': cliente.nome, 'corpo_html': corpo, 'validade': validade}
        try:
            ok = enviar_marketing_com_descadastro(
                cliente,
                lambda unsub, c=cliente, d=dados: enviar_promocao_email(
                    c.email, d, unsub_token=unsub, assunto=assunto,
                ),
            )
        except Exception:  # pylint: disable=broad-except
            logger.warning('promocao_lote_falha', exc_info=True, extra={'cliente_id': cliente.pk})
            ok = False
        if ok:
            enviados += 1
        else:
            falhas += 1
        cursor = cliente.pk

    restantes = _destinatarios_promocao().filter(pk__gt=cursor).count()
    if restantes:
        cfg.valor = str(cursor)
        cfg.save(update_fields=['valor'])
    else:
        cfg.delete()  # envio concluido: um novo disparo recomeca do inicio
    return enviados, falhas, restantes


@staff_required
@ratelimit(key='user', rate='30/h', method='POST', block=True)
def admin_disparar_promocao(request, pk):
    """Dispara e-mail da promocao p/ clientes com consentimento de marketing.

    Roda no request: lista pequena vai inteira; lista grande vai em lotes de LOTE_PROMOCAO por
    clique, com cursor (sem reenvio).

    Sem cupom: nada no agendamento aceita codigo — a promocao vale sozinha
    no preco (utils.precos). O "valido ate" nunca passa do fim da promocao.
    """
    if request.method != 'POST':
        return redirect('aranha:admin_promocoes')

    promo = get_object_or_404(Promocao, pk=pk)
    if not promo.esta_vigente:
        messages.error(request, f'A promoção "{promo.nome}" não está vigente hoje: nada foi enviado.')
        return redirect('aranha:admin_promocoes')
    try:
        validade_dias = int(request.POST.get('validade_dias') or 30)
    except (TypeError, ValueError):
        messages.error(request, 'Validade inválida.')
        return redirect('aranha:admin_promocoes')
    # "Valido ate" = min(hoje + N dias, fim da promocao): o e-mail nao promete
    # preco depois que a promocao acaba (e o job calcula hoje + N).
    validade_dias = max(0, min(365, validade_dias, (promo.data_fim - hoje()).days))

    from ...utils.email import email_configurado
    if not email_configurado():
        messages.error(
            request,
            'O envio de e-mails não está configurado no servidor. Nenhuma mensagem foi enviada.',
        )
        return redirect('aranha:admin_promocoes')

    assunto = _assunto_promocao(promo)
    corpo = promo.descricao or ''
    total = _destinatarios_promocao().count()
    detalhes = {'validade_dias': validade_dias, 'destinatarios': total}

    if total <= LOTE_PROMOCAO:
        from ...jobs import job_promocao_mensal
        try:
            job_promocao_mensal(assunto, corpo, validade_dias=validade_dias)
        except Exception as e:  # pylint: disable=broad-except
            logger.error('Erro ao disparar promocao: %s', e, exc_info=True)
            messages.error(request, 'Não foi possível iniciar o envio. Tente novamente.')
            return redirect('aranha:admin_promocoes')
        registrar_log(request.user, f'Disparou promoção: {promo.nome}', 'promocao', promo.pk, detalhes, request=request)
        if total == 0:
            messages.info(request, 'Nenhum cliente autorizou receber ofertas por e-mail.')
        else:
            messages.success(request, f'Promoção "{promo.nome}" enviada para {total} cliente(s).')
        return redirect('aranha:admin_promocoes')

    enviados, falhas, restantes = _enviar_lote_promocao(promo, assunto, corpo, validade_dias)
    registrar_log(
        request.user, f'Disparou promoção (lote): {promo.nome}', 'promocao', promo.pk,
        {**detalhes, 'enviados': enviados, 'falhas': falhas, 'restantes': restantes},
        request=request,
    )
    msg = f'Lote enviado: {enviados} e-mail(s)'
    if falhas:
        msg += f', {falhas} falha(s)'
    if restantes:
        messages.info(request, f'{msg}. Faltam {restantes} cliente(s) — clique em "Enviar" de novo para continuar.')
    else:
        messages.success(request, f'{msg}. Envio da promoção "{promo.nome}" concluído.')
    return redirect('aranha:admin_promocoes')


@staff_required
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_excluir_promocao(request, pk):
    """Exclui promoção via POST"""
    if request.method == 'POST':
        promo = get_object_or_404(Promocao, pk=pk)
        if Atendimento.objects.filter(promocao=promo).exists():
            # preco ja cobrado com esta promocao: excluir apagava o rastro do desconto
            messages.error(request, f'"{promo.nome}" já foi usada em atendimentos: desative em vez de excluir.')
            return redirect('aranha:admin_promocoes')
        try:
            nome = promo.nome
            promo.delete()
            registrar_log(request.user, f'Excluiu promoção: {nome}', 'promocao', pk, request=request)
            messages.success(request, 'Promoção excluída!')
        except Exception as e:
            logger.error(f'Erro ao excluir promoção: {e}', exc_info=True)
            messages.error(request, 'Erro ao excluir promoção.')
    return redirect('aranha:admin_promocoes')
