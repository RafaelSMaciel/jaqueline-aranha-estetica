"""Catalogo de procedimentos — lista, cria e edita (preco base versionado).

A validacao e o ProcedimentoForm (ModelForm, forms/cadastros); preco base e quem realiza sao regras de
services/catalogo. A lista mostra o preco pela mesma regra do site (utils/precos.preco_base_map) e
calcula preco e habilitacoes so da pagina aberta.
"""
import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError, transaction
from django.shortcuts import get_object_or_404, redirect, render

from ...decorators import escrita_painel, staff_required
from ...excecoes import RegraDeNegocio
from ...forms.cadastros import ProcedimentoForm
from ...forms.painel import avisar_erros, erros_por_campo
from ...models import Habilitacao, Procedimento, Profissional
from ...services import catalogo
from ...utils.auditoria import registrar_log
from ...utils.parse import id_int
from ...utils.precos import preco_base_map

logger = logging.getLogger(__name__)

POR_PAGINA = 30


def _valores_digitados(form):
    """O que veio no POST, no formato que o template usa p/ preencher o formulario de volta (texto cru;
    o checkbox pelo BooleanField do form, que nunca recusa)."""
    post = form.data
    return {
        'nome': post.get('nome', ''), 'descricao': post.get('descricao', ''),
        'duracao_minutos': post.get('duracao_minutos', ''), 'categoria': post.get('categoria', ''),
        'preco': post.get('preco', ''), 'ativo': bool(form.cleaned_data.get('ativo')),
        'habilitados': {pk for pk in map(id_int, post.getlist('profissionais')) if pk is not None},
    }


def _contexto_lista(request, editar_pk=None):
    """Contexto da lista. editar_pk: abre a pagina onde esta esse procedimento (reabrir o modal)."""
    procedimentos = Procedimento.objects.order_by('-ativo', 'categoria', 'nome', 'pk')
    page = request.GET.get('page', 1)
    if editar_pk is not None:
        pks = list(procedimentos.values_list('pk', flat=True))
        page = pks.index(editar_pk) // POR_PAGINA + 1 if editar_pk in pks else 1
    pagina = Paginator(procedimentos, POR_PAGINA).get_page(page)
    pagina.object_list = itens = list(pagina.object_list)

    # So da pagina: preco pela regra do site (base; sem base, o menor por profissional) e habilitacoes
    preco_map = preco_base_map(itens)
    habilitados = {}
    for proc_id, prof_id in Habilitacao.objects.filter(
            procedimento__in=itens, profissional__ativo=True).values_list('procedimento_id', 'profissional_id'):
        habilitados.setdefault(proc_id, set()).add(prof_id)
    for proc in itens:
        proc.preco_base = preco_map.get(proc.pk)
        proc.habilitados = habilitados.get(proc.pk, set())
        # o que o modal de edicao mostra (o salvo; no envio recusado, o digitado)
        proc.valores = {
            'nome': proc.nome, 'descricao': proc.descricao or '', 'duracao_minutos': proc.duracao_minutos,
            'categoria': proc.categoria, 'preco': proc.preco_base, 'ativo': proc.ativo,
            'habilitados': proc.habilitados,
        }
        proc.erros = {}

    return {
        'procedimentos': pagina,
        'categorias': Procedimento.CATEGORIA_CHOICES,
        'profissionais': Profissional.objects.filter(ativo=True).order_by('nome'),
    }


def _devolver_com_erro(request, erros, form, editar=None):
    """Envio recusado: a lista volta SEM redirect, com o que foi digitado e o erro em cada campo.

    Antes o redirect fechava o modal e perdia tudo (e o aviso nao dizia qual campo). editar = o
    procedimento do modal de edicao (reaberto pelo data-erro no template); None = formulario de novo."""
    contexto = _contexto_lista(request, editar_pk=editar.pk if editar else None)
    if editar is None:
        contexto['novo'] = {'valores': _valores_digitados(form), 'erros': erros}
    else:
        for proc in contexto['procedimentos']:
            if proc.pk == editar.pk:
                valores = _valores_digitados(form)
                if not form.data.get('profissionais_enviado'):   # form sem o grupo: habilitacoes intocadas
                    valores['habilitados'] = proc.habilitados
                proc.valores, proc.erros, proc.reabrir = valores, erros, True
    return render(request, 'painel/procedimentos.html', contexto)


@staff_required
def admin_procedimentos(request):
    """Lista e gerencia procedimentos."""
    return render(request, 'painel/procedimentos.html', _contexto_lista(request))


@escrita_painel
def admin_criar_procedimento(request):
    """Cria procedimento via POST (ProcedimentoForm). Nasce ativo."""
    form = ProcedimentoForm(request.POST)
    if not form.is_valid():
        avisar_erros(request, form)
        return _devolver_com_erro(request, erros_por_campo(form), form)
    d = form.cleaned_data
    try:
        with transaction.atomic():
            proc = form.save(commit=False)
            proc.ativo = True
            proc.save()
            catalogo.definir_preco_base(proc, d['preco'])   # preco base (sem profissional) a partir de hoje
            catalogo.sincronizar_habilitacoes(proc, d['profissionais'])

        registrar_log(request.user, f'Criou procedimento: {proc.nome}', 'procedimento', proc.pk, request=request)
        messages.success(request, f'Procedimento "{proc.nome}" criado!')
        if not d['profissionais']:
            messages.warning(request, 'Nenhum profissional marcado: o procedimento não aparece no agendamento '
                             'até alguém ser habilitado.')
    except (DatabaseError, ValidationError):
        logger.exception('procedimento_criar_falhou', extra={'usuario_id': request.user.pk})
        messages.error(request, 'Erro ao criar procedimento.')

    return redirect('aranha:admin_procedimentos')


@escrita_painel
def admin_editar_procedimento(request, pk):
    """Edita procedimento via POST (ProcedimentoForm).

    Preco e versionado (Preco.vigente_desde): mudar o valor cria uma nova
    vigencia a partir de hoje em vez de sobrescrever o historico (se ja
    houver vigencia de hoje, ela e ajustada) — services/catalogo.definir_preco_base.
    """
    proc = get_object_or_404(Procedimento, pk=pk)
    dados = request.POST.copy()
    dados.setdefault('nome', proc.nome)   # campo ausente = mantem o nome
    form = ProcedimentoForm(dados, instance=proc)
    if not form.is_valid():
        avisar_erros(request, form)
        return _devolver_com_erro(request, erros_por_campo(form), form, editar=proc)
    d = form.cleaned_data
    try:
        with transaction.atomic():
            form.save()
            catalogo.definir_preco_base(proc, d['preco'])
            if dados.get('profissionais_enviado'):   # grupo de checkboxes veio no formulario
                catalogo.sincronizar_habilitacoes(proc, d['profissionais'])

        registrar_log(request.user, f'Editou procedimento: {proc.nome}', 'procedimento', proc.pk, request=request)
        messages.success(request, f'Procedimento "{proc.nome}" atualizado!')
    except RegraDeNegocio as e:
        # habilitacao em uso: nada foi salvo (atomic); o modal volta aberto com o que foi marcado
        messages.error(request, e.mensagem)
        return _devolver_com_erro(request, {'profissionais': e.mensagem}, form, editar=proc)
    except (DatabaseError, ValidationError):
        logger.exception('procedimento_editar_falhou', extra={'procedimento_id': proc.pk})
        messages.error(request, 'Erro ao editar procedimento.')

    return redirect('aranha:admin_procedimentos')
