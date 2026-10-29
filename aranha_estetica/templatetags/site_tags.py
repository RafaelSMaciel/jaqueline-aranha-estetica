"""Tags do site publico (builtin em settings.TEMPLATES — nao precisa de {% load %}).

    <a {% agendar_link %} class="...">Agendar</a>
    <a {% agendar_link procedimento=proc.pk %} ...>          (pre-seleciona no wizard)
    {% menu_publico as itens %}{% for i in itens %}...{% endfor %}
    {{ termo.conteudo|texto_termo }}                       (texto do termo LGPD, sanitizado)
"""
import re
from urllib.parse import urlencode

from django import template
from django.urls import reverse
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

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


_TITULO_SECAO = re.compile(r'^\d{1,2}\. \S')


@register.filter
def texto_termo(texto):
    """Texto puro do termo (conteudo/lgpd.py: blocos por linha em branco) -> HTML sanitizado.

    Todo o texto e escapado; so a estrutura vira tag: bloco de uma linha "N. Titulo" -> <h2>, bloco
    com todas as linhas em "- " -> <ul>, o resto -> <p>. O que a cliente le na pagina e exatamente o
    conteudo da versao (o hash da prova do aceite), so com a moldura de formatacao.
    """
    partes = []
    for bloco in re.split(r'\n\s*\n', str(texto or '').strip()):
        linhas = [linha.strip() for linha in bloco.strip().splitlines() if linha.strip()]
        if not linhas:
            continue
        if len(linhas) == 1 and _TITULO_SECAO.match(linhas[0]) and len(linhas[0]) <= 90:
            partes.append(f'<h2>{escape(linhas[0])}</h2>')
        elif all(linha.startswith('- ') for linha in linhas):
            itens = ''.join(f'<li>{escape(linha[2:])}</li>' for linha in linhas)
            partes.append(f'<ul>{itens}</ul>')
        else:
            partes.append(f'<p>{"<br>".join(escape(linha) for linha in linhas)}</p>')
    return mark_safe('\n'.join(partes))  # noqa: S308 — cada pedaco de texto passou por escape()
