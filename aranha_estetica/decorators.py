from functools import wraps
from typing import Callable

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

# Teto de escritas por usuario nas telas da equipe: folga para o uso normal (aprovar varios pedidos
# seguidos) e freio para script/sessao roubada disparando POST em massa.
LIMITE_ESCRITA_EQUIPE = '30/m'


def _profissional_ativo(user):
    # O acesso ao reverse OneToOne pode levantar RelatedObjectDoesNotExist
    # (subclasse de ObjectDoesNotExist, NAO de AttributeError), entao o
    # default do getattr nao captura — tratamos explicitamente.
    try:
        prof = user.profissional
    except ObjectDoesNotExist:
        prof = None
    return prof if prof and prof.ativo else None


def staff_required(view_func: Callable) -> Callable:
    """
    Decorator que combina @login_required + verificação is_staff.
    Autenticado sem ser staff: profissional volta para o próprio portal,
    demais para o site. Não autenticado: login.
    """
    @wraps(view_func)
    @login_required
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_staff:
            messages.error(request, 'Acesso restrito à administração da clínica.')
            if _profissional_ativo(request.user):
                return redirect('aranha:profissional_agenda')
            return redirect('aranha:inicio')
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def profissional_required(view_func: Callable) -> Callable:
    """
    Acesso restrito a usuarios vinculados a um Profissional ativo.
    Staff tambem pode acessar (visao gerencial).
    """
    @wraps(view_func)
    @login_required
    def _wrapped_view(request, *args, **kwargs):
        user = request.user
        if user.is_staff:
            return view_func(request, *args, **kwargs)
        if not _profissional_ativo(user):
            messages.error(request, 'Acesso restrito a profissionais cadastrados.')
            return redirect('aranha:inicio')
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def _escrita(guarda: Callable, view_func: Callable) -> Callable:
    limite = ratelimit(key='user', rate=LIMITE_ESCRITA_EQUIPE, method='POST', block=True)
    return guarda(require_POST(limite(view_func)))


def escrita_painel(view_func: Callable) -> Callable:
    """View so de escrita do painel: staff_required + require_POST (GET -> 405) + ratelimit por
    usuario (LIMITE_ESCRITA_EQUIPE; estourou -> 429). Uma guarda so em vez de tres empilhadas
    (ou de `if request.method != 'POST': return redirect(...)` em cada view)."""
    return _escrita(staff_required, view_func)


def escrita_profissional(view_func: Callable) -> Callable:
    """Mesma guarda de escrita_painel para o portal do profissional (profissional_required)."""
    return _escrita(profissional_required, view_func)
