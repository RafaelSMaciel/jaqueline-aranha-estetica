"""CRUD de ConfiguracaoSistema (chave-valor editavel via painel)."""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..decorators import staff_required
from ..models import ConfiguracaoSistema
from ..utils.audit import registrar_log


CONFIG_SUGERIDAS = [
    ('HORARIO_FUNCIONAMENTO_INICIO', '08:00', 'Horario de abertura da clinica (HH:MM)'),
    ('HORARIO_FUNCIONAMENTO_FIM', '19:00', 'Horario de fechamento da clinica (HH:MM)'),
    ('POLITICA_CANCELAMENTO_HORAS', '24', 'Horas de antecedencia minima para cancelamento sem penalidade'),
    ('INTERVALO_ENTRE_PROCEDIMENTOS_MIN', '15', 'Minutos de intervalo entre atendimentos'),
    ('MAX_FALTAS_BLOQUEIO', '3', 'Numero de faltas consecutivas para bloquear agendamento online'),
    ('DIAS_ANTECEDENCIA_LEMBRETE', '1', 'Dias de antecedencia para enviar lembrete WhatsApp'),
    ('HORAS_ENVIO_NPS', '24', 'Horas apos atendimento REALIZADO para enviar pesquisa NPS'),
    ('DIAS_ALERTA_PACOTE_EXPIRAR', '7', 'Dias antes da expiracao para alertar cliente do pacote'),
    ('MENSAGEM_BOAS_VINDAS', 'Seja bem-vindo(a)!', 'Texto exibido no site publico'),
]


@staff_required
def admin_configuracoes(request):
    """Lista todas configuracoes + sugestoes de chaves nao cadastradas."""
    configs = ConfiguracaoSistema.objects.order_by('chave')
    chaves_existentes = set(configs.values_list('chave', flat=True))

    sugestoes = [
        {'chave': c, 'valor': v, 'descricao': d}
        for c, v, d in CONFIG_SUGERIDAS
        if c not in chaves_existentes
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
        chave = request.POST.get('chave', '').strip().upper().replace(' ', '_')
        valor = request.POST.get('valor', '').strip()
        descricao = request.POST.get('descricao', '').strip() or None

        if not chave:
            messages.error(request, 'Chave e obrigatoria.')
            return redirect('shivazen:admin_configuracoes')

        if ConfiguracaoSistema.objects.filter(chave=chave).exists():
            messages.warning(request, f'Chave "{chave}" ja existe. Edite em vez de duplicar.')
            return redirect('shivazen:admin_configuracoes')

        config = ConfiguracaoSistema.objects.create(
            chave=chave, valor=valor, descricao=descricao
        )
        registrar_log(
            request.user, f'Criou configuracao: {chave}',
            'configuracao_sistema', config.pk,
            detalhes={'valor': valor},
        )
        messages.success(request, f'Configuracao "{chave}" criada.')

    return redirect('shivazen:admin_configuracoes')


@staff_required
@require_POST
def admin_editar_configuracao(request, pk):
    """Edita valor/descricao de configuracao existente."""
    config = get_object_or_404(ConfiguracaoSistema, pk=pk)
    valor_antigo = config.valor

    config.valor = request.POST.get('valor', '').strip()
    descricao = request.POST.get('descricao', '').strip()
    if descricao:
        config.descricao = descricao
    config.save()

    registrar_log(
        request.user, f'Editou configuracao: {config.chave}',
        'configuracao_sistema', config.pk,
        detalhes={'valor_antigo': valor_antigo, 'valor_novo': config.valor},
    )
    messages.success(request, f'"{config.chave}" atualizada.')
    return redirect('shivazen:admin_configuracoes')


@staff_required
@require_POST
def admin_excluir_configuracao(request, pk):
    """Remove configuracao."""
    config = get_object_or_404(ConfiguracaoSistema, pk=pk)
    chave = config.chave
    config.delete()
    registrar_log(
        request.user, f'Excluiu configuracao: {chave}',
        'configuracao_sistema', pk,
    )
    messages.success(request, f'"{chave}" removida.')
    return redirect('shivazen:admin_configuracoes')
