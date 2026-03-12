from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.db.models import Q, Sum, F, Count
from datetime import datetime, timedelta
import json

from ..models import (
    Cliente, Atendimento, Profissional, Procedimento
)
from ..decorators import staff_required


@login_required
def painel(request):
    """Redireciona para o painel admin (staff only)."""
    if request.user.is_staff:
        return redirect('shivazen:painel_overview')
    # Se não for staff, desloga e manda para home
    from django.contrib.auth import logout as auth_logout
    auth_logout(request)
    return redirect('shivazen:inicio')


@staff_required
def painel_overview(request):
    """Dashboard principal — Overview com estatísticas"""
    hoje = timezone.now().date()
    inicio_semana = hoje - timedelta(days=hoje.weekday())
    fim_semana = inicio_semana + timedelta(days=6)
    inicio_mes = hoje.replace(day=1)

    agendamentos_hoje = Atendimento.objects.filter(
        data_hora_inicio__date=hoje,
        status_atendimento__in=['AGENDADO', 'CONFIRMADO']
    ).count()

    agendamentos_semana = Atendimento.objects.filter(
        data_hora_inicio__date__range=[inicio_semana, fim_semana],
        status_atendimento__in=['AGENDADO', 'CONFIRMADO']
    ).count()

    total_clientes = Cliente.objects.filter(ativo=True).count()
    novos_clientes = Cliente.objects.filter(data_cadastro__gte=inicio_mes).count()

    receita_total = Atendimento.objects.filter(
        data_hora_inicio__gte=inicio_mes,
        status_atendimento='REALIZADO'
    ).annotate(
        valor=F('procedimento__preco__valor')
    ).aggregate(total=Sum('valor'))['total'] or 0

    receita_mensal = f"{receita_total:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

    proximos_agendamentos = Atendimento.objects.filter(
        data_hora_inicio__gte=timezone.now(),
        status_atendimento__in=['AGENDADO', 'CONFIRMADO']
    ).select_related('cliente', 'profissional', 'procedimento').order_by('data_hora_inicio')[:10]

    # --- Dados para os Gráficos ---
    dias_semana_list = [(inicio_semana + timedelta(days=i)).strftime('%d/%m') for i in range(7)]
    dados_grafico_list = []
    for i in range(7):
        dia_atual = inicio_semana + timedelta(days=i)
        count = Atendimento.objects.filter(
            data_hora_inicio__date=dia_atual,
            status_atendimento__in=['AGENDADO', 'CONFIRMADO', 'REALIZADO']
        ).count()
        dados_grafico_list.append(count)

    dias_semana = json.dumps(dias_semana_list)
    dados_grafico_semana = json.dumps(dados_grafico_list)

    agendamentos_por_status = list(Atendimento.objects.filter(
        data_hora_inicio__gte=inicio_mes
    ).values('status_atendimento').annotate(total=Count('pk')))

    context = {
        'agendamentos_hoje': agendamentos_hoje,
        'agendamentos_semana': agendamentos_semana,
        'total_clientes': total_clientes,
        'novos_clientes': novos_clientes,
        'receita_mensal': receita_mensal,
        'proximos_agendamentos': proximos_agendamentos,
        'dias_semana': dias_semana,
        'dados_grafico_semana': dados_grafico_semana,
        'agendamentos_por_status': agendamentos_por_status,
    }

    return render(request, 'painel/painel_overview.html', context)


@staff_required
def painel_agendamentos(request):
    """Gerenciamento de agendamentos"""
    status_filter = request.GET.get('status', 'all')
    data_filter = request.GET.get('data')
    profissional_filter = request.GET.get('profissional')

    agendamentos = Atendimento.objects.all().select_related(
        'cliente', 'profissional', 'procedimento'
    ).order_by('-data_hora_inicio')

    if status_filter != 'all':
        agendamentos = agendamentos.filter(status_atendimento=status_filter.upper())

    if data_filter:
        try:
            data = datetime.strptime(data_filter, '%Y-%m-%d').date()
            agendamentos = agendamentos.filter(data_hora_inicio__date=data)
        except ValueError:
            pass

    if profissional_filter:
        agendamentos = agendamentos.filter(profissional_id=profissional_filter)

    profissionais = Profissional.objects.filter(ativo=True)

    context = {
        'agendamentos': agendamentos[:50],
        'profissionais': profissionais,
        'status_filter': status_filter,
    }

    return render(request, 'painel/painel_agendamentos.html', context)


@staff_required
def painel_clientes(request):
    """Gerenciamento de clientes"""
    search = request.GET.get('search', '')
    clientes = Cliente.objects.all().order_by('-data_cadastro')

    if search:
        clientes = clientes.filter(
            Q(nome_completo__icontains=search) |
            Q(cpf__icontains=search) |
            Q(email__icontains=search) |
            Q(telefone__icontains=search)
        )

    context = {
        'clientes': clientes[:100],
        'search': search,
    }

    return render(request, 'painel/painel_clientes.html', context)


@staff_required
def painel_profissionais(request):
    """Gerenciamento de profissionais"""
    profissionais = Profissional.objects.all().order_by('nome')

    for prof in profissionais:
        prof.total_agendamentos = Atendimento.objects.filter(profissional=prof).count()
        prof.agendamentos_mes = Atendimento.objects.filter(
            profissional=prof,
            data_hora_inicio__month=timezone.now().month,
            data_hora_inicio__year=timezone.now().year
        ).count()

    context = {'profissionais': profissionais}
    return render(request, 'painel/painel_profissionais.html', context)

@staff_required
def exportar_relatorio_excel(request):
    """Gera um relatório Excel dos últimos 30 dias de atendimentos"""
    import openpyxl
    from django.http import HttpResponse

    data_limite = timezone.now() - timedelta(days=30)
    atendimentos = Atendimento.objects.filter(data_hora_inicio__gte=data_limite).select_related('cliente', 'profissional', 'procedimento')

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="relatorio_atendimentos.xlsx"'

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Atendimentos Últimos 30 dias"

    # Header
    columns = ['ID', 'Data', 'Hora', 'Cliente', 'Profissional', 'Procedimento', 'Status', 'Valor (R$)']
    ws.append(columns)

    for at in atendimentos:
        valor = at.valor_cobrado if at.valor_cobrado else at.procedimento.preco_set.first().valor if at.procedimento.preco_set.exists() else 0
        ws.append([
            at.pk,
            at.data_hora_inicio.strftime('%d/%m/%Y'),
            at.data_hora_inicio.strftime('%H:%M'),
            at.cliente.nome_completo,
            at.profissional.nome,
            at.procedimento.nome,
            at.status_atendimento,
            float(valor)
        ])

    wb.save(response)
    return response
