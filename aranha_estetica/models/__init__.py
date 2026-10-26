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
from .agendamentos import Atendimento, Notificacao
from .prontuario import Prontuario, ProntuarioVersao, AnotacaoSessao
from .termos import VersaoTermo, AceiteTermo
from .nps import AvaliacaoNPS
from .pacotes import Pacote, ItemPacote, CompraPacote, CompraPacoteItem, ConsumoSessao
from .sistema import (
    ListaEspera, LogAuditoria, Configuracao, CodigoOtp,
)
from .push import AssinaturaPush
from .anamnese import FormularioAnamnese, RespostaAnamnese
from .links import LinkAcesso
from .historico import HistoricoAlteracao
from .extras import (
    Carteira, MovimentoCarteira,
    RegraComissao, MovimentoComissao,
)

__all__ = [
    'Usuario', 'UsuarioManager',
    'Profissional', 'AgendaHorario', 'AgendaExcecao',
    'Cliente', 'Consentimento',
    'Procedimento', 'Habilitacao', 'Preco', 'Promocao',
    'Atendimento', 'Notificacao',
    'Prontuario', 'ProntuarioVersao', 'AnotacaoSessao',
    'VersaoTermo', 'AceiteTermo',
    'AvaliacaoNPS',
    'Pacote', 'ItemPacote', 'CompraPacote', 'CompraPacoteItem', 'ConsumoSessao',
    'ListaEspera', 'LogAuditoria', 'Configuracao', 'CodigoOtp',
    'AssinaturaPush',
    'FormularioAnamnese', 'RespostaAnamnese',
    'LinkAcesso',
    'HistoricoAlteracao',
    # extras (financeiro auxiliar)
    'Carteira', 'MovimentoCarteira',
    'RegraComissao', 'MovimentoComissao',
]
