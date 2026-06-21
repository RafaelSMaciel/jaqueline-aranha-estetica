"""AJAX/API endpoints para agendamento — horarios, dias, verificacao, cancelamento."""
import json
import logging
from datetime import datetime, timedelta

from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.urls import reverse
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from ..models import (
    Atendimento,
    BloqueioAgenda,
    Cliente,
    DisponibilidadeProfissional,
    CodigoOtp,
    Procedimento,
    Profissional,
    Habilitacao,
)
# OTP email removido: SMS exclusivo via OTPService

logger = logging.getLogger(__name__)


@ratelimit(key='ip', rate='30/m', method='GET', block=True)
def api_horarios_disponiveis(request):
    """
    AJAX endpoint: retorna horários disponíveis para uma data + procedimento.
    GET params: data (YYYY-MM-DD), procedimento_id
    """
    data_str = request.GET.get('data', '')
    procedimento_id = request.GET.get('procedimento_id', '')

    if not data_str or not procedimento_id:
        return JsonResponse({'error': 'Parâmetros obrigatórios: data, procedimento_id'}, status=400)

    try:
        data_selecionada = datetime.strptime(data_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Data inválida'}, status=400)

    try:
        procedimento = Procedimento.objects.get(pk=procedimento_id, ativo=True)
    except Procedimento.DoesNotExist:
        return JsonResponse({'error': 'Procedimento não encontrado'}, status=404)

    # Buscar profissionais que fazem esse procedimento
    prof_ids = Habilitacao.objects.filter(
        procedimento=procedimento
    ).values_list('profissional_id', flat=True)

    profissionais = Profissional.objects.filter(
        pk__in=prof_ids, ativo=True
    )

    # Se não há profissionais vinculados, pegar todos os ativos
    if not profissionais.exists():
        profissionais = Profissional.objects.filter(ativo=True)

    dia_semana = data_selecionada.isoweekday() % 7 + 1

    horarios = []
    agora = timezone.now()
    prof_pks = [p.pk for p in profissionais]

    # Pre-carregar disponibilidade, atendimentos e bloqueios do dia para
    # TODOS os profissionais de uma vez (evita N+1 dentro do loop de slots).
    disp_por_prof = {
        d.profissional_id: d
        for d in DisponibilidadeProfissional.objects.filter(
            profissional_id__in=prof_pks, dia_semana=dia_semana
        )
    }

    dia_inicio = timezone.make_aware(datetime.combine(data_selecionada, datetime.min.time()))
    dia_fim = dia_inicio + timedelta(days=1)

    ocupados_por_prof = {}
    for at in Atendimento.objects.filter(
        profissional_id__in=prof_pks,
        data_hora_inicio__lt=dia_fim,
        data_hora_fim__gt=dia_inicio,
        status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO'],
    ).values_list('profissional_id', 'data_hora_inicio', 'data_hora_fim'):
        ocupados_por_prof.setdefault(at[0], []).append((at[1], at[2]))

    bloqueios_por_prof = {}
    for bl in BloqueioAgenda.objects.filter(
        profissional_id__in=prof_pks,
        data_hora_inicio__lt=dia_fim,
        data_hora_fim__gt=dia_inicio,
    ).values_list('profissional_id', 'data_hora_inicio', 'data_hora_fim'):
        bloqueios_por_prof.setdefault(bl[0], []).append((bl[1], bl[2]))

    for prof in profissionais:
        # Verificar disponibilidade do profissional nesse dia
        disp = disp_por_prof.get(prof.pk)
        if disp is None:
            continue

        ocupados = ocupados_por_prof.get(prof.pk, [])
        bloqueios = bloqueios_por_prof.get(prof.pk, [])

        # Gerar slots de 30 em 30 minutos dentro do horário do profissional
        intervalo = timedelta(minutes=30)
        hora_atual = datetime.combine(data_selecionada, disp.hora_inicio)
        hora_fim = datetime.combine(data_selecionada, disp.hora_fim)
        # O último slot precisa ter espaço para a duração do procedimento
        hora_limite = hora_fim - timedelta(minutes=procedimento.duracao_minutos)

        while hora_atual <= hora_limite:
            dt_aware = timezone.make_aware(hora_atual)
            fim_procedimento = dt_aware + timedelta(minutes=procedimento.duracao_minutos)

            # Checar se o slot está ocupado (overlap em memoria)
            ocupado = any(
                ini < fim_procedimento and fim > dt_aware
                for ini, fim in ocupados
            )

            # Checar bloqueios (em memoria)
            bloqueado = any(
                ini <= dt_aware and fim >= dt_aware
                for ini, fim in bloqueios
            )

            # Não mostrar horários passados
            passado = dt_aware < agora

            if not ocupado and not bloqueado and not passado:
                horario_str = hora_atual.strftime('%H:%M')
                # Verificar se esse horário já foi adicionado com esse profissional
                horarios.append({
                    'horario': horario_str,
                    'datetime_iso': dt_aware.isoformat(),
                    'profissional_id': prof.pk,
                    'profissional_nome': prof.nome,
                })

            hora_atual += intervalo

    # Agrupar horários por horário (um horário pode ter vários profissionais)
    horarios_agrupados = {}
    for h in horarios:
        key = h['horario']
        if key not in horarios_agrupados:
            horarios_agrupados[key] = {
                'horario': key,
                'datetime_iso': h['datetime_iso'],
                'profissionais': []
            }
        horarios_agrupados[key]['profissionais'].append({
            'id': h['profissional_id'],
            'nome': h['profissional_nome']
        })

    # Ordenar por horário
    resultado = sorted(horarios_agrupados.values(), key=lambda x: x['horario'])

    return JsonResponse({
        'data': data_str,
        'procedimento': procedimento.nome,
        'duracao': procedimento.duracao_minutos,
        'horarios': resultado
    })


@ratelimit(key='ip', rate='30/m', method='GET', block=True)
def api_dias_disponiveis(request):
    """
    AJAX: retorna quais dias do mês têm disponibilidade para um procedimento.
    GET params: mes (YYYY-MM), procedimento_id
    """
    mes_str = request.GET.get('mes', '')
    procedimento_id = request.GET.get('procedimento_id', '')

    if not mes_str or not procedimento_id:
        return JsonResponse({'error': 'Parâmetros obrigatórios'}, status=400)

    try:
        ano, mes = map(int, mes_str.split('-'))
        primeiro_dia = datetime(ano, mes, 1).date()
        if mes == 12:
            ultimo_dia = datetime(ano + 1, 1, 1).date() - timedelta(days=1)
        else:
            ultimo_dia = datetime(ano, mes + 1, 1).date() - timedelta(days=1)
    except ValueError:
        return JsonResponse({'error': 'Mês inválido'}, status=400)

    try:
        procedimento = Procedimento.objects.get(pk=procedimento_id, ativo=True)
    except Procedimento.DoesNotExist:
        return JsonResponse({'error': 'Procedimento não encontrado'}, status=404)

    # Para cada dia do mês, verificar se algum profissional tem disponibilidade
    prof_ids = Habilitacao.objects.filter(
        procedimento=procedimento
    ).values_list('profissional_id', flat=True)

    profissionais = Profissional.objects.filter(
        pk__in=prof_ids, ativo=True
    )
    if not profissionais.exists():
        profissionais = Profissional.objects.filter(ativo=True)

    # Pegar dias da semana em que os profissionais trabalham
    # (uma unica query para todos os profissionais — evita N+1)
    dias_disponibilidade = set(
        DisponibilidadeProfissional.objects.filter(
            profissional_id__in=[p.pk for p in profissionais]
        ).values_list('dia_semana', flat=True)
    )

    hoje = timezone.now().date()
    dias_com_disponibilidade = []
    dia_atual = primeiro_dia

    while dia_atual <= ultimo_dia:
        dia_semana = dia_atual.isoweekday() % 7 + 1
        if dia_semana in dias_disponibilidade and dia_atual >= hoje:
            dias_com_disponibilidade.append(dia_atual.isoformat())
        dia_atual += timedelta(days=1)

    return JsonResponse({
        'mes': mes_str,
        'dias_disponiveis': dias_com_disponibilidade,
    })


@ratelimit(key='ip', rate='5/m', method='POST', block=False)
def verificar_telefone(request):
    """AJAX: gerar ou validar código de verificação por telefone."""
    if request.method == 'POST':
        # SEGURANÇA: Rate limiting
        if getattr(request, 'limited', False):
            return JsonResponse({'error': 'Muitas tentativas. Aguarde um momento.'}, status=429)

        if request.content_type == 'application/json':
            try:
                data = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse({'error': 'Dados inválidos'}, status=400)
        else:
            data = request.POST
        action = data.get('action', '')
        from ..validators import normalizar_telefone
        telefone = normalizar_telefone(data.get('telefone', ''))

        if not telefone:
            return JsonResponse({'error': 'Telefone obrigatório'}, status=400)

        if action == 'enviar':
            from ..services.notificacao import OTPService
            from ..utils.security import client_ip as _ip
            ip = _ip(request)

            # Anti-enumeracao: resposta identica exista o cliente ou nao.
            # OTP so e gerado/enviado se houver cadastro — atacante nao distingue.
            if Cliente.objects.filter(telefone=telefone).exists():
                ident = CodigoOtp.email_para_telefone(telefone)
                # Cooldown anti-abuso/custo: nao reenvia SMS se um codigo foi gerado
                # ha pouco (resposta segue identica p/ nao vazar enumeracao/timing).
                if CodigoOtp.pode_reenviar(ident, proposito=CodigoOtp.PROPOSITO_LOGIN):
                    codigo, _obj = CodigoOtp.gerar_sms(
                        telefone, ip=ip, proposito=CodigoOtp.PROPOSITO_LOGIN,
                    )
                    if not OTPService.enviar_codigo(telefone, codigo, ip=ip):
                        logger.warning('otp_login_sms_falha', extra={'tel_suffix': telefone[-4:]})
            return JsonResponse({
                'success': True,
                'message': 'Se houver cadastro com esse telefone, o codigo chegara por SMS.',
            })

        elif action == 'verificar':
            codigo_input = data.get('codigo', '').strip()
            ok, _motivo = CodigoOtp.verificar_sms(
                telefone, codigo_input, proposito=CodigoOtp.PROPOSITO_LOGIN,
            )
            if ok:
                request.session.cycle_key()  # anti session-fixation
                request.session['telefone_verificado'] = telefone
                return JsonResponse({
                    'success': True,
                    'redirect': reverse('aranha:meus_agendamentos') + '?step=3'
                })
            return JsonResponse({
                'error': 'Código inválido ou expirado.'
            }, status=400)

    return JsonResponse({'error': 'Método não permitido'}, status=405)


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def cancelar_agendamento(request):
    """Cancela um agendamento via token seguro (anti-IDOR)."""
    if request.method != 'POST':
        return JsonResponse({'erro': 'Método não permitido'}, status=405)

    try:
        data = json.loads(request.body)
        token = data.get('token', '').strip()

        if not token:
            return JsonResponse({'erro': 'Token obrigatório'}, status=400)

        # Buscar atendimento pelo token (não por ID + telefone)
        try:
            with transaction.atomic():
                atendimento = (
                    Atendimento.objects.select_for_update()
                    .select_related('cliente', 'procedimento')
                    .get(token_cancelamento=token)
                )

                # Verificar se é futuro
                if atendimento.data_hora_inicio <= timezone.now():
                    return JsonResponse(
                        {'erro': 'Não é possível cancelar agendamentos passados'}, status=400
                    )

                # Verificar se já está cancelado
                if atendimento.status == 'CANCELADO':
                    return JsonResponse({'erro': 'Este agendamento já foi cancelado'}, status=400)

                # Cancelar
                atendimento.status = 'CANCELADO'
                atendimento.save()
                procedimento_nome = atendimento.procedimento.nome
        except Atendimento.DoesNotExist:
            return JsonResponse({'erro': 'Agendamento não encontrado'}, status=404)

        return JsonResponse({
            'sucesso': True,
            'mensagem': f'Agendamento de {procedimento_nome} cancelado com sucesso.',
        })

    except json.JSONDecodeError:
        return JsonResponse({'erro': 'Dados inválidos'}, status=400)
    except DatabaseError:
        logger.error('cancelar_agendamento_falha', exc_info=True)
        return JsonResponse({'erro': 'Ocorreu um erro interno. Tente novamente.'}, status=500)


# ─── AJAX endpoints (antigo ajax.py) ───

@require_GET
@ratelimit(key='ip', rate='30/m', method='GET', block=True)
def buscar_procedimentos(request):
    """Retorna procedimentos ativos (endpoint publico para agendamento)."""
    procedimentos = Procedimento.objects.filter(ativo=True).values(
        'id', 'nome', 'duracao_minutos'
    )
    return JsonResponse({'procedimentos': list(procedimentos)})


@require_GET
@ratelimit(key='ip', rate='30/m', method='GET', block=True)
def buscar_horarios(request):
    """Retorna horarios disponiveis para um profissional em uma data."""
    prof_id = request.GET.get('profissional_id')
    data_str = request.GET.get('data')
    if not prof_id or not data_str:
        return JsonResponse({'error': 'Parâmetros obrigatórios: profissional_id, data'}, status=400)

    try:
        data = datetime.strptime(data_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Data inválida. Use YYYY-MM-DD.'}, status=400)

    try:
        profissional = Profissional.objects.get(pk=prof_id, ativo=True)
    except Profissional.DoesNotExist:
        return JsonResponse({'error': 'Profissional não encontrado'}, status=404)

    horarios = profissional.get_horarios_disponiveis(data)
    return JsonResponse({'horarios': horarios})
