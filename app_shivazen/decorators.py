from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import redirect
from functools import wraps


def staff_required(view_func):
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
            return redirect('shivazen:inicio')
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def profissional_required(view_func):
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
        prof = getattr(user, 'profissional', None)
        if not prof or not prof.ativo:
            messages.error(request, 'Acesso restrito a profissionais cadastrados.')
            return redirect('shivazen:inicio')
        return view_func(request, *args, **kwargs)
    return _wrapped_view
