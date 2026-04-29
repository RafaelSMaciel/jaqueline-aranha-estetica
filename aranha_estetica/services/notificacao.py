"""Servicos de notificacao por canal — consent granular centralizado.

Regras corporativas:
  - OTP: exclusivamente SMS (Zenvia). Sem fallback email no fluxo principal.
  - E-mail: promocoes, marketing, pacotes, compras (transacional formal).
  - WhatsApp (Meta): lembrete D-1 + NPS pos-atendimento.

Cada disparo verifica PreferenciaComunicacao/consent do cliente antes de enviar.
"""
from __future__ import annotations

import logging
from typing import Optional

from django.utils import timezone

from ..models import Atendimento, Cliente, Notificacao

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  BASE — registro de Notificacao + helpers de consent
# ═══════════════════════════════════════════════════════════════

class _BaseNotificacao:
    @staticmethod
    def _registrar(
        atendimento: Optional[Atendimento],
        *,
        tipo: str,
        canal: str,
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
    def _marcar_enviada(notificacao: Notificacao) -> None:
        notificacao.status_envio = 'ENVIADO'
        notificacao.enviado_em = timezone.now()
        notificacao.save(update_fields=['status_envio', 'enviado_em'])

    @staticmethod
    def _marcar_falhou(notificacao: Notificacao, erro: str = '') -> None:
        notificacao.status_envio = 'FALHOU'
        notificacao.save(update_fields=['status_envio'])
        if erro:
            logger.warning('Notificacao %s falhou: %s', notificacao.pk, erro)


class NotificacaoService(_BaseNotificacao):
    """API legada. Delega para classes por canal."""

    registrar = _BaseNotificacao._registrar
    marcar_enviada = _BaseNotificacao._marcar_enviada
    marcar_falhou = _BaseNotificacao._marcar_falhou


# ═══════════════════════════════════════════════════════════════
#  OTP — SMS Zenvia exclusivo (sem email fallback)
# ═══════════════════════════════════════════════════════════════

class OTPService(_BaseNotificacao):
    """OTP sempre via SMS. Consent implicito: cliente fornece telefone = aceita."""

    @staticmethod
    def enviar_codigo(telefone: str, codigo: str, ip: Optional[str] = None) -> bool:
        from ..utils.sms import enviar_otp_sms
        if not telefone:
            logger.warning('[OTP] telefone ausente, envio abortado')
            return False
        return enviar_otp_sms(telefone, codigo, ip=ip)


# ═══════════════════════════════════════════════════════════════
#  E-MAIL — promocoes, marketing, pacotes, compras
# ═══════════════════════════════════════════════════════════════

class EmailService(_BaseNotificacao):
    """E-mail restrito a transacional/marketing. Sem OTP, sem lembretes de agenda."""

    @staticmethod
    def _tem_consent_marketing(cliente: Cliente) -> bool:
        if not cliente or not cliente.email:
            return False
        return bool(cliente.consent_email_marketing)

    @staticmethod
    def enviar_promocao(cliente: Cliente, dados: dict) -> bool:
        """Campanha marketing. Requer consent_email_marketing."""
        if not EmailService._tem_consent_marketing(cliente):
            logger.info('[EMAIL PROMO] ignorado: sem consent marketing (cli=%s)',
                        cliente.pk if cliente else None)
            return False
        from ..utils.email import enviar_promocao_email
        return enviar_promocao_email(cliente.email, dados, unsub_token=cliente.unsubscribe_token)

    @staticmethod
    def enviar_pacote_expirando(cliente: Cliente, dados: dict) -> bool:
        """Aviso de pacote proximo do vencimento. Transacional (cliente comprou)."""
        if not cliente or not cliente.email:
            return False
        from ..utils.email import enviar_pacote_expirando_email
        return enviar_pacote_expirando_email(cliente.email, dados)

    @staticmethod
    def enviar_confirmacao_compra_pacote(cliente: Cliente, dados: dict) -> bool:
        """Confirmacao formal de compra de pacote. Transacional."""
        if not cliente or not cliente.email:
            return False
        from ..utils.email import _enviar_email
        return _enviar_email(
            cliente.email,
            f'Confirmacao de compra — {dados.get("pacote_nome", "pacote")}',
            'email/pacote_expirando.html',
            dados,
        )

    @staticmethod
    def enviar_aniversario(cliente: Cliente, dados: dict) -> bool:
        """E-mail de aniversario. Requer consent_email_marketing."""
        if not EmailService._tem_consent_marketing(cliente):
            return False
        from ..utils.email import enviar_aniversario_email
        return enviar_aniversario_email(cliente.email, dados, unsub_token=cliente.unsubscribe_token)


# ═══════════════════════════════════════════════════════════════
#  WHATSAPP (Meta) — lembrete D-1 + NPS
# ═══════════════════════════════════════════════════════════════

class WhatsAppService(_BaseNotificacao):
    """WhatsApp restrito a lembrete D-1 e NPS. Consent granular obrigatorio."""

    @staticmethod
    def _tem_telefone(cliente: Cliente) -> bool:
        return bool(cliente and cliente.telefone)

    @staticmethod
    def enviar_lembrete_d1(atendimento: Atendimento) -> Optional[Notificacao]:
        """Lembrete 24h antes do atendimento via template Meta. Requer consent."""
        cliente = atendimento.cliente
        if not WhatsAppService._tem_telefone(cliente):
            return None
        if not cliente.consent_whatsapp_confirmacao:
            logger.info('[WA D-1] ignorado: sem consent confirmacao (cli=%s)', cliente.pk)
            return None
        # evita duplicidade
        ja_enviou = Notificacao.objects.filter(
            atendimento=atendimento, tipo='LEMBRETE', canal='WHATSAPP', status_envio='ENVIADO',
        ).exists()
        if ja_enviou:
            return None

        try:
            from ..utils.whatsapp import enviar_confirmacao_d1
            # enviar_confirmacao_d1 ja cria Notificacao e grava status
            return enviar_confirmacao_d1(atendimento)
        except Exception as e:
            logger.exception('[WA D-1] falha envio (cli=%s): %s', cliente.pk, e)
            return None

    @staticmethod
    def enviar_nps(atendimento: Atendimento) -> Optional[Notificacao]:
        """NPS pos-atendimento via template Meta. Requer consent_whatsapp_nps."""
        cliente = atendimento.cliente
        if not WhatsAppService._tem_telefone(cliente):
            return None
        if not cliente.consent_whatsapp_nps:
            logger.info('[WA NPS] ignorado: sem consent NPS (cli=%s)', cliente.pk)
            return None
        ja_enviou = Notificacao.objects.filter(
            atendimento=atendimento, tipo='NPS', canal='WHATSAPP', status_envio='ENVIADO',
        ).exists()
        if ja_enviou:
            return None

        try:
            import secrets
            from ..utils.whatsapp import enviar_nps_whatsapp, SITE_URL
            token = secrets.token_urlsafe(32)
            notif = Notificacao.objects.create(
                atendimento=atendimento, tipo='NPS', canal='WHATSAPP',
                token=token, status_envio='PENDENTE',
            )
            nps_url = f'{SITE_URL.rstrip("/")}/nps/{token}/'
            if enviar_nps_whatsapp(atendimento, nps_url, token):
                notif.status_envio = 'ENVIADO'
                notif.enviado_em = timezone.now()
                notif.save(update_fields=['status_envio', 'enviado_em'])
            else:
                notif.status_envio = 'FALHOU'
                notif.save(update_fields=['status_envio'])
            return notif
        except Exception as e:
            logger.exception('[WA NPS] falha envio (cli=%s): %s', cliente.pk, e)
            return None
