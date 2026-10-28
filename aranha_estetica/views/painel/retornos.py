"""Retornos obrigatorios devidos e a tela de marcar o retorno (services/retorno).

O sistema nao reserva retorno sozinho: depois do Realizado o retorno fica devido na janela do
procedimento e a recepcao marca com a cliente, escolhendo um horario livre dentro da janela.
"""
from datetime import datetime

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from ...decorators import staff_required
from ...excecoes import RegraDeNegocio
from ...models import Atendimento
from ...services import retorno as retornos
from ...services.disponibilidade import profissionais_para
from ...utils.auditoria import registrar_log
from ...utils.datas import hoje, ler_data, ler_hora
from ...utils.parse import id_int


@staff_required
def painel_retornos(request):
    """Retornos sem custo que ainda precisam ser marcados (o que vence primeiro no topo)."""
    return render(request, 'painel/retornos.html', {'devidos': retornos.devidos()})


@staff_required
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_agendar_retorno(request, pk):
    """GET ?data=&profissional_id= mostra os horarios livres do dia (so dentro da janela);
    POST data + hora marca o retorno (AGENDADO, sem custo)."""
    origem = get_object_or_404(
        Atendimento.objects.select_related('cliente', 'procedimento', 'profissional'), pk=pk,
    )
    periodo = retornos.janela(origem)
    if periodo is None or origem.status != Atendimento.STATUS_REALIZADO:
        messages.error(request, 'Este atendimento não tem retorno a marcar.')
        return redirect('aranha:painel_retornos')
    de, ate = periodo
    dados = request.POST if request.method == 'POST' else request.GET
    profissionais = profissionais_para(origem.procedimento)
    profissional = profissionais.filter(pk=id_int(dados.get('profissional_id'))).first() or origem.profissional
    try:
        dia = ler_data(dados.get('data') or '') or max(de, hoje())
    except ValueError:
        dia = max(de, hoje())

    if request.method == 'POST':
        try:
            hora = ler_hora((dados.get('hora') or '').strip())
            inicio = timezone.make_aware(datetime.combine(dia, hora))
            retorno = retornos.agendar(origem, inicio, profissional=profissional)
        except ValueError:
            messages.error(request, 'Escolha um dos horários livres.')
        except RegraDeNegocio as recusa:
            messages.error(request, recusa.mensagem)
        else:
            registrar_log(request.user, 'Agendou retorno', 'atendimento', retorno.pk,
                          {'origem': origem.pk}, request=request)
            messages.success(
                request, f'Retorno de {origem.cliente.nome} marcado para '
                         f'{timezone.localtime(inicio):%d/%m/%Y às %H:%M} (sem custo).')
            return redirect(f"{reverse('aranha:painel_agendamentos')}?data={dia.isoformat()}")

    return render(request, 'painel/retorno_novo.html', {
        'origem': origem,
        'de': de,
        'ate': ate,
        'dia': dia,
        'profissionais': profissionais,
        'profissional': profissional,
        'slots': retornos.horarios(origem, dia, profissional),
    })
