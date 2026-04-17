"""Package de forms por dominio.

Importar via: from app_shivazen.forms import AgendamentoPublicoForm
"""
from .agendamento import AgendamentoPublicoForm, CancelamentoForm
from .cliente import ClienteForm, LgpdConsentimentoForm
from .procedimento import ProcedimentoForm
from .auth import LoginForm

__all__ = [
    'AgendamentoPublicoForm', 'CancelamentoForm',
    'ClienteForm', 'LgpdConsentimentoForm',
    'ProcedimentoForm',
    'LoginForm',
]
