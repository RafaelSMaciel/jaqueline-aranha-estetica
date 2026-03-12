"""Audit logging helper for Shivazen admin actions."""
from ..models import LogAuditoria


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
            tabela_afetada=tabela,
            id_registro_afetado=id_registro,
            detalhes=detalhes,
        )
    except Exception:
        # Falha silenciosa — logging não deve quebrar a operação principal
        pass
