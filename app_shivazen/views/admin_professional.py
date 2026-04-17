"""Views de CRUD de profissionais — cadastro e edicao."""
import logging

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from ..decorators import staff_required
from ..models import (
    DisponibilidadeProfissional,
    Procedimento,
    Profissional,
    ProfissionalProcedimento,
)

logger = logging.getLogger(__name__)


@staff_required
def profissional_cadastro(request):
    if request.method == 'POST':
        try:
            nome = request.POST.get('nome', '').strip()
            especialidade = request.POST.get('especialidade', '').strip()
            ativo = request.POST.get('ativo') == 'on'

            if not nome:
                messages.error(request, 'O nome do profissional é obrigatório.')
                return redirect('shivazen:profissional_cadastro')

            profissional = Profissional.objects.create(
                nome=nome,
                especialidade=especialidade,
                ativo=ativo
            )

            # Processa disponibilidades
            dias_semana_list = ['segunda', 'terca', 'quarta', 'quinta', 'sexta', 'sabado', 'domingo']
            dia_numero = {
                'segunda': 2, 'terca': 3, 'quarta': 4, 'quinta': 5,
                'sexta': 6, 'sabado': 7, 'domingo': 1
            }

            for dia in dias_semana_list:
                hora_inicio = request.POST.get(f'hora_inicio_{dia}')
                hora_fim = request.POST.get(f'hora_fim_{dia}')
                trabalha = request.POST.get(f'trabalha_{dia}') == 'on'

                if trabalha and hora_inicio and hora_fim:
                    DisponibilidadeProfissional.objects.create(
                        profissional=profissional,
                        dia_semana=dia_numero[dia],
                        hora_inicio=hora_inicio,
                        hora_fim=hora_fim
                    )

            # Processa procedimentos
            procedimentos_ids = request.POST.getlist('procedimentos')
            for proc_id in procedimentos_ids:
                try:
                    procedimento = Procedimento.objects.get(pk=proc_id)
                    ProfissionalProcedimento.objects.get_or_create(
                        profissional=profissional,
                        procedimento=procedimento
                    )
                except Procedimento.DoesNotExist:
                    pass

            messages.success(request, f'Profissional {nome} cadastrado com sucesso!')
            return redirect('shivazen:painel_profissionais')

        except Exception as e:
            logger.error(f'Erro ao cadastrar profissional: {e}', exc_info=True)
            messages.error(request, 'Erro ao cadastrar profissional. Verifique os dados e tente novamente.')

    procedimentos = Procedimento.objects.filter(ativo=True)
    dias_semana = {
        'segunda': 'Segunda-feira',
        'terca': 'Terça-feira',
        'quarta': 'Quarta-feira',
        'quinta': 'Quinta-feira',
        'sexta': 'Sexta-feira',
        'sabado': 'Sábado',
        'domingo': 'Domingo'
    }
    context = {
        'procedimentos': procedimentos,
        'dias_semana': dias_semana
    }
    return render(request, 'painel/cadastro_profissional.html', context)


@staff_required
def profissional_editar(request, pk=None):
    """Editar profissional existente"""
    if pk:
        profissional = get_object_or_404(Profissional, pk=pk)
    else:
        messages.error(request, 'Profissional não especificado.')
        return redirect('shivazen:painel_profissionais')

    if request.method == 'POST':
        try:
            profissional.nome = request.POST.get('nome', profissional.nome).strip()
            profissional.especialidade = request.POST.get('especialidade', profissional.especialidade).strip()
            profissional.ativo = request.POST.get('ativo') == 'on'
            profissional.save()

            # Atualiza disponibilidades
            DisponibilidadeProfissional.objects.filter(profissional=profissional).delete()
            dias_semana_list = ['segunda', 'terca', 'quarta', 'quinta', 'sexta', 'sabado', 'domingo']
            dia_numero = {
                'segunda': 2, 'terca': 3, 'quarta': 4, 'quinta': 5,
                'sexta': 6, 'sabado': 7, 'domingo': 1
            }
            for dia in dias_semana_list:
                hora_inicio = request.POST.get(f'hora_inicio_{dia}')
                hora_fim = request.POST.get(f'hora_fim_{dia}')
                trabalha = request.POST.get(f'trabalha_{dia}') == 'on'
                if trabalha and hora_inicio and hora_fim:
                    DisponibilidadeProfissional.objects.create(
                        profissional=profissional,
                        dia_semana=dia_numero[dia],
                        hora_inicio=hora_inicio,
                        hora_fim=hora_fim
                    )

            # Atualiza procedimentos
            ProfissionalProcedimento.objects.filter(profissional=profissional).delete()
            procedimentos_ids = request.POST.getlist('procedimentos')
            for proc_id in procedimentos_ids:
                try:
                    procedimento = Procedimento.objects.get(pk=proc_id)
                    ProfissionalProcedimento.objects.get_or_create(
                        profissional=profissional,
                        procedimento=procedimento
                    )
                except Procedimento.DoesNotExist:
                    pass

            messages.success(request, f'Profissional {profissional.nome} atualizado com sucesso!')
            return redirect('shivazen:painel_profissionais')

        except Exception as e:
            logger.error(f'Erro ao atualizar profissional: {e}', exc_info=True)
            messages.error(request, 'Erro ao atualizar profissional. Verifique os dados e tente novamente.')

    procedimentos = Procedimento.objects.filter(ativo=True)
    disponibilidades = DisponibilidadeProfissional.objects.filter(profissional=profissional)
    procedimentos_atuais = ProfissionalProcedimento.objects.filter(
        profissional=profissional
    ).values_list('procedimento_id', flat=True)

    dias_semana = {
        'segunda': 'Segunda-feira',
        'terca': 'Terça-feira',
        'quarta': 'Quarta-feira',
        'quinta': 'Quinta-feira',
        'sexta': 'Sexta-feira',
        'sabado': 'Sábado',
        'domingo': 'Domingo'
    }

    # Mapeia disponibilidades para o template
    dia_numero_reverso = {2: 'segunda', 3: 'terca', 4: 'quarta', 5: 'quinta', 6: 'sexta', 7: 'sabado', 1: 'domingo'}
    disponibilidades_map = {}
    for disp in disponibilidades:
        dia_key = dia_numero_reverso.get(disp.dia_semana, '')
        if dia_key:
            disponibilidades_map[dia_key] = {
                'hora_inicio': disp.hora_inicio.strftime('%H:%M') if disp.hora_inicio else '',
                'hora_fim': disp.hora_fim.strftime('%H:%M') if disp.hora_fim else '',
            }

    context = {
        'profissional': profissional,
        'procedimentos': procedimentos,
        'procedimentos_atuais': list(procedimentos_atuais),
        'dias_semana': dias_semana,
        'disponibilidades_map': disponibilidades_map,
    }
    return render(request, 'painel/editar_profissional.html', context)
