import json
import logging
from datetime import datetime

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from ..decorators import staff_required
from ..models import (
    AceitePrivacidade,
    Atendimento,
    Cliente,
    LogAuditoria,
    Prontuario,
    ProntuarioPergunta,
    ProntuarioResposta,
)
from ..utils.audit import registrar_log

logger = logging.getLogger(__name__)


@staff_required
def prontuario_consentimento(request):
    """Prontuario e consentimento — lista clientes com status do prontuario."""
    search = request.GET.get('search', '')

    clientes = Cliente.objects.all().order_by('nome_completo')
    if search:
        clientes = clientes.filter(
            Q(nome_completo__icontains=search) |
            Q(cpf__icontains=search) |
            Q(telefone__icontains=search)
        )

    clientes = clientes.annotate(
        tem_prontuario=Exists(Prontuario.objects.filter(cliente=OuterRef('pk'))),
        total_respostas=Count('prontuario__prontuarioresposta', distinct=True),
        total_termos=Count('aceiteprivacidade', distinct=True),
    )

    paginator = Paginator(clientes, 50)
    page = request.GET.get('page', 1)
    clientes_page = paginator.get_page(page)

    clientes_list = [
        {
            'cliente': c,
            'tem_prontuario': c.tem_prontuario,
            'total_respostas': c.total_respostas,
            'total_termos': c.total_termos,
        }
        for c in clientes_page
    ]

    perguntas = ProntuarioPergunta.objects.filter(ativa=True)

    context = {
        'clientes_list': clientes_list,
        'clientes_page': clientes_page,
        'perguntas': perguntas,
        'total_perguntas': perguntas.count(),
        'search': search,
    }
    return render(request, 'painel/prontuario.html', context)


# ═══════════════════════════════════════
#   AUDITORIA
# ═══════════════════════════════════════

@staff_required
def admin_auditoria(request):
    """Timeline de auditoria com filtros"""
    logs = LogAuditoria.objects.select_related('usuario').order_by('-criado_em')

    # Filtros
    tabela = request.GET.get('tabela', '')
    acao_filter = request.GET.get('acao', '')
    data_filter = request.GET.get('data', '')

    if tabela:
        logs = logs.filter(tabela_afetada=tabela)
    if acao_filter:
        logs = logs.filter(acao__icontains=acao_filter)
    if data_filter:
        try:
            data = datetime.strptime(data_filter, '%Y-%m-%d').date()
            logs = logs.filter(criado_em__date=data)
        except ValueError:
            pass

    # Paginação
    paginator = Paginator(logs, 30)
    page = request.GET.get('page', 1)
    logs_page = paginator.get_page(page)

    # Tabelas únicas para filtro
    tabelas = LogAuditoria.objects.values_list('tabela_afetada', flat=True).distinct().order_by('tabela_afetada')

    context = {
        'logs': logs_page,
        'tabelas': [t for t in tabelas if t],
        'tabela_filter': tabela,
        'acao_filter': acao_filter,
        'data_filter': data_filter,
    }
    return render(request, 'painel/auditoria.html', context)


# ═══════════════════════════════════════
#   STATUS DE AGENDAMENTO
# ═══════════════════════════════════════

@staff_required
@ratelimit(key='user', rate='60/m', method='POST', block=True)
def admin_atualizar_status(request):
    """Atualiza status de um agendamento via AJAX"""
    if request.method != 'POST':
        return JsonResponse({'erro': 'Método não permitido'}, status=405)

    try:
        data = json.loads(request.body)
        atendimento_id = data.get('atendimento_id')
        novo_status = data.get('status', '').upper()

        status_validos = ['PENDENTE', 'AGENDADO', 'CONFIRMADO', 'REALIZADO', 'CANCELADO', 'FALTOU']
        if novo_status not in status_validos:
            return JsonResponse({'erro': f'Status inválido. Use: {", ".join(status_validos)}'}, status=400)

        atendimento = get_object_or_404(Atendimento, pk=atendimento_id)
        status_anterior = atendimento.status
        atendimento.status = novo_status
        atendimento.save()

        registrar_log(
            request.user,
            f'Status alterado: {status_anterior} → {novo_status}',
            'atendimento',
            atendimento_id,
            {'status_anterior': status_anterior, 'status_novo': novo_status,
             'cliente': atendimento.cliente.nome_completo}
        )

        return JsonResponse({
            'sucesso': True,
            'status_anterior': status_anterior,
            'status_novo': novo_status,
        })
    except json.JSONDecodeError:
        return JsonResponse({'erro': 'Dados inválidos'}, status=400)
    except Exception as e:
        logger.error(f'Erro ao atualizar status: {e}', exc_info=True)
        return JsonResponse({'erro': 'Ocorreu um erro interno. Tente novamente.'}, status=500)


# Nota: a antiga view setup_seed(request) foi substituida pelo management
# command `python manage.py seed` (app_shivazen/management/commands/seed.py),
# eliminando token em query param e execucao remota via URL.
