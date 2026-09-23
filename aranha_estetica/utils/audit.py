"""Audit logging helper for Jaqueline Aranha Estetica admin actions."""
import ipaddress
import logging

from django.db import DatabaseError, transaction

from ..models import LogAuditoria

logger = logging.getLogger(__name__)


def _ip_valido(valor):
    """IP textual valido ou None (GenericIPAddressField rejeitaria no banco)."""
    if not valor:
        return None
    try:
        return str(ipaddress.ip_address(str(valor).strip()))
    except ValueError:
        return None


def registrar_log(usuario, acao, tabela=None, id_registro=None, detalhes=None, *, request=None):
    """
    Registra uma ação no log de auditoria (autor + IP — LGPD art. 37).

    Args:
        usuario: Instância de Usuario (ou None para ações do sistema)
        acao: Descrição da ação (str)
        tabela: Nome da tabela afetada (str, opcional)
        id_registro: ID do registro afetado (int, opcional)
        detalhes: Dados adicionais (dict, opcional — salvo como JSON, PII mascarada)
        request: HttpRequest opcional — grava o IP de origem (client_ip) e,
            se `usuario` nao vier, o usuario autenticado do request.
    """
    try:
        ip = None
        if request is not None:
            from .security import client_ip
            ip = _ip_valido(client_ip(request))
            if usuario is None:
                usuario = getattr(request, 'user', None)
        if detalhes and isinstance(detalhes, dict):
            from ..services.auditoria import AuditoriaService
            detalhes = AuditoriaService._sanitize(detalhes)
        # Savepoint: falha de auditoria nao aborta a transacao do chamador.
        with transaction.atomic():
            LogAuditoria.objects.create(
                usuario=usuario if usuario is not None and getattr(usuario, 'is_authenticated', False) else None,
                acao=acao,
                tabela=tabela,
                registro_id=id_registro,
                detalhes=detalhes,
                ip_origem=ip,
            )
    except (DatabaseError, TypeError, ValueError) as e:
        # Nao propagar — audit log nunca deve quebrar a operacao principal.
        # Escopo restrito (DB indisponivel, payload nao-serializavel, campo
        # invalido) com exc_info p/ nao mascarar bug de programacao silenciosamente.
        logger.warning(
            'registrar_log falhou: %s | acao=%s tabela=%s id=%s',
            e, acao, tabela, id_registro, exc_info=True,
        )
