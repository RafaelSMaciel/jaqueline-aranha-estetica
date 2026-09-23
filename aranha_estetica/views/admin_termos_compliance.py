"""Dashboard de compliance de termos — visao de quem assinou e pendencias."""
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Subquery
from django.shortcuts import render
from django.utils import timezone

from ..decorators import staff_required
from ..models import (
    AceiteTermo,
    Atendimento,
    Cliente,
    VersaoTermo,
)
from ..services.termos import STATUS_ACEITA_TERMO

# Atendimento que torna o termo de procedimento exigivel da cliente
# (cancelado/faltou/reagendado nao conta).
STATUS_ALVO_PROCEDIMENTO = ('PENDENTE', 'AGENDADO', 'CONFIRMADO', 'REALIZADO')


def _clientes_alvo(versao):
    """Clientes ATIVOS de quem a versao e exigida.

    LGPD: todos os ativos. PROCEDIMENTO: ativos com atendimento (nao
    cancelado) do procedimento — ou de qualquer procedimento, no termo geral.
    Assinados e pendentes saem do MESMO conjunto: aceite de cliente inativo
    nao 'compensa' pendencia de cliente ativo (nada de -3 ou 160%).
    """
    ativos = Cliente.objects.filter(ativo=True)
    if versao.tipo == 'LGPD':
        return ativos
    atendimentos = Atendimento.objects.filter(status__in=STATUS_ALVO_PROCEDIMENTO)
    if versao.procedimento_id:
        atendimentos = atendimentos.filter(procedimento_id=versao.procedimento_id)
    return ativos.filter(pk__in=atendimentos.values('cliente_id'))


def _assinou(versao):
    return Exists(AceiteTermo.objects.filter(cliente=OuterRef('pk'), versao_termo=versao))


@staff_required
def admin_termos_compliance(request):
    """Lista versoes de termos ativas + contagem de assinaturas + pendentes."""
    tipo_filter = request.GET.get('tipo', '')
    if tipo_filter not in dict(VersaoTermo.TIPO_CHOICES):
        tipo_filter = ''
    # ?versao= nao numerico (URL manipulada) e ignorado em vez de 500
    versao_filter = request.GET.get('versao', '')
    if not versao_filter.isdigit():
        versao_filter = ''

    versoes = list(
        VersaoTermo.objects.filter(ativa=True).select_related('procedimento').order_by('-vigente_desde')
    )
    if tipo_filter:
        versoes = [v for v in versoes if v.tipo == tipo_filter]

    # 2 queries por versao ativa (poucas: 1 LGPD + termos de procedimento)
    resumo_versoes = []
    for v in versoes:
        alvo = _clientes_alvo(v)
        relevantes = alvo.count()
        assinados = alvo.filter(_assinou(v)).count() if relevantes else 0
        resumo_versoes.append({
            'versao': v,
            'assinados': assinados,
            'relevantes': relevantes,
            'pendentes': relevantes - assinados,
            'pct': round(assinados / relevantes * 100, 1) if relevantes else 0,
        })

    pendentes_lista = []
    versao_obj = None
    if versao_filter:
        try:
            versao_obj = VersaoTermo.objects.select_related('procedimento').get(pk=int(versao_filter), ativa=True)
        except VersaoTermo.DoesNotExist:
            versao_obj = None

    if versao_obj:
        # Proximo atendimento ainda ativo da cliente: e nele que o link do termo vale
        proximos = Atendimento.objects.filter(
            cliente=OuterRef('pk'),
            status__in=STATUS_ACEITA_TERMO,
            data_hora_fim__gt=timezone.now(),
        )
        if versao_obj.tipo == 'PROCEDIMENTO' and versao_obj.procedimento_id:
            proximos = proximos.filter(procedimento_id=versao_obj.procedimento_id)
        pendentes_qs = (
            _clientes_alvo(versao_obj)
            .annotate(assinou=_assinou(versao_obj))
            .filter(assinou=False)
            .annotate(proximo_atendimento_id=Subquery(
                proximos.order_by('data_hora_inicio').values('pk')[:1]
            ))
            .order_by('nome')
        )
        paginator = Paginator(pendentes_qs, 30)
        page = request.GET.get('page', 1)
        pendentes_lista = paginator.get_page(page)

    context = {
        'resumo_versoes': resumo_versoes,
        'tipo_filter': tipo_filter,
        'versao_filter': versao_filter,
        'versao_obj': versao_obj,
        'pendentes_lista': pendentes_lista,
        'tipos': VersaoTermo.TIPO_CHOICES,
    }
    return render(request, 'painel/termos_compliance.html', context)
