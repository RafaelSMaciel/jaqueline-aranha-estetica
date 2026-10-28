"""Consentimentos de comunicacao com historico (spec padrao-sql §4.c — migration 0050).

Fonte unica de leitura e escrita da tabela ``consentimento`` (append-only):

- ``registrar(...)`` grava concessao/revogacao — no-op quando o estado vigente
  ja e o pedido (nao polui o historico); ``revogar``/``revogar_todos`` atalhos;
- ``estado_atual(cliente)`` / ``vigente(...)`` leem o estado (linha mais recente
  do par; sem linha = nao concedido; empate de ``registrado_em`` = maior id);
- ``anotar_vigente(qs, ...)`` anota um queryset (Cliente ou com FK ``cliente``)
  com o estado — subquery top-1 portavel (SQLite e PG; no PG usa o indice
  ``ix_consentimento_cliente_finalidade_canal_registrado_em``). Envio de
  marketing/WhatsApp filtra por aqui;
- ``esquecer(cliente)``: anonimizacao — revoga o que esta concedido
  (origem ESQUECIMENTO) e apaga IP/user-agent do historico, que fica.

O ultimo registro manda: quem se descadastrou e depois marca o opt-in de novo
no agendamento volta a receber (decisao D2 — reconsentimento vale). O antigo
``cliente.aceita_comunicacao`` (opt-out geral) virou revogacoes na migracao.
O painel so revoga; conceder pelo painel exige justificativa registrada
(``texto``) e autor.
"""
from __future__ import annotations

from collections.abc import Iterable

from django.db import transaction
from django.db.models import OuterRef, Subquery, Value
from django.db.models.fields import BooleanField
from django.db.models.functions import Coalesce
from django.utils import timezone

from ..models.consentimentos import Consentimento

MARKETING_EMAIL = (Consentimento.MARKETING, Consentimento.EMAIL)
PESQUISA_WHATSAPP = (Consentimento.PESQUISA_SATISFACAO, Consentimento.WHATSAPP)
LEMBRETE_WHATSAPP = (Consentimento.LEMBRETE_AGENDAMENTO, Consentimento.WHATSAPP)
PARES_VALIDOS = tuple(Consentimento.PARES)

# Checkbox do wizard (name do <input>, mantido) -> par. O descadastro revoga os
# dois primeiros; o lembrete do agendamento e aviso transacional e continua.
PARES = {
    'consent_email_marketing': MARKETING_EMAIL,
    'consent_whatsapp_nps': PESQUISA_WHATSAPP,
    'consent_whatsapp_confirmacao': LEMBRETE_WHATSAPP,
}
CAMPO_DO_PAR = {par: campo for campo, par in PARES.items()}
PARES_DESCADASTRO = (MARKETING_EMAIL, PESQUISA_WHATSAPP)

# Texto exibido ao lado de cada checkbox do agendamento (o template le daqui):
# e o que fica gravado em Consentimento.texto como prova do que foi aceito.
TEXTOS_WIZARD = {
    'consent_email_marketing': (
        'Aceito receber promoções e novidades por e-mail. Posso cancelar a qualquer momento.'
    ),
    'consent_whatsapp_confirmacao': (
        'Aceito receber lembrete do meu agendamento por WhatsApp 24h antes.'
    ),
    'consent_whatsapp_nps': (
        'Aceito receber pesquisa de satisfação por WhatsApp após o atendimento.'
    ),
}
ROTULOS_PAR = {
    MARKETING_EMAIL: 'Promoções e novidades por e-mail',
    PESQUISA_WHATSAPP: 'Pesquisa de satisfação por WhatsApp',
    LEMBRETE_WHATSAPP: 'Lembrete do agendamento por WhatsApp',
}
TEXTO_DESCADASTRO = 'Descadastro pelo link do e-mail (promoções e pesquisas).'
MIN_JUSTIFICATIVA = 10

_ORDEM = ('-registrado_em', '-id')


def _cliente_id(cliente) -> int:
    return getattr(cliente, 'pk', cliente)


def _validar_par(finalidade, canal):
    if (finalidade, canal) not in PARES_VALIDOS:
        raise ValueError(f'Par de consentimento inexistente: {finalidade}/{canal}')


def _prova(request) -> tuple[str | None, str]:
    """(ip, user_agent) do titular; IP desconhecido = NULL (nunca inventado)."""
    if request is None:
        return None, ''
    from ..utils.seguranca import IP_FALLBACK, client_ip
    ip: str | None = client_ip(request)
    if not ip or ip == IP_FALLBACK:
        ip = None
    return ip, (request.META.get('HTTP_USER_AGENT') or '')[:500]


def ultimos(cliente) -> dict[tuple[str, str], Consentimento]:
    """{par: linha vigente} dos pares com historico (1 query)."""
    vistos: dict[tuple[str, str], Consentimento] = {}
    for linha in Consentimento.objects.do_titular(cliente).order_by(*_ORDEM):
        vistos.setdefault(linha.par, linha)
    return vistos


def estado_atual(cliente) -> dict[tuple[str, str], bool]:
    """{par: concedido} p/ os 3 pares (sem linha = False). 1 query."""
    linhas = ultimos(cliente)
    return {par: bool(linhas[par].concedido) if par in linhas else False for par in PARES_VALIDOS}


def estado_wizard(cliente) -> dict[str, bool]:
    """Estado atual com as chaves dos checkboxes do agendamento (pre-fill do OTP)."""
    estado = estado_atual(cliente)
    return {campo: estado[par] for campo, par in PARES.items()}


def vigente(cliente, finalidade, canal) -> bool:
    """O titular autoriza hoje este par? (1 query, top-1 pelo indice)."""
    _validar_par(finalidade, canal)
    concedido = (
        Consentimento.objects.do_titular(cliente).filter(finalidade=finalidade, canal=canal)
        .order_by(*_ORDEM).values_list('concedido', flat=True).first()
    )
    return bool(concedido)


