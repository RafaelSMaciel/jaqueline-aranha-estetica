"""
Email Notification Service — Plataforma de Clinicas

Adaptador do canal: renderiza o template e entrega (SMTP ou API HTTP do ESP). Transacionais:
confirmacao, cancelamento, lembrete D-1, pacote expirando, fila de espera, aprovacao profissional;
marketing: aniversario e promocao (headers RFC 8058); aviso interno em texto (alerta de detrator).
(OTP nao usa email — canal exclusivo SMS via utils.sms.)

Quem recebe, os links (LinkAcesso) e o registro em Notificacao ficam em services/comunicacao: este
modulo nao le o banco nem importa services.

Falha fechada (contrato 7): fora de DEBUG, backend console/dummy/filebased =
e-mail nao configurado -> as funcoes retornam False (nada de "sucesso" so no
log). Fonte unica do contrato: outros modulos importam email_configurado daqui.

Provedor HTTP (django-anymail; o Railway Hobby bloqueia SMTP) conta como
entrega real quando a chave do ESP esta em settings.ANYMAIL (montado da env em
settings/base.py); sem ela todo envio falharia -> tambem "nao configurado".
"""
import logging
from email.utils import parseaddr
import re
from html import unescape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from .pii import mask_email

_RX_SEM_TEXTO = re.compile(r'<(head|style|script)\b.*?</\1\s*>|<!--.*?-->', re.S | re.I)
_RX_LINK = re.compile(r'<a\b[^>]*?\bhref\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a\s*>', re.S | re.I)
_RX_QUEBRA = re.compile(r'<br\s*/?>|</(p|div|tr|li|h[1-6]|table)\s*>', re.I)


def html_para_texto(html: str) -> str:
    """Versao texto do e-mail (multipart/alternative): sem o CSS do <head>/<style> nem
    comentario condicional do Outlook, links como "texto: URL" (strip_tags perdia o href) e
    quebras de linha nos blocos. E o que filtro anti-spam, preview e relogio leem."""
    texto = _RX_SEM_TEXTO.sub('', html)

    def _link(m):
        url, rotulo = m.group(1).strip(), strip_tags(m.group(2)).strip()
        if not rotulo or rotulo == url or url.startswith('mailto:'):
            return rotulo or url
        return f'{rotulo}: {url}'

    texto = _RX_LINK.sub(_link, texto)
    texto = _RX_QUEBRA.sub('\n', texto)
    texto = unescape(strip_tags(texto))
    linhas = [' '.join(linha.split()) for linha in texto.splitlines()]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(linhas)).strip() + '\n'

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
    # ambiente de testes: a trava (BackendAmbienteTeste) entrega pelo backend real
    backend = getattr(settings, 'EMAIL_BACKEND_REAL', '') or getattr(settings, 'EMAIL_BACKEND', '') or ''
    if backend.endswith(_BACKENDS_SEM_ENTREGA):
        return False
    # anymail sem a chave do ESP: todo envio levantaria AnymailConfigurationError
    return not anymail_chave_faltando(backend)


def _nome_clinica() -> str:
    from .branding import get_branding
    return get_branding()['CLINIC_NAME']


def _remetente() -> str:
    return getattr(settings, 'DEFAULT_FROM_EMAIL', '') or 'noreply@localhost'


