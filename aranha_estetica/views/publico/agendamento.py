"""Fluxo publico de agendamento — 3 passos (procedimento, data/horario, confirmar).

Porta HTTP do caso de uso ``services/agendamento_online.solicitar``: o GET monta o wizard; o POST
passa pelo ``AgendamentoPublicoForm`` (campos + celular verificado por SMS NESTA sessao, exigido de
todo agendamento) e chama o service, que tem a regra inteira (termo LGPD, horario, fichas com
consentimento de saude, termos de procedimento, cadastro, reserva com o preco da data, e-mails e
provas). Aqui so honeypot, form, service e a pagina de sucesso.
"""
import logging

from django.contrib import messages
from django.db import DatabaseError, OperationalError, ProgrammingError
from django.shortcuts import redirect, render
from django_ratelimit.decorators import ratelimit

from ...excecoes import RegraDeNegocio
from ...forms.agendamento import AgendamentoPublicoForm
from ...models import (
    FormularioAnamnese,
    Profissional,
    Promocao,
    VersaoTermo,
)
from ...models.termos import TEXTO_CONSENTIMENTO_SAUDE
from ...services import agendamento_online, consentimentos
from ...services import otp as otp_service
from ...services.disponibilidade import procedimentos_agendaveis
from ...utils.captcha import turnstile_enabled, turnstile_site_key
from ...utils.datas import hoje as hoje_local
from ...utils.parse import id_int
from ...utils.pii import mask_email, mask_telefone
from ...utils.precos import aplicar_promocao, preco_base_map, promocao_vigente
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


def _precos_card(procedimentos):
    """{pk: (valor_final_hoje, promocao|None, valor_cheio)} p/ o 'A partir de' do card.

    Mesma conta do agendamento (utils.precos): promocao vigente HOJE aplicada
    sobre o valor mostrado (preco base ou, sem ele, o menor do profissional —
    valor_base). O valor gravado usa a data do atendimento e o profissional.
    """
    cheios = preco_base_map(procedimentos)
    hoje = hoje_local()
    vigentes = Promocao.objects.filter(ativa=True, data_inicio__lte=hoje, data_fim__gte=hoje)
    ha_geral = vigentes.filter(procedimento__isnull=True).exists()
    com_promo = set(
        vigentes.filter(procedimento__in=procedimentos).values_list('procedimento_id', flat=True)
    )
    out = {}
    for proc in procedimentos:
        cheio = cheios.get(proc.pk)
        if cheio is None:
            continue
        promo = (
            promocao_vigente(proc, hoje, valor_base=cheio)
            if (ha_geral or proc.pk in com_promo) else None
        )
        final = aplicar_promocao(cheio, promo)
        if promo is not None and final >= cheio:
            promo, final = None, cheio
        out[proc.pk] = (final, promo, cheio)
    return out


@ratelimit(key='ip', rate='300/h', method='GET', block=True)
def agendamento_publico(request):
    """Pagina publica de agendamento (wizard)."""
    procedimentos_com_preco = []
    procedimentos = []
    try:
        procedimentos = list(procedimentos_agendaveis())   # so quem tem profissional habilitado
        precos = _precos_card(procedimentos)
        for proc in procedimentos:
            final, promo, cheio = precos.get(proc.pk, (None, None, None))
            procedimentos_com_preco.append({
                'id': proc.pk,
                'nome': proc.nome,
                'descricao': proc.descricao or '',
                'duracao_minutos': proc.duracao_minutos,
                'preco': float(final) if final is not None else 0,
                # cheio sem promo: fallback do resumo (o valor real vem por data/profissional)
                'preco_base': float(cheio) if cheio is not None else 0,
                'preco_cheio': float(cheio) if promo is not None else None,
                'promocao': promo.nome if promo is not None else '',
                'promocao_ate': promo.data_fim if promo is not None else None,
                'categoria': proc.categoria,
                'categoria_label': proc.get_categoria_display(),
            })
    except (OperationalError, ProgrammingError):
        logger.warning('booking_tabelas_procedimento_indisponiveis')

    ids_validos = {str(p['id']) for p in procedimentos_com_preco}
    proc_preselect = request.GET.get('procedimento', '')
    if proc_preselect not in ids_validos:
        proc_preselect = ''

    prof_id = id_int(request.GET.get('profissional'))
    prof_preselect = ''
    if prof_id is not None and Profissional.objects.filter(pk=prof_id, ativo=True).exists():
        prof_preselect = str(prof_id)

    categorias_disponiveis = sorted({
        (p['categoria'], p['categoria_label']) for p in procedimentos_com_preco
    })

    formularios_anamnese = []
    try:
        # 'procedimentos': onde a ficha vale, decidido aqui (FormularioAnamnese.aplica_a) — o JS
        # so consulta, nao repete a regra
        formularios_anamnese = [
            {
                'id': form.pk,
                'nome': form.nome,
                'obrigatorio': form.obrigatorio,
                'schema': form.schema_json,
                'procedimentos': [proc.pk for proc in procedimentos if form.aplica_a(proc)],
            }
            for form in FormularioAnamnese.objects.filter(ativo=True, tipo='ANAMNESE')
        ]
    except (OperationalError, ProgrammingError):
        pass

    termo_lgpd = None
    termos_procedimento = []
    # Consentimento art. 11: o texto exibido e o da versao SAUDE que o POST grava
    texto_consentimento_saude = TEXTO_CONSENTIMENTO_SAUDE
    try:
        termo_lgpd = VersaoTermo.lgpd_vigente()
        _termo_saude, texto_consentimento_saude = VersaoTermo.texto_saude_vigente()
        termos_procedimento = [
            {
                'id': t.pk,
                'titulo': t.titulo,
                'versao': t.versao,
                'conteudo': t.conteudo,
                'procedimento_id': t.procedimento_id,
            }
            for t in VersaoTermo.objects.filter(tipo='PROCEDIMENTO', ativa=True).order_by('pk')
        ]
    except (OperationalError, ProgrammingError):
        pass

    context = {
        'procedimentos': procedimentos_com_preco,
        'categorias_disponiveis': categorias_disponiveis,
        'formularios_anamnese_data': formularios_anamnese,
        'termo_lgpd': termo_lgpd,
        'termos_procedimento_data': termos_procedimento,
        'texto_consentimento_saude': texto_consentimento_saude,
        # Rotulo de cada opt-in de comunicacao = texto gravado como prova no consentimento
        'textos_consentimento': consentimentos.TEXTOS_WIZARD,
        'proc_preselect': proc_preselect,
        'prof_preselect': prof_preselect,
        # Telefone ja verificado nesta sessao (reidratar sem exigir novo SMS)
        'otp_telefone': otp_service.telefone_verificado_agendamento(request),
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
    otp_service.limpar_verificacao_agendamento(request)
    request.session.pop(SESSAO_REHIDRATAR, None)
    return redirect('aranha:agendamento_sucesso')


def agendamento_sucesso(request):
    """Pagina de sucesso apos agendamento/reagendamento."""
    dados = request.session.pop(SESSAO_SUCESSO, None)
    if not dados:
        return redirect('aranha:agendamento_publico')
    return render(request, 'agendamento/agendamento_sucesso.html', {'dados': dados})
