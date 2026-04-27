"""Decoradores de 2FA — gating do painel custom.

Uso:
    from app_shivazen.decorators_2fa import staff_otp_required

    @staff_otp_required
    def minha_view(request):
        ...

Comportamento:
- Usuario nao autenticado -> redirect LOGIN_URL
- Usuario sem device TOTP cadastrado -> redirect 'two_factor:setup' (forca cadastro)
- Usuario autenticado mas nao verificou 2FA na sessao -> redirect 'two_factor:login'
- Usuario verificado -> view executa normalmente
"""
from functools import wraps

from django.conf import settings
from django.shortcuts import redirect
from django_otp import devices_for_user


def _has_confirmed_device(user):
    if not user.is_authenticated:
        return False
    return any(d.confirmed for d in devices_for_user(user, confirmed=True))


def staff_otp_required(view_func):
    """Exige login + device TOTP confirmado + verificacao 2FA na sessao."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect(settings.LOGIN_URL)
        if not _has_confirmed_device(user):
            return redirect('two_factor:setup')
        if not getattr(user, 'is_verified', lambda: False)():
            return redirect('two_factor:login')
        return view_func(request, *args, **kwargs)
    return wrapper
