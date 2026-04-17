"""Camada de servicos — encapsula regras de negocio.

Uso: `from app_shivazen.services import AgendamentoService`.

Objetivo: extrair logica das views e signals para classes testaveis e
reutilizaveis. Views chamam services; services orquestram models, tasks
e notificacoes.
"""
from .agendamento import AgendamentoService
from .pacote import PacoteService
from .notificacao import NotificacaoService
from .lgpd import LgpdService
from .auditoria import AuditoriaService

__all__ = [
    'AgendamentoService',
    'PacoteService',
    'NotificacaoService',
    'LgpdService',
    'AuditoriaService',
]
