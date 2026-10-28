"""Lista de espera publica (decisao 17): inscricao sem criar Cliente, com captcha, honeypot e limite.

A regra (quem e a pessoa, e-mail so com celular verificado, idempotencia) mora em
services/lista_espera.inscrever; aqui ficam o formulario, o anti-abuso e a resposta.
"""
import logging

from django.contrib import messages
from django.shortcuts import redirect, render
from django_ratelimit.decorators import ratelimit

from ...forms.publico import ListaEsperaForm, primeiro_erro
from ...models import ListaEspera, Procedimento, Profissional
from ...services import lista_espera
from ...services import otp as otp_service
from ...utils import datas
from ...utils.captcha import captcha_invalido, turnstile_enabled, turnstile_site_key

logger = logging.getLogger(__name__)

TEMPLATE = 'publico/lista_espera.html'
# 20 inscricoes por IP por dia (decisao 17): uma pessoa nao precisa de mais que isso
LIMITE_DIARIO_POR_IP = '20/d'


def _contexto(form_data=None):
    return {
        'procedimentos': list(Procedimento.objects.filter(ativo=True).order_by('nome')),
        'profissionais': list(Profissional.objects.filter(ativo=True).order_by('nome')),
        'turnos': ListaEspera.TURNO_CHOICES,
        'data_minima': datas.hoje().isoformat(),
        'form_data': form_data or {},
        'turnstile_enabled': turnstile_enabled(),
        'turnstile_site_key': turnstile_site_key(),
    }


@ratelimit(key='ip', rate=LIMITE_DIARIO_POR_IP, method='POST', block=True)
def lista_espera_publica(request):
    """Formulario publico da lista de espera. Nunca cria nem altera Cliente.

    Celular confirmado por SMS nesta sessao (OTP do wizard) liga a inscricao ao cadastro e aceita o
    e-mail de contato; sem isso a inscricao guarda so nome e celular (a recepcao faz o contato).
    A pagina de sucesso e a mesma para inscricao nova, repetida ou descartada (nao revela cadastro).
    """
    if request.method != 'POST':
        return render(request, TEMPLATE, _contexto())

    form = ListaEsperaForm(request.POST)
    form_data = {campo: str(request.POST.get(campo, ''))[:254] for campo in (
        'nome', 'telefone', 'email', 'procedimento', 'profissional', 'data_desejada', 'turno',
    )}
    if form.eh_robo():
        logger.info('lista_espera_honeypot')
        return redirect('aranha:lista_espera_sucesso')
    if captcha_invalido(request):
        messages.error(request, 'Confirme que você não é um robô e envie de novo.')
        return render(request, TEMPLATE, _contexto(form_data), status=400)
    if not form.is_valid():
        messages.error(request, primeiro_erro(form))
        return render(request, TEMPLATE, _contexto(form_data))

    dados = form.cleaned_data
    lista_espera.inscrever(
        nome=dados['nome'], telefone=dados['telefone'], email=dados['email'] or None,
        procedimento=dados['procedimento'], profissional=dados['profissional'],
        data_desejada=dados['data_desejada'], turno=dados['turno'],
        telefone_verificado=otp_service.telefone_verificado_agendamento(request) == dados['telefone'],
    )
    return redirect('aranha:lista_espera_sucesso')


def lista_espera_sucesso(request):
    """Confirmacao de inscricao na lista de espera."""
    return render(request, 'publico/lista_espera_sucesso.html')
