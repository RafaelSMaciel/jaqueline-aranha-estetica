"""System checks de configuracao de producao (Warnings; nunca derrubam o boot).

Rodam em todo comando de manage.py (inclusive o `migrate` do pre-deploy, entao
aparecem no log do deploy) e ficam mudos com DEBUG=True (dev/testes).
Levantar ImproperlyConfigured no import quebraria CI e `check --deploy` sem env.
"""
import os
from urllib.parse import urlparse

from django.conf import settings
from django.core import checks

TAG = 'config_prod'

_HOSTS_LOCAIS = {'localhost', '127.0.0.1', '0.0.0.0', '::1'}
_EMAIL_SEM_ENTREGA = (
    'console.EmailBackend', 'dummy.EmailBackend', 'locmem.EmailBackend', 'filebased.EmailBackend',
)


def _env(nome):
    return (os.environ.get(nome) or '').strip()


def _whatsapp_configurado(databases):
    if any(ch.isdigit() for ch in _env('WHATSAPP_NUMERO')):
        return True
    # Tela Branding (Configuracao) so e consultada quando o comando libera o banco
    # (ex.: migrate); `check` puro nao toca no banco.
    if databases and 'default' in databases:
        try:
            # consulta direta (sem o cache de utils/branding: nao envenena o cache)
            from .models import Configuracao
            valor = (
                Configuracao.objects.filter(chave='WHATSAPP_NUMERO')
                .values_list('valor', flat=True).first()
            )
            return any(ch.isdigit() for ch in (valor or ''))
        except Exception:  # noqa: BLE001 — tabela ausente/banco fora: trata como vazio
            return False
    return False


@checks.register(TAG)
def check_config_producao(app_configs=None, databases=None, **kwargs):
    if settings.DEBUG:
        return []
    avisos = []

    site_url = getattr(settings, 'SITE_URL', '') or ''
    parsed = urlparse(site_url)
    if (parsed.hostname or '') in _HOSTS_LOCAIS or parsed.scheme != 'https':
        avisos.append(checks.Warning(
            f'SITE_URL={site_url!r} nao e uma URL publica https.',
            hint='Defina SITE_URL=https://<dominio> (sem barra final). Links de e-mail, '
                 'WhatsApp, termos e JSON-LD usam esse valor.',
            id='aranha.W001',
        ))

    sms_dev = getattr(settings, 'SMS_DEV_LOG_ONLY', False) or _env('SMS_DEV_LOG_ONLY').lower() == 'true'
    if sms_dev:
        avisos.append(checks.Warning(
            'SMS_DEV_LOG_ONLY=true fora de DEBUG: nenhum SMS (codigo OTP) e enviado de verdade.',
            hint='Remova SMS_DEV_LOG_ONLY do ambiente de producao.',
            id='aranha.W002',
        ))
    elif not (_env('ZENVIA_API_TOKEN') and _env('ZENVIA_FROM')):
        avisos.append(checks.Warning(
            'SMS sem provedor: ZENVIA_API_TOKEN/ZENVIA_FROM ausentes — OTP por SMS '
            'falha (agendamento de cliente recorrente, Meus Agendamentos, LGPD).',
            hint='Defina ZENVIA_API_TOKEN e ZENVIA_FROM ou oriente o cliente pelo WhatsApp.',
            id='aranha.W002',
        ))

    backend = getattr(settings, 'EMAIL_BACKEND', '') or ''
    if backend.endswith(_EMAIL_SEM_ENTREGA):
        avisos.append(checks.Warning(
            f'EMAIL_BACKEND={backend!r}: nenhum e-mail e entregue (reset de senha, '
            'confirmacoes, termos, contato).',
            hint='Configure um provedor: SMTP (EMAIL_BACKEND=django.core.mail.backends.smtp.'
                 'EmailBackend + EMAIL_HOST*; o Railway so libera SMTP no plano Pro) ou um '
                 'backend HTTP. Ajuste DEFAULT_FROM_EMAIL ao dominio verificado.',
            id='aranha.W003',
        ))

    if not _whatsapp_configurado(databases):
        avisos.append(checks.Warning(
            'WHATSAPP_NUMERO vazio: botoes/links de WhatsApp ficam ocultos, inclusive o '
            'fallback do agendamento quando o SMS falha.',
            hint='Defina na tela Branding do painel ou na env WHATSAPP_NUMERO (so digitos, com DDI).',
            id='aranha.W004',
        ))

    if not _env('CRON_TOKEN'):
        avisos.append(checks.Warning(
            'CRON_TOKEN ausente: /cron/run/<job>/ recusa tudo (403) e nenhum job '
            'periodico roda (lembretes, NPS, pacotes, limpeza, LGPD).',
            hint='Defina CRON_TOKEN e agende POSTs com header X-Cron-Token (ver views/cron.py).',
            id='aranha.W005',
        ))

    return avisos
