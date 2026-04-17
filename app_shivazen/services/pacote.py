"""Servico de pacotes — debito de sessoes, expiracao."""
import logging

from django.db import transaction
from django.utils import timezone

from app_shivazen.models import Atendimento, PacoteCliente, SessaoPacote

logger = logging.getLogger(__name__)


class PacoteService:
    @classmethod
    @transaction.atomic
    def debitar_sessao_por_atendimento(cls, atendimento: Atendimento) -> SessaoPacote | None:
        """Encontra pacote ativo do cliente com o procedimento e debita uma sessao.

        Retorna SessaoPacote criada ou None se nao havia pacote aplicavel.
        """
        pacotes_ativos = (
            PacoteCliente.objects
            .filter(cliente=atendimento.cliente, status='ATIVO')
            .select_related('pacote')
            .prefetch_related('pacote__itens', 'sessoes_realizadas')
            .order_by('criado_em')
        )

        hoje = timezone.now().date()
        for pc in pacotes_ativos:
            if pc.data_expiracao and pc.data_expiracao < hoje:
                pc.status = 'EXPIRADO'
                pc.save(update_fields=['status'])
                continue

            item = pc.pacote.itens.filter(procedimento=atendimento.procedimento).first()
            if not item:
                continue

            sessoes_ja_feitas = pc.sessoes_realizadas.filter(
                atendimento__procedimento=atendimento.procedimento,
            ).count()
            if sessoes_ja_feitas >= item.quantidade_sessoes:
                continue

            sessao = SessaoPacote.objects.create(
                pacote_cliente=pc,
                atendimento=atendimento,
            )
            logger.info(
                "[PACOTE] Sessao %s/%s debitada do pacote %s",
                sessoes_ja_feitas + 1, item.quantidade_sessoes, pc.pk,
            )
            pc.verificar_finalizacao()
            return sessao

        return None
