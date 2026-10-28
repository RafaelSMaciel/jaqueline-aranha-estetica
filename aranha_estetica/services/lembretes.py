"""Lembretes: o D-1 da cliente e a pendencia de desfecho da profissional.

Lembrete D-1 (D19): o WhatsApp continua opt-in (politica da Meta) — sai so p/ quem tem
LEMBRETE_AGENDAMENTO/WHATSAPP vigente, com telefone e o canal configurado. Quem nao autorizou (ou
sem o canal) e tem e-mail recebe o lembrete por e-mail, com os mesmos links /confirmar/: aviso
transacional do servico contratado (LGPD art. 7º V), na fila de e-mail (services/comunicacao).
Roda as 08:00 e as 18:00 (quem marcou depois das 8h p/ amanha tambem e lembrada). Idempotente por
atendimento: WhatsApp ENVIADO ou e-mail na fila/enviado = ja lembrado; WhatsApp que falhou e
tentado de novo na rodada seguinte.

Sem desfecho (D14): o sistema nao marca falta sozinho (falta conta no bloqueio online e e
terminal). Todo dia a profissional recebe um push com quantos atendimentos dela ja terminaram e
seguem sem Realizado/Faltou/Cancelado — sem isso a pendencia so aparecia no log do servidor.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from django.db.models import Count, Exists, OuterRef, Q
from django.urls import reverse
from django.utils import timezone

from ..models import Atendimento, Notificacao, Usuario
from ..models.agendamentos import StatusAtendimento
from ..utils import whatsapp as canal_whatsapp
from ..utils.datas import dias_locais, hoje
from . import comunicacao, consentimentos
from .push import send_push_to_user

logger = logging.getLogger(__name__)


def _ja_lembrado():
    """Exists: lembrete deste atendimento ja saiu (qualquer canal) ou o e-mail esta na fila."""
    return Exists(Notificacao.objects.filter(
        atendimento=OuterRef('pk'), tipo=Notificacao.Tipo.LEMBRETE,
    ).filter(
        Q(status=Notificacao.Status.ENVIADO)
        | Q(canal=Notificacao.Canal.EMAIL, status=Notificacao.Status.PENDENTE),
    ))


def a_lembrar(amanha: date | None = None):
    """Atendimentos AGENDADO do dia (local) seguinte ainda nao lembrados, anotados com
    ``autoriza_whatsapp`` (LEMBRETE_AGENDAMENTO/WHATSAPP vigente)."""
    amanha = amanha or hoje() + timedelta(days=1)
    return consentimentos.anotar_vigente(
        Atendimento.objects.filter(**dias_locais('data_hora_inicio', amanha), status=StatusAtendimento.AGENDADO)
        .exclude(_ja_lembrado()),
        *consentimentos.LEMBRETE_WHATSAPP, nome='autoriza_whatsapp',
    ).select_related('cliente', 'profissional', 'procedimento').order_by('data_hora_inicio', 'pk')


def enviar_lembretes_d1(amanha: date | None = None) -> dict[str, int]:
    """Job lembrete_diario. -> {'whatsapp': enviados, 'email': enfileirados}."""
    whatsapp_disponivel = canal_whatsapp.pode_enviar_whatsapp()
    if not whatsapp_disponivel:
        logger.warning('lembrete_d1_whatsapp_nao_configurado')
    resultado = {'whatsapp': 0, 'email': 0}
    for at in a_lembrar(amanha):
        try:
            if at.autoriza_whatsapp and at.cliente.telefone and whatsapp_disponivel:
                notif = comunicacao.enviar_confirmacao_d1(at)
                resultado['whatsapp'] += notif.status == Notificacao.Status.ENVIADO
            elif comunicacao.enfileirar_email(at, Notificacao.Tipo.LEMBRETE):
                resultado['email'] += 1
        except Exception:  # noqa: BLE001 — falha de 1 atendimento nao para o lote (o guard evita reenvio)
            logger.exception('lembrete_d1_falhou', extra={'atendimento_id': at.pk})
    logger.info('lembrete_d1', extra=resultado)
    return resultado


def avisar_sem_desfecho(agora=None) -> int:
    """Job sem_desfecho: push p/ cada profissional com 'N atendimentos aguardam desfecho'.
    Devolve quantas profissionais foram avisadas (com pelo menos 1 entrega)."""
    pendentes = dict(
        Atendimento.objects.sem_desfecho(ate=agora or timezone.now())
        .order_by().values_list('profissional_id').annotate(n=Count('id'))
    )
    if not pendentes:
        return 0
    logger.warning('atendimentos_sem_desfecho', extra={'por_profissional': pendentes})
    avisadas = 0
    for usuario in Usuario.objects.filter(profissional_id__in=pendentes, ativo=True):
        n = pendentes[usuario.profissional_id]
        texto = '1 atendimento aguarda desfecho' if n == 1 else f'{n} atendimentos aguardam desfecho'
        try:
            entregues = send_push_to_user(usuario, {
                'head': 'Atendimentos sem desfecho',
                'body': f'{texto}: marque Realizado ou Faltou na sua agenda.',
                'url': reverse('aranha:profissional_agenda'),
            })
        except Exception:  # noqa: BLE001 — push e best-effort
            logger.exception('push_sem_desfecho_falhou', extra={'profissional_id': usuario.profissional_id})
            continue
        avisadas += entregues > 0
    return avisadas
