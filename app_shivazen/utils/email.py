"""
Email Notification Service — Plataforma de Clinicas

Envia emails transacionais: OTP, confirmacao, cancelamento, pacotes, aniversario.
Usa Django EmailMultiAlternatives com headers RFC 8058 para marketing.
"""
import logging
import os
import smtplib

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

CLINIC_NAME = os.environ.get('CLINIC_NAME', 'Shiva Zen')
DEFAULT_FROM = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@clinica.com.br')
SITE_URL = os.environ.get('SITE_URL', 'http://127.0.0.1:8000').rstrip('/')


def _enviar_email(destinatario, assunto, template, contexto,
                  marketing=False, preheader='', unsub_token=None):
    """Envia email HTML. Marketing inclui List-Unsubscribe (RFC 8058 one-click).

    unsub_token: Cliente.unsubscribe_token. Obrigatorio para marketing=True.
    """
    contexto.setdefault('clinic_name', CLINIC_NAME)
    contexto.setdefault('site_url', SITE_URL)
    contexto.setdefault('preheader', preheader)

    headers = {}
    if marketing and unsub_token:
        unsub_url = f'{SITE_URL}/lgpd/unsubscribe/{unsub_token}/'
        headers['List-Unsubscribe'] = (
            f'<{unsub_url}>, <mailto:{DEFAULT_FROM}?subject=unsubscribe>'
        )
        headers['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
        contexto.setdefault('unsub_url', unsub_url)

    try:
        html = render_to_string(template, contexto)
        texto = strip_tags(html)
        msg = EmailMultiAlternatives(
            subject=assunto,
            body=texto,
            from_email=DEFAULT_FROM,
            to=[destinatario],
            headers=headers or None,
        )
        msg.attach_alternative(html, 'text/html')
        msg.send(fail_silently=False)
        logger.info('[EMAIL] Enviado para %s: %s', destinatario, assunto)
        return True
    except (smtplib.SMTPException, OSError) as e:
        logger.error('[EMAIL] Falha ao enviar para %s: %s', destinatario, e)
        return False


def enviar_codigo_otp_email(email, codigo):
    """Envia codigo OTP de 6 digitos por email."""
    return _enviar_email(
        destinatario=email,
        assunto=f'{CLINIC_NAME} — Codigo de Verificacao',
        template='email/otp.html',
        contexto={'codigo': codigo},
    )


def enviar_confirmacao_agendamento_email(email, dados):
    """Envia confirmacao de agendamento por email (recibo)."""
    return _enviar_email(
        destinatario=email,
        assunto=f'{CLINIC_NAME} — Agendamento Confirmado',
        template='email/confirmacao.html',
        contexto={'dados': dados},
    )


def enviar_cancelamento_email(email, dados):
    """Notifica cancelamento de agendamento por email."""
    return _enviar_email(
        destinatario=email,
        assunto=f'{CLINIC_NAME} — Agendamento Cancelado',
        template='email/cancelamento.html',
        contexto={'dados': dados},
    )


def enviar_pacote_expirando_email(email, dados):
    """Avisa que pacote esta expirando."""
    return _enviar_email(
        destinatario=email,
        assunto=f'{CLINIC_NAME} — Seu pacote esta expirando',
        template='email/pacote_expirando.html',
        contexto={'dados': dados},
    )


def enviar_fila_espera_email(email, dados):
    """Notifica que uma vaga abriu na fila de espera."""
    return _enviar_email(
        destinatario=email,
        assunto=f'{CLINIC_NAME} — Vaga disponivel!',
        template='email/fila_espera.html',
        contexto={'dados': dados},
    )


def enviar_aniversario_email(email, dados, unsub_token=None):
    """Envia email de aniversario com desconto (marketing)."""
    return _enviar_email(
        destinatario=email,
        assunto=f'Feliz Aniversario! {CLINIC_NAME} tem um presente para voce',
        template='email/aniversario.html',
        contexto={'dados': dados},
        marketing=True,
        preheader='Presente de aniversario dentro — descontos exclusivos',
        unsub_token=unsub_token,
    )


def enviar_promocao_email(email, dados, unsub_token=None):
    """Envia promocao mensal (marketing)."""
    preheader = dados.get('preheader', '') if isinstance(dados, dict) else ''
    return _enviar_email(
        destinatario=email,
        assunto=f'{CLINIC_NAME} — Ofertas especiais deste mes',
        template='email/promocao.html',
        contexto={'dados': dados},
        marketing=True,
        preheader=preheader,
        unsub_token=unsub_token,
    )


def enviar_aprovacao_profissional_email(email, dados):
    """Notifica profissional que há novo agendamento pendente de aprovação."""
    return _enviar_email(
        destinatario=email,
        assunto=f'{CLINIC_NAME} — Novo Agendamento Pendente',
        template='email/aprovacao_profissional.html',
        contexto={'dados': dados},
    )


def enviar_nps_email(email, dados):
    """Envia pesquisa NPS por email 24h após atendimento."""
    return _enviar_email(
        destinatario=email,
        assunto=f'{CLINIC_NAME} — Como foi seu atendimento?',
        template='email/nps.html',
        contexto={'dados': dados},
    )


def enviar_termos_pendentes_email(email, dados):
    """Notifica cliente sobre termos pendentes por email."""
    return _enviar_email(
        destinatario=email,
        assunto=f'{CLINIC_NAME} — Termos de Consentimento Pendentes',
        template='email/termos_pendentes.html',
        contexto={'dados': dados},
    )
