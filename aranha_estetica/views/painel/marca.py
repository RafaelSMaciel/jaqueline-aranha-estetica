"""UI de branding — edita nome, contatos e cor da marca sem redeploy.

Persiste em Configuracao (chave-valor). utils/branding.get_branding() e o
context processor `clinica_globals` leem daqui com precedencia sobre env var,
entao a mudanca vale para o site, e-mails e WhatsApp. A validacao e o MarcaForm
(forms/cadastros); envio recusado volta a tela com o digitado e nada e gravado.

Sem upload de logo: o disco do Railway e efemero e /media/ nao e servido.
"""
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render
from django_ratelimit.decorators import ratelimit

from ...decorators import LIMITE_ESCRITA_EQUIPE, staff_required
from ...forms.cadastros import MarcaForm
from ...forms.painel import avisar_erros, erros_por_campo
from ...models import Configuracao
from ...utils.auditoria import registrar_log
from ...utils.branding import BRANDING_FIELDS, config_dict, get_branding, invalidar_cache


def _tela(request, efetivos, form=None):
    """Campos da tela: o valor em uso ou, no envio recusado, o digitado (com o erro do campo)."""
    erros = erros_por_campo(form) if form is not None else {}
    campos = [
        {'chave': chave, 'label': label, 'tipo': tipo, 'erro': erros.get(chave, ''),
         'valor': form.data.get(chave, '') if form is not None and chave in form.data else efetivos.get(chave, '')}
        for chave, label, tipo in BRANDING_FIELDS
    ]
    return render(request, 'painel/branding.html', {'campos': campos})


@staff_required
@ratelimit(key='user', rate=LIMITE_ESCRITA_EQUIPE, method='POST', block=True)
def admin_branding(request):
    """Edita todos campos de branding em uma unica tela."""
    efetivos = get_branding()
    if request.method != 'POST':
        return _tela(request, efetivos)

    form = MarcaForm(request.POST, efetivos=efetivos, salvos=config_dict())
    if not form.is_valid():
        avisar_erros(request, form)
        return _tela(request, efetivos, form)
    if not form.alteracoes:
        messages.info(request, 'Nenhuma alteração detectada.')
        return redirect('aranha:admin_branding')

    with transaction.atomic():
        for chave, valor in form.alteracoes.items():
            Configuracao.objects.update_or_create(chave=chave, defaults={'valor': valor})
    invalidar_cache()  # o signal tambem invalida; explicito por clareza
    alterados = list(form.alteracoes)
    registrar_log(
        request.user, 'Atualizou branding',
        'configuracao', None,
        detalhes={'chaves': alterados}, request=request,
    )
    n = len(alterados)
    messages.success(
        request,
        f'Branding atualizado ({n} campo{"s" if n != 1 else ""}). '
        'O site já usa os novos dados.',
    )
    return redirect('aranha:admin_branding')
