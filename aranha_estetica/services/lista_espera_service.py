"""Lista de Espera — notifica cliente compativel quando slot abre.

Handler em AtendimentoCancelado: busca registros ListaEspera com
match (procedimento + data_desejada proxima) e dispara WhatsApp template
'lista_espera_vaga' com link de reserva temporaria (TTL 30min).
"""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from typing import List

from django.db import transaction
from django.utils import timezone

from ..constants import TTL_RESERVA_LISTA_ESPERA_MINUTOS
from ..domain.event_bus import EventBus
from ..domain.events import AtendimentoCancelado
from ..models import Atendimento, ListaEspera

logger = logging.getLogger(__name__)


class ListaEsperaService:
    """Match-and-notify de espera quando slot vaga."""

    @staticmethod
    @transaction.atomic
    def notificar_compativeis(atendimento_cancelado: Atendimento) -> int:
        """Busca ListaEspera compativel com slot recem-vago. Marca notificado=True.

        Match heuristico:
          - mesmo procedimento
          - data_desejada == data do slot vago (mesmo dia)
          - profissional_desejado == profissional do slot OU NULL
          - notificado == False
          - registro mais antigo primeiro (FIFO)

        Returns: numero de notificacoes disparadas.
        """
        slot_data = atendimento_cancelado.data_hora_inicio.date()
        candidatos: List[ListaEspera] = list(
            ListaEspera.objects.select_related('cliente', 'procedimento', 'profissional_desejado')
            .filter(
                procedimento=atendimento_cancelado.procedimento,
                data_desejada=slot_data,
                notificado=False,
            )
            .order_by('criado_em')
        )

        if not candidatos:
            return 0

        # Filtro por profissional preferido (cliente que pediu prof especifico
        # so e notificado se prof do slot bate)
        match = []
        for c in candidatos:
            if c.profissional_desejado_id and c.profissional_desejado_id != atendimento_cancelado.profissional_id:
                continue
            match.append(c)

        if not match:
            return 0

        # Notifica TODOS compativeis simultaneamente — primeiro a clicar reserva
        notificados = 0
        for espera in match:
            token = secrets.token_urlsafe(24)
            espera.token_reserva = token
            espera.expira_em = timezone.now() + timedelta(minutes=TTL_RESERVA_LISTA_ESPERA_MINUTOS)
            espera.notificado = True
            espera.save(update_fields=['token_reserva', 'expira_em', 'notificado'])

            # Notifica via WhatsApp se consent + telefone
            cliente = espera.cliente
            if cliente.telefone and cliente.consent_whatsapp_confirmacao:
                _enviar_wa_lista_espera(espera, atendimento_cancelado, token)
            notificados += 1

        logger.info(
            'lista_espera_notificados',
            extra={
                'atendimento_cancelado_id': atendimento_cancelado.pk,
                'count': notificados,
            },
        )
        return notificados


def _enviar_wa_lista_espera(espera: ListaEspera, slot: Atendimento, token: str) -> None:
    """Best-effort WhatsApp template. Falha silenciosa = log."""
    try:
        from ..utils.whatsapp import enviar_template_whatsapp
        components = [{
            'type': 'body',
            'parameters': [
                {'type': 'text', 'text': espera.cliente.nome_completo},
                {'type': 'text', 'text': slot.procedimento.nome},
                {'type': 'text', 'text': slot.data_hora_inicio.strftime('%d/%m %H:%M')},
            ],
        }]
        enviar_template_whatsapp(
            espera.cliente.telefone,
            'lista_espera_vaga',
            components=components,
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning(
            'lista_espera_wa_falha',
            extra={'espera_id': espera.pk, 'error': str(exc)},
        )


@EventBus.subscribe(AtendimentoCancelado)
def _on_cancelado_notifica_espera(event: AtendimentoCancelado) -> None:
    try:
        atendimento = Atendimento.objects.select_related('procedimento', 'profissional').get(
            pk=event.atendimento_id,
        )
    except Atendimento.DoesNotExist:
        return
    ListaEsperaService.notificar_compativeis(atendimento)
