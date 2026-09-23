"""Views para prontuario, anamnese e termos de consentimento."""
import logging
from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache

from ..models import (
    AnotacaoSessao,
    AceiteTermo,
    Atendimento,
    Cliente,
    Prontuario,
)
from ..utils.audit import registrar_log
from ..utils.datas import fmt_local
from ..utils.fichas import fichas_do_cliente
from ..utils.saude import alertas_saude

logger = logging.getLogger(__name__)

# Vinculo profissional -> prontuario (a dona pode ajustar as janelas).
JANELA_REALIZADO_DIAS = 180   # atendeu a cliente nos ultimos N dias
JANELA_FUTURO_DIAS = 60       # tem horario marcado nos proximos N dias

CAMPOS_PRONTUARIO = (
    'alergias', 'contraindicacoes', 'historico_saude',
    'medicamentos_uso', 'observacoes_gerais',
)


def _perguntas_configuradas():
    """Schema do questionario do prontuario — lista [{chave, texto, tipo}].

    Vive em Configuracao chave='prontuario_perguntas' (JSON). tipo:
    'TEXTO' | 'BOOLEAN'. Substitui o EAV (remodelagem v2.1 fase 5).
    """
    import json as _json
    from ..models import Configuracao
    # iexact: a tela Configuracoes forcava upper() em chaves antigas
    cfg = Configuracao.objects.filter(chave__iexact='prontuario_perguntas').order_by('pk').first()
    if not cfg or not cfg.valor:
        return []
    try:
        perguntas = _json.loads(cfg.valor)
        return perguntas if isinstance(perguntas, list) else []
    except ValueError:
        logger.warning('prontuario_perguntas_json_invalido')
        return []


def _vinculo(prof, cliente, escrita=False):
    """Atendimento que da ao profissional acesso ao prontuario, ou None.

    Leitura: REALIZADO nos ultimos JANELA_REALIZADO_DIAS, ou PENDENTE/AGENDADO/
    CONFIRMADO entre ontem e +JANELA_FUTURO_DIAS (a profissional precisa ver os
    alertas de saude antes de aprovar um pedido). Escrita: o mesmo, sem PENDENTE.
    CANCELADO/REAGENDADO/FALTOU nunca dao vinculo.
    """
    if prof is None or not prof.ativo:
        return None
    agora = timezone.now()
    ativos = [Atendimento.STATUS_AGENDADO, Atendimento.STATUS_CONFIRMADO]
    if not escrita:
        ativos.append(Atendimento.STATUS_PENDENTE)
    return (
        Atendimento.objects.filter(cliente=cliente, profissional=prof)
        .filter(
            Q(status=Atendimento.STATUS_REALIZADO,
              data_hora_inicio__gte=agora - timedelta(days=JANELA_REALIZADO_DIAS))
            | Q(status__in=ativos,
                data_hora_inicio__gte=agora - timedelta(days=1),
                data_hora_inicio__lte=agora + timedelta(days=JANELA_FUTURO_DIAS))
        )
        .order_by('-data_hora_inicio')
        .first()
    )


def _profissional_do_usuario(user):
    prof = getattr(user, 'profissional', None)
    return prof if prof is not None and prof.ativo else None


def prontuario_access_required(escrita=False):
    """Autoriza acesso ao prontuario e registra cada leitura/negativa em LogAuditoria (LGPD).

    Convencao da trilha: tabela='prontuario', registro_id = cliente.pk (1:1),
    sem nome da cliente no texto da acao (o log sobrevive a anonimizacao).
    """
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, cliente_id, *args, **kwargs):
            cliente = get_object_or_404(Cliente, pk=cliente_id)
            user = request.user
            vinculo = None
            if not user.is_staff:
                vinculo = _vinculo(_profissional_do_usuario(user), cliente, escrita=escrita)
                if vinculo is None:
                    registrar_log(
                        user, 'Acesso NEGADO a prontuario',
                        tabela='prontuario', id_registro=cliente.pk,
                        detalhes={
                            'view': view_func.__name__,
                            'escrita': escrita,
                            'motivo': 'sem vinculo de atendimento',
                        },
                        request=request,
                    )
                    raise PermissionDenied(
                        'Acesso ao prontuário restrito à administração ou ao profissional '
                        'com atendimento recente ou marcado com a cliente.'
                    )
            if not escrita:
                detalhes = {'view': view_func.__name__}
                if vinculo is not None:
                    detalhes['atendimento_vinculo'] = vinculo.pk
                registrar_log(
                    user, 'Acessou prontuario',
                    tabela='prontuario', id_registro=cliente.pk,
                    detalhes=detalhes, request=request,
                )
            request._cliente_prontuario = cliente
            request._vinculo_prontuario = vinculo
            return view_func(request, cliente_id, *args, **kwargs)
        return _wrapped
    return decorator


