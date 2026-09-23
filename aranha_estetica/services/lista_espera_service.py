"""Lista de Espera — avisa clientes compativeis quando um horario vaga.

Mecanismo UNICO: signals.processar_mudanca_status chama
`ListaEsperaService.notificar_compativeis` quando um atendimento ativo vira
CANCELADO/REAGENDADO (qualquer caminho: FSM, painel, Django admin). A selecao
roda na transacao de quem liberou a vaga; e-mail/WhatsApp so saem no
on_commit (nunca I/O de rede dentro da transacao, nunca aviso de vaga que
sofreu rollback). O cliente so e marcado `notificado` quando algum canal
realmente entregou — senao continua na lista para contato manual da equipe.
"""
from __future__ import annotations

import logging
from typing import List

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from ..models import Atendimento, ListaEspera
from ..utils.datas import data_local, fmt_local

logger = logging.getLogger(__name__)


class ListaEsperaService:
    """Match-and-notify de espera quando slot vaga."""

    @staticmethod
    def candidatos(atendimento_liberado: Atendimento) -> List[ListaEspera]:
        """Registros compativeis com o horario liberado (FIFO).

        Match: mesmo procedimento, data_desejada == dia (local) do horario,
        profissional_desejado vazio ou igual ao do horario, ainda nao notificado.
        """
        slot_data = data_local(atendimento_liberado.data_hora_inicio)
        return [
            espera for espera in (
                ListaEspera.objects.select_related('cliente', 'procedimento', 'profissional_desejado')
                .filter(
                    procedimento_id=atendimento_liberado.procedimento_id,
                    data_desejada=slot_data,
                    notificado=False,
                )
                .order_by('criado_em')
            )
            if not espera.profissional_desejado_id
            or espera.profissional_desejado_id == atendimento_liberado.profissional_id
        ]

    @staticmethod
    def notificar_compativeis(atendimento_liberado: Atendimento) -> int:
        """Agenda (on_commit) o aviso aos compativeis. Retorna quantos foram agendados.

        Vaga no passado (ex.: limpeza automatica de pendentes vencidos) nao
        gera aviso.
        """
        if atendimento_liberado.data_hora_inicio <= timezone.now():
            return 0

        esperas = ListaEsperaService.candidatos(atendimento_liberado)
        if not esperas:
            return 0

        ids = [e.pk for e in esperas]
        atendimento_id = atendimento_liberado.pk
        transaction.on_commit(lambda: _disparar_avisos(ids, atendimento_id))
        logger.info(
            'lista_espera_avisos_agendados',
            extra={'atendimento_id': atendimento_id, 'count': len(ids)},
        )
        return len(ids)


def _link_agendamento(procedimento_id: int) -> str:
    return f"{settings.SITE_URL.rstrip('/')}{reverse('aranha:agendamento_publico')}?procedimento={procedimento_id}"


def _disparar_avisos(espera_ids, atendimento_id) -> int:
    """Pos-commit: envia e-mail e WhatsApp; marca notificado so com entrega."""
    try:
        slot = Atendimento.objects.select_related('procedimento').get(pk=atendimento_id)
    except Atendimento.DoesNotExist:
        return 0
    link = _link_agendamento(slot.procedimento_id)
    entregues = 0
    for espera in (ListaEspera.objects.select_related('cliente', 'procedimento')
                   .filter(pk__in=espera_ids, notificado=False).order_by('criado_em')):
        ok_email = _enviar_email_lista_espera(espera, slot, link)
        ok_wa = _enviar_wa_lista_espera(espera, slot, link)
        if ok_email or ok_wa:
            # UPDATE condicional: dois avisos concorrentes nao duplicam a marcacao.
            entregues += ListaEspera.objects.filter(pk=espera.pk, notificado=False).update(notificado=True)
    logger.info('lista_espera_notificados', extra={'atendimento_id': atendimento_id, 'count': entregues})
    return entregues


def _nome_para(espera: ListaEspera, destino: str) -> str:
    """Nome do cadastro so quando o aviso vai p/ o e-mail DO cadastro.

    email_contato vem do formulario publico anonimo (sem OTP): quem digitou o
    telefone de outra pessoa nao pode receber o nome cadastrado do titular.
    (O aviso manual do painel, admin_notificar_espera, segue a mesma regra.)
    """
    cadastrado = (espera.cliente.email or '').strip().lower()
    if cadastrado and (destino or '').strip().lower() == cadastrado:
        return espera.cliente.nome or ''
    return ''


def _enviar_email_lista_espera(espera: ListaEspera, slot: Atendimento, link: str) -> bool:
    """E-mail de vaga (o cliente pediu o aviso ao entrar na lista)."""
    destino = espera.email_contato or espera.cliente.email
    if not destino:
        return False
    try:
        from ..utils.email import enviar_fila_espera_email
        return bool(enviar_fila_espera_email(destino, {
            'nome': _nome_para(espera, destino),
            'procedimento': slot.procedimento.nome,
            'data': fmt_local(slot.data_hora_inicio, '%d/%m/%Y'),
            'hora': fmt_local(slot.data_hora_inicio, '%H:%M'),
            'link': link,
        }))
    except Exception as exc:  # noqa: BLE001 — best-effort por destinatario
        logger.warning('lista_espera_email_falha', extra={'espera_id': espera.pk, 'erro': type(exc).__name__})
        return False


def _enviar_wa_lista_espera(espera: ListaEspera, slot: Atendimento, link: str) -> bool:
    """WhatsApp template de vaga (requer telefone + consentimento de WhatsApp)."""
    cliente = espera.cliente
    if not (cliente.telefone and cliente.consent_whatsapp_confirmacao):
        return False
    try:
        from ..utils.whatsapp import TEMPLATE_LISTA_ESPERA, enviar_template_whatsapp
        components = [{
            'type': 'body',
            'parameters': [
                {'type': 'text', 'text': cliente.nome},
                {'type': 'text', 'text': slot.procedimento.nome},
                {'type': 'text', 'text': fmt_local(slot.data_hora_inicio, '%d/%m %H:%M')},
                {'type': 'text', 'text': link},
            ],
        }]
        return bool(enviar_template_whatsapp(cliente.telefone, TEMPLATE_LISTA_ESPERA, components=components))
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning('lista_espera_wa_falha', extra={'espera_id': espera.pk, 'erro': type(exc).__name__})
        return False
