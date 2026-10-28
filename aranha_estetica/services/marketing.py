"""Marketing por e-mail: promocoes disparadas pelo painel e felicitacao de aniversario.

So recebe quem tem MARKETING/EMAIL vigente (ultimo registro do historico de consentimento), e cada
e-mail leva o seu link de descadastro + List-Unsubscribe (services/comunicacao). WhatsApp de
marketing nao existe: o consentimento de WhatsApp coletado cobre lembrete e pesquisa, nao oferta.

Promocao (D15): o preco vale pela DATA DO ATENDIMENTO (services/precos), entao o e-mail diz "para
atendimentos de dd/mm/aaaa a dd/mm/aaaa" e leva ao agendamento com o procedimento ja escolhido;
nada de "valida ate hoje + N dias" nem cupom (nenhum fluxo aplicaria codigo). Disparo unico: o envio
roda no request, em lotes de LOTE_PROMOCAO por clique, com o cursor na propria Promocao
(envio_cursor). Cada lote e RESERVADO (cursor gravado, linha travada) antes de sair: clique duplo ou
queda no meio nunca repetem um e-mail — no pior caso quem estava no lote interrompido fica sem a
oferta. Concluido, um novo disparo so com a confirmacao explicita de reenviar para todos.

Aniversario: so felicita (sem desconto). Idempotencia do dia: jobs.executar (uma execucao por dia).
"""
from __future__ import annotations

import calendar
import logging
from datetime import date
from decimal import Decimal
from functools import partial

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ..excecoes import RegraDeNegocio
from ..models import Cliente, Promocao
from ..utils import email as canal_email
from ..utils.datas import hoje
from ..utils.formatos import formatar_brl
from . import comunicacao, consentimentos

logger = logging.getLogger(__name__)

# O envio roda DENTRO do request (sem fila de tarefas): lote pequeno p/ nao estourar o timeout do
# gunicorn; a tela pede "enviar o proximo lote" ate acabar.
LOTE_PROMOCAO = 25
FORMATO_DATA = '%d/%m/%Y'


class PromocaoJaDisparada(RegraDeNegocio):
    codigo = 'promocao_ja_disparada'


def destinatarios_promocao():
    """Clientes ativas com e-mail e MARKETING/EMAIL vigente, na ordem do cursor (pk)."""
    return consentimentos.com_consentimento(
        Cliente.objects.filter(ativo=True, email__isnull=False).exclude(email=''),
        *consentimentos.MARKETING_EMAIL, caminho='pk',
    ).order_by('pk')


def _percentual(valor: Decimal) -> str:
    """20.00 -> '20'; 15.50 -> '15,5' (texto a cliente)."""
    return f'{Decimal(valor).normalize():f}'.replace('.', ',')


def oferta(promo: Promocao) -> str:
    """'20% de desconto em todos os procedimentos' / 'Limpeza de Pele por R$ 99,90'."""
    alvo = promo.procedimento.nome if promo.procedimento is not None else 'todos os procedimentos'
    if promo.preco_promocional is not None:
        return f'{alvo} por {formatar_brl(promo.preco_promocional)}'
    return f'{_percentual(promo.desconto_percentual)}% de desconto em {alvo}'


def assunto(promo: Promocao) -> str:
    """Preco fixo mostra o preco (nunca '0% OFF')."""
    if promo.preco_promocional is not None:
        return f'{promo.nome} — por {formatar_brl(promo.preco_promocional)}'
    return f'{promo.nome} — {_percentual(promo.desconto_percentual)}% OFF'


def _contexto(promo: Promocao, cliente: Cliente) -> dict:
    return {
        'nome': cliente.nome,
        'corpo_html': promo.descricao or '',
        'oferta': oferta(promo),
        'data_inicio': promo.data_inicio.strftime(FORMATO_DATA),
        'data_fim': promo.data_fim.strftime(FORMATO_DATA),
        # o 'Agendar' dos e-mails (wizard com o procedimento; sem SMS, o WhatsApp da clinica)
        'link_agendar': canal_email.link_agendar(promo.procedimento_id),
    }


def _email_promocao(email: str | None, dados: dict, assunto: str, unsub_url: str) -> bool:
    return bool(canal_email.enviar_promocao_email(email, dados, unsub_url=unsub_url, assunto=assunto))


def _email_aniversario(email: str | None, nome: str, unsub_url: str) -> bool:
    return bool(canal_email.enviar_aniversario_email(email, {'nome': nome}, unsub_url=unsub_url))


