"""Area da cliente ("Meus agendamentos") — sessao, titular e pedidos LGPD (decisao 39).

- Sessao: a da cliente verificada (services/sessao_cliente), aberta por qualquer codigo SMS confirmado
  (portal ou wizard), presa ao TELEFONE, dura 1 h; baixar os dados exige verificacao de ate 10 min.
- Pedido de correcao ou exclusao: fica na trilha ('LGPD: pedido de <tipo>', prazo de 15 dias,
  art. 19) e vai por e-mail a clinica (encarregado > e-mail do administrador > e-mail da marca).
As preferencias de comunicacao e a revogacao do consentimento de saude ficam em
services/consentimentos (origem PORTAL).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from ..models import Atendimento, Cliente
from ..utils.auditoria import registrar_log
from ..utils.branding import get_branding
from ..utils.datas import hoje
from ..utils.telefone import mascarar
from . import sessao_cliente

logger = logging.getLogger(__name__)

PRAZO_RESPOSTA = timedelta(days=15)
TIPOS_PEDIDO = {'correcao': 'correção de dados', 'exclusao': 'exclusão de dados'}
MAX_DESCRICAO = 1000


def clientes_da_sessao(request):
    telefone = sessao_cliente.telefone(request)
    if not telefone:
        return Cliente.objects.none()
    return Cliente.objects.filter(telefone=telefone, ativo=True)


def cliente_da_sessao(request) -> Cliente | None:
    """A titular da sessao (o telefone e unico entre cadastros ativos: uq_cliente_telefone_ativo)."""
    return clientes_da_sessao(request).order_by('pk').first()


def atendimento_da_sessao(request, pk, *select_related) -> Atendimento | None:
    """Atendimento `pk` de cliente ativa com o telefone da sessao (None = nao e dela, anti-IDOR)."""
    if pk is None or not sessao_cliente.telefone(request):
        return None
    return (
        Atendimento.objects.select_related(*select_related)
        .filter(pk=pk, cliente__in=clientes_da_sessao(request))
        .first()
    )


def _destino_pedidos() -> str:
    from .nps import destino_alerta

    return (get_branding().get('ENCARREGADO_EMAIL') or '').strip() or destino_alerta()


def registrar_pedido(cliente: Cliente, tipo: str, descricao: str = '', *, request=None) -> date:
    """Pedido de correcao ou exclusao (art. 18 III e VI): trilha + e-mail a clinica. -> prazo.

    A descricao (texto livre da titular, pode ter dado pessoal) vai so no e-mail; a trilha guarda o
    tipo e o prazo (os detalhes saem na pseudonimizacao).
    """
    from ..utils.email import enviar_aviso_interno

    if tipo not in TIPOS_PEDIDO:
        raise ValueError(f'Tipo de pedido invalido: {tipo}')
    prazo = hoje() + PRAZO_RESPOSTA
    registrar_log(None, f'LGPD: pedido de {tipo}', 'cliente', cliente.pk,
                  detalhes={'prazo': prazo.isoformat()}, request=request)
    clinica = get_branding().get('CLINIC_NAME') or 'Clínica'
    corpo = (
        f'A cliente pediu {TIPOS_PEDIDO[tipo]} pela área "Meus agendamentos" do site.\n\n'
        f'Cliente: {cliente.nome} (cadastro #{cliente.pk})\n'
        f'Celular: {mascarar(cliente.telefone)}\n'
        f'Prazo de resposta (LGPD art. 19): {prazo.strftime("%d/%m/%Y")}\n\n'
        f'O que ela escreveu:\n{(descricao or "").strip()[:MAX_DESCRICAO] or "(sem descrição)"}\n\n'
        'Confirme a identidade antes de alterar ou excluir. A exclusão é a ação "Pseudonimizar" do '
        'Django admin (o registro de saúde fica pelo prazo legal).'
    )
    destino = _destino_pedidos()
    if not enviar_aviso_interno(destino, f'[{clinica}] Pedido LGPD: {TIPOS_PEDIDO[tipo]}', corpo):
        logger.error('portal_pedido_lgpd_sem_email', extra={'cliente_id': cliente.pk, 'tipo': tipo})
    return prazo
