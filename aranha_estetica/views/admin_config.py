"""CRUD de Configuracao (chave-valor editavel via painel)."""
import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..decorators import staff_required
from ..models import Configuracao
from ..utils.audit import registrar_log


# Somente chaves que o codigo de fato le (grep antes de acrescentar outra):
#   email_admin          -> tasks.py (alerta de NPS detrator)
#   prontuario_perguntas -> utils/saude.perguntas_prontuario
# Marca/contatos ficam na tela Branding (utils/branding.BRANDING_FIELDS).
CONFIG_SUGERIDAS = [
    ('email_admin', '', 'E-mail que recebe o alerta de avaliação NPS negativa (detrator)'),
    (
        'prontuario_perguntas',
        '[{"chave": "fuma", "texto": "Fuma?", "tipo": "BOOLEAN"}]',
        'Perguntas extras do prontuário (JSON: lista de {chave, texto, tipo TEXTO|BOOLEAN})',
    ),
]


def _normalizar_chave(chave: str) -> str:
    # Sem upper(): as chaves lidas pelo codigo sao minusculas (email_admin...)
    return chave.strip().replace(' ', '_')


def _validar_valor(chave: str, valor: str) -> str | None:
    """Mensagem de erro se o valor nao serve p/ a chave; None se ok."""
    chave_l = chave.lower()
    if chave_l == 'email_admin' and valor:
        try:
            validate_email(valor)
        except ValidationError:
            return 'email_admin: informe um e-mail válido.'
    if chave_l == 'prontuario_perguntas' and valor:
        try:
            perguntas = json.loads(valor)
        except ValueError:
            return 'prontuario_perguntas: JSON inválido.'
        if not isinstance(perguntas, list) or not all(
            isinstance(p, dict) and p.get('chave') and p.get('texto') for p in perguntas
        ):
            return 'prontuario_perguntas: use uma lista de objetos com "chave", "texto" e "tipo".'
    return None


@staff_required
def admin_configuracoes(request):
    """Lista todas configuracoes + sugestoes de chaves nao cadastradas."""
    configs = Configuracao.objects.order_by('chave')
    chaves_existentes = {c.lower() for c in configs.values_list('chave', flat=True)}

    sugestoes = [
        {'chave': c, 'valor': v, 'descricao': d}
        for c, v, d in CONFIG_SUGERIDAS
        if c.lower() not in chaves_existentes
    ]

    context = {
        'configs': configs,
        'sugestoes': sugestoes,
    }
    return render(request, 'painel/configuracoes.html', context)


@staff_required
def admin_criar_configuracao(request):
    """Cria nova chave de configuracao."""
    if request.method == 'POST':
        chave = _normalizar_chave(request.POST.get('chave', ''))
        valor = request.POST.get('valor', '').strip()
        descricao = request.POST.get('descricao', '').strip() or None

        if not chave:
            messages.error(request, 'Chave é obrigatória.')
            return redirect('aranha:admin_configuracoes')

        if Configuracao.objects.filter(chave__iexact=chave).exists():
            messages.warning(request, f'Chave "{chave}" já existe. Edite em vez de duplicar.')
            return redirect('aranha:admin_configuracoes')

        erro = _validar_valor(chave, valor)
        if erro:
            messages.error(request, erro)
            return redirect('aranha:admin_configuracoes')

        config = Configuracao.objects.create(
            chave=chave, valor=valor, descricao=descricao
        )
        registrar_log(
            request.user, f'Criou configuracao: {chave}',
            'configuracao', config.pk,
            detalhes={'valor': valor}, request=request,
        )
        messages.success(request, f'Configuração "{chave}" criada.')

    return redirect('aranha:admin_configuracoes')


@staff_required
@require_POST
def admin_editar_configuracao(request, pk):
    """Edita valor/descricao de configuracao existente."""
    config = get_object_or_404(Configuracao, pk=pk)
    valor_antigo = config.valor

    novo_valor = request.POST.get('valor', '').strip()
    erro = _validar_valor(config.chave, novo_valor)
    if erro:
        messages.error(request, erro)
        return redirect('aranha:admin_configuracoes')

    config.valor = novo_valor
    descricao = request.POST.get('descricao', '').strip()
    if descricao:
        config.descricao = descricao
    config.save()

    registrar_log(
        request.user, f'Editou configuracao: {config.chave}',
        'configuracao', config.pk,
        detalhes={'valor_antigo': valor_antigo, 'valor_novo': config.valor},
        request=request,
    )
    messages.success(request, f'"{config.chave}" atualizada.')
    return redirect('aranha:admin_configuracoes')


@staff_required
@require_POST
def admin_excluir_configuracao(request, pk):
    """Remove configuracao."""
    config = get_object_or_404(Configuracao, pk=pk)
    chave = config.chave
    config.delete()
    registrar_log(
        request.user, f'Excluiu configuracao: {chave}',
        'configuracao', pk, request=request,
    )
    messages.success(request, f'"{chave}" removida.')
    return redirect('aranha:admin_configuracoes')
