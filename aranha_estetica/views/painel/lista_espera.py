"""Lista de espera no painel — lista e aviso manual de vaga."""
import logging

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from ...decorators import staff_required
from ...models import ListaEspera
from ...utils.auditoria import registrar_log

logger = logging.getLogger(__name__)


@staff_required
def admin_lista_espera(request):
    """Gerencia lista de espera."""
    espera = ListaEspera.objects.select_related(
        'cliente', 'procedimento', 'profissional_desejado'
    ).order_by('-criado_em')

    paginator = Paginator(espera, 30)
    page = request.GET.get('page', 1)
    espera_page = paginator.get_page(page)

    context = {'lista_espera': espera_page}
    return render(request, 'painel/lista_espera.html', context)


@staff_required
def admin_notificar_espera(request, pk):
    """Avisa o cliente da lista de espera e marca o item como avisado.

    Se o cliente tem e-mail, envia o aviso de vaga (utils.email); o canal
    pode estar sem provedor em prod (retorna False) — a mensagem diz o que
    de fato aconteceu para a recepcao completar o contato pelo WhatsApp.
    """
    if request.method != 'POST':
        return redirect('aranha:admin_lista_espera')

    item = get_object_or_404(
        ListaEspera.objects.select_related('cliente', 'procedimento'), pk=pk,
    )
    enviado = False
    # e-mail do pedido da lista (quando o form publico coleta) > e-mail do cadastro
    destino = getattr(item, 'email_contato', None) or item.cliente.email
    if destino:
        from ...utils.email import enviar_fila_espera_email
        # O form publico da lista e anonimo: o e-mail digitado pode nao ser da
        # dona do telefone. Nome do cadastro so vai p/ o e-mail do proprio cadastro.
        cadastro = (item.cliente.email or '').strip().lower()
        nome = item.cliente.nome if cadastro and destino.strip().lower() == cadastro else ''
        try:
            enviado = bool(enviar_fila_espera_email(destino, {
                'nome': nome,
                'procedimento': item.procedimento.nome,
                'data': item.data_desejada.strftime('%d/%m/%Y'),
            }))
        except Exception:  # pylint: disable=broad-except
            logger.warning('lista_espera_email_falhou', exc_info=True, extra={'espera_id': item.pk})

    item.notificado = True
    item.save(update_fields=['notificado'])
    registrar_log(
        request.user,
        f'Avisou lista de espera ({"e-mail enviado" if enviado else "contato manual"})',
        'lista_espera', item.pk, request=request,
    )
    if enviado:
        messages.success(request, f'Aviso de vaga enviado por e-mail para {item.cliente.nome}.')
    else:
        messages.info(
            request,
            f'{item.cliente.nome} marcado como avisado. Nenhum e-mail foi enviado — '
            'faça o contato pelo WhatsApp ou telefone.',
        )
    return redirect('aranha:admin_lista_espera')
