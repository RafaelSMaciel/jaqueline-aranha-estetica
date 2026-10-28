"""Registrador unico do LogAuditoria (autor + IP — LGPD art. 37).

Autor e IP vem do Ator da borda (aranha_estetica/ator.py: middleware, cron); ``usuario`` e
``request`` explicitos ainda ganham dele (ex.: login que acabou de autenticar no proprio request).
PII de ``detalhes`` sai mascarada. Falha de auditoria nunca derruba a operacao (savepoint).
"""
import logging

from django.db import DatabaseError, transaction

from ..ator import autenticado, ator_atual, ip_valido
from ..models import LogAuditoria
from .pii import mask_cpf, mask_email, mask_telefone

logger = logging.getLogger(__name__)

_MASCARAS = {'email': mask_email, 'cpf': mask_cpf, 'telefone': mask_telefone}
_SEGREDOS = {'senha', 'password'}


def _mascarar(detalhes: dict) -> dict:
    """Mascara chaves de PII (e-mail, CPF, telefone) e esconde segredos antes de persistir."""
    out = {}
    for chave, valor in detalhes.items():
        nome = chave.lower()
        if isinstance(valor, str) and nome in _MASCARAS:
            out[chave] = _MASCARAS[nome](valor)
        elif isinstance(valor, str) and nome in _SEGREDOS:
            out[chave] = '***'
        else:
            out[chave] = valor
    return out


def autor_e_ip(usuario=None, request=None):
    """(usuario autenticado | None, ip | None): explicito > request > Ator atual."""
    ator = ator_atual()
    ip = ator.ip
    if request is not None:
        from .security import client_ip
        ip = ip_valido(client_ip(request))
        if usuario is None:
            usuario = getattr(request, 'user', None)
    if usuario is None:
        usuario = ator.usuario
    return autenticado(usuario), ip


def registrar_log(usuario=None, acao='', tabela=None, id_registro=None, detalhes=None, *, request=None):
    """Grava uma linha no LogAuditoria.

    usuario: Usuario (None = o do Ator/request; anonimo e sistema ficam sem autor).
    detalhes: dict salvo como JSON, PII mascarada. request: so quando o Ator da borda nao basta.
    """
    try:
        autor, ip = autor_e_ip(usuario, request)
        if detalhes and isinstance(detalhes, dict):
            detalhes = _mascarar(detalhes)
        with transaction.atomic():   # savepoint: falha aqui nao aborta a transacao do chamador
            LogAuditoria.objects.create(
                usuario=autor,
                acao=acao,
                tabela=tabela or '',  # NOT NULL DEFAULT '' (0054)
                registro_id=id_registro,
                detalhes=detalhes,
                ip_origem=ip,
            )
    except (DatabaseError, TypeError, ValueError) as e:
        # Escopo restrito (DB indisponivel, payload nao-serializavel, campo invalido) com
        # exc_info p/ nao mascarar bug de programacao silenciosamente.
        logger.warning(
            'registrar_log falhou: %s | acao=%s tabela=%s id=%s',
            e, acao, tabela, id_registro, exc_info=True,
        )
