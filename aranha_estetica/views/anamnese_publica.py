"""Views publicas — preenchimento de formularios via link mago.

Cliente recebe link via email/WhatsApp:
  /anamnese/<token>/  → form pre-atendimento
  /pesquisa/<token>/  → form pos-atendimento

Mesma view, branching por formulario.tipo.
Sem autenticacao — token urlsafe(32) faz controle de acesso.
"""
from datetime import timedelta

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from ..models import RespostaAnamnese
from ..models.sistema import LogAuditoria


def _get_resposta_or_404(token: str, tipo_esperado: str) -> RespostaAnamnese:
    """Busca RespostaAnamnese pelo token, valida tipo e idade do convite.

    Token expira em 60 dias para limitar exposicao caso vaze.
    """
    resposta = get_object_or_404(
        RespostaAnamnese.objects.select_related(
            'formulario', 'cliente', 'atendimento', 'atendimento__profissional', 'atendimento__procedimento',
        ),
        token=token,
    )
    if resposta.formulario.tipo != tipo_esperado:
        raise Http404('Tipo de formulario nao confere com a rota.')
    # Convite expira 60 dias apos criacao
    if resposta.criado_em < timezone.now() - timedelta(days=60):
        raise Http404('Link expirado.')
    return resposta


def _validar_respostas(schema: list, post_data) -> tuple[dict, list[str]]:
    """Valida respostas contra schema_json. Retorna (dict_respostas, erros)."""
    respostas = {}
    erros = []

    for campo in schema:
        key = campo.get('key')
        tipo = campo.get('tipo', 'text')
        label = campo.get('label', key)
        obrigatorio = campo.get('obrigatorio', False)

        if tipo == 'checkboxes':
            valor = post_data.getlist(key)
        else:
            valor = (post_data.get(key) or '').strip()

        if obrigatorio and not valor:
            erros.append(f'"{label}" e obrigatorio.')
            continue

        # validacoes especificas por tipo
        if tipo == 'email' and valor and '@' not in valor:
            erros.append(f'"{label}" deve ser email valido.')
            continue
        if tipo == 'number' and valor:
            try:
                float(valor)
            except (TypeError, ValueError):
                erros.append(f'"{label}" deve ser numero.')
                continue
        if tipo in ('select', 'scale') and valor:
            opcoes = campo.get('opcoes') or []
            if opcoes and valor not in opcoes:
                erros.append(f'"{label}": opcao invalida.')
                continue
        if tipo == 'checkboxes' and valor:
            opcoes = set(campo.get('opcoes') or [])
            if opcoes and not all(v in opcoes for v in valor):
                erros.append(f'"{label}": opcao(es) invalida(s).')
                continue
        if tipo == 'bool':
            valor = valor in ('on', 'true', '1', 'sim')

        respostas[key] = valor

    return respostas, erros


def _renderizar(request, resposta: RespostaAnamnese, erros=None):
    template = (
        'agenda/pesquisa.html'
        if resposta.formulario.tipo == 'PESQUISA'
        else 'agenda/anamnese_publica.html'
    )
    return render(request, template, {
        'resposta': resposta,
        'formulario': resposta.formulario,
        'schema': resposta.formulario.schema_json or [],
        'cliente': resposta.cliente,
        'atendimento': resposta.atendimento,
        'erros': erros or [],
        'respostas_anteriores': resposta.respostas_json or {},
    })


def _gravar_resposta(request, resposta: RespostaAnamnese):
    schema = resposta.formulario.schema_json or []
    respostas, erros = _validar_respostas(schema, request.POST)
    if erros:
        return _renderizar(request, resposta, erros=erros)

    resposta.respostas_json = respostas
    resposta.respondida_em = timezone.now()
    resposta.save(update_fields=['respostas_json', 'respondida_em'])

    LogAuditoria.objects.create(
        usuario=None,
        acao=f'Form {resposta.formulario.tipo} respondido (cliente {resposta.cliente_id})',
        tabela_afetada='resposta_anamnese',
        id_registro_afetado=resposta.pk,
    )

    if resposta.formulario.tipo == 'PESQUISA':
        return redirect('aranha:pesquisa_obrigado')
    return redirect('aranha:anamnese_obrigado')


@require_http_methods(['GET', 'POST'])
def anamnese_publica(request, token: str):
    """Form anamnese pre-atendimento (link via email/WhatsApp)."""
    resposta = _get_resposta_or_404(token, tipo_esperado='ANAMNESE')

    if resposta.respondida:
        messages.info(request, 'Voce ja respondeu este formulario. Obrigado!')
        return redirect('aranha:anamnese_obrigado')

    if request.method == 'POST':
        return _gravar_resposta(request, resposta)
    return _renderizar(request, resposta)


@require_http_methods(['GET', 'POST'])
def pesquisa_publica(request, token: str):
    """Form pesquisa pos-atendimento (link via WhatsApp 2h apos REALIZADO)."""
    resposta = _get_resposta_or_404(token, tipo_esperado='PESQUISA')

    if resposta.respondida:
        messages.info(request, 'Voce ja respondeu esta pesquisa. Obrigado!')
        return redirect('aranha:pesquisa_obrigado')

    if request.method == 'POST':
        return _gravar_resposta(request, resposta)
    return _renderizar(request, resposta)


def anamnese_obrigado(request):
    return render(request, 'agenda/anamnese_obrigado.html')


def pesquisa_obrigado(request):
    return render(request, 'agenda/pesquisa_obrigado.html')
