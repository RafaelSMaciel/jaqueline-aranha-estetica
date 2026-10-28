import logging

from django.core.paginator import Paginator
from django.shortcuts import render

from ...decorators import staff_required
from ...models import (
    LogAuditoria,
)
from ...utils.datas import dias_locais, ler_data

logger = logging.getLogger(__name__)


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
        logs = logs.filter(tabela=tabela)
    if acao_filter:
        logs = logs.filter(acao__icontains=acao_filter)
    if data_filter:
        try:
            data = ler_data(data_filter)
            logs = logs.filter(**dias_locais('criado_em', data))
        except ValueError:
            pass

    # Paginação
    paginator = Paginator(logs, 30)
    page = request.GET.get('page', 1)
    logs_page = paginator.get_page(page)

    # Tabelas únicas para filtro
    tabelas = LogAuditoria.objects.values_list('tabela', flat=True).distinct().order_by('tabela')

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


# Nota: a antiga view setup_seed(request) foi substituida pelo management
# command `python manage.py seed` (aranha_estetica/management/commands/seed.py),
# eliminando token em query param e execucao remota via URL.
