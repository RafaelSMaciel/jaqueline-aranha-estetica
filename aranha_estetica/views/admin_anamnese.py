"""CRUD FormularioAnamnese + visualizacao de respostas."""
import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import ProtectedError, RestrictedError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache

from ..decorators import staff_required
from ..models import FormularioAnamnese, Procedimento, RespostaAnamnese
from ..utils.audit import registrar_log
from ..utils.fichas import itens_ficha

# Exemplo exibido em formulario novo. Chave 'obrigatorio' SEM acento: e o que
# anamnese_publica.py e agenda/pesquisa.html leem.
SCHEMA_EXEMPLO = [
    {'key': 'gestante', 'tipo': 'bool', 'label': 'Está gestante?', 'obrigatorio': True},
    {'key': 'alergias', 'tipo': 'text', 'label': 'Possui alergias?', 'obrigatorio': False},
]


def _schema_texto(schema) -> str:
    """JSON legivel p/ o textarea (o repr Python nao era reenviavel)."""
    return json.dumps(schema, ensure_ascii=False, indent=2)


def _ctx(form_obj, posted=None):
    """Contexto comum dos renders (GET e re-render de erro)."""
    if posted is not None:
        schema_texto = posted.get('schema_json', '')
    elif form_obj is not None:
        schema_texto = _schema_texto(form_obj.schema_json)
    else:
        schema_texto = _schema_texto(SCHEMA_EXEMPLO)
    return {
        'form_obj': form_obj,
        'procedimentos': Procedimento.objects.filter(ativo=True).order_by('nome'),
        'ESCOPO_CHOICES': FormularioAnamnese.ESCOPO_CHOICES,
        'TIPO_CHOICES': FormularioAnamnese.TIPO_CHOICES,
        'CATEGORIA_CHOICES': Procedimento.CATEGORIA_CHOICES,
        'MODALIDADE_CHOICES': Procedimento.MODALIDADE_CHOICES,
        'schema_texto': schema_texto,
    }


def _mensagens_validacao(exc: ValidationError) -> list[str]:
    if hasattr(exc, 'message_dict'):
        return [m for msgs in exc.message_dict.values() for m in msgs]
    return list(exc.messages)


@staff_required
def admin_anamneses(request):
    formularios = FormularioAnamnese.objects.all().order_by('-ativo', 'escopo', 'nome')
    return render(request, 'painel/anamneses.html', {'formularios': formularios})


