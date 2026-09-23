"""Context processors globais — Plataforma de Clinicas"""
from django.conf import settings

from .utils.branding import get_branding


def _telefone_tel(telefone: str) -> str:
    """'(17) 99999-0000' -> '+5517999990000' p/ href tel: ('' se nao parecer telefone BR)."""
    digitos = ''.join(ch for ch in (telefone or '') if ch.isdigit())
    if len(digitos) in (10, 11):
        return f'+55{digitos}'
    if len(digitos) in (12, 13) and digitos.startswith('55'):
        return f'+{digitos}'
    return ''


def _nome_curto(nome: str) -> str:
    """Nome curto p/ PWA (short_name <= 12): 'Jaqueline Aranha Estetica' -> 'J. Aranha'."""
    nome = (nome or '').strip()
    if len(nome) <= 12:
        return nome
    partes = nome.split()
    if len(partes) >= 2:
        return f'{partes[0][0]}. {partes[1]}'[:12]
    return nome[:12]


def clinica_globals(request):
    """Injeta marca/contatos (tela Branding > env > default) e SITE_URL em todos os templates.

    Contatos vazios ficam '' — templates devem esconder o item (sem placeholder falso).
    """
    ctx = get_branding()
    ctx['SITE_URL'] = settings.SITE_URL
    ctx['CLINIC_PHONE_TEL'] = _telefone_tel(ctx.get('CLINIC_PHONE', ''))
    ctx['CLINIC_SHORT_NAME'] = _nome_curto(ctx.get('CLINIC_NAME', ''))
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
