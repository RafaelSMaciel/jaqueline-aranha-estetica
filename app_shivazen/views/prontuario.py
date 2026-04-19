"""Views para prontuario, anamnese e termos de consentimento."""
import logging
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from ..models import (
    AceitePrivacidade,
    AnotacaoSessao,
    AssinaturaTermoProcedimento,
    Atendimento,
    Cliente,
    Prontuario,
    ProntuarioPergunta,
    ProntuarioResposta,
    VersaoTermo,
)
from ..utils.audit import registrar_log

logger = logging.getLogger(__name__)


def _usuario_pode_ver_prontuario(user, cliente):
    """Admin OU profissional que ja atendeu o cliente."""
    if not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    prof = getattr(user, 'profissional', None)
    if not prof or not prof.ativo:
        return False
    return Atendimento.objects.filter(cliente=cliente, profissional=prof).exists()


from ..utils.security import client_ip


def _ip_request(request):
    return client_ip(request) or None


def prontuario_access_required(view_func):
    """Autoriza acesso ao prontuario e registra cada leitura/edicao em LogAuditoria (LGPD)."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, cliente_id, *args, **kwargs):
        cliente = get_object_or_404(Cliente, pk=cliente_id)
        if not _usuario_pode_ver_prontuario(request.user, cliente):
            registrar_log(
                request.user, f'Acesso NEGADO a prontuario de {cliente.nome_completo}',
                tabela='prontuario', id_registro=cliente.pk,
                detalhes={'ip': _ip_request(request), 'motivo': 'sem vinculo de atendimento'},
            )
            raise PermissionDenied('Acesso ao prontuario restrito a admin ou profissional que ja atendeu o cliente.')
        registrar_log(
            request.user, f'Acessou prontuario de {cliente.nome_completo}',
            tabela='prontuario', id_registro=cliente.pk,
            detalhes={'ip': _ip_request(request), 'view': view_func.__name__},
        )
        request._cliente_prontuario = cliente
        return view_func(request, cliente_id, *args, **kwargs)
    return _wrapped


@prontuario_access_required
def prontuario_detalhe(request, cliente_id):
    """Detalhe do prontuario de um cliente com formulario de anamnese."""
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    prontuario, created = Prontuario.objects.get_or_create(cliente=cliente)
    perguntas = ProntuarioPergunta.objects.filter(ativa=True)

    # Respostas existentes
    respostas = {
        r.pergunta_id: r
        for r in ProntuarioResposta.objects.filter(prontuario=prontuario)
    }

    # Historico de atendimentos com anotacoes
    atendimentos = Atendimento.objects.filter(
        cliente=cliente
    ).select_related('profissional', 'procedimento').prefetch_related(
        'anotacoes'
    ).order_by('-data_hora_inicio')[:30]

    # Termos assinados
    aceites = AceitePrivacidade.objects.filter(
        cliente=cliente
    ).select_related('versao_termo').order_by('-criado_em')

    assinaturas = AssinaturaTermoProcedimento.objects.filter(
        cliente=cliente
    ).select_related('versao_termo', 'atendimento').order_by('-criado_em')

    context = {
        'cliente': cliente,
        'prontuario': prontuario,
        'perguntas': perguntas,
        'respostas': respostas,
        'atendimentos': atendimentos,
        'aceites': aceites,
        'assinaturas': assinaturas,
    }
    return render(request, 'painel/prontuario_detalhe.html', context)


@prontuario_access_required
def prontuario_salvar(request, cliente_id):
    """Salva dados de anamnese do prontuario."""
    if request.method != 'POST':
        return redirect('shivazen:prontuario_detalhe', cliente_id=cliente_id)

    cliente = get_object_or_404(Cliente, pk=cliente_id)
    prontuario, _ = Prontuario.objects.get_or_create(cliente=cliente)

    # Campos de texto livre da anamnese
    prontuario.alergias = request.POST.get('alergias', '').strip()
    prontuario.contraindicacoes = request.POST.get('contraindicacoes', '').strip()
    prontuario.historico_saude = request.POST.get('historico_saude', '').strip()
    prontuario.medicamentos_uso = request.POST.get('medicamentos_uso', '').strip()
    prontuario.observacoes_gerais = request.POST.get('observacoes_gerais', '').strip()
    prontuario.save()

    # Respostas do questionario
    perguntas = ProntuarioPergunta.objects.filter(ativa=True)
    for pergunta in perguntas:
        resp, _ = ProntuarioResposta.objects.get_or_create(
            prontuario=prontuario, pergunta=pergunta
        )
        if pergunta.tipo_resposta == 'BOOLEAN':
            resp.resposta_boolean = request.POST.get(f'pergunta_{pergunta.pk}') == 'sim'
        else:
            resp.resposta_texto = request.POST.get(f'pergunta_{pergunta.pk}', '').strip()
        resp.save()

    registrar_log(request.user, f'Atualizou prontuario de {cliente.nome_completo}', 'prontuario', prontuario.pk)
    messages.success(request, 'Prontuario atualizado com sucesso!')
    return redirect('shivazen:prontuario_detalhe', cliente_id=cliente_id)


@login_required
def anotacao_sessao_salvar(request, atendimento_id):
    """Adiciona anotacao clinica a um atendimento — admin ou profissional do atendimento."""
    if request.method != 'POST':
        return JsonResponse({'erro': 'Metodo nao permitido'}, status=405)

    atendimento = get_object_or_404(Atendimento, pk=atendimento_id)
    user = request.user
    autorizado = user.is_staff
    if not autorizado:
        prof = getattr(user, 'profissional', None)
        autorizado = bool(prof and prof.ativo and atendimento.profissional_id == prof.pk)
    if not autorizado:
        registrar_log(
            user, f'Tentativa NEGADA de anotar atendimento {atendimento_id}',
            tabela='anotacao_sessao', id_registro=atendimento_id,
            detalhes={'ip': _ip_request(request)},
        )
        return JsonResponse({'erro': 'Sem permissao'}, status=403)

    texto = request.POST.get('texto', '').strip()

    if not texto:
        return JsonResponse({'erro': 'Texto obrigatorio'}, status=400)

    anotacao = AnotacaoSessao.objects.create(
        atendimento=atendimento,
        usuario=request.user,
        texto=texto,
    )

    return JsonResponse({
        'sucesso': True,
        'anotacao_id': anotacao.pk,
        'texto': anotacao.texto,
        'data': anotacao.criado_em.strftime('%d/%m/%Y %H:%M'),
    })
