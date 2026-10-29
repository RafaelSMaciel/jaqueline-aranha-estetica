"""Tags do site publico (builtin em settings.TEMPLATES — nao precisa de {% load %}).

    <a {% agendar_link %} class="...">Agendar</a>
    <a {% agendar_link procedimento=proc.pk %} ...>          (pre-seleciona no wizard)
    {% menu_publico as itens %}{% for i in itens %}...{% endfor %}
    {{ termo.conteudo|texto_termo }}                       (texto do termo LGPD, sanitizado)
    {% foto 'facial-3' alt='...' sizes='(min-width: 768px) 50vw, 100vw' classe='...' prioridade=True %}
"""
import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

from django import template
from django.templatetags.static import static
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
    ('aranha:duvidas', 'Dúvidas'),
    ('aranha:agenda_contato', 'Contato'),
)

FOTOS = 'assets/clinica'
VARIANTES = f'{FOTOS}/variantes'   # gerado por scripts/imagens.py


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


@lru_cache(maxsize=1)
def manifest_fotos() -> dict:
    """{nome: {largura, altura, larguras}} das variantes (scripts/imagens.py); {} se nao houver."""
    caminho = Path(__file__).resolve().parents[1] / 'static' / VARIANTES / 'manifest.json'
    try:
        return json.loads(caminho.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


@register.simple_tag
def foto(nome, alt, sizes='100vw', classe='', prioridade=False):
    """<picture> de uma foto de static/assets/clinica: WebP no srcset + JPEG de fallback, com
    width/height reais (o navegador reserva o espaco: sem CLS; .foto = display: contents no site.css,
    o <img> ocupa a moldura). prioridade=True e a foto do topo
    (LCP): fetchpriority="high" e sem lazy; as demais carregam so perto da tela (loading="lazy")."""
    carregar = mark_safe('fetchpriority="high"' if prioridade else 'loading="lazy"')  # noqa: S308 — literal fixo
    dados = manifest_fotos().get(nome)
    if dados is None:   # foto nova ainda sem variantes: a original, sem srcset
        return format_html('<img src="{}" alt="{}" class="{}" decoding="async" {}>',
                           static(f'{FOTOS}/{nome}.webp'), alt, classe, carregar)

    def _srcset(ext):
        return ', '.join(f'{static(f"{VARIANTES}/{nome}-{w}.{ext}")} {w}w' for w in dados['larguras'])

    larguras = dados['larguras']
    padrao = larguras[len(larguras) // 2]   # fallback sem srcset: a do meio
    return format_html(
        '<picture class="foto"><source type="image/webp" srcset="{}" sizes="{}">'
        '<img src="{}" srcset="{}" sizes="{}" width="{}" height="{}" alt="{}" class="{}" decoding="async" {}>'
        '</picture>',
        _srcset('webp'), sizes, static(f'{VARIANTES}/{nome}-{padrao}.jpg'), _srcset('jpg'), sizes,
        dados['largura'], dados['altura'], alt, classe, carregar,
    )


_PARTICULAS = {'da', 'das', 'de', 'do', 'dos', 'e'}


def _partes_do_nome(nome) -> list[str]:
    """Nome sem titulo ('Dra.') nem particula ('de', 'da')."""
    return [p for p in str(nome or '').split()
            if p.lower() not in _PARTICULAS and not p.endswith('.') and p[:1].isalpha()]


@register.filter
def iniciais(nome):
    """Monograma: 1a letra do primeiro e do ultimo nome ('Jaqueline Aranha' -> 'JA'; 'Ana' -> 'A';
    'Dra. Ana de Souza' -> 'AS': titulo e particula ficam de fora)."""
    partes = _partes_do_nome(nome)
    if not partes:
        return ''
    return (partes[0][0] + (partes[-1][0] if len(partes) > 1 else '')).upper()


@register.filter
def primeiro_nome(nome):
    """'Dra. Ana de Souza' -> 'Ana' (botao "Agendar com Ana"); sem nome util, o nome como veio."""
    partes = _partes_do_nome(nome)
    return partes[0] if partes else str(nome or '')


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
