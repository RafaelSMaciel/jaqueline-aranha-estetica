"""Views LGPD — politica de privacidade (texto do termo vigente), DSAR e descadastro de comunicacao."""
import json
import logging
import time

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from ...constants import TERMO_LGPD_CONTEUDO, TERMO_LGPD_VERSAO
from ...models import Cliente, CodigoOtp, VersaoTermo
from ...services import LgpdService, lgpd
from ...services import otp as otp_service
from ...utils import telefone as telefones
from ...utils.auditoria import registrar_log
from ...utils.captcha import captcha_invalido, turnstile_enabled, turnstile_site_key
from ...utils.sms import sms_disponivel

logger = logging.getLogger(__name__)

TEMPLATE_DSAR = 'publico/lgpd_meus_dados.html'
TEMPLATE_DESCADASTRO = 'publico/lgpd_unsubscribe.html'


def politica_privacidade(request):
    """/politica-de-privacidade/ = o texto da VersaoTermo LGPD vigente (decisao 47), com versao e
    vigencia: a cliente le exatamente o texto cujo SHA-256 vai na prova do aceite. Sem versao ativa
    (aviso aranha.W010), o texto padrao da v1.0."""
    termo = VersaoTermo.lgpd_vigente()
    return render(request, 'publico/politica_privacidade.html', {
        'termo_conteudo': termo.conteudo if termo else TERMO_LGPD_CONTEUDO,
        'termo_versao': termo.versao if termo else TERMO_LGPD_VERSAO,
        'termo_vigente_desde': termo.vigente_desde if termo else None,
    })


def _chave_telefone(_grupo, request):
    """Limite por titular: '(17) 99999-0001' e '17999990001' contam no mesmo balde."""
    return telefones.normalizar(request.POST.get('telefone')) or 'sem-telefone'


def _contexto(**extra):
    return {'turnstile_enabled': turnstile_enabled(), 'turnstile_site_key': turnstile_site_key(), **extra}


@ratelimit(key='ip', rate='10/h', method='POST', block=True)
@ratelimit(key=_chave_telefone, rate='5/h', method='POST', block=True)
def meus_dados(request):
    """"Meus dados": baixa o export do titular com codigo por SMS (services/otp, PROPOSITO_DSAR).

    Mesma quota e cooldown do OTP do portal; resposta e tempo identicos exista ou nao o cadastro. O
    export do site nao leva a ficha de saude (decisao 38: o chip pode ter mudado de dono) — ela vai
    ao e-mail cadastrado ou e entregue mediante pedido na clinica. A area "Meus agendamentos" faz o
    mesmo e ainda muda preferencias e registra pedidos.
    """
    if request.method == 'GET':
        return render(request, TEMPLATE_DSAR, _contexto())

    telefone = telefones.valido_ou_vazio(request.POST.get('telefone'))
    codigo = (request.POST.get('codigo') or '').strip()
    if not telefone:
        messages.error(request, 'Informe o celular cadastrado, com DDD.')
        return render(request, TEMPLATE_DSAR, _contexto())

    if not codigo:
        return _pedir_codigo(request, telefone)

    ok, _motivo = otp_service.verificar_otp_telefone(telefone, codigo, proposito=CodigoOtp.PROPOSITO_DSAR)
    if not ok:
        messages.error(request, 'Código inválido ou expirado.')
        return render(request, TEMPLATE_DSAR, _contexto(aguardando_codigo=True, telefone=telefone))

    cliente = Cliente.objects.filter(telefone=telefone).first()
    if not cliente:
        messages.error(request, 'Cadastro não encontrado.')
        return render(request, TEMPLATE_DSAR, _contexto())
    return resposta_export(request, cliente)


def _pedir_codigo(request, telefone):
    if captcha_invalido(request):
        messages.error(request, 'Confirme que você não é um robô e peça o código de novo.')
        return render(request, TEMPLATE_DSAR, _contexto(), status=400)
    # Checagem global do canal (nao revela se o cadastro existe).
    if not sms_disponivel():
        messages.error(
            request,
            'A verificação por SMS está indisponível no momento. Fale com a clínica para solicitar seus dados.',
        )
        return render(request, TEMPLATE_DSAR, _contexto(sms_indisponivel=True))
    inicio = time.perf_counter()
    if Cliente.objects.filter(telefone=telefone).exists():
        # cooldown e quota ANTES de gerar ficam no services/otp (o codigo anterior continua valendo)
        ok, motivo, _canal = otp_service.solicitar_otp_telefone(
            telefone, request=request, proposito=CodigoOtp.PROPOSITO_DSAR,
        )
        if not ok and motivo != 'aguarde':
            logger.warning('lgpd_dsar_sms_falha', extra={'telefone': telefones.mascarar(telefone), 'motivo': motivo})
    logger.info('lgpd_dsar_otp_solicitado', extra={'telefone': telefones.mascarar(telefone)})
    otp_service.esperar_latencia_minima(inicio)
    messages.info(
        request,
        'Se houver cadastro com este celular, o código chegará por SMS em instantes. '
        'Se não chegar, fale com a clínica.',
    )
    return render(request, TEMPLATE_DSAR, _contexto(aguardando_codigo=True, telefone=telefone))


def resposta_export(request, cliente):
    """Download do export SEM saude (self-service), com a trilha 'DSAR: exportacao de dados'."""
    try:
        dados = lgpd.exportar_dados(cliente, incluir_saude=False)
        conteudo = json.dumps(dados, ensure_ascii=False, indent=2, default=str)
    except Exception:  # noqa: BLE001 — codigo ja consumido: resposta amigavel, nunca 500
        logger.exception('lgpd_dsar_export_falhou', extra={'cliente_id': cliente.pk})
        messages.error(
            request,
            'Não conseguimos gerar seus dados agora. Solicite um novo código em instantes ou fale com a clínica.',
        )
        return render(request, TEMPLATE_DSAR, _contexto())

    registrar_log(None, 'DSAR: exportacao de dados', 'cliente', cliente.pk, request=request)
    response = HttpResponse(conteudo, content_type='application/json; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="meus_dados_{cliente.pk}.json"'
    return response


@csrf_exempt  # token de descadastro na URL e a credencial; one-click (RFC 8058) nao traz CSRF
@require_http_methods(['GET', 'POST'])
@ratelimit(key='ip', rate='20/m', method=['GET', 'POST'], block=True)
def unsubscribe(request, token: str):
    """Opt-out de marketing/pesquisas via link do e-mail.

    GET so mostra a confirmacao (scanners de link — SafeLinks, antivirus —
    fazem GET e nao podem descadastrar ninguem). POST executa: e o que o
    formulario da pagina e o one-click do provedor (List-Unsubscribe-Post) enviam.
    """
    if request.method == 'GET':
        cliente = LgpdService.cliente_por_token_descadastro(token)
        if cliente is None:
            return render(request, TEMPLATE_DESCADASTRO, {'estado': 'invalido'}, status=404)
        return render(request, TEMPLATE_DESCADASTRO, {'estado': 'confirmar', 'cliente': cliente})

    cliente = LgpdService.unsubscribe_por_token(token, request=request)
    if not cliente:
        return render(request, TEMPLATE_DESCADASTRO, {'estado': 'invalido'}, status=404)
    registrar_log(None, 'LGPD: opt-out de comunicacao', 'cliente', cliente.pk)
    return render(request, TEMPLATE_DESCADASTRO, {'estado': 'sucesso', 'cliente': cliente})
