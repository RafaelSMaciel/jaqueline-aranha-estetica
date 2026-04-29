"""CRUD FormularioAnamnese + visualizacao de respostas."""
import json

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from ..decorators import staff_required
from ..models import FormularioAnamnese, Procedimento, RespostaAnamnese
from ..utils.audit import registrar_log


@staff_required
def admin_anamneses(request):
    formularios = FormularioAnamnese.objects.all().order_by('-ativo', 'escopo', 'nome')
    return render(request, 'painel/anamneses.html', {'formularios': formularios})


@staff_required
def admin_anamnese_form(request, pk=None):
    form = get_object_or_404(FormularioAnamnese, pk=pk) if pk else None
    procedimentos = Procedimento.objects.filter(ativo=True).order_by('nome')

    if request.method == 'POST':
        nome = request.POST.get('nome', '').strip()
        escopo = request.POST.get('escopo', 'GLOBAL').strip()
        categoria = request.POST.get('categoria', '').strip()
        proc_id = request.POST.get('procedimento') or None
        schema_raw = request.POST.get('schema_json', '[]').strip() or '[]'
        ativo = request.POST.get('ativo') == '1'
        obrigatorio = request.POST.get('obrigatorio') == '1'

        try:
            schema = json.loads(schema_raw)
            if not isinstance(schema, list):
                raise ValueError
        except (ValueError, TypeError):
            messages.error(request, 'schema_json invalido (use JSON array de objetos).')
            return render(request, 'painel/anamnese_form.html', {
                'form_obj': form,
                'procedimentos': procedimentos,
                'ESCOPO_CHOICES': FormularioAnamnese.ESCOPO_CHOICES,
            })

        if not nome:
            messages.error(request, 'Nome obrigatorio.')
            return render(request, 'painel/anamnese_form.html', {
                'form_obj': form,
                'procedimentos': procedimentos,
                'ESCOPO_CHOICES': FormularioAnamnese.ESCOPO_CHOICES,
            })

        defaults = dict(
            nome=nome, escopo=escopo,
            categoria=categoria if escopo == 'CATEGORIA' else '',
            procedimento_id=proc_id if escopo == 'PROCEDIMENTO' else None,
            schema_json=schema, ativo=ativo, obrigatorio=obrigatorio,
        )
        if form:
            for k, v in defaults.items():
                setattr(form, k, v)
            form.save()
            registrar_log(request.user, f'Editou anamnese {form.nome}', 'formulario_anamnese', form.pk)
            messages.success(request, 'Formulario atualizado.')
        else:
            form = FormularioAnamnese.objects.create(**defaults)
            registrar_log(request.user, f'Criou anamnese {form.nome}', 'formulario_anamnese', form.pk)
            messages.success(request, 'Formulario criado.')
        return redirect('shivazen:admin_anamneses')

    return render(request, 'painel/anamnese_form.html', {
        'form_obj': form,
        'procedimentos': procedimentos,
        'ESCOPO_CHOICES': FormularioAnamnese.ESCOPO_CHOICES,
    })


@staff_required
def admin_anamnese_excluir(request, pk):
    form = get_object_or_404(FormularioAnamnese, pk=pk)
    if request.method == 'POST':
        nome = form.nome
        form.delete()
        registrar_log(request.user, f'Excluiu anamnese {nome}', 'formulario_anamnese', pk)
        messages.success(request, f'Formulario "{nome}" excluido.')
    return redirect('shivazen:admin_anamneses')


@staff_required
def admin_anamnese_respostas(request, pk):
    form = get_object_or_404(FormularioAnamnese, pk=pk)
    respostas = RespostaAnamnese.objects.filter(formulario=form).select_related(
        'cliente', 'atendimento'
    ).order_by('-criado_em')[:100]
    return render(request, 'painel/anamnese_respostas.html', {
        'form_obj': form,
        'respostas': respostas,
    })
