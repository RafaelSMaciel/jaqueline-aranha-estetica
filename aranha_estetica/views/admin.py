import json
import logging
from datetime import datetime

from django.core.paginator import Paginator
from django.db import DatabaseError, transaction
from django.db.models import Count, Exists, OuterRef
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django_ratelimit.decorators import ratelimit

from ..decorators import staff_required
from ..models import (
    Atendimento,
    Cliente,
    LogAuditoria,
    Prontuario,
    RespostaAnamnese,
)
from ..services.termos import termos_pendentes
from ..utils.audit import registrar_log
from ..utils.busca import q_busca_cliente
from ..utils.datas import fmt_local
from ..utils.parse import id_int
from ..utils.saude import alertas_saude

logger = logging.getLogger(__name__)


@never_cache  # lista mostra alertas de saude
@staff_required
def prontuario_consentimento(request):
    """Prontuario e consentimento — lista clientes com status do prontuario."""
    search = request.GET.get('search', '').strip()

    clientes = Cliente.objects.all().order_by('nome')
    if search:
        clientes = clientes.filter(q_busca_cliente(search, incluir_email=False))

    clientes = clientes.annotate(
        tem_prontuario=Exists(
            # registro vazio (GET legado) nao conta como prontuario
            Prontuario.objects.filter(Prontuario.Q_COM_CONTEUDO, cliente=OuterRef('pk'))
        ),
        # ficha de anamnese respondida pela cliente (booking/link)
        tem_ficha=Exists(
            RespostaAnamnese.objects.filter(cliente=OuterRef('pk'), formulario__tipo='ANAMNESE')
            .exclude(respostas_json={})
        ),
        total_termos=Count('aceites', distinct=True),
    )

    paginator = Paginator(clientes, 50)
    page = request.GET.get('page', 1)
    clientes_page = paginator.get_page(page)

    clientes_list = [
        {
            'cliente': c,
            'tem_prontuario': c.tem_prontuario,
            'tem_ficha': c.tem_ficha,
            'total_termos': c.total_termos,
            # Alerta visivel na lista; so consulta quem tem algum dado de saude
            'alertas': alertas_saude(c) if (c.tem_prontuario or c.tem_ficha) else [],
        }
        for c in clientes_page
    ]

    from .prontuario import _perguntas_configuradas
    perguntas = _perguntas_configuradas()

    context = {
        'clientes_list': clientes_list,
        'clientes_page': clientes_page,
        'perguntas': perguntas,
        'total_perguntas': len(perguntas),
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
        logs = logs.filter(tabela=tabela)
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

def _avisar_cancelamento(atendimento):
    """E-mail ao cliente quando a equipe cancela pelo painel (best-effort).

    Enfileirado apos o commit; falha de envio nunca desfaz o cancelamento.
    """
    email = atendimento.cliente.email
    if not email:
        return
    dados = {
        'nome': atendimento.cliente.nome,
        'procedimento': atendimento.procedimento.nome,
        'profissional': atendimento.profissional.nome,
        'data_hora': fmt_local(atendimento.data_hora_inicio, '%d/%m/%Y às %H:%M'),
    }

    def _enfileirar():
        try:
            from ..tasks import send_email_async
            send_email_async.delay('enviar_cancelamento_email', email, dados)
        except Exception:  # noqa: BLE001 — aviso e best-effort
            logger.exception('aviso_cancelamento_falhou', extra={'atendimento_id': atendimento.pk})

    transaction.on_commit(_enfileirar)


@staff_required
@ratelimit(key='user', rate='60/m', method='POST', block=True)
def admin_atualizar_status(request):
    """Atualiza status de um agendamento via AJAX.

    Entrada invalida -> 400/404 (antes virava 500 + traceback no Sentry).
    REALIZADO com termo de PROCEDIMENTO nao aceito: nao bloqueia duro (a
    cliente pode ter assinado em papel), mas exige o override explicito
    'sem_termo': true e deixa 'Realizado sem termo aceito' na trilha — mesma
    regra do portal do profissional.
    """
    if request.method != 'POST':
        return JsonResponse({'erro': 'Método não permitido'}, status=405)

    try:
        data = json.loads(request.body)
    except ValueError:  # JSONDecodeError e corpo que nao e UTF-8
        return JsonResponse({'erro': 'Dados inválidos'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'erro': 'Dados inválidos'}, status=400)

    # id_int: isdigit() aceitava '²' e o int() dava 500; bool/float/lista -> 400
    bruto_id = data.get('atendimento_id')
    atendimento_id = (
        id_int(bruto_id) if isinstance(bruto_id, (int, str)) and not isinstance(bruto_id, bool) else None
    )
    if atendimento_id is None:
        return JsonResponse({'erro': 'Agendamento inválido'}, status=400)
    novo_status = data.get('status')
    if not isinstance(novo_status, str) or not novo_status.strip():
        return JsonResponse({'erro': 'Status inválido'}, status=400)
    novo_status = novo_status.strip().upper()

    atendimento = (
        Atendimento.objects.select_related('cliente', 'procedimento')
        .filter(pk=atendimento_id).first()
    )
    if atendimento is None:
        return JsonResponse({'erro': 'Agendamento não encontrado'}, status=404)
    status_anterior = atendimento.status

    # Usa a FSM do model: valida a transicao e publica os eventos colaterais
    # (no-show, etc.). Evita transicoes invalidas como REALIZADO -> PENDENTE.
    metodos = {
        'CONFIRMADO': atendimento.confirmar,
        'REALIZADO': atendimento.marcar_realizado,
        'CANCELADO': atendimento.cancelar,
        'FALTOU': atendimento.marcar_falta,
        'AGENDADO': atendimento.aprovar,
    }
    acao = metodos.get(novo_status)
    if acao is None:
        return JsonResponse(
            {'erro': f'Transição para "{novo_status}" não suportada.'}, status=400
        )

    sem_termo = []
    if novo_status == 'REALIZADO' and status_anterior in ('AGENDADO', 'CONFIRMADO'):
        sem_termo = termos_pendentes(
            atendimento.cliente, atendimento.procedimento, so_procedimento=True,
        )
    if sem_termo and data.get('sem_termo') is not True:
        return JsonResponse({
            'erro': 'A cliente ainda não aceitou o termo do procedimento.',
            'termo_pendente': True,
            'codigo': 'termo_pendente',
        }, status=409)

    try:
        acao(by_user=request.user)
    except Atendimento.TransicaoInvalida as exc:
        return JsonResponse({'erro': str(exc)}, status=400)
    except DatabaseError:
        logger.exception('atualizar_status_falhou', extra={'atendimento_id': atendimento.pk})
        return JsonResponse({'erro': 'Ocorreu um erro interno. Tente novamente.'}, status=500)

    registrar_log(
        request.user,
        f'Status alterado: {status_anterior} → {novo_status}',
        'atendimento',
        atendimento.pk,
        {'status_anterior': status_anterior, 'status_novo': novo_status,
         'cliente': atendimento.cliente_id},
        request=request,
    )
    if sem_termo:
        registrar_log(
            request.user, 'Realizado sem termo aceito', 'atendimento', atendimento.pk,
            {'termos': [t.pk for t in sem_termo]}, request=request,
        )

    if novo_status == 'CANCELADO':
        _avisar_cancelamento(atendimento)

    return JsonResponse({
        'sucesso': True,
        'status_anterior': status_anterior,
        'status_novo': novo_status,
    })


# Nota: a antiga view setup_seed(request) foi substituida pelo management
# command `python manage.py seed` (aranha_estetica/management/commands/seed.py),
# eliminando token em query param e execucao remota via URL.
