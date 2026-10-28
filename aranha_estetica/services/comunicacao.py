"""Comunicacao: canais, historico (Notificacao) e a fila de e-mail.

Canais: EMAIL (utils/email), WHATSAPP (utils/whatsapp: lembrete D-1, NPS) e SMS (utils/sms, so o
OTP). Toda mensagem ligada a um atendimento e uma Notificacao (tipo x canal x status) — o
historico do que a cliente e a equipe receberam.

Fila de e-mail (sem Celery): o e-mail transacional nasce Notificacao EMAIL PENDENTE na transacao
de quem pediu (outbox: so existe se a operacao commitou) e sai no on_commit. Falhou: o cron
'emails' (jobs.job_reenviar_emails) reenvia com espera crescente ate MAX_TENTATIVAS; pedido mais
velho que VALIDADE vira FALHOU (aviso velho confunde mais do que ajuda). Sem copia de PII na
fila: destino e texto saem do atendimento NA HORA do envio (cliente esquecida = nada a enviar).

Marketing (aniversario, promocao) e avisos em lote (pacote expirando, detrator) saem direto pelos
jobs — cada um ja e idempotente e o proprio cron e o reenvio.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from ..models import Atendimento, Notificacao
from ..utils.datas import data_local, fmt_local

logger = logging.getLogger(__name__)

MAX_TENTATIVAS = 5
ESPERA_BASE = timedelta(minutes=5)      # 5, 10, 20, 40 min entre as tentativas
VALIDADE = timedelta(hours=48)
LOTE_REENVIO = 200
FORMATO_DATA_HORA = '%d/%m/%Y às %H:%M'

Tipo = Notificacao.Tipo


def link_revisar_agenda(data_hora) -> str:
    """Link (GET) p/ a agenda do portal da profissional no dia LOCAL do atendimento."""
    return f"{settings.SITE_URL}{reverse('aranha:profissional_agenda')}?data={data_local(data_hora):%Y-%m-%d}"


def _valor(at) -> str:
    from .agendamento import formatar_valor_atendimento
    return formatar_valor_atendimento(at)


def _base(at) -> dict:
    return {
        'nome': at.cliente.nome,
        'procedimento': at.procedimento.nome,
        'profissional': at.profissional.nome,
        'data_hora': fmt_local(at.data_hora_inicio, FORMATO_DATA_HORA),
    }


def _email_profissional(at) -> str:
    return getattr(getattr(at.profissional, 'usuario', None), 'email', '') or ''


# tipo -> (funcao de utils.email, destino(at), dados(at))
MENSAGENS_EMAIL: dict[str, tuple[str, Callable[[Atendimento], str | None], Callable[[Atendimento], dict]]] = {
    Tipo.CONFIRMACAO: (
        'enviar_confirmacao_agendamento_email', lambda at: at.cliente.email,
        lambda at: {**_base(at), 'valor': _valor(at)},
    ),
    Tipo.CANCELAMENTO: ('enviar_cancelamento_email', lambda at: at.cliente.email, _base),
    Tipo.APROVACAO: (
        'enviar_aprovacao_profissional_email', _email_profissional,
        lambda at: {
            'profissional': at.profissional.nome, 'cliente': at.cliente.nome,
            'procedimento': at.procedimento.nome,
            'data_hora': fmt_local(at.data_hora_inicio, FORMATO_DATA_HORA),
            'link_revisar': link_revisar_agenda(at.data_hora_inicio),
        },
    ),
}


def enfileirar_email(atendimento, tipo, mensagem: str = '') -> Notificacao | None:
    """Pede o e-mail ``tipo`` do atendimento: Notificacao PENDENTE agora, envio no on_commit.

    mensagem: paragrafo extra do e-mail (ex.: o motivo do cancelamento), gravado na notificacao
    p/ o reenvio sair igual. Sem destinatario (cliente sem e-mail, profissional sem usuario) nao
    cria nada."""
    _funcao, destino, _dados = MENSAGENS_EMAIL[tipo]
    if not destino(atendimento):
        return None
    notif = Notificacao.objects.create(
        atendimento=atendimento, tipo=tipo, canal=Notificacao.Canal.EMAIL, mensagem=mensagem,
        status=Notificacao.Status.PENDENTE, proxima_tentativa_em=timezone.now(),
    )
    transaction.on_commit(lambda: enviar_da_fila(notif.pk), robust=True)
    return notif


def enviar_da_fila(notificacao_id) -> bool:
    """Tenta enviar UMA notificacao de e-mail PENDENTE. True = enviada agora.

    Linha travada (skip_locked no PG): on_commit e cron nunca mandam a mesma duas vezes."""
    from ..utils import email as email_utils

    agora = timezone.now()
    with transaction.atomic():
        notif = (
            Notificacao.objects.select_for_update(skip_locked=True)
            .filter(pk=notificacao_id, canal=Notificacao.Canal.EMAIL, status=Notificacao.Status.PENDENTE)
            .first()
        )
        if notif is None:
            return False
        at = (Atendimento.objects.select_related('cliente', 'procedimento', 'profissional__usuario')
              .get(pk=notif.atendimento_id))
        nome_funcao, destino, dados = MENSAGENS_EMAIL[notif.tipo]
        email = destino(at)
        conteudo = {**dados(at), 'mensagem': notif.mensagem}
        ok = bool(email) and bool(getattr(email_utils, nome_funcao)(email, conteudo))
        if ok:
            notif.status, notif.enviado_em, notif.proxima_tentativa_em = (
                Notificacao.Status.ENVIADO, agora, None)
        else:
            notif.tentativas += 1
            if not email or notif.tentativas >= MAX_TENTATIVAS or notif.criado_em < agora - VALIDADE:
                notif.status, notif.proxima_tentativa_em = Notificacao.Status.FALHOU, None
                logger.error('email_fila_desistiu', extra={'notificacao_id': notif.pk, 'tipo': notif.tipo,
                                                           'tentativas': notif.tentativas})
            else:
                notif.proxima_tentativa_em = agora + ESPERA_BASE * 2 ** (notif.tentativas - 1)
        notif.save(update_fields=['status', 'enviado_em', 'tentativas', 'proxima_tentativa_em', 'atualizado_em'])
    return ok


def reenviar_pendentes(agora=None) -> dict:
    """Cron 'emails': reenvia os PENDENTE vencidos (lote). -> {'enviados', 'pendentes'}."""
    agora = agora or timezone.now()
    ids = list(
        Notificacao.objects.filter(
            canal=Notificacao.Canal.EMAIL, status=Notificacao.Status.PENDENTE,
            proxima_tentativa_em__lte=agora,
        ).order_by('proxima_tentativa_em').values_list('pk', flat=True)[:LOTE_REENVIO]
    )
    enviados = sum(enviar_da_fila(pk) for pk in ids)
    pendentes = Notificacao.objects.filter(
        canal=Notificacao.Canal.EMAIL, status=Notificacao.Status.PENDENTE,
    ).count()
    return {'enviados': enviados, 'pendentes': pendentes}