def _versao(prontuario) -> str:
    """Marca de versao p/ trava otimista (atualizado_em); '' se ainda nao existe."""
    if prontuario.pk is None or prontuario.atualizado_em is None:
        return ''
    return prontuario.atualizado_em.isoformat()


@never_cache  # dado de saude: nada de bfcache/cache de disco apos o logout
@prontuario_access_required()
def prontuario_detalhe(request, cliente_id):
    """Detalhe do prontuario de um cliente com formulario de anamnese."""
    cliente = request._cliente_prontuario
    user = request.user
    # GET nao cria Prontuario: antes o get_or_create gerava ficha vazia so por abrir
    # a pagina (selo 'Prontuário' falso e cliente fora da purga LGPD).
    prontuario = Prontuario.objects.filter(cliente=cliente).first() or Prontuario(cliente=cliente)
    perguntas = _perguntas_configuradas()
    respostas = prontuario.respostas_extras or {}

    # Historico de atendimentos com anotacoes (paginado; autor sem N+1)
    atendimentos_qs = (
        Atendimento.objects.filter(cliente=cliente)
        .select_related('profissional', 'procedimento')
        .prefetch_related(Prefetch(
            'anotacoes',
            queryset=AnotacaoSessao.objects.select_related('autor').order_by('criado_em'),
        ))
        .order_by('-data_hora_inicio')
    )
    atendimentos = Paginator(atendimentos_qs, 30).get_page(request.GET.get('page'))
    prof = None if user.is_staff else _profissional_do_usuario(user)
    for atend in atendimentos:
        # Nota so no proprio atendimento (anotacao_sessao_salvar recusa os demais)
        atend.pode_anotar = user.is_staff or (prof is not None and atend.profissional_id == prof.pk)

    # Termos assinados — AceiteTermo unifica LGPD e procedimento (fase 6);
    # separa pelo tipo da versao p/ nao listar cada aceite 2x com rotulo errado.
    termos = AceiteTermo.objects.filter(
        cliente=cliente
    ).select_related('versao_termo', 'atendimento').order_by('-criado_em')
    aceites = termos.filter(versao_termo__tipo='LGPD')
    assinaturas = termos.filter(versao_termo__tipo='PROCEDIMENTO')

    pode_editar = user.is_staff or _vinculo(prof, cliente, escrita=True) is not None

    context = {
        'cliente': cliente,
        'prontuario': prontuario,
        'versao_prontuario': _versao(prontuario),
        'perguntas': perguntas,
        'respostas': respostas,
        'alertas': alertas_saude(cliente),
        'fichas': fichas_do_cliente(cliente),
        'atendimentos': atendimentos,
        'aceites': aceites,
        'assinaturas': assinaturas,
        'pode_editar': pode_editar,
        # Profissional nao ve o menu do painel (links so de staff): usa o shell do portal
        'base_template': 'painel/base_v2.html' if user.is_staff else 'painel/prontuario_portal_base.html',
    }
    return render(request, 'painel/prontuario_detalhe.html', context)


