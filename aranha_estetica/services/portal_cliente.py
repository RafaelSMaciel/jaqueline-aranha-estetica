"""Area da cliente ("Meus agendamentos") — sessao, titular e pedidos LGPD (decisao 39).

- Sessao: aberta pelo OTP do proprio portal ou no fim do wizard (sem novo SMS), presa ao TELEFONE do
  cadastro, dura 1 h. ``sessao_recente`` (verificacao de ate 10 min) protege o download dos dados.
- Pedido de correcao ou exclusao: fica na trilha ('LGPD: pedido de <tipo>', prazo de 15 dias,
  art. 19) e vai por e-mail a clinica (encarregado > e-mail do administrador > e-mail da marca).
As preferencias de comunicacao e a revogacao do consentimento de saude ficam em
services/consentimentos (origem PORTAL).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from django.utils import timezone

from ..models import Atendimento, Cliente
from ..utils.auditoria import registrar_log
from ..utils.branding import get_branding
from ..utils.datas import hoje
from ..utils.telefone import mascarar

logger = logging.getLogger(__name__)

SESSAO_MEUS_AGENDAMENTOS = 'meus_agendamentos_telefone'
SESSAO_VERIFICADA_EM = 'meus_agendamentos_verificado_em'
DURACAO_SESSAO_SEGUNDOS = 3600
JANELA_DADOS = timedelta(minutes=10)
PRAZO_RESPOSTA = timedelta(days=15)
TIPOS_PEDIDO = {'correcao': 'correção de dados', 'exclusao': 'exclusão de dados'}
MAX_DESCRICAO = 1000


def abrir_sessao(request, telefone: str, *, verificado_em: datetime | None = None) -> None:
    """Sessao do portal presa ao telefone verificado. Anti session-fixation (cycle_key)."""
    request.session.cycle_key()
    request.session.pop('meus_agendamentos_email', None)  # chave legada
    request.session[SESSAO_MEUS_AGENDAMENTOS] = telefone
    request.session[SESSAO_VERIFICADA_EM] = (verificado_em or timezone.now()).isoformat()
    request.session.set_expiry(DURACAO_SESSAO_SEGUNDOS)


def abrir_sessao_do_wizard(request, telefone: str) -> None:
    """Fim do agendamento online: a cliente acabou de provar o celular no wizard — a area dela abre
    sem novo SMS. A hora da verificacao e a do OTP do wizard (nao agora), para o download dos dados
    continuar exigindo verificacao recente."""
    from . import otp

    verificado_em = None
    expira = request.session.get(otp.SESSAO_AGENDAMENTO_EXPIRA)
    if expira:
        try:
            verificado_em = datetime.fromisoformat(expira) - otp.VALIDADE_VERIFICACAO
        except (TypeError, ValueError):
            verificado_em = None
    if verificado_em is not None and timezone.is_naive(verificado_em):
        verificado_em = timezone.make_aware(verificado_em)
    abrir_sessao(request, telefone, verificado_em=verificado_em)   # cycle_key preserva o resto


def telefone_da_sessao(request) -> str:
    return request.session.get(SESSAO_MEUS_AGENDAMENTOS) or ''


def encerrar_sessao(request) -> None:
    for chave in (SESSAO_MEUS_AGENDAMENTOS, SESSAO_VERIFICADA_EM, 'meus_agendamentos_email'):
        request.session.pop(chave, None)


def sessao_recente(request, janela: timedelta = JANELA_DADOS) -> bool:
    """A cliente confirmou o codigo ha no maximo `janela` (o download dos dados exige)."""
    valor = request.session.get(SESSAO_VERIFICADA_EM)
    if not telefone_da_sessao(request) or not valor:
        return False
    try:
        verificado = datetime.fromisoformat(valor)
    except (TypeError, ValueError):
        return False
    if timezone.is_naive(verificado):
        verificado = timezone.make_aware(verificado)
    return timezone.now() - verificado <= janela


def clientes_da_sessao(request):
    telefone = telefone_da_sessao(request)
    if not telefone:
        return Cliente.objects.none()
    return Cliente.objects.filter(telefone=telefone, ativo=True)


def cliente_da_sessao(request) -> Cliente | None:
    """A titular da sessao (o telefone e unico entre cadastros ativos: uq_cliente_telefone_ativo)."""
    return clientes_da_sessao(request).order_by('pk').first()


def atendimento_da_sessao(request, pk, *select_related) -> Atendimento | None:
    """Atendimento `pk` de cliente ativa com o telefone da sessao (None = nao e dela, anti-IDOR)."""
    if pk is None or not telefone_da_sessao(request):
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
