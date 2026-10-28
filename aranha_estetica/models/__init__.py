# aranha_estetica/models/__init__.py — Re-exporta todos os models
# Imports existentes continuam funcionando: from aranha_estetica.models import X

from .acesso import Usuario, UsuarioManager
from .profissionais import Profissional
from .agenda import AgendaHorario, AgendaExcecao
from .clientes import Cliente
from .consentimentos import Consentimento
from .procedimentos import (
    Procedimento, Habilitacao, Preco, Promocao,
)
from .agendamentos import (
    Atendimento, CanalNotificacao, Notificacao, RespostaNotificacao, StatusAtendimento, StatusNotificacao,
    TipoNotificacao,
)
from .prontuario import Prontuario, ProntuarioVersao, AnotacaoSessao
from .termos import VersaoTermo, AceiteTermo
from .nps import AvaliacaoNPS
from .pacotes import Pacote, ItemPacote, CompraPacote, CompraPacoteItem, ConsumoSessao, StatusCompraPacote
from .sistema import (
    ListaEspera, LogAuditoria, Configuracao, CodigoOtp,
)
from .push import AssinaturaPush
from .anamnese import FormularioAnamnese, RespostaAnamnese
from .links import LinkAcesso
from .historico import HistoricoAlteracao
from .extras import (
    Carteira, MovimentoCarteira, OrigemMovimentoCarteira, TipoMovimentoCarteira,
    RegraComissao, MovimentoComissao, StatusComissao, TipoMovimentoComissao,
)

__all__ = [
    'Usuario', 'UsuarioManager',
    'Profissional', 'AgendaHorario', 'AgendaExcecao',
    'Cliente', 'Consentimento',
    'Procedimento', 'Habilitacao', 'Preco', 'Promocao',
    'Atendimento', 'Notificacao',
    'StatusAtendimento', 'TipoNotificacao', 'CanalNotificacao', 'StatusNotificacao', 'RespostaNotificacao',
    'Prontuario', 'ProntuarioVersao', 'AnotacaoSessao',
    'VersaoTermo', 'AceiteTermo',
    'AvaliacaoNPS',
    'Pacote', 'ItemPacote', 'CompraPacote', 'CompraPacoteItem', 'ConsumoSessao', 'StatusCompraPacote',
    'ListaEspera', 'LogAuditoria', 'Configuracao', 'CodigoOtp',
    'AssinaturaPush',
    'FormularioAnamnese', 'RespostaAnamnese',
    'LinkAcesso',
    'HistoricoAlteracao',
    # extras (financeiro auxiliar)
    'Carteira', 'MovimentoCarteira', 'OrigemMovimentoCarteira', 'TipoMovimentoCarteira',
    'RegraComissao', 'MovimentoComissao', 'StatusComissao', 'TipoMovimentoComissao',
]