@never_cache
@prontuario_access_required(escrita=True)
def prontuario_salvar(request, cliente_id):
    """Salva dados de anamnese do prontuario.

    So altera os campos presentes no POST (POST parcial nao apaga alergias),
    recusa gravacao sobre versao desatualizada (duas pessoas editando) e
    registra na trilha apenas os NOMES dos campos alterados.
    """
    if request.method != 'POST':
        return redirect('aranha:prontuario_detalhe', cliente_id=cliente_id)

    cliente = request._cliente_prontuario
    vinculo = request._vinculo_prontuario

    with transaction.atomic():
        prontuario, criado = Prontuario.objects.select_for_update().get_or_create(cliente=cliente)

        versao_enviada = request.POST.get('versao')
        versao_atual = '' if criado else _versao(prontuario)
        if versao_enviada is not None and versao_enviada != versao_atual:
            messages.error(
                request,
                'O prontuário foi alterado por outra pessoa enquanto você editava. '
                'Nada foi salvo: revise os dados atuais e salve de novo.',
            )
            return redirect('aranha:prontuario_detalhe', cliente_id=cliente_id)

        alterados = []
        for campo in CAMPOS_PRONTUARIO:
            if campo not in request.POST:
                continue
            novo = request.POST.get(campo, '').strip()
            if (getattr(prontuario, campo) or '') != novo:
                setattr(prontuario, campo, novo)
                alterados.append(campo)

        # Respostas do questionario configuravel -> JSONB (so as perguntas enviadas)
        respostas = dict(prontuario.respostas_extras or {})
        for pergunta in _perguntas_configuradas():
            chave = pergunta.get('chave')
            nome = f'pergunta_{chave}'
            if not chave or nome not in request.POST:
                continue
            valor = request.POST.get(nome)
            if pergunta.get('tipo') == 'BOOLEAN':
                if valor not in ('sim', 'nao'):
                    continue
                novo = (valor == 'sim')
                if respostas.get(chave) is novo:
                    continue
            else:
                novo = (valor or '').strip()
                if (respostas.get(chave) or '') == novo:
                    continue
            respostas[chave] = novo
            alterados.append(nome)

        if not alterados:
            messages.info(request, 'Nenhuma alteração no prontuário.')
            return redirect('aranha:prontuario_detalhe', cliente_id=cliente_id)

        prontuario.respostas_extras = respostas
        prontuario.save()  # um unico save: atualizado_em = nova versao

    detalhes = {'campos_alterados': alterados}
    if vinculo is not None:
        detalhes['atendimento_vinculo'] = vinculo.pk
    registrar_log(
        request.user, 'Atualizou prontuario', 'prontuario', cliente.pk,
        detalhes=detalhes, request=request,
    )
    messages.success(request, 'Prontuário atualizado com sucesso!')
    return redirect('aranha:prontuario_detalhe', cliente_id=cliente_id)


@login_required
def anotacao_sessao_salvar(request, atendimento_id):
    """Adiciona anotacao clinica a um atendimento — admin ou profissional do atendimento."""
    if request.method != 'POST':
        return JsonResponse({'erro': 'Método não permitido'}, status=405)

    atendimento = get_object_or_404(Atendimento, pk=atendimento_id)
    user = request.user
    autorizado = user.is_staff
    if not autorizado:
        prof = _profissional_do_usuario(user)
        autorizado = bool(prof and atendimento.profissional_id == prof.pk)
    if not autorizado:
        registrar_log(
            user, 'Tentativa NEGADA de anotar atendimento',
            tabela='anotacao_sessao', id_registro=atendimento_id,
            request=request,
        )
        return JsonResponse({'erro': 'Sem permissão'}, status=403)

    texto = request.POST.get('texto', '').strip()

    if not texto:
        return JsonResponse({'erro': 'Texto obrigatório'}, status=400)

    anotacao = AnotacaoSessao.objects.create(
        atendimento=atendimento,
        autor=request.user,
        texto=texto,
    )
    registrar_log(
        user, 'Criou anotacao', 'anotacao_sessao', anotacao.pk,
        detalhes={'atendimento': atendimento.pk, 'cliente': atendimento.cliente_id},
        request=request,
    )

    return JsonResponse({
        'sucesso': True,
        'anotacao_id': anotacao.pk,
        'texto': anotacao.texto,
        'data': fmt_local(anotacao.criado_em),  # hora local, nao UTC
    })
