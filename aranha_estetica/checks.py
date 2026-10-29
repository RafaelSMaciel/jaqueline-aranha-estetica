"""System checks de configuracao de producao (Warnings; nunca derrubam o boot).

Excecao: os Errors barram o migrate do pre-deploy de proposito (o deploy anterior segue no ar):
aranha.E001 (sem CHAVES_CIFRAGEM valida), E002 (sem Postgres), E003 (segredo fraco ou de exemplo)
— so nos perfis publicados (prod/staging) — e E004 (OTP_MAX_TENTATIVAS fora do teto do banco), em
qualquer perfil. O aranha.W014 e o inverso: so no dev (DEBUG=True), avisa ambiente local fora do
requirements.lock.

Rodam em todo comando de manage.py (inclusive o `migrate` do pre-deploy, entao
aparecem no log do deploy) e ficam mudos com DEBUG=True (dev/testes).
Levantar ImproperlyConfigured no import quebraria CI e `check --deploy` sem env.

Os que consultam o banco (W004 via Branding, W006, W007 via Branding, W010) so
rodam quando o comando libera o banco (`migrate`, `check --database default`). No
`migrate` eles rodam ANTES das migrations (fora da transacao do migrate_atomico):
no 1o deploy quem avisa de painel sem administrador e o bootstrap_admin (roda
depois do migrate), e o W010 pode aparecer antes da migration que cria os termos.
"""
import sys
from email.utils import parseaddr
from urllib.parse import urlparse

from django.conf import settings
from django.core import checks
from django.db import DatabaseError

from .utils.email import _BACKENDS_SEM_ENTREGA, anymail_chave_faltando, esp_anymail
from .utils.sms import sms_configurado

TAG = 'config_prod'

_HOSTS_LOCAIS = {'localhost', '127.0.0.1', '0.0.0.0', '::1'}
# Runtime (utils/email) + locmem/test do anymail: so existem em testes; em prod
# e aviso de config, nunca regra de runtime (os testes contam com eles entregando).
_EMAIL_SEM_ENTREGA = (*_BACKENDS_SEM_ENTREGA, 'locmem.EmailBackend',
                      'anymail.backends.test.EmailBackend')
# DEFAULT_FROM_EMAIL de exemplo (settings/base.py): nenhum ESP aceita remetente
# de dominio que a clinica nao verificou
_DOMINIOS_REMETENTE_PLACEHOLDER = {'', 'clinica.com.br', 'localhost', 'example.com'}


def _perfil_publicado() -> bool:
    """prod.py e staging.py (CIFRAGEM_EXIGE_CHAVE). Os Errors de config so valem nesses perfis: o
    runner da suite roda os checks com DEBUG=False, mas com a chave e o banco de dev."""
    return bool(getattr(settings, 'CIFRAGEM_EXIGE_CHAVE', False))


def _banco_liberado(databases):
    return bool(databases) and 'default' in databases


def _config_efetiva(chave, databases):
    """Valor efetivo: tela Branding (Configuracao, com o banco liberado) > env (settings.MARCA_ENV).

    Mesma precedencia de utils/branding.get_branding (`db.get(chave) or env`:
    valor salvo no banco vence a env; vazio no banco cai na env), mas com
    consulta direta: nao envenena o cache do branding. Sem o banco liberado
    (build/`check` puro) so a env conta.
    """
    if _banco_liberado(databases):
        try:
            from .models import Configuracao
            valor_db = (
                Configuracao.objects.filter(chave=chave)
                .values_list('valor', flat=True).first()
            )
        except Exception:  # noqa: BLE001 — tabela ausente/banco fora: trata como vazio
            valor_db = None
        if valor_db:
            return valor_db.strip()
    return (settings.MARCA_ENV.get(chave) or '').strip()


def _whatsapp_configurado(databases):
    """Mesma regra do runtime: numero que normalizar_whatsapp descarta ('123',
    sem DDD) esconde os botoes no site — aqui conta como nao configurado."""
    from .utils.branding import normalizar_whatsapp
    return bool(normalizar_whatsapp(_config_efetiva('WHATSAPP_NUMERO', databases)))


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


