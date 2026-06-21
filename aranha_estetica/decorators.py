from functools import wraps
from typing import Callable

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import redirect


def staff_required(view_func: Callable) -> Callable:
    """
    Decorator que combina @login_required + verificação is_staff.
    Redireciona para o painel do cliente se autenticado mas não staff,
    ou para login se não autenticado.
    """
    @wraps(view_func)
    @login_required
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_staff:
            messages.error(request, 'Acesso negado. Você precisa ser administrador.')
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
        # O acesso ao reverse OneToOne pode levantar RelatedObjectDoesNotExist
        # (subclasse de ObjectDoesNotExist, NAO de AttributeError), entao o
        # default do getattr nao captura — tratamos explicitamente.
        try:
            prof = user.profissional
        except ObjectDoesNotExist:
            prof = None
        if not prof or not prof.ativo:
            messages.error(request, 'Acesso restrito a profissionais cadastrados.')
            return redirect('aranha:inicio')
        return view_func(request, *args, **kwargs)
    return _wrapped_view
