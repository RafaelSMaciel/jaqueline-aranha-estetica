"""Fluxo publico de agendamento — 3 passos (procedimento, data/horario, confirmar).

Porta HTTP do caso de uso ``services/agendamento_online.solicitar``: o GET monta o wizard; o POST
passa pelo ``AgendamentoPublicoForm`` (campos + celular verificado por SMS NESTA sessao, exigido de
todo agendamento) e chama o service, que tem a regra inteira (termo LGPD, horario, fichas com
consentimento de saude, termos de procedimento, cadastro, reserva com o preco da data, e-mails e
provas). Aqui so honeypot, form, service e a pagina de sucesso.
"""
import logging

from django.contrib import messages
from django.db import DatabaseError
from django.shortcuts import redirect, render
from django_ratelimit.decorators import ratelimit

from ...excecoes import RegraDeNegocio
from ...forms.agendamento import AgendamentoPublicoForm
from ...models import (
    Cliente,
    FormularioAnamnese,
    Profissional,
    VersaoTermo,
)
from ...services import agendamento_online, consentimentos, portal_cliente, termos
from ...services import otp as otp_service
from ...services.disponibilidade import procedimentos_agendaveis
from ...services.precos import precos_de_vitrine
from ...utils.captcha import turnstile_enabled, turnstile_site_key
from ...utils.parse import id_int
from ...utils.pii import mask_email, mask_telefone
from ...utils.seguranca import client_ip as _client_ip
from ...utils.sms import sms_disponivel

logger = logging.getLogger(__name__)

SESSAO_REHIDRATAR = 'booking_rehidratar'
SESSAO_SUCESSO = 'agendamento_sucesso'
MSG_FALHA = 'Ocorreu um erro ao confirmar o agendamento. Tente novamente.'


def _voltar_com_erro(request, mensagem):
    """Erro no confirmar: mensagem + wizard reidrata o estado salvo no navegador."""
    messages.error(request, mensagem)
    request.session[SESSAO_REHIDRATAR] = True
    return redirect('aranha:agendamento_publico')


def _cards(procedimentos):
    """Cards do passo 1: o "A partir de" e a vitrine do dia (services/precos — mesma conta do
    agendamento); o valor gravado usa a data do atendimento e a profissional (API de horarios)."""
    vitrine = precos_de_vitrine(procedimentos)
    cards = []
    for proc in procedimentos:
        preco = vitrine.get(proc.pk)
        promocao = preco.promocao if preco else None
        cards.append({
            'id': proc.pk,
            'nome': proc.nome,
            'descricao': proc.descricao or '',
            'duracao_minutos': proc.duracao_minutos,
            'preco': float(preco.final) if preco else 0,
            # cheio sem promo: fallback do resumo (o valor real vem por data/profissional)
            'preco_base': float(preco.cheio) if preco else 0,
            'preco_cheio': float(preco.cheio) if promocao else None,
            'promocao': promocao.nome if promocao else '',
            'promocao_ate': promocao.data_fim if promocao else None,
            'categoria': proc.categoria,
            'categoria_label': proc.get_categoria_display(),
        })
    return cards


def _fichas(procedimentos):
    """Fichas de anamnese do wizard; 'procedimentos' = onde cada uma vale, decidido aqui
    (FormularioAnamnese.aplica_a) — o JS so consulta, nao repete a regra."""
    return [
        {
            'id': form.pk,
            'nome': form.nome,
            'obrigatorio': form.obrigatorio,
            'schema': form.schema_json,
            'procedimentos': [proc.pk for proc in procedimentos if form.aplica_a(proc)],
        }
        for form in FormularioAnamnese.objects.filter(ativo=True, tipo=FormularioAnamnese.TIPO_ANAMNESE)
        .order_by('pk')
    ]


def _termos(procedimentos):
    """Termos de procedimento aceitos no passo 3; 'procedimentos' = onde cada um vale
    (services/termos.vale_para, a regra do servidor) — o JS so consulta."""
    lista = []
    for termo in VersaoTermo.objects.filter(tipo='PROCEDIMENTO', ativa=True).order_by('pk'):
        onde = [proc.pk for proc in procedimentos if termos.vale_para(termo, proc)]
        if onde:
            lista.append({
                'id': termo.pk, 'titulo': termo.titulo, 'versao': termo.versao, 'conteudo': termo.conteudo,
                'procedimentos': onde,
            })
    return lista


