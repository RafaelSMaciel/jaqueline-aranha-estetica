"""Pagina de contato: formulario com captcha, honeypot, limite por IP e teto diario global.

O e-mail do contato divide a cota do provedor com o reset de senha e os avisos da clinica: alem do
limite por IP (20/dia), um teto global de mensagens por dia protege essa cota — acima dele a pagina
oferece o WhatsApp e o evento vai para o log. Sem caixa de marketing (decisao 48).
"""
import logging

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.mail import EmailMessage
from django.shortcuts import redirect, render
from django_ratelimit.decorators import ratelimit

from ...forms.publico import ContatoForm, primeiro_erro
from ...utils import datas
from ...utils.branding import get_branding
from ...utils.captcha import captcha_invalido, turnstile_enabled, turnstile_site_key
from ...utils.email import email_configurado
from ...utils.pii import mask_email

logger = logging.getLogger(__name__)

TEMPLATE = 'publico/contato.html'
TETO_DIARIO_GLOBAL = 50
LIMITE_DIARIO_POR_IP = '20/d'
_CAMPOS = ('name', 'email', 'phone', 'subject', 'message')


def destino_contato() -> str:
    """Caixa que recebe o formulario: Branding/env CLINIC_EMAIL -> DEFAULT_FROM_EMAIL (se nao for noreply)."""
    destino = (get_branding().get('CLINIC_EMAIL') or getattr(settings, 'CLINIC_EMAIL', '') or '').strip()
    if destino:
        return destino
    padrao = (getattr(settings, 'DEFAULT_FROM_EMAIL', '') or '').strip()
    local = padrao.split('<')[-1].split('@')[0].lower()
    if not padrao or local.startswith(('noreply', 'no-reply', 'nao-responda', 'naoresponda')):
        return ''
    return padrao


def _cabe_no_teto_do_dia() -> bool:
    """Conta a mensagem no teto global do dia (cache.add + incr, atomico). False = acima do teto."""
    chave = f'contato:teto:{datas.hoje().isoformat()}'
    cache.add(chave, 0, timeout=60 * 60 * 26)
    try:
        return cache.incr(chave) <= TETO_DIARIO_GLOBAL
    except ValueError:   # a chave expirou entre o add e o incr
        return True


def _render(request, form_data=None, *, envio_falhou=False, status=200):
    return render(request, TEMPLATE, {
        'form_data': form_data or {},
        'envio_falhou': envio_falhou,
        'turnstile_enabled': turnstile_enabled(),
        'turnstile_site_key': turnstile_site_key(),
    }, status=status)


def _enviar(form) -> int:
    dados = form.cleaned_data
    destino = destino_contato()
    corpo = (
        f'Nome: {dados["name"]}\n'
        f'E-mail: {dados["email"]}\n'
        f'Telefone: {dados["phone"] or "não informado"}\n'
        f'Assunto: {form.assunto}\n\n'
        f'Mensagem:\n{dados["message"]}'
    )
    # console/dummy fora de DEBUG 'enviam' (retornam 1) mas nada chega: falha fechado
    if not (destino and email_configurado()):
        logger.error('contato_email_indisponivel',
                     extra={'tem_destino': bool(destino), 'from_email': mask_email(dados['email'])})
        return 0
    try:
        return EmailMessage(
            subject=f'Contato site: {form.assunto}', body=corpo,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None), to=[destino], reply_to=[dados['email']],
        ).send(fail_silently=False)
    except Exception:  # noqa: BLE001 — qualquer falha de envio vira aviso honesto
        logger.exception('contato_email_falha',
                         extra={'from_email': mask_email(dados['email']), 'subject': dados['subject']})
        return 0


@ratelimit(key='ip', rate=LIMITE_DIARIO_POR_IP, method='POST', block=True)
@ratelimit(key='ip', rate='5/m', method='POST', block=True)
def agenda_contato(request):
    """Pagina de contato com form POST (PRG). Se o e-mail nao puder ser entregue (ou o teto do dia
    acabou), NAO finge sucesso: re-renderiza com os dados preenchidos e oferece o WhatsApp."""
    if request.method != 'POST':
        return _render(request)

    form = ContatoForm(request.POST)
    form_data = {campo: str(request.POST.get(campo, ''))[:5000] for campo in _CAMPOS}
    if form.eh_robo():
        logger.info('contato_honeypot')
        messages.success(request, 'Mensagem enviada! Entraremos em contato em breve.')
        return redirect('aranha:agenda_contato')
    if captcha_invalido(request):
        messages.error(request, 'Confirme que você não é um robô e envie de novo.')
        return _render(request, form_data, status=400)
    if not form.is_valid():
        messages.error(request, primeiro_erro(form))
        return _render(request, form_data)

    if not _cabe_no_teto_do_dia():
        logger.error('contato_teto_diario_atingido', extra={'teto': TETO_DIARIO_GLOBAL})
        enviado = 0
    else:
        enviado = _enviar(form)
    if not enviado:
        b = get_branding()
        outro_canal = any(b.get(k) for k in ('WHATSAPP_NUMERO', 'CLINIC_PHONE', 'CLINIC_EMAIL'))
        messages.error(
            request,
            'Não conseguimos enviar sua mensagem agora. Seus dados continuam no formulário: tente de novo '
            'mais tarde' + (' ou use outro canal de contato desta página.' if outro_canal else '.'),
        )
        return _render(request, form_data, envio_falhou=True)

    messages.success(request, 'Mensagem enviada! Entraremos em contato em breve.')
    return redirect('aranha:agenda_contato')
