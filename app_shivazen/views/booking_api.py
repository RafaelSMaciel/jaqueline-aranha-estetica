"""AJAX/API endpoints para agendamento — horarios, dias, verificacao, cancelamento."""
import json
import logging
import random
import string
from datetime import datetime, timedelta

from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from ..models import (
    Atendimento,
    BloqueioAgenda,
    Cliente,
    CodigoVerificacao,
    DisponibilidadeProfissional,
    Procedimento,
    Profissional,
    ProfissionalProcedimento,
)
from ..utils.email import enviar_codigo_otp_email

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
    prof_ids = ProfissionalProcedimento.objects.filter(
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

    for prof in profissionais:
        # Verificar disponibilidade do profissional nesse dia
        try:
            disp = DisponibilidadeProfissional.objects.get(
                profissional=prof, dia_semana=dia_semana
            )
        except DisponibilidadeProfissional.DoesNotExist:
            continue

        # Gerar slots de 30 em 30 minutos dentro do horário do profissional
        intervalo = timedelta(minutes=30)
        hora_atual = datetime.combine(data_selecionada, disp.hora_inicio)
        hora_fim = datetime.combine(data_selecionada, disp.hora_fim)
        # O último slot precisa ter espaço para a duração do procedimento
        hora_limite = hora_fim - timedelta(minutes=procedimento.duracao_minutos)

        while hora_atual <= hora_limite:
            dt_aware = timezone.make_aware(hora_atual)
            fim_procedimento = dt_aware + timedelta(minutes=procedimento.duracao_minutos)

            # Checar se o slot está ocupado
            ocupado = Atendimento.objects.filter(
                profissional=prof,
                data_hora_inicio__lt=fim_procedimento,
                data_hora_fim__gt=dt_aware,
                status__in=['AGENDADO', 'CONFIRMADO']
            ).exists()

            # Checar bloqueios
            bloqueado = BloqueioAgenda.objects.filter(
                profissional=prof,
                data_hora_inicio__lte=dt_aware,
                data_hora_fim__gte=dt_aware
            ).exists()

            # Não mostrar horários passados
            passado = dt_aware < timezone.now()

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
    prof_ids = ProfissionalProcedimento.objects.filter(
        procedimento=procedimento
    ).values_list('profissional_id', flat=True)

    profissionais = Profissional.objects.filter(
        pk__in=prof_ids, ativo=True
    )
    if not profissionais.exists():
        profissionais = Profissional.objects.filter(ativo=True)

    # Pegar dias da semana em que os profissionais trabalham
    dias_disponibilidade = set()
    for prof in profissionais:
        disps = DisponibilidadeProfissional.objects.filter(profissional=prof)
        for d in disps:
            dias_disponibilidade.add(d.dia_semana)

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

        data = json.loads(request.body) if request.content_type == 'application/json' else request.POST
        action = data.get('action', '')
        telefone = data.get('telefone', '').strip()

        if not telefone:
            return JsonResponse({'error': 'Telefone obrigatório'}, status=400)

        if action == 'enviar':
            # Verificar se existe algum cliente com esse telefone
            if not Cliente.objects.filter(telefone=telefone).exists():
                return JsonResponse({
                    'error': 'Nenhum agendamento encontrado com esse telefone.'
                }, status=404)

            # Gerar código de 6 dígitos
            codigo = ''.join(random.choices(string.digits, k=6))

            # Invalidar códigos anteriores
            CodigoVerificacao.objects.filter(telefone=telefone, usado=False).update(usado=True)

            # Criar novo código
            CodigoVerificacao.objects.create(telefone=telefone, codigo=codigo)

            # Em producao, enviar via Email
            logger.info(f'Codigo de verificacao gerado para telefone: {telefone[-4:]}')

            # Buscar email do cliente para enviar OTP
            cliente_otp = Cliente.objects.filter(telefone=telefone).first()
            if cliente_otp and cliente_otp.email:
                try:
                    enviar_codigo_otp_email(cliente_otp.email, codigo)
                except Exception:
                    logger.error(f'Falha ao enviar OTP por email para {telefone[-4:]}', exc_info=True)

            return JsonResponse({
                'success': True,
                'message': 'Codigo de verificacao enviado para seu email cadastrado.',
            })

        elif action == 'verificar':
            codigo_input = data.get('codigo', '').strip()
            if CodigoVerificacao.consumir(telefone, codigo_input):
                request.session['telefone_verificado'] = telefone
                return JsonResponse({
                    'success': True,
                    'redirect': reverse('shivazen:meus_agendamentos') + '?step=3'
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
            atendimento = Atendimento.objects.select_related('cliente', 'procedimento').get(
                token_cancelamento=token
            )
        except Atendimento.DoesNotExist:
            return JsonResponse({'erro': 'Agendamento não encontrado'}, status=404)

        # Verificar se é futuro
        if atendimento.data_hora_inicio <= timezone.now():
            return JsonResponse({'erro': 'Não é possível cancelar agendamentos passados'}, status=400)

        # Verificar se já está cancelado
        if atendimento.status == 'CANCELADO':
            return JsonResponse({'erro': 'Este agendamento já foi cancelado'}, status=400)

        # Cancelar
        atendimento.status = 'CANCELADO'
        atendimento.save()

        return JsonResponse({
            'sucesso': True,
            'mensagem': f'Agendamento de {atendimento.procedimento.nome} cancelado com sucesso.',
        })

    except json.JSONDecodeError:
        return JsonResponse({'erro': 'Dados inválidos'}, status=400)
    except Exception as e:
        logger.error(f'Erro ao cancelar agendamento: {e}', exc_info=True)
        return JsonResponse({'erro': 'Ocorreu um erro interno. Tente novamente.'}, status=500)
