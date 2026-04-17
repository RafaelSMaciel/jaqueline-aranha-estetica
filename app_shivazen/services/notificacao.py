"""Servico de notificacoes — agrega WhatsApp, e-mail, criacao de Notificacao."""
import logging

from app_shivazen.models import Atendimento, Notificacao

logger = logging.getLogger(__name__)


class NotificacaoService:
    @staticmethod
    def registrar(
        atendimento: Atendimento,
        *,
        tipo: str = 'LEMBRETE',
        canal: str = 'WHATSAPP',
        mensagem: str = '',
    ) -> Notificacao:
        return Notificacao.objects.create(
            atendimento=atendimento,
            tipo=tipo,
            canal=canal,
            status_envio='PENDENTE',
            mensagem=mensagem,
        )

    @staticmethod
    def marcar_enviada(notificacao: Notificacao) -> None:
        from django.utils import timezone
        notificacao.status_envio = 'ENVIADO'
        notificacao.enviado_em = timezone.now()
        notificacao.save(update_fields=['status_envio', 'enviado_em'])

    @staticmethod
    def marcar_falhou(notificacao: Notificacao, erro: str = '') -> None:
        notificacao.status_envio = 'FALHOU'
        notificacao.save(update_fields=['status_envio'])
        if erro:
            logger.warning('Notificacao %s falhou: %s', notificacao.pk, erro)
