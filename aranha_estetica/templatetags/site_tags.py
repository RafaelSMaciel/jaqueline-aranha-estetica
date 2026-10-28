"""Tags do site publico (builtin em settings.TEMPLATES — nao precisa de {% load %}).

    <a {% agendar_link %} class="...">Agendar</a>
    <a {% agendar_link procedimento=proc.pk %} ...>          (pre-seleciona no wizard)
    {% menu_publico as itens %}{% for i in itens %}...{% endfor %}
"""
from urllib.parse import urlencode

from django import template
from django.urls import reverse
from django.utils.html import format_html

register = template.Library()

MENU = (
    ('aranha:inicio', 'Início'),
    ('aranha:quem_somos', 'Sobre'),
    ('aranha:especialidades', 'Serviços'),
    ('aranha:equipe', 'Equipe'),
    ('aranha:promocoes', 'Promoções'),
    ('/#faq', 'Dúvidas'),
    ('aranha:agenda_contato', 'Contato'),
)


@register.simple_tag(takes_context=True)
def agendar_link(context, **filtros):
    """Atributos do link "Agendar": o wizard (com a pre-selecao) ou — sem SMS, quando o wizard nao
    conclui (o codigo do celular nao chega) — o WhatsApp da clinica, em outra aba."""
    whatsapp = context.get('AGENDAR_WHATSAPP')
    if whatsapp:
        return format_html('href="{}" target="_blank" rel="noopener"', whatsapp)
    url = reverse('aranha:agendamento_publico')
    query = urlencode({k: v for k, v in filtros.items() if v not in (None, '')})
    return format_html('href="{}"', f'{url}?{query}' if query else url)


@register.simple_tag(takes_context=True)
def menu_publico(context):
    """Itens do menu (desktop e celular saem da mesma lista): href, rotulo, atual."""
    request = context.get('request')
    atual = getattr(getattr(request, 'resolver_match', None), 'view_name', '')
    return [
        {'href': alvo if alvo.startswith('/') else reverse(alvo), 'rotulo': rotulo, 'atual': alvo == atual}
        for alvo, rotulo in MENU
    ]
