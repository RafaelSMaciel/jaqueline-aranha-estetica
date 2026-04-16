"""
Email Notification Service — Plataforma de Clinicas

Envia emails transacionais: OTP, confirmacao, cancelamento, pacotes, aniversario.
Usa Django send_mail com templates HTML renderizados.
"""
import os
import logging
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

CLINIC_NAME = os.environ.get('CLINIC_NAME', 'Clinica Estetica')
DEFAULT_FROM = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@clinica.com.br')
SITE_URL = os.environ.get('SITE_URL', 'http://127.0.0.1:8000').rstrip('/')


def _enviar_email(destinatario, assunto, template, contexto):
    """Envia email HTML usando template Django. Retorna True/False."""
    contexto.setdefault('clinic_name', CLINIC_NAME)
    contexto.setdefault('site_url', SITE_URL)
    try:
        html = render_to_string(template, contexto)
        texto = strip_tags(html)
        send_mail(
            subject=assunto,
            message=texto,
            from_email=DEFAULT_FROM,
            recipient_list=[destinatario],
            html_message=html,
            fail_silently=False,
        )
        logger.info('[EMAIL] Enviado para %s: %s', destinatario, assunto)
        return True
    except Exception as e:
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


def enviar_aniversario_email(email, dados):
    """Envia email de aniversario com desconto."""
    return _enviar_email(
        destinatario=email,
        assunto=f'Feliz Aniversario! {CLINIC_NAME} tem um presente para voce',
        template='email/aniversario.html',
        contexto={'dados': dados},
    )
