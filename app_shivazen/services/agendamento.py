"""Servico de agendamento — orquestra criacao, cancelamento, reagendamento."""
import logging
from datetime import timedelta
from typing import Optional

from django.db import transaction
from django.utils import timezone

from app_shivazen.models import Atendimento, Cliente, Procedimento, Profissional

logger = logging.getLogger(__name__)


class AgendamentoService:
    """Encapsula regras de negocio de atendimentos."""

    STATUS_ATIVOS = ['PENDENTE', 'AGENDADO', 'CONFIRMADO']

    @classmethod
    @transaction.atomic
    def criar(
        cls,
        *,
        cliente: Cliente,
        profissional: Profissional,
        procedimento: Procedimento,
        data_hora_inicio,
        valor_cobrado: Optional[float] = None,
        valor_original: Optional[float] = None,
        descricao_preco: str = "",
        status: str = 'PENDENTE',
    ) -> Atendimento:
        """Cria atendimento com checagem de colisao de horario."""
        fim = data_hora_inicio + timedelta(minutes=procedimento.duracao_minutos or 30)

        conflito = Atendimento.objects.filter(
            profissional=profissional,
            status__in=cls.STATUS_ATIVOS,
            data_hora_inicio__lt=fim,
            data_hora_fim__gt=data_hora_inicio,
        ).exists()
        if conflito:
            raise ValueError("Horario ja ocupado para este profissional.")

        atendimento = Atendimento.objects.create(
            cliente=cliente,
            profissional=profissional,
            procedimento=procedimento,
            data_hora_inicio=data_hora_inicio,
            data_hora_fim=fim,
            valor_cobrado=valor_cobrado,
            valor_original=valor_original,
            descricao_preco=descricao_preco,
            status=status,
        )
        logger.info("Atendimento %s criado (cliente=%s)", atendimento.pk, cliente.pk)
        return atendimento

    @classmethod
    @transaction.atomic
    def cancelar(cls, atendimento: Atendimento, motivo: str = "") -> Atendimento:
        if atendimento.status in ('CANCELADO', 'REALIZADO'):
            raise ValueError(f"Atendimento ja esta em estado terminal: {atendimento.status}")
        atendimento.status = 'CANCELADO'
        atendimento.save(update_fields=['status', 'atualizado_em'])
        logger.info("Atendimento %s cancelado. Motivo: %s", atendimento.pk, motivo)
        return atendimento

    @classmethod
    @transaction.atomic
    def reagendar(cls, atendimento: Atendimento, nova_data_inicio) -> Atendimento:
        """Cria novo atendimento vinculado ao anterior via reagendado_de."""
        novo = cls.criar(
            cliente=atendimento.cliente,
            profissional=atendimento.profissional,
            procedimento=atendimento.procedimento,
            data_hora_inicio=nova_data_inicio,
            valor_cobrado=atendimento.valor_cobrado,
            valor_original=atendimento.valor_original,
            descricao_preco=atendimento.descricao_preco or "",
        )
        novo.reagendado_de = atendimento
        novo.save(update_fields=['reagendado_de'])

        atendimento.status = 'REAGENDADO'
        atendimento.save(update_fields=['status', 'atualizado_em'])
        return novo

    @staticmethod
    def ativos_no_futuro_do_cliente(cliente: Cliente):
        """Queryset otimizado dos atendimentos futuros do cliente."""
        return (
            Atendimento.objects
            .filter(
                cliente=cliente,
                data_hora_inicio__gte=timezone.now(),
                status__in=AgendamentoService.STATUS_ATIVOS,
            )
            .select_related('profissional', 'procedimento')
            .order_by('data_hora_inicio')
        )