def termos_sem_versao_ativa():
    """Tipos de termo global sem versao ativa: ['LGPD', 'SAUDE'] ou parte.

    Sem LGPD vigente o booking recusa confirmar; sem SAUDE o consentimento de
    dado de saude (art. 11) fica sem prova em AceiteTermo. Levanta DatabaseError
    se a tabela/coluna nao existir (banco antes do migrate).
    """
    from .models import VersaoTermo

    vigentes = (('LGPD', VersaoTermo.lgpd_vigente), ('SAUDE', VersaoTermo.saude_vigente))
    return [tipo for tipo, vigente in vigentes if vigente() is None]


def prazos_fora_do_termo(conteudo: str | None = None) -> list[str]:
    """Prazos de settings.RETENCAO_* que o texto do termo LGPD vigente nao publica: ['CHAVE=valor'].

    A linha esperada ('- O que: prazo, complemento.') sai de conteudo/lgpd.linha_prazo com o valor
    EFETIVO do settings — a mesma que a politica v1.0 imprime com o padrao. Sem termo vigente: [] (o
    W010 ja avisa). Levanta DatabaseError antes do migrate.
    """
    from .conteudo.lgpd import PRAZOS_RETENCAO, linha_prazo
    from .models import VersaoTermo

    if conteudo is None:
        termo = VersaoTermo.lgpd_vigente()
        if termo is None:
            return []
        conteudo = termo.conteudo
    faltando = []
    for rotulo, chave, _padrao, unidade, complemento in PRAZOS_RETENCAO:
        if chave is None:
            continue
        valor = getattr(settings, chave)
        if linha_prazo(rotulo, valor, unidade, complemento) not in conteudo:
            faltando.append(f'{chave}={valor}')
    return faltando


def _aviso_anymail(backend):
    """W011: backend anymail que nao vai entregar — chave do ESP ausente (o
    runtime ja trata como nao configurado) ou remetente de exemplo (o ESP recusa)."""
    falta = anymail_chave_faltando(backend)
    remetente = getattr(settings, 'DEFAULT_FROM_EMAIL', '') or ''
    if falta:
        if falta.endswith('_*'):
            msg = (f'EMAIL_BACKEND={backend!r}: os settings nao repassam a chave deste '
                   'provedor ao anymail — todo envio falha.')
        else:
            msg = f'EMAIL_BACKEND={backend!r} sem {falta}: todo envio falha.'
        return checks.Warning(
            msg + ' O site trata o e-mail como nao configurado (reset de senha, '
                  'confirmacoes, termos, contato).',
            hint='Defina a chave do provedor no Railway. Suportados (settings/base.py): '
                 'resend + RESEND_API_KEY, brevo + BREVO_API_KEY, sendgrid + SENDGRID_API_KEY, '
                 'mailgun + MAILGUN_API_KEY (+ MAILGUN_SENDER_DOMAIN), '
                 'postmark + POSTMARK_SERVER_TOKEN.',
            id='aranha.W011',
        )
    if parseaddr(remetente)[1].rpartition('@')[2].lower() in _DOMINIOS_REMETENTE_PLACEHOLDER:
        return checks.Warning(
            f'DEFAULT_FROM_EMAIL={remetente!r} e remetente de exemplo: o provedor HTTP '
            'recusa remetente de dominio nao verificado (nada e entregue).',
            hint='Verifique o dominio da clinica no provedor (SPF/DKIM) e defina '
                 'DEFAULT_FROM_EMAIL="Nome <contato@seudominio.com.br>".',
            id='aranha.W011',
        )
    return None


@checks.register(TAG)
def check_chaves_cifragem(app_configs=None, **kwargs):
    """E001 (Error, nao Warning): prod sem CHAVES_CIFRAGEM valida. Barra o migrate do
    pre-deploy — melhor nao subir do que gravar prontuario sem a chave da clinica."""
    if not _perfil_publicado():
        return []
    from django.core.exceptions import ImproperlyConfigured

    from .utils import cifragem
    try:
        for finalidade in cifragem.FINALIDADES:
            cifragem.cofre(finalidade)
    except ImproperlyConfigured as exc:
        return [checks.Error(
            str(exc),
            hint='Defina CHAVES_CIFRAGEM no Railway (python manage.py gerar_chave_cifragem) e guarde '
                 'uma copia offline: sem ela o prontuario cifrado nao abre.',
            id='aranha.E001',
        )]
    return []


