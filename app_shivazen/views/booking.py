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


def agendamento_publico(request):

    """

    Exibe a agenda para o público geral.

    Permite visualizar horários sem login.

    Login só é exigido na confirmação.

    """

    # Se já estiver logado e for staff, redireciona para painel admin

    if request.user.is_authenticated and request.user.is_staff:

        return redirect('shivazen:painel')

        

    # Lógica para exibir a agenda (similar ao antigo agendaCadastro)

    todos_procedimentos = Procedimento.objects.filter(ativo=True)

    

    # Lógica de datas (semana atual/navegação)

    semana_param = request.GET.get('semana', 0)

    try:

        offset_semanas = int(semana_param)

    except ValueError:

        offset_semanas = 0

        

    hoje = timezone.now().date()

    inicio_semana = hoje - timedelta(days=hoje.weekday()) + timedelta(weeks=offset_semanas)

    fim_semana = inicio_semana + timedelta(days=6)

    

    # Dias da semana para o cabeçalho

    dias_semana = []

    for i in range(7):

        dia = inicio_semana + timedelta(days=i)

        dias_semana.append({

            'data': dia,

            'verbose': dia.strftime('%A').capitalize() # Requer locale pt-BR ou tradução manual

        })

        

    # Estrutura da agenda (Horários x Dias)

    horarios_base = [f"{h:02d}:00" for h in range(8, 19)] # 08:00 as 18:00

    agenda_semanal = {}

    

    for horario in horarios_base:

        agenda_semanal[horario] = []

        for i in range(7):

            dia = inicio_semana + timedelta(days=i)

            data_hora_str = f"{dia} {horario}"

            dt_aware = timezone.make_aware(datetime.strptime(data_hora_str, "%Y-%m-%d %H:%M"))

            

            # Verificar disponibilidade (Lógica simplificada)

            # Na prática, verifica se há profissionais livres

            profissionais_livres = []

            profissionais = Profissional.objects.filter(ativo=True)

            

            for prof in profissionais:

                # Verifica bloqueios e agendamentos

                ocupado = Atendimento.objects.filter(

                    profissional=prof,

                    data_hora_inicio=dt_aware,

                    status_atendimento__in=['AGENDADO', 'CONFIRMADO']

                ).exists()

                

                bloqueado = BloqueioAgenda.objects.filter(

                    profissional=prof,

                    data_inicio__lte=dt_aware,

                    data_fim__gte=dt_aware

                ).exists()

                

                if not ocupado and not bloqueado:

                    profissionais_livres.append({

                        'id': prof.id_profissional,

                        'nome': prof.nome

                    })

            

            slot = {

                'datetime_iso': dt_aware.isoformat(),

                'disponivel': len(profissionais_livres) > 0,

                'profissionais_disponiveis': len(profissionais_livres),

                'profissionais_json': json.dumps(profissionais_livres)

            }

            agenda_semanal[horario].append(slot)



    context = {

        'todos_procedimentos': todos_procedimentos,

        'dias_semana': dias_semana,

        'agenda_semanal': agenda_semanal,

        'semana_atual_display': f"{inicio_semana.strftime('%d/%m')} a {fim_semana.strftime('%d/%m')}",

        'semana_anterior': offset_semanas - 1,

        'semana_seguinte': offset_semanas + 1,

    }

    

    return render(request, 'agenda/agendamento_publico.html', context)





