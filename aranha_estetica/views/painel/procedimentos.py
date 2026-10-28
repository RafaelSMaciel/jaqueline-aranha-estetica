"""Catalogo de procedimentos — lista, cria e edita (preco base versionado)."""
import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from ...decorators import staff_required
from ...excecoes import RegraDeNegocio
from ...forms.painel import ProcedimentoForm, avisar_erros, erros_por_campo
from ...models import Atendimento, Habilitacao, Preco, Procedimento, Profissional
from ...utils.auditoria import registrar_log
from ...utils.datas import hoje

logger = logging.getLogger(__name__)


def _preco_base_vigente_map(procedimento_ids):
    """{procedimento_id: Decimal} do preco base VIGENTE (vigente_desde <= hoje).

    Preco e versionado por vigente_desde: pega a vigencia mais recente ja em
    vigor (ignora reajustes futuros agendados)."""
    mapa = {}
    for p in Preco.objects.filter(
        procedimento_id__in=procedimento_ids,
        profissional__isnull=True,
        vigente_desde__lte=hoje(),
    ).order_by('procedimento_id', '-vigente_desde', '-pk'):
        mapa.setdefault(p.procedimento_id, p.valor)
    return mapa


def _sincronizar_habilitacoes(proc, profissionais):
    """Habilitacoes do procedimento = os profissionais marcados. Tirar quem ainda tem atendimento
    futuro deste procedimento e recusado (RegraDeNegocio): reagende/cancele antes."""
    novos = {p.pk for p in profissionais}
    atuais = set(Habilitacao.objects.filter(procedimento=proc).values_list('profissional_id', flat=True))
    sair = atuais - novos
    if sair:
        presos = (Atendimento.objects.filter(
            procedimento=proc, profissional_id__in=sair, status__in=Atendimento.STATUS_ATIVOS,
            data_hora_inicio__gte=timezone.now(),
        ).select_related('profissional').order_by('data_hora_inicio'))
        if presos.exists():
            nomes = sorted({a.profissional.nome for a in presos})
            raise RegraDeNegocio(
                f'{", ".join(nomes)} ainda tem {presos.count()} atendimento(s) futuro(s) de {proc.nome}: '
                'reagende ou cancele antes de tirar a habilitação.', codigo='habilitacao_em_uso')
        Habilitacao.objects.filter(procedimento=proc, profissional_id__in=sair).delete()
    for pk in novos - atuais:
        Habilitacao.objects.create(procedimento=proc, profissional_id=pk)


POR_PAGINA = 30


def _valores_digitados(post):
    """O que veio no POST, no formato que o template usa p/ preencher o formulario de volta."""
    return {
        'nome': post.get('nome', ''), 'descricao': post.get('descricao', ''),
        'duracao_minutos': post.get('duracao_minutos', ''), 'categoria': post.get('categoria', ''),
        'preco': post.get('preco', ''), 'ativo': post.get('ativo') == '1',
        'habilitados': {int(v) for v in post.getlist('profissionais') if v.isascii() and v.isdigit()},
    }


def _contexto_lista(request, editar_pk=None):
    """Contexto da lista. editar_pk: abre a pagina onde esta esse procedimento (reabrir o modal)."""
    procedimentos = list(
        Procedimento.objects.all().order_by('-ativo', 'categoria', 'nome')
    )

    # Preco base vigente (profissional=NULL) por procedimento — um único query
    preco_map = _preco_base_vigente_map([pr.pk for pr in procedimentos])
    habilitados = {}
    for proc_id, prof_id in Habilitacao.objects.filter(profissional__ativo=True).values_list(
            'procedimento_id', 'profissional_id'):
        habilitados.setdefault(proc_id, set()).add(prof_id)
    for proc in procedimentos:
        proc.preco_base = preco_map.get(proc.pk)
        proc.habilitados = habilitados.get(proc.pk, set())
        # o que o modal de edicao mostra (o salvo; no envio recusado, o digitado)
        proc.valores = {
            'nome': proc.nome, 'descricao': proc.descricao or '', 'duracao_minutos': proc.duracao_minutos,
            'categoria': proc.categoria, 'preco': proc.preco_base, 'ativo': proc.ativo,
            'habilitados': proc.habilitados,
        }
        proc.erros = {}

    paginator = Paginator(procedimentos, POR_PAGINA)
    page = request.GET.get('page', 1)
    if editar_pk is not None:
        pks = [pr.pk for pr in procedimentos]
        page = pks.index(editar_pk) // POR_PAGINA + 1 if editar_pk in pks else 1
    procs_page = paginator.get_page(page)

    return {
        'procedimentos': procs_page,
        'categorias': Procedimento.CATEGORIA_CHOICES,
        'profissionais': Profissional.objects.filter(ativo=True).order_by('nome'),
    }


