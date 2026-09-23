"""Views LGPD — DSAR (export de dados), unsubscribe, cookie consent."""
import json
import logging

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from ..models import Cliente, CodigoOtp
from ..services import LgpdService
from ..services.auditoria import AuditoriaService
from ..utils.sms import enviar_otp_sms, sms_disponivel
from ..utils.sms import pode_enviar as sms_pode_enviar

logger = logging.getLogger(__name__)

TEMPLATE_DSAR = 'publico/lgpd_meus_dados.html'
TEMPLATE_DESCADASTRO = 'publico/lgpd_unsubscribe.html'


@ratelimit(key='ip', rate='10/h', method='POST', block=True)
@ratelimit(key='post:telefone', rate='5/h', method='POST', block=True)
def meus_dados(request):
    """Pagina DSAR: cliente solicita seus dados (via telefone + OTP)."""
    if request.method == 'GET':
        return render(request, TEMPLATE_DSAR, {})

    from ..validators import normalizar_telefone
    telefone = normalizar_telefone(request.POST.get('telefone') or '')
    codigo = (request.POST.get('codigo') or '').strip()

    if not telefone:
        messages.error(request, 'Informe o telefone cadastrado.')
        return render(request, TEMPLATE_DSAR, {})

    if not codigo:
        # Checagem global do canal (nao revela se o cadastro existe).
        if not sms_disponivel():
            messages.error(
                request,
                'A verificação por SMS está indisponível no momento. '
                'Fale com a clínica para solicitar seus dados.',
            )
            return render(request, TEMPLATE_DSAR, {'sms_indisponivel': True})
        # OTP hashed + envio SMS real. Anti-enumeracao: mensagem identica
        # exista o cliente ou nao; codigo NUNCA vai para log.
        from ..utils.security import client_ip
        ip = client_ip(request)
        if Cliente.objects.filter(telefone=telefone).exists():
            # Cooldown e quota ANTES de gerar: gerar() invalida o codigo
            # anterior — sem SMS saindo, o titular ficaria sem codigo valido.
            if not CodigoOtp.pode_reenviar(
                CodigoOtp.email_para_telefone(telefone), proposito=CodigoOtp.PROPOSITO_DSAR,
            ):
                logger.info('lgpd_dsar_otp_cooldown', extra={'tel_suffix': telefone[-4:]})
            elif not sms_pode_enviar(telefone, ip=ip):
                logger.warning('lgpd_dsar_sms_quota', extra={'tel_suffix': telefone[-4:]})
            else:
                codigo_plano, _obj = CodigoOtp.gerar_sms(
                    telefone, ip=ip, proposito=CodigoOtp.PROPOSITO_DSAR,
                )
                if not enviar_otp_sms(telefone, codigo_plano, ip=ip):
                    logger.warning('lgpd_dsar_sms_falha', extra={'tel_suffix': telefone[-4:]})
        logger.info('lgpd_dsar_otp_solicitado', extra={'tel_suffix': telefone[-4:]})
        messages.info(
            request,
            'Se houver cadastro com este telefone, o código chegará por SMS em instantes. '
            'Se não chegar, fale com a clínica.',
        )
        return render(request, TEMPLATE_DSAR, {'aguardando_codigo': True, 'telefone': telefone})

    ok, _motivo = CodigoOtp.verificar_sms(telefone, codigo, proposito=CodigoOtp.PROPOSITO_DSAR)
    if not ok:
        messages.error(request, 'Código inválido ou expirado.')
        return render(request, TEMPLATE_DSAR, {'aguardando_codigo': True, 'telefone': telefone})

    cliente = Cliente.objects.filter(telefone=telefone).first()
    if not cliente:
        messages.error(request, 'Cadastro não encontrado.')
        return render(request, TEMPLATE_DSAR, {})

    try:
        dados = LgpdService.exportar_dados_cliente(cliente)
        conteudo = json.dumps(dados, ensure_ascii=False, indent=2, default=str)
    except Exception:  # noqa: BLE001 — OTP ja consumido: resposta amigavel, nunca 500
        logger.exception('lgpd_dsar_export_falhou', extra={'cliente_id': cliente.pk})
        messages.error(
            request,
            'Não conseguimos gerar seus dados agora. Solicite um novo código em instantes '
            'ou fale com a clínica.',
        )
        return render(request, TEMPLATE_DSAR, {})

    AuditoriaService.registrar(
        request=request,
        acao='DSAR: exportacao de dados',
        tabela='cliente',
        id_registro=cliente.pk,
    )

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

    cliente = LgpdService.unsubscribe_por_token(token)
    if not cliente:
        return render(request, TEMPLATE_DESCADASTRO, {'estado': 'invalido'}, status=404)
    AuditoriaService.registrar(
        request=request,
        acao='LGPD: opt-out de comunicacao',
        tabela='cliente',
        id_registro=cliente.pk,
    )
    return render(request, TEMPLATE_DESCADASTRO, {'estado': 'sucesso', 'cliente': cliente})


@ratelimit(key='ip', rate='30/m', method='POST', block=True)
@require_http_methods(['POST'])
def aceitar_cookies(request):
    """Registra consentimento granular de cookies (essencial sempre, analytics, marketing)."""
    from django.utils import timezone
    prefs = {
        'essential': True,
        'analytics': request.POST.get('analytics', '0') == '1',
        'marketing': request.POST.get('marketing', '0') == '1',
    }
    request.session['cookie_consent'] = True
    request.session['cookie_consent_prefs'] = prefs
    request.session['cookie_consent_ts'] = timezone.now().isoformat()
    return JsonResponse({'success': True, 'prefs': prefs})
