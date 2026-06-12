"""Views LGPD — DSAR (export de dados), unsubscribe, cookie consent."""
import json
import logging

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from ..models import Cliente, CodigoOtp
from ..services import LgpdService
from ..services.auditoria import AuditoriaService

logger = logging.getLogger(__name__)


@ratelimit(key='ip', rate='10/h', method='POST', block=True)
@ratelimit(key='post:telefone', rate='5/h', method='POST', block=True)
def meus_dados(request):
    """Pagina DSAR: cliente solicita seus dados (via telefone + OTP)."""
    if request.method == 'GET':
        return render(request, 'publico/lgpd_meus_dados.html', {})

    from ..validators import normalizar_telefone
    telefone = normalizar_telefone(request.POST.get('telefone') or '')
    codigo = (request.POST.get('codigo') or '').strip()

    if not telefone:
        messages.error(request, 'Informe o telefone cadastrado.')
        return render(request, 'publico/lgpd_meus_dados.html', {})

    if not codigo:
        # OTP hashed + envio SMS real. Anti-enumeracao: mensagem identica
        # exista o cliente ou nao; codigo NUNCA vai para log.
        from ..services.notificacao import OTPService
        from ..utils.security import client_ip
        if Cliente.objects.filter(telefone=telefone).exists():
            codigo_plano, _obj = CodigoOtp.gerar_sms(
                telefone, ip=client_ip(request), proposito=CodigoOtp.PROPOSITO_DSAR,
            )
            if not OTPService.enviar_codigo(telefone, codigo_plano, ip=client_ip(request)):
                logger.warning('lgpd_dsar_sms_falha', extra={'tel_suffix': telefone[-4:]})
        logger.info('lgpd_dsar_otp_solicitado', extra={'tel_suffix': telefone[-4:]})
        messages.info(request, 'Se houver cadastro, o codigo chegara por SMS.')
        return render(request, 'publico/lgpd_meus_dados.html', {'aguardando_codigo': True, 'telefone': telefone})

    ok, _motivo = CodigoOtp.verificar_sms(telefone, codigo, proposito=CodigoOtp.PROPOSITO_DSAR)
    if not ok:
        messages.error(request, 'Codigo invalido ou expirado.')
        return render(request, 'publico/lgpd_meus_dados.html', {'aguardando_codigo': True, 'telefone': telefone})

    cliente = Cliente.objects.filter(telefone=telefone).first()
    if not cliente:
        messages.error(request, 'Paciente nao encontrado.')
        return render(request, 'publico/lgpd_meus_dados.html', {})

    dados = LgpdService.exportar_dados_cliente(cliente)
    AuditoriaService.registrar(
        request=request,
        acao='DSAR: exportacao de dados',
        tabela='cliente',
        id_registro=cliente.pk,
    )

    response = HttpResponse(
        json.dumps(dados, ensure_ascii=False, indent=2, default=str),
        content_type='application/json; charset=utf-8',
    )
    response['Content-Disposition'] = f'attachment; filename="meus_dados_{cliente.pk}.json"'
    return response


@ratelimit(key='ip', rate='5/m', method=['GET', 'POST'], block=True)
def unsubscribe(request, token: str):
    """Opt-out de comunicacao via link em emails/WhatsApp."""
    cliente = LgpdService.unsubscribe_por_token(token)
    if not cliente:
        return render(request, 'publico/lgpd_unsubscribe.html', {'sucesso': False}, status=404)
    AuditoriaService.registrar(
        request=request,
        acao='LGPD: opt-out de comunicacao',
        tabela='cliente',
        id_registro=cliente.pk,
    )
    return render(request, 'publico/lgpd_unsubscribe.html', {'sucesso': True, 'cliente': cliente})


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
