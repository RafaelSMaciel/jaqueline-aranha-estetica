"""Workflow engine — avalia regras e dispara acoes.

Triggers:
  ON_BOOK / ON_CANCEL / ON_NO_SHOW: chamar `disparar_evento(tipo, atendimento)` apos transicao.
  BEFORE_EVENT / AFTER_EVENT: chamar `executar_pendentes()` periodicamente (cron).

Deduplicacao: WorkflowExecucao(regra, atendimento) UNIQUE.
"""
import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import (
    Atendimento, Notificacao, WorkflowExecucao, WorkflowRegra,
)

logger = logging.getLogger(__name__)


def _enfileirar_notificacao(regra, atendimento):
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


def _disparar_push(regra, atendimento):
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


def _disparar_webhook(regra, atendimento):
    import requests
    url = regra.config_json.get('webhook_url')
    if not url:
        return False, 'webhook_url ausente'
    try:
        resp = requests.post(url, json={
            'evento': regra.trigger,
            'atendimento_id': atendimento.pk,
            'cliente_id': atendimento.cliente_id,
            'data_hora_inicio': atendimento.data_hora_inicio.isoformat(),
            'status': atendimento.status,
        }, timeout=5)
        return resp.ok, f'http {resp.status_code}'
    except requests.RequestException as e:
        return False, f'erro: {e}'


def _executar_acao(regra, atendimento):
    if regra.acao in ('SEND_EMAIL', 'SEND_SMS', 'SEND_WHATSAPP'):
        return _enfileirar_notificacao(regra, atendimento)
    if regra.acao == 'SEND_PUSH':
        return _disparar_push(regra, atendimento)
    if regra.acao == 'WEBHOOK':
        return _disparar_webhook(regra, atendimento)
    return False, f'acao desconhecida: {regra.acao}'


def _executar_regra(regra, atendimento):
    """Executa regra com deduplicacao via UNIQUE(regra, atendimento)."""
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
    except Exception as e:
        logger.exception('Workflow regra %s falhou: %s', regra.pk, e)
        exec_row.status = 'FALHOU'
        exec_row.detalhe = str(e)[:500]
        exec_row.save(update_fields=['status', 'detalhe'])
        return False


def disparar_evento(trigger_tipo, atendimento):
    """Chamar em signals/views apos transicao.

    trigger_tipo: 'ON_BOOK' | 'ON_CANCEL' | 'ON_NO_SHOW'
    """
    regras = WorkflowRegra.objects.filter(ativo=True, trigger=trigger_tipo)
    disparadas = 0
    for regra in regras:
        if _executar_regra(regra, atendimento):
            disparadas += 1
    return disparadas


def executar_pendentes():
    """Avalia regras BEFORE_EVENT / AFTER_EVENT e dispara as elegiveis.

    Janela de busca: -7d..+30d. Dedup via WorkflowExecucao.
    Retorna dict {regra_id: count}.
    """
    agora = timezone.now()
    janela_inicio = agora - timedelta(days=7)
    janela_fim = agora + timedelta(days=30)

    resultado = {}
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