@checks.register(TAG)
def check_postgres_obrigatorio(app_configs=None, databases=None, **kwargs):
    """E002: producao (ou staging) sem Postgres — sem DATABASE_URL o Django caia em SQLite em
    silencio: sem as procedures/triggers/views da 0056-0060 (Postgres e a fonte unica da retencao
    LGPD, D23) e com o banco apagado a cada deploy. W015: Postgres fora do major 18 (o CI e as
    paridades fn_*/vw_* so validam a 18)."""
    if not _perfil_publicado():
        return []
    avisos = []
    if 'postgresql' not in settings.DATABASES['default'].get('ENGINE', ''):
        avisos.append(checks.Error(
            'Producao sem Postgres: DATABASE_URL ausente (o banco caiu no SQLite).',
            hint='No Railway, variavel de referencia DATABASE_URL=${{Postgres.DATABASE_URL}} no servico web.',
            id='aranha.E002',
        ))
    elif _banco_liberado(databases):
        from django.db import connection
        try:
            major = connection.pg_version // 10000
        except DatabaseError:
            major = None
        if major is not None and major != 18:
            avisos.append(checks.Warning(
                f'Postgres {major} no ar; o sistema e validado no Postgres 18 (CI, paridades, procedures).',
                hint='Crie o servico com a imagem ghcr.io/railwayapp-templates/postgres-ssl:18 (nunca :latest).',
                id='aranha.W015',
            ))
    return avisos


# Valores de exemplo/build que nunca podem assinar sessao, reset de senha e OTP em producao
_SECRET_KEYS_DE_EXEMPLO = {'troque-essa-chave-em-producao', 'build-only', 'ci-only-nao-secreto'}
SECRET_KEY_MIN_CARACTERES = 50
SECRET_KEY_MIN_DISTINTOS = 5
# Segredos compartilhados em header (cron, handshake do webhook da Meta): sem freio de forca bruta
# alem do rate limit, o tamanho e a defesa
TOKEN_MIN_CARACTERES = 32
_TOKENS_COMPARTILHADOS = ('CRON_TOKEN', 'WHATSAPP_VERIFY_TOKEN')


def _secret_key_fraca(chave: str) -> bool:
    return (
        len(chave) < SECRET_KEY_MIN_CARACTERES
        or len(set(chave)) < SECRET_KEY_MIN_DISTINTOS
        or chave.startswith('django-insecure-')
        or chave in _SECRET_KEYS_DE_EXEMPLO
    )


@checks.register(TAG)
def check_segredos(app_configs=None, **kwargs):
    """E003: segredo fraco ou de exemplo num perfil publicado. A SECRET_KEY assina a sessao, o token
    de reset de senha e (sem OTP_HMAC_KEY) o HMAC dos OTP: chave do .env.example ou curta deixa
    forjar tudo isso. Antes so a presenca era conferida."""
    if not _perfil_publicado():
        return []
    erros = []
    if _secret_key_fraca(settings.SECRET_KEY or ''):
        erros.append(checks.Error(
            f'DJANGO_SECRET_KEY fraca ou de exemplo: exige {SECRET_KEY_MIN_CARACTERES}+ caracteres '
            f'aleatorios (sem o prefixo django-insecure-).',
            hint='Gere com: python -c "from django.core.management.utils import get_random_secret_key; '
                 'print(get_random_secret_key())" e defina no Railway.',
            id='aranha.E003',
        ))
    for nome in _TOKENS_COMPARTILHADOS:
        valor = getattr(settings, nome, '') or ''
        if valor and len(valor) < TOKEN_MIN_CARACTERES:
            erros.append(checks.Error(
                f'{nome} com menos de {TOKEN_MIN_CARACTERES} caracteres: segredo de header adivinhavel.',
                hint='Gere com: python -c "import secrets; print(secrets.token_urlsafe(32))" e atualize '
                     'tambem o agendador/provedor que envia o header.',
                id='aranha.E003',
            ))
    return erros


