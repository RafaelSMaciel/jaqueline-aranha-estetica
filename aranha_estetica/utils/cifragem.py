"""Cifragem de dado sensivel em repouso (LGPD art. 46; dado de saude, art. 11).

Fernet (AES-128-CBC + HMAC-SHA256, lib `cryptography`) com as chaves mestras de
settings.CHAVES_CIFRAGEM (env, separadas por virgula): a 1a cifra, todas decifram — trocar a
chave = pôr a nova na frente, rodar `manage.py rotacionar_chave_cifragem` e so depois tirar a
antiga. Cada finalidade usa uma subchave derivada por HKDF ('saude' p/ prontuario/fichas;
'segredos' p/ segredo de integracao — hoje sem campo: era o token OAuth do Google Agenda, removido
na 0071, e fica porque a 0060 ainda o cifra): o texto de uma nao abre com a outra.

Formato gravado: 'cif1:' + token Fernet (texto) ou {"_cifra": "<token>"} (JSON). Texto sem o
prefixo e legado em claro: le como esta e o proximo save cifra. Vazio ('' / {}) fica vazio — os
`<> ''`/`<> '{}'` do SQL (vw_cliente_resumo, fn_clientes_para_anonimizar) continuam valendo.

Sem CHAVES_CIFRAGEM: dev/testes derivam uma chave da SECRET_KEY; com CIFRAGEM_EXIGE_CHAVE
(prod) e erro (system check aranha.E001 barra o deploy antes). Perder a chave = perder o
prontuario (20 anos): a dona guarda uma copia offline (docs/PROJECT.md §14).
"""
from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

PREFIXO = 'cif1:'
CHAVE_JSON = '_cifra'
FINALIDADES = ('saude', 'segredos')


class ErroCifragem(Exception):
    """Texto cifrado que nenhuma chave configurada abre (chave errada/perdida ou dado adulterado)."""


def gerar_chave() -> str:
    return Fernet.generate_key().decode()


def chaves_mestras() -> tuple[str, ...]:
    chaves = tuple(c.strip() for c in (getattr(settings, 'CHAVES_CIFRAGEM', None) or ()) if c and c.strip())
    if chaves:
        return chaves
    if getattr(settings, 'CIFRAGEM_EXIGE_CHAVE', False):
        raise ImproperlyConfigured(
            'CHAVES_CIFRAGEM ausente: o dado de saude seria gravado sem a chave da clinica. '
            'Gere com `python manage.py gerar_chave_cifragem` (guarde uma copia offline).'
        )
    # dev/testes: estavel enquanto a SECRET_KEY local nao mudar
    bruto = hashlib.sha256(b'shivazen-dev-cifragem|' + settings.SECRET_KEY.encode()).digest()
    return (base64.urlsafe_b64encode(bruto).decode(),)


@lru_cache(maxsize=16)
def _cofre(finalidade: str, mestras: tuple[str, ...]) -> MultiFernet:
    if finalidade not in FINALIDADES:
        raise ValueError(f'finalidade de cifragem desconhecida: {finalidade!r}')
    fernets = []
    for mestra in mestras:
        try:
            bruto = base64.urlsafe_b64decode(mestra.encode())
        except (ValueError, TypeError) as exc:
            raise ImproperlyConfigured('CHAVES_CIFRAGEM: chave fora do formato Fernet (base64 de 32 bytes)') from exc
        if len(bruto) != 32:
            raise ImproperlyConfigured('CHAVES_CIFRAGEM: chave fora do formato Fernet (base64 de 32 bytes)')
        sub = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                   info=f'shivazen/{finalidade}'.encode()).derive(bruto)
        fernets.append(Fernet(base64.urlsafe_b64encode(sub)))
    return MultiFernet(fernets)


def cofre(finalidade: str = 'saude') -> MultiFernet:
    return _cofre(finalidade, chaves_mestras())


def canonico(obj) -> str:
    """JSON estavel (chaves ordenadas): mesmo conteudo = mesmo texto claro."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def esta_cifrado(valor) -> bool:
    return isinstance(valor, str) and valor.startswith(PREFIXO)


def json_cifrado(valor) -> bool:
    return isinstance(valor, dict) and set(valor) == {CHAVE_JSON}


def cifrar(texto: str, finalidade: str = 'saude') -> str:
    return PREFIXO + cofre(finalidade).encrypt(str(texto).encode()).decode()


def decifrar(valor: str, finalidade: str = 'saude') -> str:
    """'cif1:...' -> texto claro. Sem o prefixo devolve como esta (legado em claro)."""
    if not esta_cifrado(valor):
        return valor
    try:
        return cofre(finalidade).decrypt(valor[len(PREFIXO):].encode()).decode()
    except InvalidToken as exc:
        raise ErroCifragem(
            'Nenhuma chave de CHAVES_CIFRAGEM abre este dado (chave trocada sem rotacionar?).'
        ) from exc


def cifrar_json(obj, finalidade: str = 'saude') -> dict:
    return {CHAVE_JSON: cofre(finalidade).encrypt(canonico(obj).encode()).decode()}


def decifrar_json(valor, finalidade: str = 'saude'):
    if not json_cifrado(valor):
        return valor
    try:
        return json.loads(cofre(finalidade).decrypt(valor[CHAVE_JSON].encode()).decode())
    except InvalidToken as exc:
        raise ErroCifragem(
            'Nenhuma chave de CHAVES_CIFRAGEM abre este dado (chave trocada sem rotacionar?).'
        ) from exc


def recifrar(valor, finalidade: str = 'saude'):
    """Texto/JSON cifrado com chave antiga -> cifrado com a 1a chave (rotacao). Resto igual."""
    if esta_cifrado(valor):
        return PREFIXO + cofre(finalidade).rotate(valor[len(PREFIXO):].encode()).decode()
    if json_cifrado(valor):
        return {CHAVE_JSON: cofre(finalidade).rotate(valor[CHAVE_JSON].encode()).decode()}
    return valor


def decifrar_profundo(obj, finalidade: str = 'saude'):
    """Decifra tudo que estiver cifrado dentro de uma estrutura JSON (trilha historico_alteracao,
    exibida decifrada no Django admin). Nunca levanta: o que nao abre vira marcador."""
    try:
        if esta_cifrado(obj):
            return decifrar(obj, finalidade)
        if json_cifrado(obj):
            return decifrar_profundo(decifrar_json(obj, finalidade), finalidade)
    except ErroCifragem:
        return '[cifrado: chave indisponivel]'
    if isinstance(obj, dict):
        return {k: decifrar_profundo(v, finalidade) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decifrar_profundo(v, finalidade) for v in obj]
    return obj
