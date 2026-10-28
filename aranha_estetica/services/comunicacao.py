"""Comunicacao: o caso de uso de cada mensagem, o historico (Notificacao) e a fila de e-mail.

Canais = adaptadores so de transporte, sem banco: utils/email (SMTP ou API do ESP), utils/whatsapp
(Cloud API da Meta) e utils/sms (so o OTP). Aqui fica o resto: quem recebe, o link publico
(LinkAcesso; so o hash no banco), o texto e o registro em Notificacao (tipo x canal x status) — o
historico do que a cliente e a equipe receberam.

Fila de e-mail (sem Celery): o e-mail transacional nasce Notificacao EMAIL PENDENTE na transacao
de quem pediu (outbox: so existe se a operacao commitou) e sai no on_commit. Falhou: o cron
'emails' (jobs.job_emails) reenvia com espera crescente ate MAX_TENTATIVAS; pedido mais velho que
VALIDADE vira FALHOU (aviso velho confunde mais do que ajuda). Sem copia de PII na fila: destino e
texto saem do atendimento NA HORA do envio (cliente esquecida = nada a enviar). Mensagem com link
(lembrete D-1) emite o link na hora do envio; tentativa que falha descarta o seu.

WhatsApp (lembrete D-1 e NPS) sai na hora, pelo job: a Notificacao e o link sao gravados ANTES do
envio (o link so chega a cliente depois de existir) e o resultado vira ENVIADO ou FALHOU.

Marketing (aniversario, promocao — services/marketing) leva em cada e-mail o seu link de
descadastro (enviar_marketing_com_descadastro).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from ..constants import VALIDADE_LINK_NPS
from ..models import Atendimento, Notificacao
from ..models.agendamentos import StatusAtendimento
from ..utils import email as canal_email
from ..utils import whatsapp as canal_whatsapp
from ..utils.datas import data_local, fmt_local
from . import links

logger = logging.getLogger(__name__)

MAX_TENTATIVAS = 5
ESPERA_BASE = timedelta(minutes=5)      # 5, 10, 20, 40 min entre as tentativas
VALIDADE = timedelta(hours=48)
LOTE_REENVIO = 200
FORMATO_DATA_HORA = '%d/%m/%Y às %H:%M'

Tipo = Notificacao.Tipo
Canal = Notificacao.Canal
Status = Notificacao.Status


# ─── Links absolutos (o token so vive neles) ──────────────────────────────

def url_absoluta(caminho: str) -> str:
    return f"{settings.SITE_URL.rstrip('/')}{caminho}"


def link_revisar_agenda(data_hora) -> str:
    """Link (GET) p/ a agenda do portal da profissional no dia LOCAL do atendimento."""
    return url_absoluta(f"{reverse('aranha:profissional_agenda')}?data={data_local(data_hora):%Y-%m-%d}")


def links_confirmacao(token: str) -> dict[str, str]:
    """Os dois botoes do lembrete D-1 (WhatsApp e e-mail): a pagina /confirmar/ so muda o status
    no POST — o GET (pre-visualizacao do link, antivirus do e-mail) apenas mostra."""
    base = url_absoluta(reverse('aranha:confirmar_presenca', args=[token]))
    return {'link_confirmar': f'{base}?acao=confirmar', 'link_cancelar': f'{base}?acao=cancelar'}


def url_descadastro(token: str) -> str:
    """Descadastro do e-mail de marketing (link do rodape + List-Unsubscribe)."""
    return url_absoluta(reverse('aranha:lgpd_unsubscribe', args=[token]))


# ─── Dados das mensagens ──────────────────────────────────────────────────

def _valor(at) -> str:
    from .agendamento import formatar_valor_atendimento
    return formatar_valor_atendimento(at)


def _base(at) -> dict:
    return {
        'nome': at.cliente.nome,
        'procedimento': at.procedimento.nome,
        'profissional': at.profissional.nome,
        'data_hora': fmt_local(at.data_hora_inicio, FORMATO_DATA_HORA),
    }


def _email_cliente(at) -> str | None:
    return at.cliente.email


def _email_profissional(at) -> str:
    return getattr(getattr(at.profissional, 'usuario', None), 'email', '') or ''


def _lembrete_vale(at) -> bool:
    """Lembrete so faz sentido p/ horario futuro ainda de pe (cancelado na fila = descartado)."""
    return at.status in (StatusAtendimento.AGENDADO, StatusAtendimento.CONFIRMADO) and at.data_hora_inicio > timezone.now()


@dataclass(frozen=True)
class MensagemEmail:
    """Um tipo de e-mail da fila: a funcao do canal, o destino e os dados (lidos na hora do envio)."""
    enviar: Callable[[str, dict], bool]
    destino: Callable[[Atendimento], str | None]
    dados: Callable[[Atendimento], dict]
    # leva os botoes confirmar/cancelar (LinkAcesso CONFIRMAR_PRESENCA da propria notificacao)
    pede_confirmacao: bool = False
    # ainda faz sentido enviar? (False = a notificacao vira FALHOU sem envio)
    vale: Callable[[Atendimento], bool] | None = None


MENSAGENS_EMAIL: dict[str, MensagemEmail] = {
    Tipo.CONFIRMACAO: MensagemEmail(
        canal_email.enviar_confirmacao_agendamento_email, _email_cliente,
        lambda at: {**_base(at), 'valor': _valor(at)},
    ),
    Tipo.CANCELAMENTO: MensagemEmail(canal_email.enviar_cancelamento_email, _email_cliente, _base),
    Tipo.LEMBRETE: MensagemEmail(
        canal_email.enviar_lembrete_email, _email_cliente, _base, pede_confirmacao=True, vale=_lembrete_vale,
    ),
    Tipo.APROVACAO: MensagemEmail(
        canal_email.enviar_aprovacao_profissional_email, _email_profissional,
        lambda at: {
            'profissional': at.profissional.nome, 'cliente': at.cliente.nome,
            'procedimento': at.procedimento.nome,
            'data_hora': fmt_local(at.data_hora_inicio, FORMATO_DATA_HORA),
            'link_revisar': link_revisar_agenda(at.data_hora_inicio),
        },
    ),
}


# ─── Fila de e-mail ───────────────────────────────────────────────────────

def enfileirar_email(atendimento, tipo, mensagem: str = '') -> Notificacao | None:
    """Pede o e-mail ``tipo`` do atendimento: Notificacao PENDENTE agora, envio no on_commit.

    mensagem: paragrafo extra do e-mail (ex.: o motivo do cancelamento), gravado na notificacao
    p/ o reenvio sair igual. Sem destinatario (cliente sem e-mail, profissional sem usuario) nao
    cria nada."""
    if not MENSAGENS_EMAIL[tipo].destino(atendimento):
        return None
    notif = Notificacao.objects.create(
        atendimento=atendimento, tipo=tipo, canal=Canal.EMAIL, mensagem=mensagem,
        status=Status.PENDENTE, proxima_tentativa_em=timezone.now(),
    )
    transaction.on_commit(lambda: enviar_da_fila(notif.pk), robust=True)
    return notif


def enviar_da_fila(notificacao_id) -> bool:
    """Tenta enviar UMA notificacao de e-mail PENDENTE. True = enviada agora.

    Linha travada (skip_locked no PG): on_commit e cron nunca mandam a mesma duas vezes."""
    agora = timezone.now()
    with transaction.atomic():
        notif = (
            Notificacao.objects.select_for_update(skip_locked=True)
            .filter(pk=notificacao_id, canal=Canal.EMAIL, status=Status.PENDENTE)
            .first()
        )
        if notif is None:
            return False
        at = (Atendimento.objects.select_related('cliente', 'procedimento', 'profissional__usuario')
              .get(pk=notif.atendimento_id))
        msg = MENSAGENS_EMAIL[notif.tipo]
        email = msg.destino(at)
        if msg.vale is not None and not msg.vale(at):
            notif.status, notif.proxima_tentativa_em = Status.FALHOU, None
            notif.save(update_fields=['status', 'proxima_tentativa_em', 'atualizado_em'])
            logger.info('email_fila_descartado', extra={'notificacao_id': notif.pk, 'tipo': notif.tipo})
            return False
        conteudo = {**msg.dados(at), 'mensagem': notif.mensagem}
        token = None
        if msg.pede_confirmacao and email:
            token = links.emitir(links.CONFIRMAR_PRESENCA, notif, expira_em=at.data_hora_inicio)
            conteudo.update(links_confirmacao(token))
        ok = bool(email and msg.enviar(email, conteudo))
        if ok:
            notif.status, notif.enviado_em, notif.proxima_tentativa_em = Status.ENVIADO, agora, None
        else:
            if token:
                links.descartar(token)   # o link desta tentativa nunca saiu do servidor
            notif.tentativas += 1
            if not email or notif.tentativas >= MAX_TENTATIVAS or notif.criado_em < agora - VALIDADE:
                notif.status, notif.proxima_tentativa_em = Status.FALHOU, None
                logger.error('email_fila_desistiu', extra={'notificacao_id': notif.pk, 'tipo': notif.tipo,
                                                           'tentativas': notif.tentativas})
            else:
                notif.proxima_tentativa_em = agora + ESPERA_BASE * 2 ** (notif.tentativas - 1)
        notif.save(update_fields=['status', 'enviado_em', 'tentativas', 'proxima_tentativa_em', 'atualizado_em'])
    return ok


def reenviar_pendentes(agora=None) -> dict:
    """Cron 'emails': reenvia os PENDENTE vencidos (lote). -> {'enviados', 'pendentes'}."""
    agora = agora or timezone.now()
    ids = list(
        Notificacao.objects.filter(
            canal=Canal.EMAIL, status=Status.PENDENTE, proxima_tentativa_em__lte=agora,
        ).order_by('proxima_tentativa_em').values_list('pk', flat=True)[:LOTE_REENVIO]
    )
    enviados = sum(enviar_da_fila(pk) for pk in ids)
    pendentes = Notificacao.objects.filter(canal=Canal.EMAIL, status=Status.PENDENTE).count()
    return {'enviados': enviados, 'pendentes': pendentes}


# ─── WhatsApp ─────────────────────────────────────────────────────────────

def _registrar_resultado(notif: Notificacao, ok: bool) -> Notificacao:
    notif.status = Status.ENVIADO if ok else Status.FALHOU
    notif.enviado_em = timezone.now() if ok else None
    notif.save(update_fields=['status', 'enviado_em', 'mensagem', 'atualizado_em'])
    return notif


def _parametros(*textos: str) -> list[dict]:
    return [{'type': 'body', 'parameters': [{'type': 'text', 'text': t} for t in textos]}]


def _enviar_template(notif: Notificacao, telefone, template: str, components: list[dict]) -> Notificacao:
    """Envia e grava o resultado. Erro inesperado do canal tambem vira FALHOU (a notificacao nunca
    fica PENDENTE orfa e o lote segue)."""
    try:
        ok = canal_whatsapp.enviar_template(telefone, template, components)
    except Exception:  # noqa: BLE001 — falha de 1 destinatario nao derruba o lote
        logger.exception('whatsapp_envio_falhou', extra={'notificacao_id': notif.pk, 'template': template})
        ok = False
    return _registrar_resultado(notif, ok)


def enviar_confirmacao_d1(atendimento) -> Notificacao:
    """Lembrete D-1 por WhatsApp (template TEMPLATE_CONFIRMACAO_D1, categoria UTILITY).

    Parametros do template (ordem): {{1}} nome · {{2}} data dd/mm/aaaa · {{3}} hora HH:MM (fuso da
    clinica) · {{4}} procedimento · {{5}} profissional · {{6}} link confirmar · {{7}} link cancelar.
    O link (CONFIRMAR_PRESENCA) vale ate o inicio do atendimento."""
    data = fmt_local(atendimento.data_hora_inicio, '%d/%m/%Y')
    hora = fmt_local(atendimento.data_hora_inicio, '%H:%M')
    template = canal_whatsapp.TEMPLATE_CONFIRMACAO_D1
    with transaction.atomic():
        notif = Notificacao.objects.create(
            atendimento=atendimento, tipo=Tipo.LEMBRETE, canal=Canal.WHATSAPP, status=Status.PENDENTE,
            # sem o nome da cliente (PII fora do historico de envio)
            mensagem=(f'[Template {template}] {data} {hora} / '
                      f'{atendimento.procedimento.nome} c/ {atendimento.profissional.nome}'),
        )
        token = links.emitir(links.CONFIRMAR_PRESENCA, notif, expira_em=atendimento.data_hora_inicio)
    botoes = links_confirmacao(token)
    return _enviar_template(notif, atendimento.cliente.telefone, template, _parametros(
        atendimento.cliente.nome, data, hora, atendimento.procedimento.nome, atendimento.profissional.nome,
        botoes['link_confirmar'], botoes['link_cancelar'],
    ))


def enviar_nps_whatsapp(atendimento) -> Notificacao:
    """Pesquisa NPS por WhatsApp (template TEMPLATE_NPS): {{1}} nome · {{2}} procedimento ·
    {{3}} link da pesquisa (LinkAcesso NPS, VALIDADE_LINK_NPS a partir do envio)."""
    template = canal_whatsapp.TEMPLATE_NPS
    with transaction.atomic():
        notif = Notificacao.objects.create(
            atendimento=atendimento, tipo=Tipo.NPS, canal=Canal.WHATSAPP, status=Status.PENDENTE,
            mensagem=f'[Template {template}] NPS {atendimento.procedimento.nome}',
        )
        token = links.emitir(links.NPS, notif, expira_em=notif.criado_em + VALIDADE_LINK_NPS)
    url = url_absoluta(reverse('aranha:nps_web', args=[token]))
    return _enviar_template(notif, atendimento.cliente.telefone, template, _parametros(
        atendimento.cliente.nome, atendimento.procedimento.nome, url,
    ))


# ─── Marketing ────────────────────────────────────────────────────────────

def enviar_marketing_com_descadastro(cliente, enviar: Callable[[str], bool]) -> bool:
    """Emite o link de descadastro DESTE e-mail e chama ``enviar(url_de_descadastro)``.

    Cada e-mail de marketing leva o seu token (link_acesso DESCADASTRO; so o hash no banco). Se o
    envio falhar, o link — que nunca saiu do servidor — e descartado."""
    token = links.emitir(links.DESCADASTRO, cliente)
    ok = False
    try:
        ok = bool(enviar(url_descadastro(token)))
    finally:
        if not ok:
            links.descartar(token)
    return ok
