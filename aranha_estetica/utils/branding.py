"""Marca/contatos da clinica — fonte unica p/ site, e-mails e WhatsApp.

Precedencia: valor salvo na tela Branding do painel (tabela Configuracao)
-> variavel de ambiente -> default. Contatos sem valor ficam '' (os templates
escondem o item) — nunca exibir telefone/e-mail ficticio.
"""
import logging
import os
import re

from django.core.cache import cache

logger = logging.getLogger(__name__)

_HEX_RX = re.compile(r'^#[0-9a-fA-F]{3,8}$')

CONFIG_CACHE_KEY = 'branding_config_dict'  # invalidado em signals.invalidar_cache_branding
CONFIG_CACHE_TTL = 600  # 10 min

# (chave, label, tipo do input) — editaveis na tela Branding
BRANDING_FIELDS = [
    ('CLINIC_NAME', 'Nome da clínica', 'text'),
    ('CLINIC_SUBTITLE', 'Subtítulo', 'text'),
    ('CLINIC_EMAIL', 'E-mail de contato', 'email'),
    ('CLINIC_PHONE', 'Telefone exibido', 'text'),
    ('CLINIC_ADDRESS', 'Endereço', 'text'),
    ('CLINIC_HOURS', 'Horário de funcionamento', 'text'),
    ('WHATSAPP_NUMERO', 'WhatsApp (só dígitos com DDI, ex: 5517991234567)', 'text'),
    ('INSTAGRAM_URL', 'URL do Instagram', 'url'),
    ('THEME_COLOR', 'Cor da barra do navegador/app no celular (hex, ex: #C9A84C)', 'color'),
]

DEFAULTS = {
    'CLINIC_NAME': 'Jaqueline Aranha Estética',
    'CLINIC_SUBTITLE': 'Estética facial e corporal · Atendimento exclusivo',
    'CLINIC_EMAIL': '',
    'CLINIC_PHONE': '',
    'CLINIC_ADDRESS': 'R. Humberto Delboni, 1107 - Jardim Fuscaldo, São José do Rio Preto - SP',
    'CLINIC_HOURS': 'Seg a Sex · 9h às 19h  |  Sáb · 9h às 13h',
    'WHATSAPP_NUMERO': '',
    'INSTAGRAM_URL': '',
    'THEME_COLOR': '#C9A84C',
}


def config_dict() -> dict:
    """{chave: valor} da tabela Configuracao (cache 10 min). Tolera banco indisponivel."""
    cached = cache.get(CONFIG_CACHE_KEY)
    if cached is not None:
        return cached
    try:
        from ..models import Configuracao
        data = {c.chave: c.valor for c in Configuracao.objects.all()}
    except Exception:  # noqa: BLE001 — banco fora (healthcheck/migrate): cai p/ env
        return {}
    cache.set(CONFIG_CACHE_KEY, data, CONFIG_CACHE_TTL)
    return data


def invalidar_cache() -> None:
    cache.delete(CONFIG_CACHE_KEY)


def normalizar_whatsapp(valor) -> str:
    """Numero p/ wa.me: so digitos, com DDI. '' se invalido.

    '(17) 99123-4567' -> '5517991234567' (10-11 digitos = numero BR sem DDI: prefixa 55).
    Sem isso, wa.me/17991234567 abre +1 (EUA/Canada). Final precisa ter 12-13 digitos.
    """
    digitos = ''.join(ch for ch in str(valor or '') if ch.isdigit())
    if not digitos:
        return ''
    if len(digitos) in (10, 11):
        digitos = f'55{digitos}'
    if len(digitos) not in (12, 13):
        logger.warning('whatsapp_numero_invalido', extra={'digitos': len(digitos)})
        return ''
    return digitos


def get_branding() -> dict:
    """Marca efetiva: Configuracao > env > DEFAULTS. Valores sempre str."""
    db = config_dict()
    out = {}
    for chave, default in DEFAULTS.items():
        valor = (db.get(chave) or os.environ.get(chave) or default or '').strip()
        out[chave] = valor
    out['WHATSAPP_NUMERO'] = normalizar_whatsapp(out['WHATSAPP_NUMERO'])
    # Valores vao p/ href/meta: so aceita formatos seguros (senao cai no default)
    if not out['INSTAGRAM_URL'].lower().startswith(('https://', 'http://')):
        out['INSTAGRAM_URL'] = ''
    if not _HEX_RX.match(out['THEME_COLOR']):
        out['THEME_COLOR'] = DEFAULTS['THEME_COLOR']
    return out
