# aranha_estetica/models/__init__.py — Re-exporta todos os models
# Imports existentes continuam funcionando: from aranha_estetica.models import X

from .acesso import Usuario, UsuarioManager
from .profissionais import Profissional
from .agenda import AgendaHorario, AgendaExcecao
from .clientes import Cliente
from .procedimentos import (
    Procedimento, Habilitacao, Preco, Promocao,
)
from .agendamentos import Atendimento, Notificacao
from .prontuario import Prontuario, ProntuarioVersao, AnotacaoSessao
from .termos import VersaoTermo, AceiteTermo
from .nps import AvaliacaoNPS
from .pacotes import Pacote, ItemPacote, CompraPacote, ConsumoSessao
from .sistema import (
    ListaEspera, LogAuditoria, Configuracao, CodigoOtp,
)
from .push import AssinaturaPush
from .anamnese import FormularioAnamnese, RespostaAnamnese
from .extras import (
    Carteira, MovimentoCarteira,
    RegraComissao, MovimentoComissao,
)

__all__ = [
    'Usuario', 'UsuarioManager',
    'Profissional', 'AgendaHorario', 'AgendaExcecao',
    'Cliente',
    'Procedimento', 'Habilitacao', 'Preco', 'Promocao',
    'Atendimento', 'Notificacao',
    'Prontuario', 'ProntuarioVersao', 'AnotacaoSessao',
    'VersaoTermo', 'AceiteTermo',
    'AvaliacaoNPS',
    'Pacote', 'ItemPacote', 'CompraPacote', 'ConsumoSessao',
    'ListaEspera', 'LogAuditoria', 'Configuracao', 'CodigoOtp',
    'AssinaturaPush',
    'FormularioAnamnese', 'RespostaAnamnese',
    # extras (financeiro auxiliar)
    'Carteira', 'MovimentoCarteira',
    'RegraComissao', 'MovimentoComissao',
]