@ratelimit(key='ip', rate='300/h', method='GET', block=True)
def agendamento_publico(request):
    """Pagina publica de agendamento (wizard)."""
    procedimentos = list(procedimentos_agendaveis())   # so quem tem profissional habilitado
    cards = _cards(procedimentos)

    proc_preselect = request.GET.get('procedimento', '')
    if proc_preselect not in {str(proc.pk) for proc in procedimentos}:
        proc_preselect = ''

    prof_id = id_int(request.GET.get('profissional'))
    prof_preselect = ''
    if prof_id is not None and Profissional.objects.filter(pk=prof_id, ativo=True).exists():
        prof_preselect = str(prof_id)

    # Consentimento art. 11: o texto exibido e o da versao SAUDE que o POST grava
    _termo_saude, texto_consentimento_saude = VersaoTermo.texto_saude_vigente()
    otp_telefone = otp_service.telefone_verificado_agendamento(request)
    titular = Cliente.objects.filter(telefone=otp_telefone).first() if otp_telefone else None
    context = {
        'procedimentos': cards,
        'categorias_disponiveis': sorted({(c['categoria'], c['categoria_label']) for c in cards}),
        'formularios_anamnese_data': _fichas(procedimentos),
        'termo_lgpd': VersaoTermo.lgpd_vigente(),
        'termos_procedimento_data': _termos(procedimentos),
        'texto_consentimento_saude': texto_consentimento_saude,
        # Rotulo de cada opt-in de comunicacao = texto gravado como prova no consentimento
        'textos_consentimento': consentimentos.TEXTOS_WIZARD,
        'proc_preselect': proc_preselect,
        'prof_preselect': prof_preselect,
        # Telefone ja verificado nesta sessao (reidratar sem exigir novo SMS) e se ja tem cadastro
        # (D16: o nome da titular fica so leitura)
        'otp_telefone': otp_telefone,
        'otp_titular': titular is not None,
        'otp_fichas_em_dia': agendamento_online.fichas_em_dia_do_wizard(titular),
        'rehidratar': bool(request.session.pop(SESSAO_REHIDRATAR, False)),
        # Sem canal de SMS (prod sem provedor) o OTP e impossivel: avisa ja no
        # passo 1 (antes de escolher tratamento/horario) e de novo no passo 3
        'sms_disponivel': sms_disponivel(),
        'turnstile_site_key': turnstile_site_key(),
        'turnstile_enabled': turnstile_enabled(),
    }

    return render(request, 'agendamento/agendamento_publico.html', context)


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def confirmar_agendamento(request):
    """Pedido de agendamento SEM login: form (entrada + celular verificado por SMS) -> caso de uso
    (services/agendamento_online) -> pagina de sucesso. Recusa volta ao wizard com a mensagem."""
    if request.method != 'POST':
        return redirect('aranha:agendamento_publico')

    if request.POST.get('website', '').strip():
        logger.info('booking_honeypot_triggered', extra={'ip': _client_ip(request)})
        return redirect('aranha:agendamento_publico')

    form = AgendamentoPublicoForm(request.POST, request=request)
    if not form.is_valid():
        return _voltar_com_erro(request, form.primeiro_erro())
    pedido = form.pedido()

    try:
        atendimento = agendamento_online.solicitar(pedido, request=request)
    except RegraDeNegocio as recusa:
        if recusa.codigo == 'horario_ocupado':
            logger.info('booking_slot_corrida_perdida', extra={'telefone': mask_telefone(pedido.telefone)})
        return _voltar_com_erro(request, recusa.mensagem)
    except DatabaseError as exc:
        logger.error(
            'booking_confirmar_falha',
            extra={
                'erro': str(exc),
                'email': mask_email(pedido.email) if pedido.email else None,
                'telefone': mask_telefone(pedido.telefone),
            },
            exc_info=True,
        )
        return _voltar_com_erro(request, MSG_FALHA)

    return _concluir(request, atendimento)


def _concluir(request, atendimento):
    """Ponto unico depois de um pedido aceito: resumo p/ a pagina de sucesso e fim da verificacao
    do celular (o proximo agendamento pede outro SMS)."""
    request.session[SESSAO_SUCESSO] = agendamento_online.resumo_confirmacao(atendimento)
    # P10 (decisao 39): a area da cliente abre aqui, sem novo SMS — o celular acabou de ser verificado
    portal_cliente.abrir_sessao_do_wizard(request, atendimento.cliente.telefone)
    otp_service.limpar_verificacao_agendamento(request)
    request.session.pop(SESSAO_REHIDRATAR, None)
    return redirect('aranha:agendamento_sucesso')


def agendamento_sucesso(request):
    """Pagina de sucesso apos agendamento/reagendamento."""
    dados = request.session.pop(SESSAO_SUCESSO, None)
    if not dados:
        return redirect('aranha:agendamento_publico')
    return render(request, 'agendamento/agendamento_sucesso.html', {'dados': dados})
