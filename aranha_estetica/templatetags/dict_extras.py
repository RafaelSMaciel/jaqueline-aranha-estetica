"""Filtros de template p/ dicts (painel/agendamentos.html: alerta de saude por atendimento)."""
from django import template

register = template.Library()


@register.filter
def get_item(dicionario, chave):
    """{{ meu_dict|get_item:chave }} — acesso por chave dinamica."""
    if isinstance(dicionario, dict):
        return dicionario.get(chave)
    return None