def confirmar_agendamento(request):

    """

    Processa a confirmação do agendamento após login.

    """

    if not request.user.is_authenticated:

        # Se não estiver logado, salva dados na sessão e manda pro login

        if request.method == 'POST':

            request.session['agendamento_pendente'] = {

                'datetime': request.POST.get('datetime'),

                'procedimento': request.POST.get('procedimento'),

                'profissional': request.POST.get('profissional')

            }

            return redirect(f"{reverse('shivazen:usuarioLogin')}?next={reverse('shivazen:confirmar_agendamento')}")

        else:

            return redirect('shivazen:agendamento_publico')

            

    # Se já estiver logado (ou voltou do login)

    dados = request.session.pop('agendamento_pendente', None)

    

    # Se não tem dados na sessão, tenta pegar do POST (caso o usuário já estivesse logado no modal)

    if not dados and request.method == 'POST':

        dados = {

            'datetime': request.POST.get('datetime'),

            'procedimento': request.POST.get('procedimento'),

            'profissional': request.POST.get('profissional')

        }

        

    if not dados:

        messages.error(request, 'Dados do agendamento perdidos. Tente novamente.')

        return redirect('shivazen:agendamento_publico')

        

    try:

        # Criar o agendamento

        cliente = Cliente.objects.get(email=request.user.email)

        profissional = Profissional.objects.get(pk=dados['profissional'])

        procedimento = Procedimento.objects.get(pk=dados['procedimento'])

        data_hora = datetime.fromisoformat(dados['datetime'])

        

        Atendimento.objects.create(

            cliente=cliente,

            profissional=profissional,

            procedimento=procedimento,

            data_hora_inicio=data_hora,

            status_atendimento='AGENDADO'

        )

        

        messages.success(request, 'Agendamento realizado com sucesso!')

        return redirect('shivazen:painel_cliente')

        

    except Exception as e:

        messages.error(request, f'Erro ao confirmar agendamento: {e}')

        return redirect('shivazen:agendamento_publico')



# AJAX Views (Públicas)



def agendaCadastro(request):

    if request.method == 'POST':

        try:

            id_cliente = request.session.get('cliente_id')

            id_profissional = request.POST.get('profissional')

            id_procedimento = request.POST.get('procedimento')

            data_hora_str = request.POST.get('horario_selecionado') # Ex: "2024-10-30T10:30:00"

            

            if not all([id_cliente, id_profissional, id_procedimento, data_hora_str]):

                messages.error(request, 'Todos os campos são obrigatórios.')

                return redirect('shivazen:agendamento_publico')



            data_hora_inicio = datetime.fromisoformat(data_hora_str)

            

            cliente = Cliente.objects.get(pk=id_cliente)

            profissional = Profissional.objects.get(pk=id_profissional)

            procedimento = Procedimento.objects.get(pk=id_procedimento)



            data_hora_fim = data_hora_inicio + timedelta(minutes=procedimento.duracao_minutos)



            # Lógica de conflito (mantida)

            conflitos = Atendimento.objects.filter(

                profissional=profissional,

                data_hora_inicio__lt=data_hora_fim,

                data_hora_fim__gt=data_hora_inicio,

                status_atendimento__in=['AGENDADO', 'CONFIRMADO']

            ).exists()



            if conflitos:

                messages.error(request, 'Este horário já foi agendado. Por favor, escolha outro.')

                return redirect('shivazen:agendamento_publico')



            Atendimento.objects.create(

                cliente=cliente,

                profissional=profissional,

                procedimento=procedimento,

                data_hora_inicio=data_hora_inicio,

                data_hora_fim=data_hora_fim,

                status_atendimento='AGENDADO'

            )

            messages.success(request, 'Seu agendamento foi realizado com sucesso!')

            return redirect('shivazen:painel')



        except (Cliente.DoesNotExist, Profissional.DoesNotExist, Procedimento.DoesNotExist):

            messages.error(request, 'Erro ao encontrar dados essenciais (cliente, profissional ou procedimento).')

        except ValueError:

             messages.error(request, 'Formato de data ou hora inválido.')

        except Exception as e:

            messages.error(request, f'Ocorreu um erro ao agendar: {e}')

        

        return redirect('shivazen:agendamento_publico')



    profissionais = Profissional.objects.filter(ativo=True)

    context = {'profissionais': profissionais}

    return render(request, 'agenda/agendamento.html', context)



# --- VIEWS AUXILIARES PARA AJAX (Refatoradas) ---




