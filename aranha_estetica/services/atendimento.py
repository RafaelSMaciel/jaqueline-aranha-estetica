"""Caso de uso do atendimento: TODOS os efeitos de criar e de mudar status, em ordem, num lugar.

Antes os efeitos moravam em dois mecanismos implicitos: post_save (signals.py — faltas, pacote,
lista de espera, push/gcal) e EventBus (comissao, cashback, retorno — assinantes registrados por
import no apps.ready, ordem = ordem dos imports). Saber o que acontece ao marcar REALIZADO exigia
abrir cinco arquivos. Agora:

- ``ao_mudar_status``: chamado pela FSM (Atendimento._transicionar) na MESMA transacao do status
  — efeito que falha desfaz a transicao inteira (status + livro-razao gravam juntos ou nada grava);
- ``ao_criar``: chamado por services.agenda.reservar (as 5 portas) e pelo "adicionar" do Django
  admin — push e Google Calendar da profissional.

I/O externo (e-mail, WhatsApp, push, gcal) so no on_commit, best-effort.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import F, Q

from ..models.agendamentos import STATUS_ATIVOS, StatusAtendimento

logger = logging.getLogger(__name__)

# Saidas que devolvem o horario p/ a lista de espera.
STATUS_LIBERAM_VAGA = (StatusAtendimento.CANCELADO, StatusAtendimento.REAGENDADO)


def ao_mudar_status(at, anterior, novo, *, pela_cliente=False) -> None:
    """Efeitos de ``anterior`` -> ``novo`` (a FSM ja validou a transicao e gravou o status).

    pela_cliente: a propria cliente cancelou/remarcou (portal ou link) — a profissional e avisada
    (push); quando a clinica cancela, quem e avisada e a cliente (services.agendamento)."""
    if anterior == StatusAtendimento.REALIZADO:
        _desfazer_realizacao(at)
    if novo == StatusAtendimento.REALIZADO:
        from .comissao import ComissaoService
        from .fidelidade import FidelidadeService

        at.cliente.resetar_faltas()
        debitar_sessao_de_pacote(at)
        ComissaoService.calcular_comissao(at)
        FidelidadeService.liberar_cashback_indicacao(at)
        # retorno obrigatorio fica DEVIDO (services.retorno.devidos): a recepcao marca com a cliente
    elif novo == StatusAtendimento.CANCELADO:
        from .comissao import ComissaoService
        from .fidelidade import FidelidadeService

        ComissaoService.estornar_comissao(at)
        FidelidadeService.estornar_cashback_de_atendimento(at, motivo='atendimento cancelado')
    elif novo == StatusAtendimento.FALTOU:
        at.cliente.registrar_falta()   # 3 faltas seguidas -> agendamento online suspenso
        logger.info('falta_registrada', extra={'cliente_id': at.cliente_id,
                                               'faltas': at.cliente.faltas_consecutivas})

    if novo in (StatusAtendimento.CANCELADO, StatusAtendimento.FALTOU, StatusAtendimento.REAGENDADO):
        from .fidelidade import FidelidadeService
        FidelidadeService.devolver_credito_usado(at)   # credito de indicacao abatido volta p/ a cliente

    if novo in STATUS_LIBERAM_VAGA and anterior in STATUS_ATIVOS:
        _avisar_lista_de_espera(at)
        if pela_cliente:
            titulo = 'Cliente cancelou' if novo == StatusAtendimento.CANCELADO else 'Cliente remarcou'
            transaction.on_commit(lambda: _push_profissional(at, titulo))


def _desfazer_realizacao(at) -> None:
    """Inverso dos efeitos do REALIZADO (Atendimento.desfazer_realizacao). Comissao ja paga recusa
    (RegraDeNegocio) e desfaz a transicao inteira. As faltas zeradas no Realizado nao voltam."""
    from ..models import Atendimento
    from .comissao import ComissaoService
    from .fidelidade import FidelidadeService

    ComissaoService.estornar_para_desfazer(at)
    FidelidadeService.estornar_cashback_de_atendimento(at, motivo='realização desfeita')
    devolver_sessao_de_pacote(at)
    for retorno in Atendimento.objects.filter(atendimento_origem=at, eh_retorno=True, status__in=STATUS_ATIVOS):
        retorno.cancelar(motivo='realização do atendimento de origem desfeita')   # avisa a cliente (painel)


def devolver_sessao_de_pacote(at) -> None:
    """Apaga o consumo do atendimento e reabre a compra que tinha finalizado com ele."""
    from ..models import CompraPacote, ConsumoSessao
    from ..utils.datas import hoje

    consumo = ConsumoSessao.objects.select_related('compra_pacote').filter(atendimento=at).first()
    if consumo is None:
        return
    compra = consumo.compra_pacote
    consumo.delete()
    if compra.status == CompraPacote.STATUS_FINALIZADO:
        vencida = compra.data_expiracao is not None and compra.data_expiracao < hoje()
        compra.status = CompraPacote.STATUS_EXPIRADO if vencida else CompraPacote.STATUS_ATIVO
        compra.save(update_fields=['status'])
    logger.info('pacote_sessao_devolvida', extra={'compra_pacote_id': compra.pk, 'atendimento_id': at.pk})


def ao_criar(at) -> None:
    """Novo atendimento: push p/ a profissional e evento no Google Calendar dela (on_commit)."""
    transaction.on_commit(lambda: _push_profissional(at, 'Novo agendamento'))
    transaction.on_commit(lambda: _sync_gcal(at))


def debitar_sessao_de_pacote(at) -> None:
    """Debita 1 sessao do pacote da cliente que cobre o procedimento.

    - Saldo pelo ITEM CONGELADO na venda (CompraPacoteItem), nunca pelo catalogo (editavel).
    - Retorno gratuito NAO consome sessao paga (REGRAS §7).
    - Validade na DATA DA SESSAO (local), nao no dia em que se marca REALIZADO: sessao feita ate o
      ultimo dia debita mesmo com o pacote ja EXPIRADO pelo job; sessao apos a validade nao.
    - select_for_update serializa debitos concorrentes da mesma cliente (no PG o
      tg_consumo_sessao_validar trava o item e confere o saldo de novo).
    """
    from ..models import CompraPacote, ConsumoSessao
    from ..utils.datas import data_local

    if at.eh_retorno or hasattr(at, 'sessao_pacote_vinculada'):
        return
    data_sessao = data_local(at.data_hora_inicio)
    with transaction.atomic():
        pacotes = CompraPacote.objects.select_for_update().filter(
            cliente_id=at.cliente_id, status__in=(CompraPacote.STATUS_ATIVO, CompraPacote.STATUS_EXPIRADO),
        ).filter(
            Q(data_expiracao__isnull=True) | Q(data_expiracao__gte=data_sessao),
        ).order_by(F('data_expiracao').asc(nulls_last=True), 'criado_em')
        for pc in pacotes:
            item = pc.itens.filter(procedimento_id=at.procedimento_id).first()
            if item is None:
                continue
            feitas = item.consumos.count()
            if feitas < item.quantidade_sessoes:
                ConsumoSessao.objects.create(compra_pacote=pc, compra_pacote_item=item, atendimento=at)
                logger.info('pacote_sessao_debitada', extra={
                    'compra_pacote_id': pc.pk, 'sessao': feitas + 1, 'total': item.quantidade_sessoes,
                })
                pc.verificar_finalizacao()
                return


def _avisar_lista_de_espera(at) -> None:
    """Vaga liberada: seleciona os compativeis nesta transacao (e-mail/WhatsApp no on_commit do
    service). Savepoint + best-effort: o aviso nunca desfaz o cancelamento."""
    from .lista_espera import ListaEsperaService
    try:
        with transaction.atomic():
            ListaEsperaService.notificar_compativeis(at)
    except Exception:  # noqa: BLE001
        logger.exception('lista_espera_falhou', extra={'atendimento_id': at.pk})


def _push_profissional(at, titulo) -> None:
    """Push p/ a profissional do atendimento (best-effort: sem inscricao, nada acontece)."""
    try:
        user = getattr(at.profissional, 'usuario', None)
        if user is None:
            return
        from ..utils.datas import fmt_local
        from .push import send_push_to_user
        send_push_to_user(user, {
            'head': titulo,
            'body': f'{at.cliente.nome} - {at.procedimento.nome} em {fmt_local(at.data_hora_inicio, "%d/%m %H:%M")}',
            'url': '/profissional/',
        })
    except Exception:  # noqa: BLE001 — push e best-effort
        logger.exception('push_profissional_falhou', extra={'atendimento_id': at.pk})


def _sync_gcal(at) -> None:
    try:
        from .gcal import gcal_disponivel, push_atendimento
        if gcal_disponivel() and at.profissional.gcal_refresh_token:
            push_atendimento(at)
    except Exception:  # noqa: BLE001 — sync e best-effort
        logger.exception('gcal_push_falhou', extra={'atendimento_id': at.pk})
