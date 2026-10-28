"""Context processors globais — Plataforma de Clinicas"""
from urllib.parse import quote

from django.conf import settings

from .utils import telefone
from .utils.branding import get_branding


def _nome_curto(nome: str) -> str:
    """Nome curto p/ PWA (short_name <= 12): 'Jaqueline Aranha Estetica' -> 'J. Aranha'."""
    nome = (nome or '').strip()
    if len(nome) <= 12:
        return nome
    partes = nome.split()
    if len(partes) >= 2:
        return f'{partes[0][0]}. {partes[1]}'[:12]
    return nome[:12]


def _agendar_whatsapp(numero: str) -> str:
    """URL do WhatsApp p/ os CTAs globais 'Agendar' quando o online nao conclui.

    O wizard exige OTP por SMS: sem provedor (sms_disponivel False) a cliente
    escolhia tudo e so no fim via que nao dava. '' = CTA segue p/ o wizard
    (SMS disponivel ou WhatsApp nao configurado).
    """
    if not numero:
        return ''
    from .utils.sms import sms_disponivel
    if sms_disponivel():
        return ''
    return f"https://wa.me/{numero}?text={quote('Olá! Gostaria de agendar um horário.')}"


def clinica_globals(request):
    """Injeta marca/contatos (tela Branding > env > default) e SITE_URL em todos os templates.

    Contatos vazios ficam '' — templates devem esconder o item (sem placeholder falso).
    """
    ctx = get_branding()
    ctx['SITE_URL'] = settings.SITE_URL
    ctx['CLINIC_PHONE_TEL'] = telefone.href_tel(ctx.get('CLINIC_PHONE', ''))
    ctx['CLINIC_SHORT_NAME'] = _nome_curto(ctx.get('CLINIC_NAME', ''))
    ctx['AGENDAR_WHATSAPP'] = _agendar_whatsapp(ctx.get('WHATSAPP_NUMERO', ''))
    # ambiente de testes publicado: faixa no topo e robots.txt fechado (settings/staging.py)
    ctx['AMBIENTE_TESTE'] = getattr(settings, 'AMBIENTE_TESTE', False)
    return ctx


def csp_nonce(request):
    """Expoe o nonce CSP gerado pelo middleware para uso em templates."""
    return {'csp_nonce': getattr(request, 'csp_nonce', '')}


def tema_atual(request):
    """Tema escolhido (cookie `tema`: claro/escuro). Sem escolha valida = '' — o <html> sai sem
    data-theme e o CSS segue o sistema (prefers-color-scheme, tokens.css)."""
    valor = request.COOKIES.get('tema', '')
    return {'tema': valor if valor in ('claro', 'escuro') else ''}
