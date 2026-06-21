"""Servico de pacotes — debito de sessoes, expiracao."""
import logging

from django.db import transaction
from django.utils import timezone

from aranha_estetica.models import Atendimento, CompraPacote, ConsumoSessao

logger = logging.getLogger(__name__)


class PacoteService:
    @classmethod
    @transaction.atomic
    def debitar_sessao_por_atendimento(cls, atendimento: Atendimento) -> ConsumoSessao | None:
        """Encontra pacote ativo do cliente com o procedimento e debita uma sessao.

        Retorna ConsumoSessao criada ou None se nao havia pacote aplicavel.
        """
        # select_for_update serializa o debito: dois atendimentos concorrentes
        # do mesmo cliente/procedimento nao podem ambos ler a contagem antiga e
        # criar consumo alem do limite do pacote (over-debit).
        pacotes_ativos = (
            CompraPacote.objects
            .select_for_update()
            .filter(cliente=atendimento.cliente, status='ATIVO')
            .select_related('pacote')
            .prefetch_related('pacote__itens', 'sessoes_realizadas__atendimento')
            .order_by('criado_em')
        )

        hoje = timezone.now().date()
        for pc in pacotes_ativos:
            if pc.data_expiracao and pc.data_expiracao < hoje:
                pc.status = 'EXPIRADO'
                pc.save(update_fields=['status'])
                continue

            # Filtra em memoria sobre objetos prefetchados (evita N+1: usar
            # .filter() em cima do related descartaria o prefetch).
            item = next(
                (i for i in pc.pacote.itens.all()
                 if i.procedimento_id == atendimento.procedimento_id),
                None,
            )
            if not item:
                continue

            sessoes_ja_feitas = sum(
                1 for s in pc.sessoes_realizadas.all()
                if s.atendimento.procedimento_id == atendimento.procedimento_id
            )
            if sessoes_ja_feitas >= item.quantidade_sessoes:
                continue

            sessao = ConsumoSessao.objects.create(
                compra_pacote=pc,
                atendimento=atendimento,
            )
            logger.info(
                "[PACOTE] Sessao %s/%s debitada do pacote %s",
                sessoes_ja_feitas + 1, item.quantidade_sessoes, pc.pk,
            )
            pc.verificar_finalizacao()
            return sessao

        return None
