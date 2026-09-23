"""AJAX/API endpoints para agendamento — horarios, dias e cancelamento por token.

Disponibilidade vem SEMPRE do SlotService (services/disponibilidade.py):
feriado, folga/horario diferente, bloqueios (inclusive recorrentes/globais),
buffer, min_notice, max_advance e sobreposicao no intervalo inteiro.
"""
import json
import logging
from datetime import date, datetime, time, timedelta

from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from ..models import (
    Atendimento,
    DisponibilidadeProfissional,
    ExcecaoDisponibilidade,
    Feriado,
    Procedimento,
)
from ..services.disponibilidade import SlotService, profissionais_para
from ..utils.precos import preco_com_promocao

logger = logging.getLogger(__name__)


def _procedimento_ativo(procedimento_id):
    """(procedimento, JsonResponse de erro)."""
    procedimento_id = str(procedimento_id or '').strip()
    if not procedimento_id:
        return None, JsonResponse({'error': 'Parâmetros obrigatórios: data, procedimento_id'}, status=400)
    if not procedimento_id.isdigit():
        return None, JsonResponse({'error': 'Parâmetro inválido'}, status=400)
    try:
        return Procedimento.objects.get(pk=int(procedimento_id), ativo=True), None
    except Procedimento.DoesNotExist:
        return None, JsonResponse({'error': 'Procedimento não encontrado'}, status=404)


def _preco_json(procedimento, profissional, dia):
    """{'valor', 'valor_cheio', 'promocao'} (strings decimais; None = a consultar)."""
    valor, promocao, cheio = preco_com_promocao(procedimento, profissional, dia)
    return {
        'valor': str(valor) if valor is not None else None,
        'valor_cheio': str(cheio) if promocao is not None else None,
        'promocao': promocao.nome if promocao is not None else '',
    }


def _profissionais(request, procedimento):
    """Profissionais habilitados; ?profissional_id= restringe (link 'Agendar com X').

    Se o profissional pedido nao realiza o procedimento, ignora o filtro
    (mostra todos) em vez de deixar o calendario vazio.
    """
    profissionais = list(profissionais_para(procedimento))
    prof_id = str(request.GET.get('profissional_id') or '').strip()
    if prof_id.isdigit():
        filtrados = [p for p in profissionais if p.pk == int(prof_id)]
        if filtrados:
            return filtrados
    return profissionais


