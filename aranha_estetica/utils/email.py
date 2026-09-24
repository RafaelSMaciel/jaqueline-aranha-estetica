"""
Email Notification Service — Plataforma de Clinicas

Envia emails transacionais: confirmacao, cancelamento, pacotes, aniversario,
fila de espera, aprovacao profissional, promocao, termos pendentes.
(OTP nao usa email — canal exclusivo SMS via utils.sms.)
Usa Django EmailMultiAlternatives com headers RFC 8058 para marketing.

Falha fechada (contrato 7): fora de DEBUG, backend console/dummy/filebased =
e-mail nao configurado -> as funcoes retornam False (nada de "sucesso" so no
log). Fonte unica do contrato: outros modulos importam email_configurado daqui.

Provedor HTTP (django-anymail; o Railway Hobby bloqueia SMTP) conta como
entrega real quando a chave do ESP esta em settings.ANYMAIL (montado da env em
settings/base.py); sem ela todo envio falharia -> tambem "nao configurado".
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import strip_tags

from .pii import mask_email

logger = logging.getLogger(__name__)

# Backends que nao entregam nada (imprimem/descartam/gravam no disco efemero
# do container; inclui anymail.backends.console). locmem NAO entra: e o backend
# do test runner (so o check de deploy o trata como aviso, idem o test do anymail).
_BACKENDS_SEM_ENTREGA = ('console.EmailBackend', 'dummy.EmailBackend', 'filebased.EmailBackend')

# django-anymail: ESP (modulo em anymail.backends) -> chave exigida em
# settings.ANYMAIL. settings/base.py so repassa da env as chaves destes ESPs.
ANYMAIL_CHAVE_POR_ESP = {
    'resend': 'RESEND_API_KEY',
    'brevo': 'BREVO_API_KEY',
    'sendgrid': 'SENDGRID_API_KEY',
    'mailgun': 'MAILGUN_API_KEY',
    'postmark': 'POSTMARK_SERVER_TOKEN',
}
# Backends do anymail que nao falam com ESP (sem chave): console/test
_ANYMAIL_SEM_ESP = ('console', 'test')


def esp_anymail(backend: str) -> str:
    """'resend' p/ 'anymail.backends.resend.EmailBackend'; '' se nao e anymail."""
    partes = (backend or '').split('.')
    if len(partes) >= 3 and partes[:2] == ['anymail', 'backends']:
        return partes[2]
    return ''


def anymail_chave_faltando(backend=None) -> str:
    """Chave do ESP ausente em settings.ANYMAIL ('' = nada falta ou nao e anymail).

    ESP fora de ANYMAIL_CHAVE_POR_ESP (os settings nao leem a chave dele da env)
    devolve '<ESP>_*'.
    """
    if backend is None:
        backend = getattr(settings, 'EMAIL_BACKEND', '') or ''
    esp = esp_anymail(backend)
    if not esp or esp in _ANYMAIL_SEM_ESP:
        return ''
    chave = ANYMAIL_CHAVE_POR_ESP.get(esp)
    if chave is None:
        return f'{esp.upper()}_*'
    valor = (getattr(settings, 'ANYMAIL', None) or {}).get(chave)
    return '' if str(valor or '').strip() else chave


def email_configurado() -> bool:
    """True se o backend entrega de fato (em DEBUG o console basta)."""
    if settings.DEBUG:
        return True
    backend = getattr(settings, 'EMAIL_BACKEND', '') or ''
    if backend.endswith(_BACKENDS_SEM_ENTREGA):
        return False
    # anymail sem a chave do ESP: todo envio levantaria AnymailConfigurationError
    return not anymail_chave_faltando(backend)


def _nome_clinica() -> str:
    from .branding import get_branding
    return get_branding()['CLINIC_NAME']


def _remetente() -> str:
    return getattr(settings, 'DEFAULT_FROM_EMAIL', '') or 'noreply@localhost'


def url_descadastro(unsub_token: str) -> str:
    """URL absoluta do descadastro (link do rodape + List-Unsubscribe)."""
    return settings.SITE_URL.rstrip('/') + reverse('aranha:lgpd_unsubscribe', args=[unsub_token])


def _enviar_email(destinatario, assunto, template, contexto,
                  marketing=False, preheader='', unsub_token=None):
    """Envia email HTML. Marketing inclui List-Unsubscribe (RFC 8058 one-click).

    unsub_token: Cliente.token_descadastro. Obrigatorio para marketing=True
    (sem ele o e-mail NAO sai — LGPD exige o opt-out em toda mensagem).
    Retorna True so se o backend aceitou a mensagem.
    """
    if not destinatario:
        return False
    if not email_configurado():
        logger.warning('email_nao_configurado', extra={'template': template})
        return False
    if marketing and not unsub_token:
        logger.error('email_marketing_sem_descadastro', extra={'template': template})
        return False

    contexto.setdefault('clinic_name', _nome_clinica())
    contexto.setdefault('site_url', settings.SITE_URL.rstrip('/'))
    contexto.setdefault('preheader', preheader)

    remetente = _remetente()
    headers = {}
    if marketing:
        unsub_url = url_descadastro(unsub_token)
        headers['List-Unsubscribe'] = (
            f'<{unsub_url}>, <mailto:{remetente}?subject=unsubscribe>'
        )
        headers['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
        contexto.setdefault('unsub_url', unsub_url)

    try:
        html = render_to_string(template, contexto)
        texto = strip_tags(html)
        msg = EmailMultiAlternatives(
            subject=assunto,
            body=texto,
            from_email=remetente,
            to=[destinatario],
            headers=headers or None,
        )
        msg.attach_alternative(html, 'text/html')
        enviados = msg.send(fail_silently=False)
    except Exception as e:  # noqa: BLE001 — SMTP/API do provedor: nunca quebra o fluxo
        logger.error(
            'email_falha_envio',
            extra={'destinatario': mask_email(destinatario), 'template': template,
                   'erro': type(e).__name__},
        )
        return False
    if not enviados:
        logger.error('email_nao_aceito', extra={'destinatario': mask_email(destinatario), 'template': template})
        return False
    logger.info('email_enviado', extra={'destinatario': mask_email(destinatario), 'template': template})
    return True


def enviar_confirmacao_agendamento_email(email, dados):
    """Envia confirmacao de agendamento por email (recibo)."""
    return _enviar_email(
        destinatario=email,
        assunto=f'{_nome_clinica()} — Agendamento confirmado',
        template='email/confirmacao.html',
        contexto={'dados': dados},
    )


def enviar_cancelamento_email(email, dados):
    """Notifica cancelamento de agendamento por email."""
    return _enviar_email(
        destinatario=email,
        assunto=f'{_nome_clinica()} — Agendamento cancelado',
        template='email/cancelamento.html',
        contexto={'dados': dados},
    )


def enviar_pacote_expirando_email(email, dados):
    """Avisa que pacote esta expirando."""
    return _enviar_email(
        destinatario=email,
        assunto=f'{_nome_clinica()} — Seu pacote está expirando',
        template='email/pacote_expirando.html',
        contexto={'dados': dados},
    )


def enviar_fila_espera_email(email, dados):
    """Notifica que uma vaga abriu na fila de espera."""
    return _enviar_email(
        destinatario=email,
        assunto=f'{_nome_clinica()} — Vaga disponível!',
        template='email/fila_espera.html',
        contexto={'dados': dados},
    )


def enviar_aniversario_email(email, dados, unsub_token=None):
    """Envia felicitacao de aniversario (marketing; sem desconto/cupom)."""
    return _enviar_email(
        destinatario=email,
        assunto=f'Feliz aniversário! Um abraço da {_nome_clinica()}',
        template='email/aniversario.html',
        contexto={'dados': dados},
        marketing=True,
        preheader='Um carinho da nossa equipe para o seu dia',
        unsub_token=unsub_token,
    )


def enviar_promocao_email(email, dados, unsub_token=None, assunto=None):
    """Envia promocao mensal (marketing).

    Sanitiza `dados['corpo_html']` via bleach se presente — mitiga XSS
    e limita tags HTML permitidas (admin trusted mas defesa em profundidade).
    """
    preheader = dados.get('preheader', '') if isinstance(dados, dict) else ''
    if isinstance(dados, dict) and dados.get('corpo_html'):
        try:
            import bleach
            dados = dict(dados)  # shallow copy p/ nao mutar original
            dados['corpo_html'] = bleach.clean(
                dados['corpo_html'],
                tags=['p', 'br', 'strong', 'em', 'a', 'ul', 'ol', 'li',
                      'h2', 'h3', 'h4', 'img', 'span', 'div'],
                attributes={
                    'a': ['href', 'title', 'target', 'rel'],
                    'img': ['src', 'alt', 'width', 'height'],
                    'span': ['style'],
                    'div': ['style'],
                },
                protocols=['http', 'https', 'mailto'],
                strip=True,
            )
        except ImportError:
            # bleach nao instalado — fallback escape total
            from django.utils.html import escape
            dados = dict(dados)
            dados['corpo_html'] = escape(dados['corpo_html'])
    # promocao.html usa variaveis top-level ({{ nome }}, {{ validade }}, {{ corpo_html }}...),
    # entao o contexto vai FLAT (nao embrulhado em {'dados': ...}).
    contexto = dict(dados) if isinstance(dados, dict) else {'dados': dados}
    return _enviar_email(
        destinatario=email,
        assunto=assunto or f'{_nome_clinica()} — Ofertas especiais deste mês',
        template='email/promocao.html',
        contexto=contexto,
        marketing=True,
        preheader=preheader,
        unsub_token=unsub_token,
    )


def enviar_aprovacao_profissional_email(email, dados):
    """Notifica profissional que há novo agendamento pendente de aprovação."""
    return _enviar_email(
        destinatario=email,
        assunto=f'{_nome_clinica()} — Novo agendamento pendente',
        template='email/aprovacao_profissional.html',
        contexto={'dados': dados},
    )


def enviar_termos_pendentes_email(email, dados):
    """Notifica cliente sobre termos pendentes por email."""
    return _enviar_email(
        destinatario=email,
        assunto=f'{_nome_clinica()} — Termos de consentimento pendentes',
        template='email/termos_pendentes.html',
        contexto={'dados': dados},
    )
