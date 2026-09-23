"""System checks de configuracao de producao (Warnings; nunca derrubam o boot).

Rodam em todo comando de manage.py (inclusive o `migrate` do pre-deploy, entao
aparecem no log do deploy) e ficam mudos com DEBUG=True (dev/testes).
Levantar ImproperlyConfigured no import quebraria CI e `check --deploy` sem env.

Os que consultam o banco (W004 via Branding, W006, W007 via Branding) so rodam
quando o comando libera o banco (`migrate`, `check --database default`). No
`migrate` eles rodam ANTES das migrations: no 1o deploy quem avisa de painel
sem administrador e o bootstrap_admin (roda depois do migrate).
"""
import os
from urllib.parse import urlparse

from django.conf import settings
from django.core import checks

from .utils.email import _BACKENDS_SEM_ENTREGA
from .utils.sms import sms_configurado

TAG = 'config_prod'

_HOSTS_LOCAIS = {'localhost', '127.0.0.1', '0.0.0.0', '::1'}
# Runtime (utils/email) + locmem: locmem so existe no runner de testes; em prod
# e aviso de config, nunca regra de runtime (os testes contam com ele entregando).
_EMAIL_SEM_ENTREGA = (*_BACKENDS_SEM_ENTREGA, 'locmem.EmailBackend')


def _env(nome):
    return (os.environ.get(nome) or '').strip()


def _banco_liberado(databases):
    return bool(databases) and 'default' in databases


def _config_efetiva(chave, databases):
    """Valor da env ou, com o banco liberado, da tela Branding (Configuracao).

    Mesma regra de utils/branding.get_branding (valor vazio no banco cai na env),
    mas com consulta direta: nao envenena o cache do branding.
    """
    valor = _env(chave)
    if valor or not _banco_liberado(databases):
        return valor
    try:
        from .models import Configuracao
        return (
            Configuracao.objects.filter(chave=chave)
            .values_list('valor', flat=True).first() or ''
        ).strip()
    except Exception:  # noqa: BLE001 — tabela ausente/banco fora: trata como vazio
        return ''


def _whatsapp_configurado(databases):
    return any(ch.isdigit() for ch in _config_efetiva('WHATSAPP_NUMERO', databases))


def ha_admin_utilizavel():
    """Existe ADMIN ativo com senha utilizavel (alguem consegue entrar no painel)?

    Usado pelo check W006 e pelo bootstrap_admin (pre-deploy). Levanta excecao
    se a tabela nao existir — quem chama decide o que fazer.
    """
    from django.contrib.auth import get_user_model
    from django.contrib.auth.hashers import is_password_usable

    Usuario = get_user_model()
    senhas = Usuario.objects.filter(
        papel=Usuario.PAPEL_ADMIN, ativo=True,
    ).values_list('password', flat=True)
    return any(senha and is_password_usable(senha) for senha in senhas)


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
    elif not sms_configurado():
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

    if _banco_liberado(databases):
        try:
            sem_admin = not ha_admin_utilizavel()
        except Exception:  # noqa: BLE001 — banco novo (antes do migrate): nada a dizer
            sem_admin = False
        if sem_admin:
            avisos.append(checks.Warning(
                'Nenhum ADMIN ativo com senha utilizavel: ninguem entra no painel.',
                hint='Defina ADMIN_EMAIL/ADMIN_PASSWORD (e ADMIN_NOME) no Railway e faca '
                     'redeploy (o bootstrap_admin do pre-deploy cria/reativa a conta).',
                id='aranha.W006',
            ))

    if '@' not in _config_efetiva('CLINIC_EMAIL', databases):
        avisos.append(checks.Warning(
            'CLINIC_EMAIL vazio: o site fica sem e-mail de contato (canal do titular LGPD, '
            'arts. 18 e 41) e o alerta de NPS detrator perde o fallback.',
            hint='Defina na tela Branding do painel ou na env CLINIC_EMAIL.',
            id='aranha.W007',
        ))

    if getattr(settings, 'ADMIN_2FA_OBRIGATORIO', True) is False:
        avisos.append(checks.Warning(
            'ADMIN_2FA_OBRIGATORIO=false fora de DEBUG: administradores entram no painel '
            'e no Django admin sem segundo fator.',
            hint='Use so como valvula de emergencia; remova a env e cadastre o 2FA '
                 '(ou `manage.py setup_2fa <email>`).',
            id='aranha.W008',
        ))

    return avisos
