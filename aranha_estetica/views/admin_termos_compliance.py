"""Dashboard de compliance de termos — visao de quem assinou e pendencias."""
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef
from django.shortcuts import render

from ..decorators import staff_required
from ..models import (
    AceiteTermo,
    Atendimento,
    Cliente,
    VersaoTermo,
)


@staff_required
def admin_termos_compliance(request):
    """Lista versoes de termos ativas + contagem de assinaturas + pendentes."""
    tipo_filter = request.GET.get('tipo', '')
    versao_filter = request.GET.get('versao', '')

    versoes = list(
        VersaoTermo.objects.filter(ativa=True).select_related('procedimento').order_by('-vigente_desde')
    )
    if tipo_filter:
        versoes = [v for v in versoes if v.tipo == tipo_filter]

    # Pre-agrega contagens fora do loop para evitar N+1.
    versao_ids = [v.pk for v in versoes]
    assinaturas_por_versao = dict(
        AceiteTermo.objects.filter(versao_termo_id__in=versao_ids)
        .values_list('versao_termo_id')
        .annotate(c=Count('id'))
        .values_list('versao_termo_id', 'c')
    )
    # Clientes ativos: usado por todas as versoes LGPD — uma unica query.
    clientes_ativos_count = Cliente.objects.filter(ativo=True).count()
    # Clientes distintos por procedimento (versoes nao-LGPD com procedimento).
    proc_ids = [v.procedimento_id for v in versoes if v.tipo != 'LGPD' and v.procedimento_id]
    clientes_por_proc = dict(
        Atendimento.objects.filter(procedimento_id__in=proc_ids)
        .values_list('procedimento_id')
        .annotate(c=Count('cliente_id', distinct=True))
        .values_list('procedimento_id', 'c')
    ) if proc_ids else {}

    resumo_versoes = []
    for v in versoes:
        assinaturas_count = assinaturas_por_versao.get(v.pk, 0)
        if v.tipo == 'LGPD':
            clientes_relevantes = clientes_ativos_count
            pendentes = clientes_relevantes - assinaturas_count
        else:
            if v.procedimento_id:
                clientes_relevantes = clientes_por_proc.get(v.procedimento_id, 0)
            else:
                clientes_relevantes = 0
            pendentes = max(0, clientes_relevantes - assinaturas_count)

        resumo_versoes.append({
            'versao': v,
            'assinados': assinaturas_count,
            'relevantes': clientes_relevantes,
            'pendentes': pendentes,
            'pct': round((assinaturas_count / clientes_relevantes * 100), 1) if clientes_relevantes else 0,
        })

    pendentes_lista = []
    versao_obj = None
    if versao_filter:
        try:
            versao_obj = VersaoTermo.objects.select_related('procedimento').get(pk=versao_filter, ativa=True)
        except VersaoTermo.DoesNotExist:
            versao_obj = None

    if versao_obj:
        if versao_obj.tipo == 'LGPD':
            assinatura_sub = AceiteTermo.objects.filter(
                cliente=OuterRef('pk'), versao_termo=versao_obj
            )
            pendentes_qs = Cliente.objects.filter(ativo=True).annotate(
                assinou=Exists(assinatura_sub)
            ).filter(assinou=False).order_by('nome')
        else:
            if versao_obj.procedimento_id:
                atend_cli_ids = (
                    Atendimento.objects.filter(procedimento_id=versao_obj.procedimento_id)
                    .values_list('cliente_id', flat=True).distinct()
                )
                assinatura_sub = AceiteTermo.objects.filter(
                    cliente=OuterRef('pk'), versao_termo=versao_obj
                )
                pendentes_qs = Cliente.objects.filter(
                    pk__in=atend_cli_ids, ativo=True
                ).annotate(
                    assinou=Exists(assinatura_sub)
                ).filter(assinou=False).order_by('nome')
            else:
                pendentes_qs = Cliente.objects.none()

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