def anotar_vigente(qs, finalidade, canal, *, caminho='cliente', nome='tem_consentimento'):
    """Anota `nome` (bool) com o estado vigente do par p/ o cliente em `caminho`.

    caminho: 'pk' p/ queryset de Cliente; 'cliente' (padrao) p/ Atendimento e
    afins. Uso: ``anotar_vigente(qs, *PESQUISA_WHATSAPP).filter(tem_consentimento=True)``.
    """
    _validar_par(finalidade, canal)
    ultimo = (
        Consentimento.objects.filter(cliente=OuterRef(caminho), finalidade=finalidade, canal=canal)
        .order_by(*_ORDEM).values('concedido')[:1]
    )
    return qs.annotate(**{nome: Coalesce(Subquery(ultimo), Value(False), output_field=BooleanField())})


def com_consentimento(qs, finalidade, canal, *, caminho='cliente'):
    """So as linhas cujo cliente autoriza hoje o par."""
    nome = f'_consentimento_{finalidade.lower()}_{canal.lower()}'
    return anotar_vigente(qs, finalidade, canal, caminho=caminho, nome=nome).filter(**{nome: True})


@transaction.atomic
def registrar(cliente, finalidade, canal, concedido, *, origem, texto='', request=None,
              usuario=None, registrado_em=None) -> Consentimento | None:
    """Grava concessao/revogacao (append-only). None = no-op (estado ja era esse).

    A linha do cliente e travada (select_for_update) p/ serializar registros
    simultaneos do mesmo titular. ``request`` = prova do titular (IP/UA);
    ``usuario`` = autor quando a equipe registra (painel/admin).
    """
    from ..models import Cliente

    _validar_par(finalidade, canal)
    if origem not in dict(Consentimento.ORIGEM_CHOICES):
        raise ValueError(f'Origem de consentimento invalida: {origem}')
    cliente_id = _cliente_id(cliente)
    list(Cliente.all_objects.select_for_update().filter(pk=cliente_id).values_list('pk', flat=True))
    if vigente(cliente_id, finalidade, canal) == bool(concedido):
        return None
    ip, user_agent = _prova(request)
    autor = usuario if getattr(usuario, 'is_authenticated', False) and getattr(usuario, 'pk', None) else None
    return Consentimento.objects.create(
        cliente_id=cliente_id, finalidade=finalidade, canal=canal, concedido=bool(concedido),
        origem=origem, texto=(texto or '').strip(), ip_origem=ip, user_agent=user_agent,
        usuario_autor=autor, registrado_em=registrado_em or timezone.now(),
    )


def revogar(cliente, finalidade, canal, *, origem, texto='', request=None, usuario=None):
    return registrar(cliente, finalidade, canal, False, origem=origem, texto=texto,
                     request=request, usuario=usuario)


def revogar_todos(cliente, *, origem, texto='', pares: Iterable[tuple[str, str]] | None = None,
                  request=None, usuario=None) -> list[Consentimento]:
    """Revoga os pares (todos por padrao) que estao concedidos; devolve as linhas novas."""
    novas = []
    for finalidade, canal in (pares or PARES_VALIDOS):
        linha = revogar(cliente, finalidade, canal, origem=origem, texto=texto,
                        request=request, usuario=usuario)
        if linha is not None:
            novas.append(linha)
    return novas


def registrar_do_wizard(cliente, marcados: dict[str, bool], *, sincronizados: bool,
                        cadastro_novo: bool, request) -> list[Consentimento]:
    """Checkboxes do agendamento -> historico (origem AGENDAMENTO_ONLINE).

    Marcado concede (texto = rotulo exibido). Desmarcado so revoga quando o
    checkbox mostrava o estado do cadastro (``sincronizados`` = prefill do OTP)
    e o cadastro ja existia: sem isso o POST nunca revoga as cegas.
    """
    novas = []
    for campo, (finalidade, canal) in PARES.items():
        marcado = bool(marcados.get(campo))
        if not marcado and (cadastro_novo or not sincronizados):
            continue
        linha = registrar(cliente, finalidade, canal, marcado, origem=Consentimento.ORIGEM_AGENDAMENTO_ONLINE,
                          texto=TEXTOS_WIZARD[campo], request=request)
        if linha is not None:
            novas.append(linha)
    return novas


def historico(cliente) -> list[dict]:
    """Historico completo do titular (DSAR), mais recente primeiro."""
    return [
        {
            'finalidade': c.finalidade,
            'canal': c.canal,
            'concedido': c.concedido,
            'origem': c.origem,
            'texto': c.texto,
            'registrado_em': c.registrado_em.isoformat() if c.registrado_em else None,
            'ip': c.ip_origem,
        }
        for c in Consentimento.objects.do_titular(cliente).order_by(*_ORDEM)
    ]


def resumo_painel(cliente) -> list[dict]:
    """Linhas da ficha do cliente: estado vigente de cada par com a origem/data."""
    linhas = ultimos(cliente)
    out = []
    for par in PARES_VALIDOS:
        linha = linhas.get(par)
        out.append({
            'chave': f'{par[0]}:{par[1]}',
            'rotulo': ROTULOS_PAR[par],
            'concedido': bool(linha and linha.concedido),
            'registro': linha,
        })
    return out


def par_da_chave(chave: str) -> tuple[str, str] | None:
    """'MARKETING:EMAIL' (POST do painel) -> par valido ou None."""
    finalidade, _sep, canal = (chave or '').partition(':')
    return (finalidade, canal) if (finalidade, canal) in PARES_VALIDOS else None


