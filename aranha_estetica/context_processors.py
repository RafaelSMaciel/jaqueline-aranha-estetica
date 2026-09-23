"""Context processors globais — Plataforma de Clinicas"""
from django.conf import settings

from .utils.branding import get_branding


def clinica_globals(request):
    """Injeta marca/contatos (tela Branding > env > default) e SITE_URL em todos os templates.

    Contatos vazios ficam '' — templates devem esconder o item (sem placeholder falso).
    """
    ctx = get_branding()
    ctx['SITE_URL'] = settings.SITE_URL
    return ctx


def csp_nonce(request):
    """Expoe o nonce CSP gerado pelo middleware para uso em templates."""
    return {'csp_nonce': getattr(request, 'csp_nonce', '')}


def tema_atual(request):
    """Retorna o tema visual (claro/escuro) lido do cookie do usuario."""
    valor = request.COOKIES.get('tema', 'claro')
    if valor not in ('claro', 'escuro'):
        valor = 'claro'
    return {'tema': valor}