@checks.register(TAG)
def check_tentativas_otp(app_configs=None, **kwargs):
    """E004: OTP_MAX_TENTATIVAS fora de 1..10. Acima do teto do CHECK do banco o 11o erro do codigo
    virava IntegrityError (500) no meio do agendamento; zero bloquearia todo codigo."""
    from .models.sistema import OTP_TETO_TENTATIVAS

    maximo = settings.OTP_MAX_TENTATIVAS
    if 1 <= maximo <= OTP_TETO_TENTATIVAS:
        return []
    return [checks.Error(
        f'OTP_MAX_TENTATIVAS={maximo} fora de 1..{OTP_TETO_TENTATIVAS} (teto do banco, '
        'ck_codigo_otp_qtd_tentativas_0_10).',
        hint='Use o padrao (5) ou um valor ate 10.',
        id='aranha.E004',
    )]


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

    if settings.SMS_DEV_LOG_ONLY:
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
    elif not (settings.TURNSTILE_SECRET_KEY and settings.TURNSTILE_SITE_KEY):
        # SMS real sem captcha: o OTP e obrigatorio em todo agendamento e a cota
        # de SMS e global (utils/sms) — poucos IPs travam o booking de todos.
        avisos.append(checks.Warning(
            'SMS configurado sem Turnstile (TURNSTILE_SECRET_KEY + TURNSTILE_SITE_KEY): '
            'o pedido de codigo OTP fica sem captcha e poucos IPs esgotam a cota global '
            'de SMS (SMS_MAX_GLOBAL_HORA), travando o agendamento online de todos.',
            hint='Crie um widget no Cloudflare Turnstile e defina as duas chaves no Railway.',
            id='aranha.W009',
        ))

    # ambiente de testes (staging): a trava BackendAmbienteTeste entrega pelo backend real
    backend = getattr(settings, 'EMAIL_BACKEND_REAL', '') or getattr(settings, 'EMAIL_BACKEND', '') or ''
    if backend.endswith(_EMAIL_SEM_ENTREGA):
        avisos.append(checks.Warning(
            f'EMAIL_BACKEND={backend!r}: nenhum e-mail e entregue (reset de senha, '
            'confirmacoes, termos, contato).',
            hint='Configure um provedor. Railway Hobby (sem SMTP): backend HTTP do anymail, '
                 'ex.: EMAIL_BACKEND=anymail.backends.resend.EmailBackend + RESEND_API_KEY. '
                 'Plano Pro: SMTP (django.core.mail.backends.smtp.EmailBackend + EMAIL_HOST*). '
                 'DEFAULT_FROM_EMAIL = remetente do dominio verificado no provedor.',
            id='aranha.W003',
        ))
    elif esp_anymail(backend):
        aviso = _aviso_anymail(backend)
        if aviso:
            avisos.append(aviso)

    if not _whatsapp_configurado(databases):
        avisos.append(checks.Warning(
            'WHATSAPP_NUMERO vazio ou invalido: botoes/links de WhatsApp ficam ocultos, '
            'inclusive o fallback do agendamento quando o SMS falha.',
            hint='Defina na tela Branding do painel ou na env WHATSAPP_NUMERO: ex.: 5517991234567, '
                 'ou DDD+numero (10-11 digitos ganham 55 sozinho). A tela Branding vence a env.',
            id='aranha.W004',
        ))

    if not settings.CRON_TOKEN:
        avisos.append(checks.Warning(
            'CRON_TOKEN ausente: /cron/run/<job>/ recusa tudo (403) e nenhum job '
            'periodico roda (lembretes, NPS, pacotes, limpeza, LGPD).',
            hint='Defina CRON_TOKEN e agende POSTs com header X-Cron-Token (ver views/infra/cron.py).',
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
        try:
            sem_termo = termos_sem_versao_ativa()
        except DatabaseError:  # banco novo/antigo (antes do migrate): nada a dizer
            sem_termo = []
        if sem_termo:
            avisos.append(checks.Warning(
                f'Sem versao ativa do termo {" e ".join(sem_termo)}: sem LGPD o agendamento '
                'online recusa confirmar; sem SAUDE o consentimento de dado de saude '
                '(art. 11) fica sem prova (AceiteTermo).',
                hint='Painel > Termos: publique (ou reative) a versao do tipo que falta. As '
                     'migrations 0045/0047 criam as v1.0 quando nao ha ativa — no pre-deploy '
                     'este aviso roda antes delas; confira com `check --database default`.',
                id='aranha.W010',
            ))
        try:
            divergentes = prazos_fora_do_termo()
        except DatabaseError:  # banco antes do migrate
            divergentes = []
        if divergentes:
            avisos.append(checks.Warning(
                f'Prazo de retencao diferente do publicado no termo LGPD vigente: {", ".join(divergentes)}. '
                'A cliente aceitou o prazo do texto; a procedure aplica o do settings.',
                hint='Volte o settings.RETENCAO_* ao prazo publicado ou publique uma nova versao do termo '
                     'LGPD (Painel > Termos) com a tabela de prazos atualizada (conteudo/lgpd.py).',
                id='aranha.W016',
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

    from .constants import TRILHA_RETENCAO_MINIMA_DIAS
    retencao = getattr(settings, 'RETENCAO_LOG_AUDITORIA_DIAS', TRILHA_RETENCAO_MINIMA_DIAS)
    if retencao < TRILHA_RETENCAO_MINIMA_DIAS:
        avisos.append(checks.Warning(
            f'RETENCAO_LOG_AUDITORIA_DIAS={retencao} abaixo de {TRILHA_RETENCAO_MINIMA_DIAS}: o Postgres '
            'so deixa apagar trilha com 180+ dias (tg_log_auditoria_imutavel / '
            'tg_historico_alteracao_imutavel) e a purga do job_housekeeping falha.',
            hint='Use 180 ou mais (Marco Civil art. 15: registros de acesso por 6 meses); padrao 1825.',
            id='aranha.W013',
        ))

    if getattr(settings, 'PROFISSIONAL_2FA_OBRIGATORIO', True) is False:
        avisos.append(checks.Warning(
            'PROFISSIONAL_2FA_OBRIGATORIO=false fora de DEBUG: profissionais leem prontuario e '
            'alertas de saude (dado sensivel, LGPD art. 11) so com a senha.',
            hint='Use so como valvula de emergencia; remova a env. Profissional sem celular: '
                 'gere codigos de backup em /painel/seguranca/2fa/ e entregue impressos.',
            id='aranha.W012',
        ))

    return avisos


@checks.register('ambiente_dev')
def check_ambiente_igual_ao_lock(app_configs=None, **kwargs):
    """Dev: Python ou pacote local diferente do CI/imagem (Python 3.12 + requirements.lock).

    Django 5.2.1 local x 5.2.17 no lock (e Python 3.14) escondia do dev o que so o CI via.
    Pacote do lock nao instalado e ignorado (o lock e da imagem Linux).
    """
    if not settings.DEBUG:
        return []
    from importlib import metadata

    try:
        linhas = (settings.BASE_DIR / 'requirements.lock').read_text(encoding='utf-8').splitlines()
    except OSError:
        return []
    diferentes = []
    if sys.version_info[:2] != (3, 12):
        diferentes.append(f'Python {sys.version_info.major}.{sys.version_info.minor} (CI 3.12)')
    for linha in linhas:
        nome, _, versao = linha.split('#', 1)[0].strip().partition('==')
        if not versao:
            continue
        try:
            instalada = metadata.version(nome)
        except metadata.PackageNotFoundError:
            continue
        if instalada != versao:
            diferentes.append(f'{nome} {instalada} (lock {versao})')
    if not diferentes:
        return []
    resumo = ', '.join(diferentes[:5]) + (f' e mais {len(diferentes) - 5}' if len(diferentes) > 5 else '')
    return [checks.Warning(
        f'Ambiente local diferente do CI e da imagem: {resumo}.',
        hint='venv com Python 3.12 e `pip install -r requirements-dev.txt -c requirements.lock` '
             '(docs/DESENVOLVIMENTO.md).',
        id='aranha.W014',
    )]
