# aranha_estetica/models/__init__.py — Re-exporta todos os models
# Imports existentes continuam funcionando: from aranha_estetica.models import X

from .acesso import Usuario, UsuarioManager
from .profissionais import (
    Profissional, DisponibilidadeProfissional, BloqueioAgenda, ExcecaoDisponibilidade,
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
    ListaEspera, LogAuditoria, ConfiguracaoSistema, OtpCode, Feriado,
)
from .push import WebPushSubscription
from .anamnese import FormularioAnamnese, RespostaAnamnese
from .extras import (
    CreditoCliente, MovimentoCredito,
    RegraComissao, MovimentoComissao,
)

__all__ = [
    'Usuario', 'UsuarioManager',
    'Profissional', 'DisponibilidadeProfissional', 'BloqueioAgenda', 'ExcecaoDisponibilidade',
    'Cliente',
    'Procedimento', 'ProfissionalProcedimento', 'Preco', 'Promocao',
    'Atendimento', 'Notificacao',
    'Prontuario', 'ProntuarioPergunta', 'ProntuarioResposta', 'AnotacaoSessao',
    'VersaoTermo', 'AceitePrivacidade', 'AssinaturaTermoProcedimento',
    'AvaliacaoNPS',
    'Pacote', 'ItemPacote', 'PacoteCliente', 'SessaoPacote',
    'ListaEspera', 'LogAuditoria', 'ConfiguracaoSistema', 'OtpCode',
    'Feriado',
    'WebPushSubscription',
    'FormularioAnamnese', 'RespostaAnamnese',
    # extras (financeiro auxiliar)
    'CreditoCliente', 'MovimentoCredito',
    'RegraComissao', 'MovimentoComissao',
]