def _devolver_com_erro(request, erros, post, editar=None):
    """Envio recusado: a lista volta SEM redirect, com o que foi digitado e o erro em cada campo.

    Antes o redirect fechava o modal e perdia tudo (e o aviso nao dizia qual campo). editar = o
    procedimento do modal de edicao (reaberto pelo data-erro no template); None = formulario de novo."""
    contexto = _contexto_lista(request, editar_pk=editar.pk if editar else None)
    if editar is None:
        contexto['novo'] = {'valores': _valores_digitados(post), 'erros': erros}
    else:
        for proc in contexto['procedimentos']:
            if proc.pk == editar.pk:
                valores = _valores_digitados(post)
                if not post.get('profissionais_enviado'):   # form sem o grupo: habilitacoes intocadas
                    valores['habilitados'] = proc.habilitados
                proc.valores, proc.erros, proc.reabrir = valores, erros, True
    return render(request, 'painel/procedimentos.html', contexto)


@staff_required
def admin_procedimentos(request):
    """Lista e gerencia procedimentos."""
    return render(request, 'painel/procedimentos.html', _contexto_lista(request))


@staff_required
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_criar_procedimento(request):
    """Cria procedimento via POST (ProcedimentoForm)."""
    if request.method != 'POST':
        return redirect('aranha:admin_procedimentos')

    form = ProcedimentoForm(request.POST)
    if not form.is_valid():
        avisar_erros(request, form)
        return _devolver_com_erro(request, erros_por_campo(form), request.POST)
    d = form.cleaned_data
    try:
        with transaction.atomic():
            proc = Procedimento.objects.create(
                nome=d['nome'], descricao=d['descricao'].strip(), duracao_minutos=d['duracao_minutos'],
                categoria=d['categoria'], ativo=True,
            )
            # Preco base (sem profissional), vigente a partir de hoje
            if d['preco'] is not None:
                Preco.objects.create(procedimento=proc, valor=d['preco'], vigente_desde=hoje())
            _sincronizar_habilitacoes(proc, d['profissionais'])

        registrar_log(request.user, f'Criou procedimento: {proc.nome}', 'procedimento', proc.pk, request=request)
        messages.success(request, f'Procedimento "{proc.nome}" criado!')
        if not d['profissionais']:
            messages.warning(request, 'Nenhum profissional marcado: o procedimento não aparece no agendamento '
                             'até alguém ser habilitado.')
    except (DatabaseError, ValidationError) as e:
        logger.error(f'Erro ao criar procedimento: {e}', exc_info=True)
        messages.error(request, 'Erro ao criar procedimento.')

    return redirect('aranha:admin_procedimentos')


@staff_required
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_editar_procedimento(request, pk):
    """Edita procedimento via POST (ProcedimentoForm).

    Preco e versionado (Preco.vigente_desde): mudar o valor cria uma nova
    vigencia a partir de hoje em vez de sobrescrever o historico (se ja
    houver vigencia de hoje, ela e ajustada).
    """
    proc = get_object_or_404(Procedimento, pk=pk)
    if request.method != 'POST':
        return redirect('aranha:admin_procedimentos')

    dados = request.POST.copy()
    dados.setdefault('nome', proc.nome)   # campo ausente = mantem o nome
    form = ProcedimentoForm(dados, instance=proc)
    if not form.is_valid():
        avisar_erros(request, form)
        return _devolver_com_erro(request, erros_por_campo(form), dados, editar=proc)
    d = form.cleaned_data
    try:
        with transaction.atomic():
            proc.nome = d['nome']
            proc.descricao = d['descricao'].strip()
            proc.duracao_minutos = d['duracao_minutos']
            proc.categoria = d['categoria']
            proc.ativo = d['ativo']
            proc.save()

            if d['preco'] is not None:
                vigente = _preco_base_vigente_map([proc.pk]).get(proc.pk)
                if vigente is None or vigente != d['preco']:
                    Preco.objects.update_or_create(
                        procedimento=proc, profissional=None, vigente_desde=hoje(),
                        defaults={'valor': d['preco']},
                    )
            if dados.get('profissionais_enviado'):   # grupo de checkboxes veio no formulario
                _sincronizar_habilitacoes(proc, d['profissionais'])

        registrar_log(request.user, f'Editou procedimento: {proc.nome}', 'procedimento', proc.pk, request=request)
        messages.success(request, f'Procedimento "{proc.nome}" atualizado!')
    except RegraDeNegocio as e:
        # habilitacao em uso: nada foi salvo (atomic); o modal volta aberto com o que foi marcado
        messages.error(request, e.mensagem)
        return _devolver_com_erro(request, {'profissionais': e.mensagem}, dados, editar=proc)
    except (DatabaseError, ValidationError) as e:
        logger.error(f'Erro ao editar procedimento: {e}', exc_info=True)
        messages.error(request, 'Erro ao editar procedimento.')

    return redirect('aranha:admin_procedimentos')
