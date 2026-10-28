"""Sessao de pacote PREVISTA: que compra vai cobrir um atendimento ainda nao realizado.

O debito so acontece no Realizado (services.atendimento.debitar_sessao_de_pacote); antes disso a
recepcao e a cliente viam o preco cheio e podiam cobrar duas vezes. Aqui vale a mesma ordem do
debito — a compra que vence primeiro, depois a mais antiga; validade conferida na data da sessao;
retorno nunca usa pacote — descontando as sessoes que os atendimentos ativos anteriores da cliente
ja vao consumir. So leitura: nada e reservado no pacote.

Tambem o aviso de pacote vencendo (job pacote_expirando).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger(__name__)

# Aviso de pacote vencendo: com estes dias de antecedencia (job pacote_expirando, 1x por dia)
DIAS_AVISO_EXPIRACAO = (7, 1)


def avisar_expirando(dia=None) -> int:
    """E-mail a quem tem pacote ATIVO vencendo em 7 ou 1 dia COM saldo. Devolve quantos sairam.

    Transacional (o pacote comprado): nao depende de consentimento de marketing. Saldo pela mesma
    fonte da ficha (itens congelados na venda). Uma vez por dia (jobs.JOBS_UMA_VEZ_POR_DIA): nao ha
    marca por compra. Falha de 1 destinatario e logada e o lote segue."""
    from ..models import CompraPacote
    from ..utils.datas import hoje
    from ..utils.email import enviar_pacote_expirando_email

    dia = dia or hoje()
    enviados = 0
    for dias in DIAS_AVISO_EXPIRACAO:
        compras = CompraPacote.objects.filter(
            status=CompraPacote.STATUS_ATIVO, data_expiracao=dia + timedelta(days=dias),
        ).select_related('cliente').prefetch_related('itens__procedimento', 'sessoes_realizadas')
        for compra in compras:
            restantes = compra.sessoes_restantes()
            if restantes <= 0:
                continue
            if not compra.cliente.email:
                logger.warning('pacote_expirando_sem_email', extra={'cliente_id': compra.cliente_id})
                continue
            try:
                ok = enviar_pacote_expirando_email(compra.cliente.email, {
                    'nome': compra.cliente.nome,
                    'pacote': compra.pacote_nome,
                    'dias': dias,
                    'sessoes_restantes': restantes,
                    # sessao conta pela data em que acontece (services/atendimento)
                    'valido_ate': f'{compra.data_expiracao:%d/%m/%Y}',
                })
            except Exception:  # noqa: BLE001 — falha de 1 destinatario nao para o lote
                logger.exception('pacote_expirando_falha', extra={'cliente_id': compra.cliente_id})
                continue
            enviados += bool(ok)
    return enviados


def sessoes_previstas(atendimentos) -> dict[int, dict]:
    """{atendimento_id: {'pacote': nome, 'restam': sessoes do item depois desta}} dos ativos que um
    pacote da cliente vai cobrir. 2 queries p/ a lista inteira (+ prefetch dos itens)."""
    from ..models import Atendimento, CompraPacote
    from ..models.agendamentos import STATUS_ATIVOS

    alvo = {at.pk for at in atendimentos if at.status in STATUS_ATIVOS and not at.eh_retorno}
    if not alvo:
        return {}
    clientes = {at.cliente_id for at in atendimentos if at.pk in alvo}
    compras = list(
        CompraPacote.objects.filter(
            cliente_id__in=clientes, status__in=(CompraPacote.STATUS_ATIVO, CompraPacote.STATUS_EXPIRADO),
        ).prefetch_related('itens', 'sessoes_realizadas')
        .order_by(F('data_expiracao').asc(nulls_last=True), 'criado_em')
    )
    if not compras:
        return {}

    saldo: dict[int, int] = {}
    por_cliente = defaultdict(list)
    for compra in compras:
        usadas = compra.consumos_por_item()
        for item in compra.itens.all():
            saldo[item.pk] = item.quantidade_sessoes - usadas.get(item.pk, 0)
        por_cliente[compra.cliente_id].append(compra)

    fila = (
        Atendimento.objects.filter(cliente_id__in=por_cliente, status__in=STATUS_ATIVOS, eh_retorno=False)
        .order_by('data_hora_inicio', 'pk').only('pk', 'cliente_id', 'procedimento_id', 'data_hora_inicio')
    )
    previstas = {}
    for at in fila:
        dia = timezone.localdate(at.data_hora_inicio)
        for compra in por_cliente[at.cliente_id]:
            if compra.data_expiracao is not None and compra.data_expiracao < dia:
                continue
            item = next((i for i in compra.itens.all()
                         if i.procedimento_id == at.procedimento_id and saldo[i.pk] > 0), None)
            if item is None:
                continue
            saldo[item.pk] -= 1
            if at.pk in alvo:
                previstas[at.pk] = {'pacote': compra.pacote_nome, 'restam': saldo[item.pk]}
            break
    return previstas


# ─── cancelamento e prorrogacao da compra (painel) ──────────────────────────
# Simetricos a CompraPacote.vender: o caso de uso fica aqui (testavel); a view so le o form e mostra.
def reembolso_sugerido(compra) -> Decimal:
    """Σ sessoes restantes x valor unitario CONGELADO de cada item (o que a cliente nao usou) —
    sugestao editavel do cancelamento, nunca maior que o valor pago."""
    restante = sum((linha['restantes'] * linha['item'].valor_unitario for linha in compra.saldo_por_procedimento()),
                   Decimal('0.00'))
    return min(restante, compra.valor_pago)


def cancelar_compra(compra, reembolso: Decimal, *, motivo: str, por=None, request=None) -> None:
    """Cancela a compra ATIVA com o valor devolvido em DINHEIRO (0 a valor_pago) e o motivo.

    Grava cancelado_em: o reembolso deduz a receita do MES DO CANCELAMENTO (competencia), nao o da
    venda — mes fechado nao muda. UPDATE condicional (so ATIVO cancela): status errado e corrida
    perdida viram RegraDeNegocio. Sessoes ja usadas continuam registradas. Auditoria sem o nome da
    cliente (o log sobrevive a pseudonimizacao).
    """
    from ..excecoes import RegraDeNegocio
    from ..models import CompraPacote
    from ..utils.auditoria import registrar_log

    motivo = (motivo or '').strip()
    if not motivo:
        raise RegraDeNegocio('Descreva o motivo e o acordo do cancelamento.', codigo='motivo_obrigatorio')
    if reembolso is None or reembolso < 0 or reembolso > compra.valor_pago:
        raise RegraDeNegocio('O valor devolvido vai de 0 até o valor pago.', codigo='reembolso_invalido')
    restantes = compra.sessoes_restantes()
    with transaction.atomic():
        cancelada = CompraPacote.objects.filter(pk=compra.pk, status=CompraPacote.STATUS_ATIVO).update(
            status=CompraPacote.STATUS_CANCELADO, valor_reembolsado=reembolso, cancelado_em=timezone.now(),
        )
        if not cancelada:
            atual = CompraPacote.objects.filter(pk=compra.pk).values_list('status', flat=True).first() or ''
            rotulo = str(dict(CompraPacote.STATUS_CHOICES).get(atual, atual or '?')).lower()
            raise RegraDeNegocio(f'O pacote "{compra.pacote_nome}" não está ativo ({rotulo}).',
                                 codigo='compra_nao_ativa')
        registrar_log(
            por, f'Cancelou pacote "{compra.pacote_nome}"', 'compra_pacote', compra.pk,
            {'cliente': compra.cliente_id, 'observacao': motivo, 'valor_pago': str(compra.valor_pago),
             'valor_reembolsado': str(reembolso), 'sessoes_restantes': restantes},
            request=request,
        )
    compra.refresh_from_db()


def prorrogar_validade(compra, nova_data, *, motivo: str, por=None, request=None) -> None:
    """Nova validade (so ADMIN: tela do painel). Compra ATIVA ou EXPIRADA, data depois da atual e
    nao no passado; EXPIRADA volta a ATIVA. Motivo obrigatorio e auditoria de/para (o historico de
    compra_pacote tambem grava a mudanca, no PG)."""
    from ..excecoes import RegraDeNegocio
    from ..models import CompraPacote
    from ..utils.auditoria import registrar_log
    from ..utils.datas import hoje

    motivo = (motivo or '').strip()
    if not motivo:
        raise RegraDeNegocio('Descreva o motivo da prorrogação.', codigo='motivo_obrigatorio')
    with transaction.atomic():
        travada = CompraPacote.objects.select_for_update().get(pk=compra.pk)
        if travada.status not in (CompraPacote.STATUS_ATIVO, CompraPacote.STATUS_EXPIRADO):
            raise RegraDeNegocio(
                f'Só pacote ativo ou expirado é prorrogado (este está {travada.get_status_display().lower()}).',
                codigo='compra_nao_prorrogavel')
        if travada.data_expiracao is not None and nova_data <= travada.data_expiracao:
            raise RegraDeNegocio('A nova validade precisa ser depois da atual.', codigo='validade_nao_posterior')
        if nova_data < hoje():
            raise RegraDeNegocio('A nova validade não pode ser no passado.', codigo='validade_no_passado')
        antes = {'data_expiracao': travada.data_expiracao.isoformat() if travada.data_expiracao else None,
                 'status': travada.status}
        travada.data_expiracao = nova_data
        if travada.status == CompraPacote.STATUS_EXPIRADO:
            travada.status = CompraPacote.STATUS_ATIVO
        travada.save(update_fields=['data_expiracao', 'status', 'atualizado_em'])
        registrar_log(
            por, f'Prorrogou validade do pacote "{travada.pacote_nome}"', 'compra_pacote', travada.pk,
            {'cliente': travada.cliente_id, 'motivo': motivo, 'de': antes,
             'para': {'data_expiracao': nova_data.isoformat(), 'status': travada.status}},
            request=request,
        )
    compra.refresh_from_db()
