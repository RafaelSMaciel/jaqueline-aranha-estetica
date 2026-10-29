"""Filtros de formato (builtin em settings.TEMPLATES — nao precisa de {% load %}).

    {{ atendimento.valor_cobrado|brl }}   ->  R$ 1.350,00   (vazio -> '')
    {{ cliente.telefone|telefone }}       ->  (17) 99999-0001 (fora do padrao BR: como veio)
"""
from django import template

from ..utils import telefone as _telefone
from ..utils.formatos import formatar_brl

register = template.Library()


@register.filter
def brl(valor):
    return formatar_brl(valor)


@register.filter
def telefone(valor):
    return _telefone.formatar(valor)
