"""Workflow engine — avalia regras e dispara acoes.

Triggers:
  ON_BOOK / ON_CANCEL / ON_NO_SHOW: chamar `disparar_evento(tipo, atendimento)` apos transicao.
  BEFORE_EVENT / AFTER_EVENT: chamar `executar_pendentes()` periodicamente (cron).

Deduplicacao: WorkflowExecucao(regra, atendimento) UNIQUE.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Tuple, TYPE_CHECKING

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import (
    Atendimento, Notificacao, WorkflowExecucao, WorkflowRegra,
)

if TYPE_CHECKING:
    from ..models import WorkflowRegra as _Regra  # noqa: F401

logger = logging.getLogger(__name__)

# Janela de busca para regras temporais (BEFORE/AFTER_EVENT)
JANELA_BUSCA_PASSADO = timedelta(days=7)
JANELA_BUSCA_FUTURO = timedelta(days=30)
WEBHOOK_TIMEOUT_SEGUNDOS = 5

ActionResult = Tuple[bool, str]


def _enfileirar_notificacao(regra: WorkflowRegra, atendimento: Atendimento) -> ActionResult:
    """Cria Notificacao pendente. Dispatcher existente (tasks.py) consome."""
    canal_map = {
        'SEND_EMAIL': 'EMAIL',
        'SEND_SMS': 'SMS',
        'SEND_WHATSAPP': 'WHATSAPP',
    }
    canal = canal_map.get(regra.acao)
    if not canal:
        return False, f'acao {regra.acao} nao mapeia para Notificacao'
    tipo = regra.config_json.get('tipo_notificacao', 'LEMBRETE')
    Notificacao.objects.create(
        atendimento=atendimento,
        tipo=tipo,
        canal=canal,
        status_envio='PENDENTE',
        mensagem=regra.template or '',
    )
    return True, 'enfileirada'


def _disparar_push(regra: WorkflowRegra, atendimento: Atendimento) -> ActionResult:
    """Dispara Web Push para o profissional vinculado ao atendimento."""
    try:
        from .push import send_push_to_user
    except ImportError:
        return False, 'push module ausente'
    user = getattr(atendimento.profissional, 'usuario', None)
    if not user:
        return False, 'profissional sem usuario'
    payload = {
        'head': regra.config_json.get('titulo', 'Notificacao'),
        'body': regra.template or f'Atendimento {atendimento.pk}',
        'url': regra.config_json.get('url', '/painel/'),
    }
    ok = send_push_to_user(user, payload)
    return ok, 'push enviado' if ok else 'falha push'


def _disparar_webhook(regra: WorkflowRegra, atendimento: Atendimento) -> ActionResult:
    """Enfileira webhook em Celery — fallback sincrono se Celery indisponivel."""
    url = regra.config_json.get('webhook_url')
    if not url:
        return False, 'webhook_url ausente'
    payload = {
        'evento': regra.trigger,
        'atendimento_id': atendimento.pk,
        'cliente_id': atendimento.cliente_id,
        'data_hora_inicio': atendimento.data_hora_inicio.isoformat(),
        'status': atendimento.status,
    }
    try:
        from ..tasks import dispatch_webhook
        dispatch_webhook.delay(url, payload)
        return True, 'enfileirado'
    except (ImportError, AttributeError) as exc:
        # Celery/broker indisponivel — fallback sincrono
        import requests
        try:
            resp = requests.post(url, json=payload, timeout=WEBHOOK_TIMEOUT_SEGUNDOS)
            return resp.ok, f'http {resp.status_code} (sync fallback)'
        except requests.RequestException as e:
            return False, f'erro: {e} (sync fallback {exc})'


def _condicao_match(regra: WorkflowRegra, atendimento: Atendimento) -> ActionResult:
    """Filtros configuraveis em config_json. Retorna (ok, detalhe_skip)."""
    cfg = regra.config_json or {}
    modalidade_filter = cfg.get('modalidade_filter')
    if modalidade_filter:
        modalidade = getattr(atendimento.procedimento, 'modalidade', None)
        if modalidade != modalidade_filter:
            return False, f'skip modalidade={modalidade} != {modalidade_filter}'
    categoria_filter = cfg.get('categoria_filter')
    if categoria_filter:
        if atendimento.procedimento.categoria != categoria_filter:
            return False, f'skip categoria={atendimento.procedimento.categoria} != {categoria_filter}'
    return True, ''


def _disparar_pesquisa_whatsapp(regra: WorkflowRegra, atendimento: Atendimento) -> ActionResult:
    """Cria RespostaAnamnese + manda WhatsApp template pesquisa_online."""
    cfg = regra.config_json or {}
    formulario_id = cfg.get('formulario_id')
    if not formulario_id:
        return False, 'formulario_id ausente em config_json'

    from ..models import FormularioAnamnese
    try:
        formulario = FormularioAnamnese.objects.get(pk=formulario_id, ativo=True, tipo='PESQUISA')
    except FormularioAnamnese.DoesNotExist:
        return False, f'FormularioAnamnese pk={formulario_id} (tipo PESQUISA, ativo) nao encontrado'

    from .notificacao import WhatsAppService
    notif = WhatsAppService.enviar_pesquisa(atendimento, formulario)
    if notif and notif.status_envio == 'ENVIADO':
        return True, f'pesquisa enviada notif={notif.pk}'
    return False, f'falha envio pesquisa (notif={notif.pk if notif else None})'


def _executar_acao(regra: WorkflowRegra, atendimento: Atendimento) -> ActionResult:
    """Roteia para handler conforme regra.acao."""
    cfg = regra.config_json or {}
    tipo_notif = cfg.get('tipo_notificacao')

    # Branch especial: pesquisa pos-atendimento detalhada
    if tipo_notif == 'PESQUISA' and regra.acao == 'SEND_WHATSAPP':
        return _disparar_pesquisa_whatsapp(regra, atendimento)

    if regra.acao in ('SEND_EMAIL', 'SEND_SMS', 'SEND_WHATSAPP'):
        return _enfileirar_notificacao(regra, atendimento)
    if regra.acao == 'SEND_PUSH':
        return _disparar_push(regra, atendimento)
    if regra.acao == 'WEBHOOK':
        return _disparar_webhook(regra, atendimento)
    return False, f'acao desconhecida: {regra.acao}'


def _executar_regra(regra: WorkflowRegra, atendimento: Atendimento) -> bool:
    """Executa regra com deduplicacao via UNIQUE(regra, atendimento)."""
    # Filtros pre-reserva (sem criar WorkflowExecucao p/ permitir disparo futuro
    # se atendimento for editado e voltar a satisfazer condicao)
    cond_ok, _cond_detalhe = _condicao_match(regra, atendimento)
    if not cond_ok:
        return False

    try:
        with transaction.atomic():
            exec_row = WorkflowExecucao.objects.create(
                regra=regra, atendimento=atendimento, status='SKIPPED', detalhe='reservando'
            )
    except IntegrityError:
        return False  # ja executado

    try:
        ok, detalhe = _executar_acao(regra, atendimento)
        exec_row.status = 'OK' if ok else 'FALHOU'
        exec_row.detalhe = detalhe[:500]
        exec_row.save(update_fields=['status', 'detalhe'])
        return ok
    except (ConnectionError, TimeoutError) as exc:
        logger.warning(
            'workflow_regra_network_error',
            extra={'regra_id': regra.pk, 'atendimento_id': atendimento.pk, 'error': str(exc)},
        )
        exec_row.status = 'FALHOU'
        exec_row.detalhe = f'network: {exc}'[:500]
        exec_row.save(update_fields=['status', 'detalhe'])
        return False
    except (ValueError, KeyError, AttributeError, IntegrityError) as exc:
        logger.exception(
            'workflow_regra_payload_error',
            extra={'regra_id': regra.pk, 'atendimento_id': atendimento.pk, 'error': str(exc)},
        )
        exec_row.status = 'FALHOU'
        exec_row.detalhe = str(exc)[:500]
        exec_row.save(update_fields=['status', 'detalhe'])
        return False


def disparar_evento(trigger_tipo: str, atendimento: Atendimento) -> int:
    """Chamar em signals/views apos transicao.

    Args:
        trigger_tipo: 'ON_BOOK' | 'ON_CANCEL' | 'ON_NO_SHOW'.
        atendimento: instancia que triggou.

    Returns:
        Numero de regras disparadas com sucesso.
    """
    regras = WorkflowRegra.objects.filter(ativo=True, trigger=trigger_tipo)
    disparadas = 0
    for regra in regras:
        if _executar_regra(regra, atendimento):
            disparadas += 1
    return disparadas


def executar_pendentes() -> dict[int, int]:
    """Avalia regras BEFORE_EVENT / AFTER_EVENT e dispara as elegiveis.

    Janela de busca: -7d..+30d. Dedup via WorkflowExecucao.

    Returns:
        Dict {regra_id: count_disparos}.
    """
    agora = timezone.now()
    janela_inicio = agora - JANELA_BUSCA_PASSADO
    janela_fim = agora + JANELA_BUSCA_FUTURO

    resultado: dict[int, int] = {}
    regras_temporais = WorkflowRegra.objects.filter(
        ativo=True, trigger__in=['BEFORE_EVENT', 'AFTER_EVENT']
    )

    for regra in regras_temporais:
        offset = timedelta(minutes=abs(regra.offset_minutos))
        ja_executados = set(
            WorkflowExecucao.objects.filter(regra=regra)
            .values_list('atendimento_id', flat=True)
        )

        if regra.trigger == 'BEFORE_EVENT':
            limite_disparo = agora + offset
            qs = Atendimento.objects.filter(
                data_hora_inicio__gt=agora,
                data_hora_inicio__lte=min(limite_disparo, janela_fim),
                data_hora_inicio__gte=janela_inicio,
                status__in=['AGENDADO', 'CONFIRMADO'],
            )
        else:  # AFTER_EVENT
            limite_disparo = agora - offset
            qs = Atendimento.objects.filter(
                data_hora_fim__lt=limite_disparo,
                data_hora_fim__gte=janela_inicio,
                status__in=['REALIZADO', 'CONFIRMADO'],
            )

        count = 0
        for at in qs:
            if at.pk in ja_executados:
                continue
            if _executar_regra(regra, at):
                count += 1
        resultado[regra.pk] = count
    return resultado
