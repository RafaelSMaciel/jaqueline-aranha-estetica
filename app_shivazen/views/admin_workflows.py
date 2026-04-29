"""CRUD WorkflowRegra no painel admin."""
import json

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from ..decorators import staff_required
from ..models import WorkflowRegra, WorkflowExecucao
from ..utils.audit import registrar_log


@staff_required
def admin_workflows(request):
    regras = WorkflowRegra.objects.all().order_by('-ativo', 'trigger', 'nome')
    return render(request, 'painel/workflows.html', {'regras': regras})


@staff_required
def admin_workflow_form(request, pk=None):
    regra = get_object_or_404(WorkflowRegra, pk=pk) if pk else None

    if request.method == 'POST':
        nome = request.POST.get('nome', '').strip()
        trigger = request.POST.get('trigger', '').strip()
        acao = request.POST.get('acao', '').strip()
        offset = request.POST.get('offset_minutos', '0').strip()
        template = request.POST.get('template', '').strip()
        ativo = request.POST.get('ativo') == '1'
        config_raw = request.POST.get('config_json', '{}').strip() or '{}'

        try:
            offset_int = int(offset)
        except ValueError:
            offset_int = 0
        try:
            config = json.loads(config_raw)
            if not isinstance(config, dict):
                raise ValueError
        except (ValueError, TypeError):
            messages.error(request, 'config_json invalido (use JSON object).')
            return render(request, 'painel/workflow_form.html', {
                'regra': regra,
                'TRIGGER_CHOICES': WorkflowRegra.TRIGGER_CHOICES,
                'ACAO_CHOICES': WorkflowRegra.ACAO_CHOICES,
            })

        if not nome or trigger not in dict(WorkflowRegra.TRIGGER_CHOICES) or acao not in dict(WorkflowRegra.ACAO_CHOICES):
            messages.error(request, 'Campos obrigatorios ausentes ou invalidos.')
            return render(request, 'painel/workflow_form.html', {
                'regra': regra,
                'TRIGGER_CHOICES': WorkflowRegra.TRIGGER_CHOICES,
                'ACAO_CHOICES': WorkflowRegra.ACAO_CHOICES,
            })

        if regra:
            regra.nome = nome
            regra.trigger = trigger
            regra.acao = acao
            regra.offset_minutos = offset_int
            regra.template = template
            regra.ativo = ativo
            regra.config_json = config
            regra.save()
            registrar_log(request.user, f'Editou workflow {regra.nome}', 'workflow_regra', regra.pk)
            messages.success(request, 'Regra atualizada.')
        else:
            regra = WorkflowRegra.objects.create(
                nome=nome, trigger=trigger, acao=acao,
                offset_minutos=offset_int, template=template,
                ativo=ativo, config_json=config,
            )
            registrar_log(request.user, f'Criou workflow {regra.nome}', 'workflow_regra', regra.pk)
            messages.success(request, 'Regra criada.')
        return redirect('shivazen:admin_workflows')

    return render(request, 'painel/workflow_form.html', {
        'regra': regra,
        'TRIGGER_CHOICES': WorkflowRegra.TRIGGER_CHOICES,
        'ACAO_CHOICES': WorkflowRegra.ACAO_CHOICES,
    })


@staff_required
def admin_workflow_excluir(request, pk):
    regra = get_object_or_404(WorkflowRegra, pk=pk)
    if request.method == 'POST':
        nome = regra.nome
        regra.delete()
        registrar_log(request.user, f'Excluiu workflow {nome}', 'workflow_regra', pk)
        messages.success(request, f'Regra "{nome}" excluida.')
    return redirect('shivazen:admin_workflows')


@staff_required
def admin_workflow_toggle(request, pk):
    regra = get_object_or_404(WorkflowRegra, pk=pk)
    if request.method == 'POST':
        regra.ativo = not regra.ativo
        regra.save(update_fields=['ativo', 'atualizado_em'])
        registrar_log(
            request.user,
            f'{"Ativou" if regra.ativo else "Desativou"} workflow {regra.nome}',
            'workflow_regra', regra.pk,
        )
    return redirect('shivazen:admin_workflows')


@staff_required
def admin_workflow_execucoes(request, pk):
    regra = get_object_or_404(WorkflowRegra, pk=pk)
    execucoes = WorkflowExecucao.objects.filter(regra=regra).select_related(
        'atendimento', 'atendimento__cliente'
    ).order_by('-executado_em')[:100]
    return render(request, 'painel/workflow_execucoes.html', {
        'regra': regra,
        'execucoes': execucoes,
    })
