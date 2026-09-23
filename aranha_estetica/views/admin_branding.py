"""UI de branding — edita nome, contatos e cor da marca sem redeploy.

Persiste em Configuracao (chave-valor). utils/branding.get_branding() e o
context processor `clinica_globals` leem daqui com precedencia sobre env var,
entao a mudanca vale para o site, e-mails e WhatsApp.

Sem upload de logo: o disco do Railway e efemero e /media/ nao e servido.
"""
import re

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email
from django.shortcuts import redirect, render

from ..decorators import staff_required
from ..models import Configuracao
from ..utils.audit import registrar_log
from ..utils.branding import BRANDING_FIELDS, config_dict, get_branding, invalidar_cache

_HEX_COR = re.compile(r'^#[0-9a-fA-F]{6}$')


def _validar(chave: str, valor: str):
    """(valor_normalizado, erro). Vazio e sempre valido (volta ao padrao)."""
    if not valor:
        return '', None
    if chave == 'WHATSAPP_NUMERO':
        digitos = re.sub(r'\D', '', valor)
        if not 12 <= len(digitos) <= 13:
            return valor, 'WhatsApp: use DDI + DDD + número, só dígitos (ex: 5517991234567).'
        return digitos, None
    if chave == 'CLINIC_EMAIL':
        try:
            validate_email(valor)
        except ValidationError:
            return valor, 'E-mail de contato inválido.'
    if chave == 'INSTAGRAM_URL':
        try:
            URLValidator(schemes=['https', 'http'])(valor)
        except ValidationError:
            return valor, 'URL do Instagram inválida (ex: https://instagram.com/suaclinica).'
    if chave == 'THEME_COLOR' and not _HEX_COR.match(valor):
        return valor, 'Cor da marca: use o formato hexadecimal #RRGGBB.'
    return valor, None


@staff_required
def admin_branding(request):
    """Edita todos campos de branding em uma unica tela."""
    if request.method == 'POST':
        salvos = config_dict()
        efetivos = get_branding()
        alterados, erros = [], []
        for chave, _label, _tipo in BRANDING_FIELDS:
            if chave not in request.POST:
                continue
            valor, erro = _validar(chave, request.POST.get(chave, '').strip())
            if erro:
                erros.append(erro)
                continue
            # So grava o que muda o valor em uso (input type=color sempre envia
            # algo — nao pode virar override silencioso do padrao).
            if valor == efetivos.get(chave, ''):
                continue
            if not valor and not salvos.get(chave):
                continue  # ja estava sem override: segue env/padrao
            Configuracao.objects.update_or_create(chave=chave, defaults={'valor': valor})
            alterados.append(chave)

        for erro in erros:
            messages.error(request, erro)
        if alterados:
            invalidar_cache()  # o signal tambem invalida; explicito por clareza
            registrar_log(
                request.user, 'Atualizou branding',
                'configuracao_sistema', None,
                detalhes={'chaves': alterados},
            )
            n = len(alterados)
            messages.success(
                request,
                f'Branding atualizado ({n} campo{"s" if n != 1 else ""}). '
                'O site já usa os novos dados.',
            )
        elif not erros:
            messages.info(request, 'Nenhuma alteração detectada.')

        return redirect('aranha:admin_branding')

    efetivos = get_branding()
    campos = [
        {'chave': chave, 'label': label, 'tipo': tipo, 'valor': efetivos.get(chave, '')}
        for chave, label, tipo in BRANDING_FIELDS
    ]
    return render(request, 'painel/branding.html', {'campos': campos})
