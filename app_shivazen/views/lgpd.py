"""Views LGPD — DSAR (export de dados), unsubscribe, cookie consent."""
import json
import logging

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from ..models import Cliente, CodigoVerificacao
from ..services import LgpdService
from ..services.auditoria import AuditoriaService

logger = logging.getLogger(__name__)


def meus_dados(request):
    """Pagina DSAR: cliente solicita seus dados (via telefone + OTP)."""
    if request.method == 'GET':
        return render(request, 'publico/lgpd_meus_dados.html', {})

    telefone = (request.POST.get('telefone') or '').strip()
    codigo = (request.POST.get('codigo') or '').strip()

    if not telefone:
        messages.error(request, 'Informe o telefone cadastrado.')
        return render(request, 'publico/lgpd_meus_dados.html', {})

    if not codigo:
        # Emitir OTP via servico existente — fallback sem integracao real
        import secrets
        ultimo = CodigoVerificacao.objects.create(
            telefone=telefone, codigo=f'{secrets.randbelow(1000000):06d}',
        )
        logger.info('LGPD OTP emitido para %s (codigo=%s)', telefone[:4] + '***', ultimo.codigo)
        messages.info(request, 'Codigo enviado. Verifique seu WhatsApp/SMS.')
        return render(request, 'publico/lgpd_meus_dados.html', {'aguardando_codigo': True, 'telefone': telefone})

    if not CodigoVerificacao.consumir(telefone, codigo):
        messages.error(request, 'Codigo invalido ou expirado.')
        return render(request, 'publico/lgpd_meus_dados.html', {'aguardando_codigo': True, 'telefone': telefone})

    cliente = Cliente.objects.filter(telefone=telefone).first()
    if not cliente:
        messages.error(request, 'Cliente nao encontrado.')
        return render(request, 'publico/lgpd_meus_dados.html', {})

    dados = LgpdService.exportar_dados_cliente(cliente)
    AuditoriaService.registrar(
        request=request,
        acao='DSAR: exportacao de dados',
        tabela_afetada='cliente',
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
        tabela_afetada='cliente',
        id_registro=cliente.pk,
    )
    return render(request, 'publico/lgpd_unsubscribe.html', {'sucesso': True, 'cliente': cliente})


@require_http_methods(['POST'])
def aceitar_cookies(request):
    """Endpoint AJAX para registrar consentimento de cookies em sessao."""
    request.session['cookie_consent'] = True
    request.session['cookie_consent_ts'] = str(__import__('datetime').datetime.now().isoformat())
    return JsonResponse({'success': True})
