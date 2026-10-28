"""Links publicos com token — emitir, resolver, marcar uso, revogar (spec padrao-sql §4.b).

O token (``secrets.token_urlsafe(32)``, 256 bits) so existe em claro na URL
entregue; o banco guarda ``sha256(token)`` em ``link_acesso.token_hash``. A
busca e por igualdade do hash (indice unico), entao nao ha comparacao de
segredo em Python. sha256 SEM chave de proposito: com 256 bits nao ha forca
bruta, e rotacionar a SECRET_KEY num incidente nao pode derrubar links ja
enviados (spec §7.4).

Finalidades (LinkAcesso.FINALIDADE_CHOICES) e o alvo de cada uma em
LinkAcesso.CAMPO_ALVO. ``resolver`` devolve None para token vazio, longo
demais, desconhecido, de outra finalidade, revogado ou expirado
(``aceitar_expirado=True`` devolve o vencido nao revogado: a view mostra a
pagina "link expirado"/somente leitura e NAO executa a acao).
"""
from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterable

from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from ..constants import VALIDADE_LINK_FORMULARIO
from ..models import LinkAcesso

TAMANHO_MAXIMO_TOKEN = 128

GERENCIAR_AGENDAMENTO = LinkAcesso.GERENCIAR_AGENDAMENTO
CONFIRMAR_PRESENCA = LinkAcesso.CONFIRMAR_PRESENCA
NPS = LinkAcesso.NPS
TERMO = LinkAcesso.TERMO
FORMULARIO = LinkAcesso.FORMULARIO
DESCADASTRO = LinkAcesso.DESCADASTRO
FEED_ICS = LinkAcesso.FEED_ICS


def sha256_hex(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def gerar_token() -> str:
    return secrets.token_urlsafe(32)


def _filtro_alvo(finalidade, alvo) -> dict:
    campo = LinkAcesso.CAMPO_ALVO[finalidade]
    return {f'{campo}_id': getattr(alvo, 'pk', alvo)}


def emitir(finalidade: str, alvo, *, expira_em=None) -> str:
    """Cria o link e devolve o token EM CLARO (unica vez em que ele existe)."""
    token = gerar_token()
    LinkAcesso.objects.create(
        finalidade=finalidade, token_hash=sha256_hex(token), expira_em=expira_em,
        **_filtro_alvo(finalidade, alvo),
    )
    return token


def resolver(finalidades, token, *, select_related: Iterable[str] = (),
             aceitar_expirado: bool = False) -> LinkAcesso | None:
    """Link vigente do token para uma das finalidades (None = 404)."""
    if isinstance(finalidades, str):
        finalidades = (finalidades,)
    if not isinstance(token, str):
        return None
    token = token.strip()
    if not token or len(token) > TAMANHO_MAXIMO_TOKEN:
        return None
    link = (
        LinkAcesso.objects.select_related(*select_related)
        .filter(finalidade__in=finalidades, token_hash=sha256_hex(token), revogado_em__isnull=True)
        .first()
    )
    if link is None or (link.expirado and not aceitar_expirado):
        return None
    return link


def marcar_usado(link: LinkAcesso) -> None:
    """Primeiro uso efetivo (idempotente: so grava se ainda nao usado)."""
    agora = timezone.now()
    if LinkAcesso.objects.filter(pk=link.pk, usado_em__isnull=True).update(usado_em=agora):
        link.usado_em = agora


def revogar(finalidade: str, alvo) -> int:
    """Revoga os links vigentes da finalidade para o alvo (ex.: rotacao do ICS)."""
    return LinkAcesso.objects.filter(
        finalidade=finalidade, revogado_em__isnull=True, **_filtro_alvo(finalidade, alvo),
    ).update(revogado_em=timezone.now())


def descartar(token: str) -> None:
    """Apaga o link de um token que nunca saiu do servidor (ex.: e-mail nao enviado)."""
    if token:
        LinkAcesso.objects.filter(token_hash=sha256_hex(token)).delete()


def vigente(finalidade: str, alvo) -> LinkAcesso | None:
    """Link mais recente nao revogado e nao expirado (p/ o painel: 'ativo desde')."""
    agora = timezone.now()
    return (
        LinkAcesso.objects.filter(finalidade=finalidade, revogado_em__isnull=True, **_filtro_alvo(finalidade, alvo))
        .filter(Q(expira_em__isnull=True) | Q(expira_em__gt=agora))
        .order_by('-criado_em', '-pk').first()
    )


def gerar_link_feed_ics(profissional) -> str:
    """Rotacao do feed ICS: revoga o link vigente e emite outro. Devolve o token.

    O token so existe nesta resposta (o painel/portal renderiza a URL uma vez).
    """
    with transaction.atomic():
        revogar(FEED_ICS, profissional)
        return emitir(FEED_ICS, profissional)


def aplicada_em_0049():
    """Instante em que a 0049 foi registrada (links com criado_em anterior = migrados
    de token em texto puro: a URL nao pode ser reexibida). None se nao registrada."""
    from django.db import connection
    from django.db.migrations.recorder import MigrationRecorder

    return (
        MigrationRecorder(connection).migration_qs
        .filter(app='aranha_estetica', name='0049_link_acesso')
        .values_list('applied', flat=True).first()
    )


# ─── URLs absolutas (o token so vive nelas) ────────────────────────────────

def emitir_link_formulario(resposta) -> str:
    """Convite da ficha/pesquisa: emite o link FORMULARIO e devolve o caminho publico.

    Vale constants.VALIDADE_LINK_FORMULARIO (60 dias, como o convite antigo).
    """
    token = emitir(FORMULARIO, resposta, expira_em=timezone.now() + VALIDADE_LINK_FORMULARIO)
    rota = 'aranha:pesquisa_publica' if resposta.formulario.tipo == 'PESQUISA' else 'aranha:anamnese_publica'
    return reverse(rota, args=[token])
