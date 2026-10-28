"""Formatos pt-BR para texto ao cliente e telas (fonte unica do dinheiro)."""
from decimal import Decimal, InvalidOperation


def formatar_brl(valor) -> str:
    """Valor monetario em pt-BR ('R$ 1.234,50'); '' se vazio/invalido."""
    if valor in (None, ''):
        return ''
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return ''
    texto = f'{numero:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return f'R$ {texto}'
