"""Tags do painel admin (painel/base_v2.html)."""
from django import template
from django.urls import reverse

register = template.Library()


@register.simple_tag
def webpush_configurado():
    """True se a chave VAPID publica existe — so entao o painel oferece ativar push."""
    from ..services.push import get_vapid_public_key
    return bool(get_vapid_public_key())


# Menu lateral do painel: (secao, [(rota, rotulo, icone, telas em que o item fica ativo)]).
# "Telas": nome exato da url ou, com '*', trecho do nome (ex.: '*procedimento' cobre criar/editar).
# O icone e o nome do desenho em cotton/icone.html (<c-icone nome=...>).
MENU_PAINEL = (
    ('Operação', (
        ('aranha:painel_overview', 'Visão geral', 'visao-geral', ('painel_overview', 'painel')),
        ('aranha:painel_agendamentos', 'Agenda', 'agenda', ('painel_agendamentos',)),
        ('aranha:admin_calendar', 'Calendário', 'calendario', ('admin_calendar',)),
        ('aranha:painel_retornos', 'Retornos', 'retornos', ('painel_retornos', 'admin_agendar_retorno')),
        ('aranha:painel_clientes', 'Clientes', 'clientes', ('painel_clientes',)),
        ('aranha:prontuario_consentimento', 'Prontuário', 'prontuario', ('*prontuario',)),
        ('aranha:painel_notificacoes', 'Notificações', 'notificacoes', ('painel_notificacoes',)),
    )),
    ('Cadastros', (
        ('aranha:admin_procedimentos', 'Procedimentos', 'procedimentos', ('*procedimento',)),
        ('aranha:admin_promocoes', 'Promoções', 'promocoes', ('*promoco',)),
        ('aranha:admin_anamneses', 'Anamneses', 'anamneses', ('*anamnese',)),
        ('aranha:admin_pacotes', 'Pacotes', 'pacotes', ('*pacote',)),
        ('aranha:painel_profissionais', 'Profissionais', 'profissionais',
         ('painel_profissionais', 'profissional_cadastro', 'profissional_editar')),
    )),
    ('Relatórios', (
        ('aranha:dashboard_financeiro', 'Financeiro', 'financeiro', ('dashboard_financeiro',)),
        ('aranha:painel_comissoes', 'Comissões', 'comissoes', ('*comiss',)),
        ('aranha:painel_nps', 'NPS', 'nps', ('painel_nps',)),
    )),
    ('Sistema', (
        ('aranha:admin_bloqueios', 'Bloqueios', 'bloqueios', ('*bloqueio',)),
        ('aranha:admin_lista_espera', 'Lista de espera', 'espera', ('*espera',)),
        ('aranha:admin_termos', 'Termos', 'termos', ('*termo',)),
        ('aranha:admin_usuarios', 'Usuários', 'usuarios', ('*usuario',)),
        ('aranha:admin_configuracoes', 'Regras operacionais', 'configuracoes', ('*configura',)),
        ('aranha:admin_branding', 'Identidade visual', 'marca', ('admin_branding',)),
        ('aranha:admin_auditoria', 'Auditoria', 'auditoria', ('admin_auditoria',)),
        ('aranha:admin_2fa_setup', 'Segurança / 2FA', 'seguranca', ('*admin_2fa',)),
    )),
)


def _ativo(url_name, telas):
    return any(url_name == t or (t.startswith('*') and t[1:] in url_name) for t in telas)


@register.simple_tag(takes_context=True)
def menu_painel(context):
    """Secoes do menu lateral: [{'titulo', 'itens': [{'href', 'rotulo', 'icone', 'atual'}]}].

    Uma lista so (espelho de site_tags.menu_publico): o template faz um laco e marca o item da
    tela aberta com aria-current="page" (antes: 20 links copiados, ativo so pela cor)."""
    request = context.get('request')
    url_name = getattr(getattr(request, 'resolver_match', None), 'url_name', None) or ''
    return [
        {'titulo': titulo, 'itens': [
            {'href': reverse(rota), 'rotulo': rotulo, 'icone': icone, 'atual': _ativo(url_name, telas)}
            for rota, rotulo, icone, telas in itens
        ]}
        for titulo, itens in MENU_PAINEL
    ]
