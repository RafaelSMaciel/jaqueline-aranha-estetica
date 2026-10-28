"""Lista de espera no painel — lista e aviso manual de vaga (services/lista_espera.avisar_manual)."""
import logging

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from ...decorators import escrita_painel, staff_required
from ...models import ListaEspera
from ...services import lista_espera
from ...utils.auditoria import registrar_log

logger = logging.getLogger(__name__)


@staff_required
def admin_lista_espera(request):
    """Inscricoes na fila (com cadastro verificado ou so com nome/celular de contato)."""
    espera = ListaEspera.objects.select_related(
        'cliente', 'procedimento', 'profissional_desejado'
    ).order_by('data_desejada', 'criado_em')

    paginator = Paginator(espera, 30)
    espera_page = paginator.get_page(request.GET.get('page', 1))
    return render(request, 'painel/lista_espera.html', {'lista_espera': espera_page})


@escrita_painel
def admin_notificar_espera(request, pk):
    """Botao "Avisar": e-mail quando ha destino (mesma regra de privacidade do aviso automatico) e o
    aviso fica registrado; sem e-mail a recepcao completa pelo WhatsApp ou telefone."""
    item = get_object_or_404(
        ListaEspera.objects.select_related('cliente', 'procedimento'), pk=pk,
    )
    enviado = lista_espera.avisar_manual(item)
    registrar_log(
        request.user,
        f'Avisou lista de espera ({"e-mail enviado" if enviado else "contato manual"})',
        'lista_espera', item.pk, request=request,
    )
    if enviado:
        messages.success(request, f'Aviso de vaga enviado por e-mail para {item.nome}.')
    else:
        messages.info(
            request,
            f'{item.nome}: aviso registrado. Nenhum e-mail foi enviado — '
            'faça o contato pelo WhatsApp ou telefone.',
        )
    return redirect('aranha:admin_lista_espera')
