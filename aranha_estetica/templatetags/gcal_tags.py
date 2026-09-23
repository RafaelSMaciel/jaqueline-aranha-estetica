"""Tags de template da integracao Google Calendar (opcional)."""
from django import template

register = template.Library()


@register.simple_tag
def gcal_disponivel():
    """{% gcal_disponivel as gcal_ativo %} — True so com libs google + env OAuth.

    Sem integracao configurada o painel esconde Conectar/Sync (o clique so
    gerava erro).
    """
    from ..services.gcal import gcal_disponivel as _disponivel
    return _disponivel()
