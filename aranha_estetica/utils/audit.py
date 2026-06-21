"""Audit logging helper for Jaqueline Aranha Estetica admin actions."""
import logging

from django.db import DatabaseError

from ..models import LogAuditoria

logger = logging.getLogger(__name__)


def registrar_log(usuario, acao, tabela=None, id_registro=None, detalhes=None):
    """
    Registra uma ação no log de auditoria.

    Args:
        usuario: Instância de Usuario (ou None para ações do sistema)
        acao: Descrição da ação (str)
        tabela: Nome da tabela afetada (str, opcional)
        id_registro: ID do registro afetado (int, opcional)
        detalhes: Dados adicionais (dict, opcional — salvo como JSON)
    """
    try:
        LogAuditoria.objects.create(
            usuario=usuario if usuario and usuario.is_authenticated else None,
            acao=acao,
            tabela=tabela,
            registro_id=id_registro,
            detalhes=detalhes,
        )
    except (DatabaseError, TypeError, ValueError) as e:
        # Nao propagar — audit log nunca deve quebrar a operacao principal.
        # Escopo restrito (DB indisponivel, payload nao-serializavel, campo
        # invalido) com exc_info p/ nao mascarar bug de programacao silenciosamente.
        logger.warning(
            'registrar_log falhou: %s | acao=%s tabela=%s id=%s',
            e, acao, tabela, id_registro, exc_info=True,
        )
