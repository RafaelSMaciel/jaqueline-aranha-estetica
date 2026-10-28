"""Filtros de formato (builtin em settings.TEMPLATES — nao precisa de {% load %}).

    {{ atendimento.valor_cobrado|brl }}   ->  R$ 1.350,00   (vazio -> '')
"""
from django import template

from ..utils.formatos import formatar_brl

register = template.Library()


@register.filter
def brl(valor):
    return formatar_brl(valor)
