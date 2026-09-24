"""Views publicas — preenchimento de formularios via link mago.

Cliente recebe link via email/WhatsApp:
  /anamnese/<token>/  → form pre-atendimento
  /pesquisa/<token>/  → form pos-atendimento

Mesma view, branching por formulario.tipo.
Sem autenticacao — token urlsafe(32) faz controle de acesso.
"""
from datetime import timedelta

from django.contrib import messages
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from ..models import AceiteTermo, RespostaAnamnese, VersaoTermo
from ..models.sistema import LogAuditoria
from ..services.anamnese import validar_respostas
from ..utils.audit import registrar_log

MSG_SEM_CONSENTIMENTO = (
    'Para enviar a ficha, marque a autorização de uso das suas informações de saúde.'
)


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


def _valores_postados(schema: list, post_data) -> dict:
    """POST -> {key: valor} do schema (checkboxes como lista: QueryDict.get daria so o ultimo).

    Entrada do validador compartilhado e o que re-exibimos no erro (nao apaga a ficha).
    """
    valores = {}
    for campo in schema:
        if not isinstance(campo, dict) or not campo.get('key'):
            continue
        key = campo['key']
        if campo.get('tipo') == 'checkboxes':
            valores[key] = post_data.getlist(key)
        else:
            valores[key] = post_data.get(key, '')
    return valores


def _renderizar(request, resposta: RespostaAnamnese, erros=None, valores=None):
    template = (
        'agenda/pesquisa.html'
        if resposta.formulario.tipo == 'PESQUISA'
        else 'agenda/anamnese_publica.html'
    )
    valores = valores or {}
    # texto exibido = o da versao SAUDE que o POST grava no aceite (art. 11)
    texto_saude = ''
    if resposta.formulario.tipo != 'PESQUISA':
        _termo_saude, texto_saude = VersaoTermo.texto_saude_vigente()
    # template nao indexa dict por variavel: valor vai junto de cada campo
    schema = [
        dict(c, valor=valores.get(c.get('key'), [] if c.get('tipo') == 'checkboxes' else ''))
        for c in (resposta.formulario.schema_json or [])
    ]
    return render(request, template, {
        'resposta': resposta,
        'formulario': resposta.formulario,
        'schema': schema,
        'cliente': resposta.cliente,
        'atendimento': resposta.atendimento,
        'erros': erros or [],
        'texto_consentimento_saude': texto_saude,
    })


def _gravar_resposta(request, resposta: RespostaAnamnese):
    schema = resposta.formulario.schema_json or []
    valores = _valores_postados(schema, request.POST)
    # Mesmo validador do booking: bool 'sim'/'nao' -> True/False, opcoes e tamanhos conferidos
    respostas, erros = validar_respostas(schema, valores)
    eh_ficha_saude = resposta.formulario.tipo != 'PESQUISA'
    if eh_ficha_saude and request.POST.get('consent_dados_saude') != 'on':
        erros = [*erros, MSG_SEM_CONSENTIMENTO]
    if erros:
        return _renderizar(request, resposta, erros=erros, valores=valores)

    with transaction.atomic():
        resposta.respostas_json = respostas
        resposta.respondida_em = timezone.now()
        resposta.save(update_fields=['respostas_json', 'respondida_em'])

        LogAuditoria.objects.create(
            usuario=None,
            acao=f'Form {resposta.formulario.tipo} respondido (cliente {resposta.cliente_id})',
            tabela='resposta_anamnese',
            registro_id=resposta.pk,
        )

        if eh_ficha_saude:
            # Consentimento art. 11 = aceite da versao SAUDE vigente (prova no
            # banco: IP, user-agent, SHA-256) + trilha com o texto consentido.
            termo_saude, texto_saude = VersaoTermo.texto_saude_vigente()
            aceite = AceiteTermo.registrar(
                resposta.cliente, termo_saude, request, resposta.atendimento,
            )
            registrar_log(
                None, 'Consentimento de dados de saude (LGPD art. 11) na ficha publica',
                'resposta_anamnese', resposta.pk,
                detalhes={
                    'cliente_id': resposta.cliente_id, 'texto': texto_saude,
                    'aceite_id': getattr(aceite, 'pk', None),
                },
                request=request,
            )

    if resposta.formulario.tipo == 'PESQUISA':
        return redirect('aranha:pesquisa_obrigado')
    return redirect('aranha:anamnese_obrigado')


@never_cache  # ficha de saude (art. 11): nada em cache/bfcache (tablet da recepcao)
@require_http_methods(['GET', 'POST'])
def anamnese_publica(request, token: str):
    """Form anamnese pre-atendimento (link via email/WhatsApp)."""
    resposta = _get_resposta_or_404(token, tipo_esperado='ANAMNESE')

    if resposta.respondida:
        messages.info(request, 'Você já respondeu este formulário. Obrigado!')
        return redirect('aranha:anamnese_obrigado')

    if request.method == 'POST':
        return _gravar_resposta(request, resposta)
    return _renderizar(request, resposta)


@never_cache
@require_http_methods(['GET', 'POST'])
def pesquisa_publica(request, token: str):
    """Form pesquisa pos-atendimento (link via WhatsApp 2h apos REALIZADO)."""
    resposta = _get_resposta_or_404(token, tipo_esperado='PESQUISA')

    if resposta.respondida:
        messages.info(request, 'Você já respondeu esta pesquisa. Obrigado!')
        return redirect('aranha:pesquisa_obrigado')

    if request.method == 'POST':
        return _gravar_resposta(request, resposta)
    return _renderizar(request, resposta)


def anamnese_obrigado(request):
    return render(request, 'agenda/anamnese_obrigado.html')


def pesquisa_obrigado(request):
    return render(request, 'agenda/pesquisa_obrigado.html')
