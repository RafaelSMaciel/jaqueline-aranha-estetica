# aranha_estetica/models/__init__.py — Re-exporta todos os models
# Imports existentes continuam funcionando: from aranha_estetica.models import X

from .acesso import Usuario, UsuarioManager
from .profissionais import (
    Profissional, DisponibilidadeProfissional, BloqueioAgenda, ExcecaoDisponibilidade,
)
from .clientes import Cliente
from .procedimentos import (
    Procedimento, Habilitacao, Preco, Promocao,
)
from .agendamentos import Atendimento, Notificacao
from .prontuario import Prontuario, AnotacaoSessao
from .termos import VersaoTermo, AceiteTermo
from .nps import AvaliacaoNPS
from .pacotes import Pacote, ItemPacote, CompraPacote, ConsumoSessao
from .sistema import (
    ListaEspera, LogAuditoria, Configuracao, CodigoOtp, Feriado,
)
from .push import AssinaturaPush
from .anamnese import FormularioAnamnese, RespostaAnamnese
from .extras import (
    Carteira, MovimentoCarteira,
    RegraComissao, MovimentoComissao,
)

__all__ = [
    'Usuario', 'UsuarioManager',
    'Profissional', 'DisponibilidadeProfissional', 'BloqueioAgenda', 'ExcecaoDisponibilidade',
    'Cliente',
    'Procedimento', 'Habilitacao', 'Preco', 'Promocao',
    'Atendimento', 'Notificacao',
    'Prontuario', 'AnotacaoSessao',
    'VersaoTermo', 'AceiteTermo',
    'AvaliacaoNPS',
    'Pacote', 'ItemPacote', 'CompraPacote', 'ConsumoSessao',
    'ListaEspera', 'LogAuditoria', 'Configuracao', 'CodigoOtp',
    'Feriado',
    'AssinaturaPush',
    'FormularioAnamnese', 'RespostaAnamnese',
    # extras (financeiro auxiliar)
    'Carteira', 'MovimentoCarteira',
    'RegraComissao', 'MovimentoComissao',
]
