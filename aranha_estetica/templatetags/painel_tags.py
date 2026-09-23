"""Tags do painel admin (painel/base_v2.html)."""
from django import template

register = template.Library()


@register.simple_tag
def webpush_configurado():
    """True se a chave VAPID publica existe — so entao o painel oferece ativar push."""
    from ..services.push import get_vapid_public_key
    return bool(get_vapid_public_key())
