# app_shivazen/models/__init__.py — Re-exporta todos os models
# Imports existentes continuam funcionando: from app_shivazen.models import X

from .acesso import (
    Funcionalidade, Perfil, PerfilFuncionalidade,
    Usuario, UsuarioManager,
)
from .profissionais import (
    Profissional, DisponibilidadeProfissional, BloqueioAgenda,
)
from .clientes import Cliente
from .procedimentos import (
    Procedimento, ProfissionalProcedimento, Preco, Promocao,
)
from .agendamentos import Atendimento, Notificacao
from .prontuario import (
    Prontuario, ProntuarioPergunta, ProntuarioResposta, AnotacaoSessao,
)
from .termos import VersaoTermo, AceitePrivacidade, AssinaturaTermoProcedimento
from .nps import AvaliacaoNPS
from .pacotes import Pacote, ItemPacote, PacoteCliente, SessaoPacote
from .sistema import (
    ListaEspera, LogAuditoria, ConfiguracaoSistema, CodigoVerificacao,
)

__all__ = [
    'Funcionalidade', 'Perfil', 'PerfilFuncionalidade', 'Usuario', 'UsuarioManager',
    'Profissional', 'DisponibilidadeProfissional', 'BloqueioAgenda',
    'Cliente',
    'Procedimento', 'ProfissionalProcedimento', 'Preco', 'Promocao',
    'Atendimento', 'Notificacao',
    'Prontuario', 'ProntuarioPergunta', 'ProntuarioResposta', 'AnotacaoSessao',
    'VersaoTermo', 'AceitePrivacidade', 'AssinaturaTermoProcedimento',
    'AvaliacaoNPS',
    'Pacote', 'ItemPacote', 'PacoteCliente', 'SessaoPacote',
    'ListaEspera', 'LogAuditoria', 'ConfiguracaoSistema', 'CodigoVerificacao',
]
