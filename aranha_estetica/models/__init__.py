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
from .agendamentos import Atendimento, StatusAtendimento
from .notificacoes import CanalNotificacao, Notificacao, RespostaNotificacao, StatusNotificacao, TipoNotificacao
from .prontuario import Prontuario, ProntuarioVersao, AnotacaoSessao
from .termos import VersaoTermo, AceiteTermo
from .nps import AvaliacaoNPS
from .pacotes import Pacote, ItemPacote, CompraPacote, CompraPacoteItem, ConsumoSessao, StatusCompraPacote
from .sistema import (
    ListaEspera, LogAuditoria, CodigoOtp,
)
from .configuracao import Configuracao
from .push import AssinaturaPush
from .anamnese import FormularioAnamnese, RespostaAnamnese
from .links import LinkAcesso
from .historico import HistoricoAlteracao
from .carteira import Carteira, MovimentoCarteira, OrigemMovimentoCarteira, TipoMovimentoCarteira
from .comissao import MovimentoComissao, RegraComissao, StatusComissao, TipoMovimentoComissao
from .execucoes import ExecucaoJob

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
    # carteira e comissao
    'Carteira', 'MovimentoCarteira', 'OrigemMovimentoCarteira', 'TipoMovimentoCarteira',
    'RegraComissao', 'MovimentoComissao', 'StatusComissao', 'TipoMovimentoComissao',
    'ExecucaoJob',
]
