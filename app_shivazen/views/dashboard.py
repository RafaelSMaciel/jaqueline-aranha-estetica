from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from datetime import datetime, timedelta
from django.utils import timezone
from django.db.models import Q, Sum, F
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.urls import reverse
import json

from ..models import * 


def painel(request):

    """Redireciona para o painel correto baseado no tipo de usuário"""

    if request.user.is_staff:

        return redirect('shivazen:painel_overview')

    else:

        return redirect('shivazen:painel_cliente')






def painel_cliente(request):

    """Painel específico para clientes (não-admin)"""

    try:

        cliente = Cliente.objects.get(email=request.user.email)

        cliente_id = cliente.id_cliente

        

        agendamentos = Atendimento.objects.filter(

            cliente_id=cliente_id

        ).select_related('profissional', 'procedimento').order_by('-data_hora_inicio')[:10]

        

        agendamentos_proximos = Atendimento.objects.filter(

            cliente_id=cliente_id,

            data_hora_inicio__gte=datetime.now(),

            status_atendimento__in=['AGENDADO', 'CONFIRMADO']

        ).select_related('profissional', 'procedimento').order_by('data_hora_inicio')[:5]

        

        total_agendamentos = Atendimento.objects.filter(cliente_id=cliente_id).count()

        agendamentos_realizados = Atendimento.objects.filter(

            cliente_id=cliente_id,

            status_atendimento='REALIZADO'

        ).count()

        

        context = {

            'cliente': cliente,

            'agendamentos': agendamentos,

            'agendamentos_proximos': agendamentos_proximos,

            'total_agendamentos': total_agendamentos,

            'agendamentos_realizados': agendamentos_realizados,

        }

        

        return render(request, 'cliente/painel_cliente.html', context)

    except Cliente.DoesNotExist:

        messages.error(request, 'Cliente não encontrado. Entre em contato com o suporte.')

        return redirect('shivazen:usuarioLogout')






def painel_overview(request):

    """Dashboard principal - Overview com estatísticas"""

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

    

    # Calcular receita real dos atendimentos realizados no mês

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

    

    context = {

        'agendamentos_hoje': agendamentos_hoje,

        'agendamentos_semana': agendamentos_semana,

        'total_clientes': total_clientes,

        'novos_clientes': novos_clientes,

        'receita_mensal': receita_mensal,

        'proximos_agendamentos': proximos_agendamentos,

    }

    

    return render(request, 'painel/painel_overview.html', context)






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






def perfil(request):

    """Perfil do usuário - Usa templates diferentes para admin vs cliente"""

    try:

        cliente = Cliente.objects.get(email=request.user.email)

    except Cliente.DoesNotExist:

        cliente = None

    

    if request.method == 'POST':

        if cliente:

            cliente.nome_completo = request.POST.get('nome_completo', cliente.nome_completo)

            cliente.telefone = request.POST.get('telefone', cliente.telefone)

            cliente.save()

            messages.success(request, 'Perfil atualizado com sucesso!')

        return redirect('shivazen:perfil')

    

    historico = []

    if cliente:

        historico = Atendimento.objects.filter(

            cliente=cliente

        ).select_related('profissional', 'procedimento').order_by('-data_hora_inicio')[:20]

    

    context = {

        'cliente': cliente,

        'historico': historico,

    }

    

    # Staff vê template com sidebar, Cliente vê template limpo

    if request.user.is_staff:

        return render(request, 'usuario/perfil.html', context)  

    else:

        return render(request, 'cliente/perfil_cliente.html', context)



# ========================================

# AGENDAMENTO P�aBLICO (SEM LOGIN)

# ========================================