def _reservar_lote(promo: Promocao, *, lote: int, reenviar: bool) -> tuple[list[Cliente], int]:
    """Trava a promocao, escolhe o proximo lote e grava o cursor ANTES do envio. -> (lote, restantes)."""
    with transaction.atomic():
        travada = Promocao.objects.select_for_update().get(pk=promo.pk)
        if travada.envio_cursor is None:              # nao iniciado ou ja concluido
            if travada.disparada_em is not None and not reenviar:
                raise PromocaoJaDisparada(
                    f'A promoção "{travada.nome}" já foi enviada a todos. Para mandar de novo, '
                    'confirme o reenvio para todos.',
                )
            cursor = 0
            travada.disparada_em = timezone.now()
        else:
            cursor = travada.envio_cursor
        clientes = list(destinatarios_promocao().filter(pk__gt=cursor)[:lote])
        ultimo = clientes[-1].pk if clientes else cursor
        restantes = destinatarios_promocao().filter(pk__gt=ultimo).count()
        travada.envio_cursor = ultimo if restantes else None
        travada.save(update_fields=['envio_cursor', 'disparada_em', 'atualizado_em'])
    promo.envio_cursor, promo.disparada_em = travada.envio_cursor, travada.disparada_em
    return clientes, restantes


def enviar_promocao(promo: Promocao, *, lote: int = LOTE_PROMOCAO, reenviar: bool = False) -> tuple[int, int, int]:
    """Envia o proximo lote da promocao. -> (enviados, falhas, restantes).

    Recusa (RegraDeNegocio): fora da vigencia, e-mail nao configurado ou ja enviada a todos sem
    ``reenviar`` (PromocaoJaDisparada). Falha de 1 destinatario nao para o lote nem volta o cursor."""
    if not promo.esta_vigente:
        raise RegraDeNegocio(f'A promoção "{promo.nome}" não está vigente hoje: nada foi enviado.',
                             codigo='promocao_fora_da_vigencia')
    if not canal_email.email_configurado():
        raise RegraDeNegocio('O envio de e-mails não está configurado no servidor. Nenhuma mensagem foi enviada.',
                             codigo='email_nao_configurado')
    clientes, restantes = _reservar_lote(promo, lote=lote, reenviar=reenviar)
    titulo = assunto(promo)
    enviados = falhas = 0
    for cliente in clientes:
        try:
            ok = comunicacao.enviar_marketing_com_descadastro(
                cliente, partial(_email_promocao, cliente.email, _contexto(promo, cliente), titulo),
            )
        except Exception:  # noqa: BLE001 — falha de 1 destinatario nao para o lote
            logger.warning('promocao_envio_falhou', exc_info=True, extra={'cliente_id': cliente.pk})
            ok = False
        enviados += ok
        falhas += not ok
    logger.info('promocao_lote', extra={'promocao_id': promo.pk, 'enviados': enviados, 'falhas': falhas,
                                        'restantes': restantes})
    return enviados, falhas, restantes


# ─── Aniversario ──────────────────────────────────────────────────────────

def aniversariantes_do_dia(data: date | None = None):
    """Clientes ativas que fazem aniversario na data (29/02 comemora em 28/02 nos anos nao bissextos)."""
    data = data or hoje()
    filtro = Q(data_nascimento__month=data.month, data_nascimento__day=data.day)
    if data.month == 2 and data.day == 28 and not calendar.isleap(data.year):
        filtro |= Q(data_nascimento__month=2, data_nascimento__day=29)
    return Cliente.objects.filter(filtro, ativo=True)


def felicitar_aniversariantes(data: date | None = None) -> int:
    """Job aniversario: e-mail de felicitacao (marketing, sem desconto) a quem tem MARKETING/EMAIL
    vigente. Devolve quantos sairam. Falha pontual e logada e o lote segue (repetir o lote
    reenviaria aos ja parabenizados)."""
    enviados = 0
    for cliente in destinatarios_promocao().filter(pk__in=aniversariantes_do_dia(data).values('pk')):
        try:
            ok = comunicacao.enviar_marketing_com_descadastro(
                cliente, partial(_email_aniversario, cliente.email, cliente.nome),
            )
        except Exception:  # noqa: BLE001
            logger.exception('aniversario_email_falha', extra={'cliente_id': cliente.pk})
            ok = False
        enviados += ok
    logger.info('aniversario_disparado', extra={'emails': enviados})
    return enviados
