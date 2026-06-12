"""Filtros de template p/ dicts — usado pelo questionario JSONB do prontuario."""
from django import template

register = template.Library()


@register.filter
def get_item(dicionario, chave):
    """{{ meu_dict|get_item:chave }} — acesso por chave dinamica."""
    if isinstance(dicionario, dict):
        return dicionario.get(chave)
    return None
