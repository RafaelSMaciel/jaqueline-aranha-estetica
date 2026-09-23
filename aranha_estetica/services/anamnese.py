"""Validacao das respostas de ficha de anamnese contra o schema do formulario.

Fonte unica p/ o booking (JSON do wizard) e o link publico /anamnese/<token>/
(POST do formulario): descarta chaves fora do schema, confere tipo/opcoes/
tamanho e normaliza bool ('sim'/'nao'/'on'/...) p/ True/False — a mesma
pergunta nao pode ficar gravada ora como 'sim', ora como True.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

# Limites por campo (o do texto curto pode ser ajustado no schema com "max").
MAX_TEXTO = 1000
MAX_TEXTO_LONGO = 4000
MAX_NUMERO = 30

_BOOL_SIM = {'true', 'sim', 's', 'on', '1', 'yes'}
_BOOL_NAO = {'false', 'nao', 'não', 'n', 'off', '0', 'no'}


def _vazio(valor) -> bool:
    if valor is None:
        return True
    if isinstance(valor, str):
        return not valor.strip()
    if isinstance(valor, (list, tuple)):
        return not any(str(v).strip() for v in valor)
    return False


def _texto(valor, limite, label):
    if isinstance(valor, (dict, list, tuple)):
        return None, f'"{label}": resposta inválida.'
    texto = str(valor).strip()
    if len(texto) > limite:
        return None, f'"{label}": use no máximo {limite} caracteres.'
    return texto, None


def _limpar_campo(campo: dict, valor):
    """(valor_limpo, erro|None) de um campo JA preenchido."""
    tipo = campo.get('tipo', 'text')
    label = campo.get('label') or campo.get('key')

    if tipo == 'bool':
        if isinstance(valor, bool):
            return valor, None
        chave = str(valor).strip().lower() if not isinstance(valor, (dict, list, tuple)) else ''
        if chave in _BOOL_SIM:
            return True, None
        if chave in _BOOL_NAO:
            return False, None
        return None, f'"{label}": responda sim ou não.'

    if tipo == 'checkboxes':
        itens = [valor] if isinstance(valor, str) else valor
        if not isinstance(itens, (list, tuple)) or any(
            isinstance(v, (dict, list, tuple)) for v in itens
        ):
            return None, f'"{label}": resposta inválida.'
        opcoes = {str(o) for o in (campo.get('opcoes') or [])}
        itens = [str(v).strip() for v in itens if str(v).strip()]
        if opcoes and not all(v in opcoes for v in itens):
            return None, f'"{label}": opção(ões) inválida(s).'
        return itens, None

    if tipo in ('select', 'scale'):
        texto, erro = _texto(valor, MAX_TEXTO, label)
        if erro:
            return None, erro
        opcoes = [str(o) for o in (campo.get('opcoes') or [])]
        if opcoes and texto not in opcoes:
            return None, f'"{label}": opção inválida.'
        return texto, None

    if tipo == 'number':
        texto, erro = _texto(valor, MAX_NUMERO, label)
        if erro:
            return None, erro
        try:
            numero = float(texto.replace(',', '.'))
        except ValueError:
            return None, f'"{label}" deve ser um número.'
        if not math.isfinite(numero):
            return None, f'"{label}" deve ser um número.'
        return texto, None

    if tipo == 'date':
        texto, erro = _texto(valor, MAX_NUMERO, label)
        if erro:
            return None, erro
        try:
            date.fromisoformat(texto)
        except ValueError:
            return None, f'"{label}": data inválida.'
        return texto, None

    if tipo == 'email':
        texto, erro = _texto(valor, 254, label)
        if erro:
            return None, erro
        try:
            validate_email(texto)
        except ValidationError:
            return None, f'"{label}" deve ser um e-mail válido.'
        return texto, None

    limite = MAX_TEXTO_LONGO if tipo == 'longtext' else campo.get('max') or MAX_TEXTO
    try:
        limite = min(int(limite), MAX_TEXTO_LONGO)
    except (TypeError, ValueError):
        limite = MAX_TEXTO
    return _texto(valor, limite, label)


def validar_respostas(schema, dados: Mapping) -> tuple[dict, list[str]]:
    """Valida `dados` ({key: valor}) contra o schema_json do formulario.

    Retorna (respostas_limpas, erros). So entram chaves do schema; campo vazio
    (None/''/lista vazia) fica fora do resultado e so gera erro se obrigatorio
    (bool False e resposta valida). bool vira True/False; checkboxes vira lista
    de opcoes validas; demais tipos ficam como texto (strip) dentro do limite.
    """
    limpas: dict = {}
    erros: list[str] = []
    if not isinstance(dados, Mapping):
        dados = {}
    for campo in schema if isinstance(schema, list) else []:
        if not isinstance(campo, dict) or not campo.get('key'):
            continue
        key = campo['key']
        label = campo.get('label') or key
        valor = dados.get(key)
        if _vazio(valor):
            if campo.get('obrigatorio'):
                erros.append(f'"{label}" é obrigatório.')
            continue
        limpo, erro = _limpar_campo(campo, valor)
        if erro:
            erros.append(erro)
            continue
        if campo.get('tipo') == 'checkboxes' and not limpo and campo.get('obrigatorio'):
            erros.append(f'"{label}" é obrigatório.')
            continue
        limpas[key] = limpo
    return limpas, erros