@ratelimit(key='ip', rate='30/m', method='GET', block=True)
def api_horarios_disponiveis(request):
    """AJAX: horarios livres (hora local) de uma data p/ um procedimento.

    GET: data (YYYY-MM-DD), procedimento_id, profissional_id (opcional).
    """
    data_str = request.GET.get('data', '')
    if not data_str or not request.GET.get('procedimento_id'):
        return JsonResponse({'error': 'Parâmetros obrigatórios: data, procedimento_id'}, status=400)

    try:
        data_selecionada = datetime.strptime(data_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Data inválida'}, status=400)

    procedimento, erro = _procedimento_ativo(request.GET.get('procedimento_id'))
    if erro:
        return erro

    agrupados = {}
    for prof in _profissionais(request, procedimento):
        slots = SlotService.slots_livres(prof, data_selecionada, procedimento)
        if not slots:
            continue
        # Preco NA DATA do atendimento p/ este profissional (promocao inclusa):
        # o resumo do wizard mostra exatamente o valor que o booking grava.
        info = {'id': prof.pk, 'nome': prof.nome, **_preco_json(procedimento, prof, data_selecionada)}
        for hhmm in slots:
            if hhmm not in agrupados:
                inicio = timezone.make_aware(
                    datetime.combine(data_selecionada, time.fromisoformat(hhmm))
                )
                agrupados[hhmm] = {
                    'horario': hhmm,
                    'datetime_iso': inicio.isoformat(),
                    'profissionais': [],
                }
            agrupados[hhmm]['profissionais'].append(info)

    return JsonResponse({
        'data': data_str,
        'procedimento': procedimento.nome,
        'duracao': procedimento.duracao_minutos,
        'horarios': [agrupados[k] for k in sorted(agrupados)],
    })


@ratelimit(key='ip', rate='30/m', method='GET', block=True)
def api_dias_disponiveis(request):
    """AJAX: dias do mes com expediente p/ o procedimento (sem feriado/folga,
    entre hoje e o max_advance do profissional).

    GET: mes (YYYY-MM), procedimento_id, profissional_id (opcional).
    """
    mes_str = request.GET.get('mes', '')
    if not mes_str or not request.GET.get('procedimento_id'):
        return JsonResponse({'error': 'Parâmetros obrigatórios'}, status=400)

    try:
        ano, mes = map(int, mes_str.split('-'))
        primeiro_dia = date(ano, mes, 1)
        ultimo_dia = (date(ano + 1, 1, 1) if mes == 12 else date(ano, mes + 1, 1)) - timedelta(days=1)
    except (ValueError, TypeError, OverflowError):
        return JsonResponse({'error': 'Mês inválido'}, status=400)

    procedimento, erro = _procedimento_ativo(request.GET.get('procedimento_id'))
    if erro:
        return erro

    profissionais = _profissionais(request, procedimento)
    hoje = timezone.localdate()
    inicio = max(primeiro_dia, hoje)
    dias = []
    if profissionais and inicio <= ultimo_dia:
        prof_ids = [p.pk for p in profissionais]
        feriados = set(Feriado.objects.filter(
            data__range=(inicio, ultimo_dia), bloqueia_agendamento=True,
        ).values_list('data', flat=True))
        semanal = {}
        for prof_id, dia_semana in DisponibilidadeProfissional.objects.filter(
            profissional_id__in=prof_ids,
        ).values_list('profissional_id', 'dia_semana'):
            semanal.setdefault(prof_id, set()).add(dia_semana)
        folgas, horario_diferente = set(), set()
        for prof_id, data_ex, tipo in ExcecaoDisponibilidade.objects.filter(
            profissional_id__in=prof_ids, data__range=(inicio, ultimo_dia),
        ).values_list('profissional_id', 'data', 'tipo'):
            (folgas if tipo == 'FOLGA' else horario_diferente).add((prof_id, data_ex))

        dia = inicio
        while dia <= ultimo_dia:
            if dia not in feriados:
                dia_semana = dia.isoweekday() % 7 + 1
                for prof in profissionais:
                    if dia > hoje + timedelta(days=prof.max_advance_dias):
                        continue
                    if (prof.pk, dia) in folgas:
                        continue
                    if (prof.pk, dia) in horario_diferente or dia_semana in semanal.get(prof.pk, ()):
                        dias.append(dia.isoformat())
                        break
            dia += timedelta(days=1)

    return JsonResponse({
        'mes': mes_str,
        'dias_disponiveis': dias,
    })


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def cancelar_agendamento(request):
    """Cancela um agendamento via token seguro (anti-IDOR), pela FSM."""
    if request.method != 'POST':
        return JsonResponse({'erro': 'Método não permitido'}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'erro': 'Dados inválidos'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'erro': 'Dados inválidos'}, status=400)
    token = str(data.get('token') or '').strip()
    if not token:
        return JsonResponse({'erro': 'Token obrigatório'}, status=400)

    try:
        with transaction.atomic():
            try:
                atendimento = (
                    Atendimento.objects.select_for_update()
                    .select_related('procedimento')
                    .get(token_cancelamento=token)
                )
            except Atendimento.DoesNotExist:
                return JsonResponse({'erro': 'Agendamento não encontrado'}, status=404)

            if atendimento.data_hora_inicio <= timezone.now():
                return JsonResponse(
                    {'erro': 'Não é possível cancelar agendamentos passados'}, status=400
                )
            if atendimento.status not in Atendimento.STATUS_ATIVOS:
                return JsonResponse({'erro': 'Este agendamento não pode mais ser cancelado'}, status=400)
            try:
                # FSM: auditoria + evento AtendimentoCancelado (lista de espera etc.)
                atendimento.cancelar(motivo='Cancelado pelo cliente (portal)')
            except Atendimento.TransicaoInvalida:
                return JsonResponse({'erro': 'Este agendamento não pode mais ser cancelado'}, status=400)
            procedimento_nome = atendimento.procedimento.nome
    except DatabaseError:
        logger.error('cancelar_agendamento_falha', exc_info=True)
        return JsonResponse({'erro': 'Ocorreu um erro interno. Tente novamente.'}, status=500)

    return JsonResponse({
        'sucesso': True,
        'mensagem': f'Agendamento de {procedimento_nome} cancelado com sucesso.',
    })

