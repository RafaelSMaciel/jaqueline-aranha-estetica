"""Dashboard de compliance de termos — visao de quem assinou e pendencias."""
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef
from django.shortcuts import render

from ..decorators import staff_required
from ..models import (
    AceitePrivacidade,
    AssinaturaTermoProcedimento,
    Atendimento,
    Cliente,
    VersaoTermo,
)


@staff_required
def admin_termos_compliance(request):
    """Lista versoes de termos ativas + contagem de assinaturas + pendentes."""
    tipo_filter = request.GET.get('tipo', '')
    versao_filter = request.GET.get('versao', '')

    versoes = VersaoTermo.objects.filter(ativa=True).select_related('procedimento').order_by('-vigente_desde')
    if tipo_filter:
        versoes = versoes.filter(tipo=tipo_filter)

    resumo_versoes = []
    for v in versoes:
        if v.tipo == 'LGPD':
            assinaturas_count = AceitePrivacidade.objects.filter(versao_termo=v).count()
            clientes_relevantes = Cliente.objects.filter(ativo=True).count()
            pendentes = clientes_relevantes - assinaturas_count
        else:
            assinaturas_count = AssinaturaTermoProcedimento.objects.filter(versao_termo=v).count()
            if v.procedimento_id:
                clientes_relevantes = (
                    Atendimento.objects.filter(procedimento_id=v.procedimento_id)
                    .values('cliente_id').distinct().count()
                )
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
            assinatura_sub = AceitePrivacidade.objects.filter(
                cliente=OuterRef('pk'), versao_termo=versao_obj
            )
            pendentes_qs = Cliente.objects.filter(ativo=True).annotate(
                assinou=Exists(assinatura_sub)
            ).filter(assinou=False).order_by('nome_completo')
        else:
            if versao_obj.procedimento_id:
                atend_cli_ids = (
                    Atendimento.objects.filter(procedimento_id=versao_obj.procedimento_id)
                    .values_list('cliente_id', flat=True).distinct()
                )
                assinatura_sub = AssinaturaTermoProcedimento.objects.filter(
                    cliente=OuterRef('pk'), versao_termo=versao_obj
                )
                pendentes_qs = Cliente.objects.filter(
                    pk__in=atend_cli_ids, ativo=True
                ).annotate(
                    assinou=Exists(assinatura_sub)
                ).filter(assinou=False).order_by('nome_completo')
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