def _enviar_email(destinatario, assunto, template, contexto,
                  marketing=False, preheader='', unsub_url=None):
    """Envia email HTML. Marketing inclui List-Unsubscribe (RFC 8058 one-click).

    unsub_url: URL absoluta do descadastro DESTE e-mail (services/comunicacao
    .enviar_marketing_com_descadastro). Obrigatoria para marketing=True (sem ela
    o e-mail NAO sai — LGPD exige o opt-out em toda mensagem).
    Retorna True so se o backend aceitou a mensagem.
    """
    if not destinatario:
        return False
    if not email_configurado():
        logger.warning('email_nao_configurado', extra={'template': template})
        return False
    if marketing and not unsub_url:
        logger.error('email_marketing_sem_descadastro', extra={'template': template})
        return False

    contexto.setdefault('clinic_name', _nome_clinica())
    contexto.setdefault('site_url', settings.SITE_URL.rstrip('/'))
    contexto.setdefault('preheader', preheader)

    remetente = _remetente()
    headers = {}
    if marketing:
        headers['List-Unsubscribe'] = (
            f'<{unsub_url}>, <mailto:{remetente}?subject=unsubscribe>'
        )
        headers['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
        contexto.setdefault('unsub_url', unsub_url)

    try:
        html = render_to_string(template, contexto)
        texto = html_para_texto(html)
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


def enviar_lembrete_email(email, dados):
    """Lembrete D-1 com os links de confirmar/cancelar (quem nao autorizou o WhatsApp)."""
    return _enviar_email(
        destinatario=email,
        assunto=f'{_nome_clinica()} — Lembrete do seu horário de amanhã',
        template='email/lembrete.html',
        contexto={'dados': dados},
        preheader='Confirme sua presença com um clique',
    )


def enviar_aviso_interno(destinatario, assunto, corpo) -> bool:
    """E-mail em texto puro para a equipe da clinica (alerta de NPS detrator, pedidos do titular).

    Mesma falha fechada dos demais: sem backend real, False (quem chama tenta de novo depois)."""
    if not destinatario:
        return False
    if not email_configurado():
        logger.warning('email_nao_configurado', extra={'template': 'aviso_interno'})
        return False
    try:
        enviados = EmailMultiAlternatives(
            subject=assunto, body=corpo, from_email=_remetente(), to=[destinatario],
        ).send(fail_silently=False)
    except Exception as e:  # noqa: BLE001 — SMTP/API do provedor: nunca quebra o fluxo
        logger.error('email_falha_envio', extra={'destinatario': mask_email(destinatario),
                                                 'template': 'aviso_interno', 'erro': type(e).__name__})
        return False
    if not enviados:
        logger.error('email_nao_aceito', extra={'destinatario': mask_email(destinatario), 'template': 'aviso_interno'})
        return False
    logger.info('email_enviado', extra={'destinatario': mask_email(destinatario), 'template': 'aviso_interno'})
    return True


def enviar_aniversario_email(email, dados, unsub_url=None):
    """Envia felicitacao de aniversario (marketing; sem desconto/cupom)."""
    return _enviar_email(
        destinatario=email,
        assunto=f'Feliz aniversário! Um abraço da {_nome_clinica()}',
        template='email/aniversario.html',
        contexto={'dados': dados},
        marketing=True,
        preheader='Um carinho da nossa equipe para o seu dia',
        unsub_url=unsub_url,
    )


def enviar_promocao_email(email, dados, unsub_url=None, assunto=None):
    """Envia promocao (marketing), disparada pelo painel (services/marketing).

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
    # promocao.html usa variaveis top-level ({{ nome }}, {{ oferta }}, {{ data_inicio }}, {{ corpo_html }}...),
    # entao o contexto vai FLAT (nao embrulhado em {'dados': ...}).
    contexto = dict(dados) if isinstance(dados, dict) else {'dados': dados}
    return _enviar_email(
        destinatario=email,
        assunto=assunto or f'{_nome_clinica()} — Ofertas especiais',
        template='email/promocao.html',
        contexto=contexto,
        marketing=True,
        preheader=preheader,
        unsub_url=unsub_url,
    )


def enviar_aprovacao_profissional_email(email, dados):
    """Notifica profissional que há novo agendamento pendente de aprovação."""
    return _enviar_email(
        destinatario=email,
        assunto=f'{_nome_clinica()} — Novo agendamento pendente',
        template='email/aprovacao_profissional.html',
        contexto={'dados': dados},
    )


class BackendAmbienteTeste:
    """EMAIL_BACKEND do ambiente de testes publicado (settings/staging.py): so entrega aos
    DESTINATARIOS_TESTE, pelo backend real (EMAIL_BACKEND_REAL). Pega todo e-mail do sistema —
    inclusive o reset de senha do Django —, nao so os de utils/email. Destinatario fora da lista
    some da mensagem; mensagem sem ninguem permitido nao sai (log envio_bloqueado_ambiente_teste)."""

    def __init__(self, fail_silently=False, **kwargs):
        from django.core.mail import get_connection
        self.real = get_connection(getattr(settings, 'EMAIL_BACKEND_REAL', ''), fail_silently=fail_silently, **kwargs)

    def open(self):
        return self.real.open()

    def close(self):
        return self.real.close()

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def send_messages(self, mensagens):
        from .seguranca import destino_permitido
        liberadas = []
        for msg in mensagens:
            msg.to = [d for d in msg.to if destino_permitido(parseaddr(d)[1])]
            msg.cc = [d for d in msg.cc if destino_permitido(parseaddr(d)[1])]
            msg.bcc = [d for d in msg.bcc if destino_permitido(parseaddr(d)[1])]
            if msg.to or msg.cc or msg.bcc:
                liberadas.append(msg)
            else:
                logger.info('envio_bloqueado_ambiente_teste', extra={'canal': 'email'})
        return self.real.send_messages(liberadas) if liberadas else 0
