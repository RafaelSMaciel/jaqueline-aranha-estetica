"""Marcadores das listas de atendimentos do painel (alerta de saude, termo, pacote).

Calculados em lote p/ a pagina inteira: a view (dashboard.painel_agendamentos)
continua devolvendo so o queryset paginado.
"""
from django import template

register = template.Library()


@register.simple_tag
def marcadores_atendimentos(atendimentos):
    """{'alertas': {cliente_id: [...]}, 'termo_pendente': {ids}, 'termo_link': {ids},
    'ficha_pendente': {ids}, 'pacote': {ids}}.

    termo_pendente: termo de procedimento ainda nao aceito (atendimento ativo);
    termo_link: dos pendentes, os que ainda aceitam assinatura (link valido);
    ficha_pendente: ficha de anamnese obrigatoria nunca respondida (ativo);
    pacote: sessao debitada de pacote (nao cobrar de novo).
    """
    from ..models import ConsumoSessao
    from ..services.alertas import alertas_por_cliente, ids_com_ficha_pendente
    from ..services.termos import aceita_assinatura, ids_com_termo_procedimento_pendente

    lista = list(atendimentos or [])
    if not lista:
        return {'alertas': {}, 'termo_pendente': set(), 'termo_link': set(),
                'ficha_pendente': set(), 'pacote': set()}
    termo_pendente = ids_com_termo_procedimento_pendente(lista)
    return {
        'alertas': alertas_por_cliente(at.cliente for at in lista),
        'termo_pendente': termo_pendente,
        'termo_link': {at.pk for at in lista if at.pk in termo_pendente and aceita_assinatura(at)},
        'ficha_pendente': ids_com_ficha_pendente(lista),
        'pacote': set(
            ConsumoSessao.objects.filter(atendimento_id__in=[at.pk for at in lista])
            .values_list('atendimento_id', flat=True)
        ),
    }
