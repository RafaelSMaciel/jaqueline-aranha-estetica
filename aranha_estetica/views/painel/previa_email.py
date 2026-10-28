"""Pre-visualizacao dos e-mails (staff) com dados ficticios."""
import logging

from django.conf import settings

from ...decorators import staff_required

logger = logging.getLogger(__name__)


EMAIL_PREVIEW_FIXTURES = {
    'otp': {
        'template': 'email/otp.html',
        'contexto': {'codigo': '123456', 'clinic_name': 'Jaqueline Aranha Estética',
                     'preheader': 'Seu codigo de verificacao'},
    },
    'confirmacao': {
        'template': 'email/confirmacao.html',
        'contexto': {'dados': {
            'nome': 'Maria Silva', 'procedimento': 'Limpeza de Pele',
            'profissional': 'Jaqueline Aranha', 'data_hora': '20/04/2026 as 14:00',
            'valor': '180,00',
        }, 'clinic_name': 'Jaqueline Aranha Estética'},
    },
    # Mesmo contexto do envio real (services/marketing): aniversario so felicita (sem
    # desconto) e a promocao usa variaveis de primeiro nivel (periodo do atendimento, sem cupom).
    'aniversario': {
        'template': 'email/aniversario.html',
        'contexto': {'dados': {'nome': 'Maria Silva'},
                     'clinic_name': 'Jaqueline Aranha Estética',
                     'unsub_url': '#preview',
                     'preheader': 'Um carinho da nossa equipe para o seu dia'},
    },
    'promocao': {
        'template': 'email/promocao.html',
        'contexto': {
            'nome': 'Maria Silva', 'corpo_html': '<p>Oferta especial do mês.</p>',
            'oferta': '20% de desconto em Limpeza de Pele',
            'data_inicio': '01/05/2026', 'data_fim': '31/05/2026', 'link_agendar': '#preview',
            'clinic_name': 'Jaqueline Aranha Estética', 'unsub_url': '#preview',
        },
    },
    'lembrete': {
        'template': 'email/lembrete.html',
        'contexto': {'dados': {
            'nome': 'Maria Silva', 'procedimento': 'Limpeza de Pele',
            'profissional': 'Jaqueline Aranha', 'data_hora': '20/04/2026 às 14:00',
            'link_confirmar': '#preview', 'link_cancelar': '#preview',
        }, 'clinic_name': 'Jaqueline Aranha Estética'},
    },
    'cancelamento': {
        'template': 'email/cancelamento.html',
        'contexto': {'dados': {
            'nome': 'Maria Silva', 'procedimento': 'Limpeza de Pele',
            'data_hora': '20/04/2026 as 14:00', 'profissional': 'Jaqueline Aranha',
        }, 'clinic_name': 'Jaqueline Aranha Estética'},
    },
    'nps': {
        'template': 'email/nps.html',
        'contexto': {'dados': {
            'nome': 'Maria Silva', 'procedimento': 'Limpeza de Pele',
            'link_nps': '#preview',
        }, 'clinic_name': 'Jaqueline Aranha Estética'},
    },
    'fila_espera': {
        'template': 'email/fila_espera.html',
        'contexto': {'dados': {
            'nome': 'Maria Silva', 'procedimento': 'Limpeza de Pele',
            'data': '20/04/2026',
        }, 'clinic_name': 'Jaqueline Aranha Estética'},
    },
    'pacote_expirando': {
        'template': 'email/pacote_expirando.html',
        'contexto': {'dados': {
            'nome': 'Maria Silva', 'pacote': 'Facial Premium',
            'dias': 7, 'sessoes_restantes': 3,
        }, 'clinic_name': 'Jaqueline Aranha Estética'},
    },
}


@staff_required
def admin_email_preview(request, nome=None):
    """Preview de templates de email para staff. Renderiza com fixture."""
    from django.http import HttpResponse
    from django.template.loader import render_to_string

    if nome is None or nome not in EMAIL_PREVIEW_FIXTURES:
        lista = '<br>'.join(
            f'<a href="/painel/email-preview/{k}/">{k}</a>' for k in EMAIL_PREVIEW_FIXTURES
        )
        return HttpResponse(f'<h1>Email Previews</h1>{lista}')

    fx = EMAIL_PREVIEW_FIXTURES[nome]
    # site_url como no envio real (utils.email faz setdefault): CTAs absolutos
    # e sem VariableDoesNotExist em `{{ x|default:site_url }}`
    contexto = {'site_url': settings.SITE_URL.rstrip('/'), **fx['contexto']}
    html = render_to_string(fx['template'], contexto)
    # A previa e servida sob a CSP do painel (style-src com nonce): sem o
    # nonce o navegador descarta o <style> do e-mail (media queries, dark mode).
    nonce = getattr(request, 'csp_nonce', '')
    if nonce:
        html = html.replace('<style', f'<style nonce="{nonce}"')
    return HttpResponse(html)
