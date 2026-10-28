"""Quem esta agindo agora — usuario, IP e origem — num contexto so p/ as duas trilhas.

LogAuditoria (utils/audit.registrar_log) e historico_alteracao (trigger do PG, GUCs ``app.*``)
liam o autor de lugares diferentes: o ``request`` passado a mao em cada chamada, o
``_autor_e_ip`` do LGPD, as GUCs definidas no middleware e no cron. Agora a BORDA abre o contexto
uma vez e o resto le dele:

    with atuando(Ator.do_request(request)):     # ContextoBancoMiddleware
    with atuando(Ator(ip=ip, origem='cron:x')):  # views/cron

``atuando`` guarda o Ator num ContextVar (por thread/request) e, no Postgres, espelha nas GUCs da
sessao (utils/banco.definir_contexto_banco); na saida volta ao contexto de fora — aninhar (cron
dentro do request) funciona. Fora de qualquer borda (shell, testes): Ator() vazio = sistema.
"""
from __future__ import annotations

import ipaddress
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from django.db import DatabaseError, connections

logger = logging.getLogger(__name__)


def ip_valido(valor) -> str | None:
    """IP textual valido ou None (GenericIPAddressField rejeitaria; 0.0.0.0 = desconhecido)."""
    from .utils.security import IP_FALLBACK

    if not valor or str(valor).strip() == IP_FALLBACK:
        return None
    try:
        return str(ipaddress.ip_address(str(valor).strip()))
    except ValueError:
        return None


def autenticado(usuario):
    """O usuario se estiver autenticado; senao None (AnonymousUser, None)."""
    return usuario if usuario is not None and getattr(usuario, 'is_authenticated', False) else None


@dataclass(frozen=True)
class Ator:
    usuario: object = None   # Usuario autenticado; None = cliente anonima ou sistema
    ip: str | None = None
    origem: str = ''         # web | cron:<job> | comando:<job> | migracao

    @classmethod
    def do_request(cls, request, origem='web') -> Ator:
        from .utils.security import client_ip
        return cls(usuario=autenticado(getattr(request, 'user', None)), ip=ip_valido(client_ip(request)),
                   origem=origem)

    @property
    def usuario_id(self):
        return getattr(self.usuario, 'pk', None)


_ATUAL: ContextVar[Ator] = ContextVar('ator', default=Ator())


def ator_atual() -> Ator:
    return _ATUAL.get()


@contextmanager
def atuando(ator: Ator):
    token = _ATUAL.set(ator)
    _espelhar_no_banco(ator)
    try:
        yield ator
    finally:
        _ATUAL.reset(token)
        _espelhar_no_banco(_ATUAL.get())


def _espelhar_no_banco(ator: Ator) -> None:
    """GUCs app.* (so PG) = o Ator. Falhou: contexto incerto -> descarta a conexao (a proxima
    nasce limpa, connection_created), nunca volta com o usuario de outra pessoa."""
    from .utils.banco import definir_contexto_banco

    conexao = connections['default']
    if conexao.vendor != 'postgresql':
        return
    try:
        definir_contexto_banco(ator.usuario_id or '', ator.ip or '', ator.origem)
    except DatabaseError:
        logger.warning('contexto_banco_falhou', exc_info=True)
        if not conexao.in_atomic_block:
            conexao.close()
