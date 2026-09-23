"""Views de CRUD de promocoes — listagem, criacao, edicao, exclusao e disparo."""
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django_ratelimit.decorators import ratelimit

from ..decorators import staff_required
from ..models import Cliente, Configuracao, Procedimento, Promocao
from ..utils.audit import registrar_log
from ..utils.datas import hoje
from ..utils.parse import id_int
from .admin_management import _parse_preco

logger = logging.getLogger(__name__)

# Sem worker Celery (prod: eager) o envio roda DENTRO do request: limita cada
# clique a um lote pequeno p/ nao estourar o timeout do gunicorn.
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
        from ..services.agendamento_service import formatar_brl
        return f'{promo.nome} — por {formatar_brl(promo.preco_promocional)}'
    return f'{promo.nome} — {_fmt_desconto(promo.desconto_percentual)}% OFF'


@staff_required
def admin_promocoes(request):
    """Lista todas as promoções"""
    promocoes = Promocao.objects.select_related('procedimento').order_by('-ativa', '-data_inicio')

    paginator = Paginator(promocoes, 30)
    page = request.GET.get('page', 1)
    promocoes_page = paginator.get_page(page)

    procedimentos = Procedimento.objects.filter(ativo=True)
    context = {
        'promocoes': promocoes_page,
        'procedimentos': procedimentos,
    }
    return render(request, 'painel/promocoes.html', context)


@staff_required
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_criar_promocao(request):
    """Cria nova promoção via POST"""
    if request.method == 'POST':
        # 404 real (procedimento inexistente) nao deve ser mascarado como erro interno
        procedimento = get_object_or_404(Procedimento, pk=request.POST.get('procedimento'))
        try:
            promo = Promocao.objects.create(
                nome=request.POST.get('nome', '').strip(),
                descricao=request.POST.get('descricao', '').strip(),
                desconto_percentual=_parse_desconto(request.POST.get('desconto', '10')),
                procedimento=procedimento,
                data_inicio=request.POST.get('data_inicio'),
                data_fim=request.POST.get('data_fim'),
                ativa=request.POST.get('ativa') == '1',
            )
            registrar_log(request.user, f'Criou promoção: {promo.nome}', 'promocao', promo.pk, {'desconto': str(promo.desconto_percentual)}, request=request)
            messages.success(request, 'Promoção criada com sucesso!')
        except (ValueError, ValidationError, IntegrityError) as e:
            logger.error(f'Erro ao criar promoção: {e}', exc_info=True)
            messages.error(request, 'Erro ao criar promoção. Verifique desconto (até 100%) e datas (fim depois do início).')
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
            messages.error(request, 'Procedimento inválido.')
            return redirect('aranha:admin_promocoes')
        # 404 real (procedimento inexistente) nao deve ser mascarado como erro interno
        procedimento = get_object_or_404(Procedimento, pk=proc_id) if pid else None
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
            promo.save()
            registrar_log(request.user, f'Editou promoção: {promo.nome}', 'promocao', promo.pk, request=request)
            messages.success(request, 'Promoção atualizada!')
        except (ValueError, ValidationError, IntegrityError) as e:
            logger.error(f'Erro ao atualizar promoção: {e}', exc_info=True)
            messages.error(request, 'Erro ao atualizar promoção. Verifique desconto (até 100%) e datas (fim depois do início).')
    return redirect('aranha:admin_promocoes')


def _destinatarios_promocao():
    """Mesmo publico do job_promocao_mensal: consentimento explicito de marketing."""
    return Cliente.objects.filter(
        ativo=True,
        email__isnull=False,
        consent_email_marketing=True,
        aceita_comunicacao=True,
    ).exclude(email='').order_by('pk')


def _enviar_lote_promocao(promo, assunto, corpo, validade_dias):
    """Envia o proximo lote (LOTE_PROMOCAO) a partir do cursor persistido.

    O cursor (ultimo pk processado) fica em Configuracao para sobreviver a
    restart e evitar reenvio a quem ja recebeu. Retorna (enviados, falhas, restantes).
    """
    from ..utils.email import enviar_promocao_email

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
        try:
            ok = enviar_promocao_email(
                cliente.email,
                {'nome': cliente.nome, 'corpo_html': corpo, 'validade': validade},
                unsub_token=cliente.token_descadastro,
                assunto=assunto,
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

    - Com worker Celery: enfileira o job (assincrono).
    - Sem worker (eager, prod atual): lista pequena vai inteira; lista grande
      vai em lotes de LOTE_PROMOCAO por clique, com cursor (sem reenvio).

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

    from ..utils.email import email_configurado
    if not email_configurado():
        messages.error(
            request,
            'O envio de e-mails não está configurado no servidor. Nenhuma mensagem foi enviada.',
        )
        return redirect('aranha:admin_promocoes')

    assunto = _assunto_promocao(promo)
    corpo = promo.descricao or ''
    total = _destinatarios_promocao().count()
    eager = getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False)
    detalhes = {'validade_dias': validade_dias, 'destinatarios': total}

    if not eager or total <= LOTE_PROMOCAO:
        from ..tasks import job_promocao_mensal
        try:
            job_promocao_mensal.delay(assunto, corpo, validade_dias=validade_dias)
        except Exception as e:  # pylint: disable=broad-except
            logger.error('Erro ao disparar promocao: %s', e, exc_info=True)
            messages.error(request, 'Não foi possível iniciar o envio. Tente novamente.')
            return redirect('aranha:admin_promocoes')
        registrar_log(request.user, f'Disparou promoção: {promo.nome}', 'promocao', promo.pk, detalhes, request=request)
        if total == 0:
            messages.info(request, 'Nenhum cliente autorizou receber ofertas por e-mail.')
        elif eager:
            messages.success(request, f'Promoção "{promo.nome}" enviada para {total} cliente(s).')
        else:
            messages.success(request, f'Envio de "{promo.nome}" agendado para {total} cliente(s).')
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
        try:
            promo = get_object_or_404(Promocao, pk=pk)
            nome = promo.nome
            promo.delete()
            registrar_log(request.user, f'Excluiu promoção: {nome}', 'promocao', pk, request=request)
            messages.success(request, 'Promoção excluída!')
        except Exception as e:
            logger.error(f'Erro ao excluir promoção: {e}', exc_info=True)
            messages.error(request, 'Erro ao excluir promoção.')
    return redirect('aranha:admin_promocoes')