@staff_required
def admin_anamnese_form(request, pk=None):
    form = get_object_or_404(FormularioAnamnese, pk=pk) if pk else None

    if request.method == 'POST':
        post = request.POST
        nome = post.get('nome', '').strip()
        # Sem tipo no POST (form antigo) mantem o tipo atual — nunca rebaixa PESQUISA.
        tipo = post.get('tipo', '').strip() or (form.tipo if form else 'ANAMNESE')
        escopo = post.get('escopo', 'GLOBAL').strip()
        categoria = post.get('categoria', '').strip()
        modalidade = post.get('modalidade', '').strip()
        proc_id = post.get('procedimento', '').strip()
        schema_raw = post.get('schema_json', '[]').strip() or '[]'
        ativo = post.get('ativo') == '1'
        obrigatorio = post.get('obrigatorio') == '1'

        # Formulario que ja tem respostas nao muda de perguntas no lugar: as
        # respostas antigas guardam so {key: valor} e perderiam label/sentido.
        # Nesse caso nasce uma nova versao e a anterior e desativada.
        versionar = (
            form is not None
            and RespostaAnamnese.objects.filter(formulario=form).exclude(respostas_json={}).exists()
        )
        schema_anterior = form.schema_json if form is not None else None
        obj = form if form is not None else FormularioAnamnese()
        # Atribui antes de validar: o re-render de erro mostra o que foi enviado
        obj.nome = nome
        obj.tipo = tipo
        obj.escopo = escopo
        obj.categoria = categoria if escopo == 'CATEGORIA' else ''
        obj.modalidade = modalidade if escopo == 'MODALIDADE' else ''
        obj.procedimento_id = int(proc_id) if escopo == 'PROCEDIMENTO' and proc_id.isdigit() else None
        obj.ativo = ativo
        obj.obrigatorio = obrigatorio

        def _erro(msgs):
            for m in msgs:
                messages.error(request, m)
            return render(request, 'painel/anamnese_form.html', _ctx(obj, posted=post))

        try:
            obj.schema_json = json.loads(schema_raw)
        except (ValueError, TypeError):
            return _erro(['Campos do formulário: JSON inválido (use uma lista de objetos).'])

        erros = []
        if not nome:
            erros.append('Nome obrigatório.')
        if tipo not in dict(FormularioAnamnese.TIPO_CHOICES):
            erros.append('Tipo de formulário inválido.')
        if escopo not in dict(FormularioAnamnese.ESCOPO_CHOICES):
            erros.append('Escopo inválido.')
        if escopo == 'CATEGORIA' and categoria not in dict(Procedimento.CATEGORIA_CHOICES):
            erros.append('Escolha a categoria do escopo.')
        if escopo == 'MODALIDADE' and modalidade not in dict(Procedimento.MODALIDADE_CHOICES):
            erros.append('Escolha a modalidade do escopo.')
        if escopo == 'PROCEDIMENTO' and obj.procedimento_id is None:
            erros.append('Escolha o procedimento do escopo.')
        if erros:
            return _erro(erros)

        try:
            # clean() do model valida o schema (lista de objetos com key/tipo/label)
            # — antes nunca era chamado e item string quebrava a anamnese publica.
            obj.full_clean()
        except ValidationError as exc:
            return _erro(_mensagens_validacao(exc))

        if versionar and obj.schema_json != schema_anterior:
            with transaction.atomic():
                antigo_pk = obj.pk
                obj.pk = None  # clone: nova linha com os dados enviados
                obj._state.adding = True
                obj.save()
                FormularioAnamnese.objects.filter(pk=antigo_pk).update(ativo=False)
            registrar_log(
                request.user, f'Criou nova versao da anamnese {obj.nome}', 'formulario_anamnese', obj.pk,
                detalhes={'versao_anterior': antigo_pk}, request=request,
            )
            messages.success(
                request,
                'Este formulário já tinha respostas: as perguntas novas viraram uma nova versão e a '
                'anterior foi desativada (as respostas antigas continuam com as perguntas originais).',
            )
            return redirect('aranha:admin_anamneses')

        obj.save()

        if form is not None:
            registrar_log(request.user, f'Editou anamnese {obj.nome}', 'formulario_anamnese', obj.pk, request=request)
            messages.success(request, 'Formulário atualizado.')
        else:
            registrar_log(request.user, f'Criou anamnese {obj.nome}', 'formulario_anamnese', obj.pk, request=request)
            messages.success(request, 'Formulário criado.')
        return redirect('aranha:admin_anamneses')

    return render(request, 'painel/anamnese_form.html', _ctx(form))


@staff_required
def admin_anamnese_excluir(request, pk):
    form = get_object_or_404(FormularioAnamnese, pk=pk)
    if request.method == 'POST':
        nome = form.nome
        try:
            form.delete()
        except (RestrictedError, ProtectedError):
            # Respostas de clientes referenciam o formulario (RESTRICT): desativa
            # em vez de apagar, preservando o historico preenchido.
            form.ativo = False
            form.save(update_fields=['ativo', 'atualizado_em'])
            registrar_log(
                request.user, f'Desativou anamnese {nome} (possui respostas)',
                'formulario_anamnese', pk, request=request,
            )
            messages.warning(
                request,
                f'O formulário "{nome}" já tem respostas de clientes: foi desativado em vez de excluído.',
            )
            return redirect('aranha:admin_anamneses')
        registrar_log(request.user, f'Excluiu anamnese {nome}', 'formulario_anamnese', pk, request=request)
        messages.success(request, f'Formulário "{nome}" excluído.')
    return redirect('aranha:admin_anamneses')


@never_cache  # dado de saude: sem bfcache/cache de disco apos o logout
@staff_required
def admin_anamnese_respostas(request, pk):
    form = get_object_or_404(FormularioAnamnese, pk=pk)
    qs = RespostaAnamnese.objects.filter(formulario=form).select_related(
        'cliente', 'atendimento__procedimento', 'formulario',
    ).order_by('-criado_em')
    respostas = Paginator(qs, 30).get_page(request.GET.get('page'))
    for r in respostas:
        r.itens = itens_ficha(r)  # label da pergunta + Sim/Não, nao a chave crua
    # Leitura de dado de saude de varias clientes: fica na trilha (LGPD art. 37)
    registrar_log(
        request.user, 'Leu respostas de anamnese', 'resposta_anamnese', form.pk,
        detalhes={'clientes': sorted({r.cliente_id for r in respostas}), 'pagina': respostas.number},
        request=request,
    )
    return render(request, 'painel/anamnese_respostas.html', {
        'form_obj': form,
        'respostas': respostas,
    })
