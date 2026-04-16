import os
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum, Q
from django.http import JsonResponse, HttpResponse
from datetime import datetime, timedelta
from django.utils import timezone
from django.core.paginator import Paginator
from django_ratelimit.decorators import ratelimit
import json

from ..models import (
    Profissional, Cliente, Procedimento, Atendimento, Preco,
    DisponibilidadeProfissional, ProfissionalProcedimento, BloqueioAgenda,
    Promocao, LogAuditoria
)
from ..decorators import staff_required
from ..utils.audit import registrar_log
import logging

logger = logging.getLogger(__name__)


@staff_required
def prontuarioconsentimento(request):
    """Prontuario e consentimento — lista clientes com status do prontuario."""
    from ..models import Prontuario, ProntuarioPergunta, ProntuarioResposta, AceitePrivacidade
    search = request.GET.get('search', '')

    clientes = Cliente.objects.all().order_by('nome_completo')
    if search:
        clientes = clientes.filter(
            Q(nome_completo__icontains=search) |
            Q(cpf__icontains=search) |
            Q(telefone__icontains=search)
        )

    from django.db.models import Count, Exists, OuterRef, Subquery

    clientes = clientes.annotate(
        tem_prontuario=Exists(Prontuario.objects.filter(cliente=OuterRef('pk'))),
        total_respostas=Count('prontuario__prontuarioresposta', distinct=True),
        total_termos=Count('aceiteprivacidade', distinct=True),
    )

    paginator = Paginator(clientes, 50)
    page = request.GET.get('page', 1)
    clientes_page = paginator.get_page(page)

    clientes_list = [
        {
            'cliente': c,
            'tem_prontuario': c.tem_prontuario,
            'total_respostas': c.total_respostas,
            'total_termos': c.total_termos,
        }
        for c in clientes_page
    ]

    perguntas = ProntuarioPergunta.objects.filter(ativa=True)

    context = {
        'clientes_list': clientes_list,
        'clientes_page': clientes_page,
        'perguntas': perguntas,
        'total_perguntas': perguntas.count(),
        'search': search,
    }
    return render(request, 'painel/painel_prontuario.html', context)


@staff_required
def profissionalCadastro(request):
    if request.method == 'POST':
        try:
            nome = request.POST.get('nome', '').strip()
            especialidade = request.POST.get('especialidade', '').strip()
            ativo = request.POST.get('ativo') == 'on'

            if not nome:
                messages.error(request, 'O nome do profissional é obrigatório.')
                return redirect('shivazen:profissionalCadastro')

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
    return render(request, 'telas/tela_cadastro_profissional.html', context)


@staff_required
def profissionalEditar(request, pk=None):
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
    return render(request, 'telas/tela_editar_profissional.html', context)


# ═══════════════════════════════════════
#   PROMOÇÕES — CRUD
# ═══════════════════════════════════════

@staff_required
def admin_promocoes(request):
    """Lista todas as promoções"""
    promocoes = Promocao.objects.select_related('procedimento').order_by('-ativa', '-data_inicio')

    paginator = Paginator(promocoes, 30)
    page = request.GET.get('page', 1)
    promocoes_page = paginator.get_page(page)

    procedimentos = Procedimento.objects.filter(ativo=True)
    context = {
        'promocoes': promocoes_page,
        'procedimentos': procedimentos,
    }
    return render(request, 'painel/admin_promocoes.html', context)


@staff_required
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_criar_promocao(request):
    """Cria nova promoção via POST"""
    if request.method == 'POST':
        try:
            procedimento = get_object_or_404(Procedimento, pk=request.POST.get('procedimento'))
            promo = Promocao.objects.create(
                nome=request.POST.get('nome', '').strip(),
                descricao=request.POST.get('descricao', '').strip(),
                desconto_percentual=int(request.POST.get('desconto', 10)),
                procedimento=procedimento,
                data_inicio=request.POST.get('data_inicio'),
                data_fim=request.POST.get('data_fim'),
                ativa=request.POST.get('ativa') == '1',
            )
            registrar_log(request.user, f'Criou promoção: {promo.nome}', 'promocao', promo.pk, {'desconto': str(promo.desconto_percentual)})
            messages.success(request, 'Promoção criada com sucesso!')
        except Exception as e:
            logger.error(f'Erro ao criar promoção: {e}', exc_info=True)
            messages.error(request, 'Erro ao criar promoção. Verifique os dados e tente novamente.')
    return redirect('shivazen:admin_promocoes')


@staff_required
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_editar_promocao(request, pk):
    """Edita promoção existente via POST"""
    promo = get_object_or_404(Promocao, pk=pk)
    if request.method == 'POST':
        try:
            promo.nome = request.POST.get('nome', promo.nome).strip()
            promo.descricao = request.POST.get('descricao', promo.descricao).strip()
            promo.desconto_percentual = int(request.POST.get('desconto', promo.desconto_percentual))
            promo.procedimento = get_object_or_404(Procedimento, pk=request.POST.get('procedimento'))
            promo.data_inicio = request.POST.get('data_inicio')
            promo.data_fim = request.POST.get('data_fim')
            promo.ativa = request.POST.get('ativa') == '1'
            promo.save()
            registrar_log(request.user, f'Editou promoção: {promo.nome}', 'promocao', promo.pk)
            messages.success(request, 'Promoção atualizada!')
        except Exception as e:
            logger.error(f'Erro ao atualizar promoção: {e}', exc_info=True)
            messages.error(request, 'Erro ao atualizar promoção. Verifique os dados e tente novamente.')
    return redirect('shivazen:admin_promocoes')


@staff_required
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_excluir_promocao(request, pk):
    """Exclui promoção via POST"""
    if request.method == 'POST':
        try:
            promo = get_object_or_404(Promocao, pk=pk)
            nome = promo.nome
            promo.delete()
            registrar_log(request.user, f'Excluiu promoção: {nome}', 'promocao', pk)
            messages.success(request, 'Promoção excluída!')
        except Exception as e:
            logger.error(f'Erro ao excluir promoção: {e}', exc_info=True)
            messages.error(request, 'Erro ao excluir promoção.')
    return redirect('shivazen:admin_promocoes')


# ═══════════════════════════════════════
#   AUDITORIA
# ═══════════════════════════════════════

@staff_required
def admin_auditoria(request):
    """Timeline de auditoria com filtros"""
    logs = LogAuditoria.objects.select_related('usuario').order_by('-criado_em')

    # Filtros
    tabela = request.GET.get('tabela', '')
    acao_filter = request.GET.get('acao', '')
    data_filter = request.GET.get('data', '')

    if tabela:
        logs = logs.filter(tabela_afetada=tabela)
    if acao_filter:
        logs = logs.filter(acao__icontains=acao_filter)
    if data_filter:
        try:
            data = datetime.strptime(data_filter, '%Y-%m-%d').date()
            logs = logs.filter(criado_em__date=data)
        except ValueError:
            pass

    # Paginação
    paginator = Paginator(logs, 30)
    page = request.GET.get('page', 1)
    logs_page = paginator.get_page(page)

    # Tabelas únicas para filtro
    tabelas = LogAuditoria.objects.values_list('tabela_afetada', flat=True).distinct().order_by('tabela_afetada')

    context = {
        'logs': logs_page,
        'tabelas': [t for t in tabelas if t],
        'tabela_filter': tabela,
        'acao_filter': acao_filter,
        'data_filter': data_filter,
    }
    return render(request, 'painel/admin_auditoria.html', context)


# ═══════════════════════════════════════
#   STATUS DE AGENDAMENTO
# ═══════════════════════════════════════

@staff_required
@ratelimit(key='user', rate='60/m', method='POST', block=True)
def admin_atualizar_status(request):
    """Atualiza status de um agendamento via AJAX"""
    if request.method != 'POST':
        return JsonResponse({'erro': 'Método não permitido'}, status=405)

    try:
        data = json.loads(request.body)
        atendimento_id = data.get('atendimento_id')
        novo_status = data.get('status', '').upper()

        status_validos = ['AGENDADO', 'CONFIRMADO', 'REALIZADO', 'CANCELADO', 'FALTOU']
        if novo_status not in status_validos:
            return JsonResponse({'erro': f'Status inválido. Use: {", ".join(status_validos)}'}, status=400)

        atendimento = get_object_or_404(Atendimento, pk=atendimento_id)
        status_anterior = atendimento.status
        atendimento.status = novo_status
        atendimento.save()

        registrar_log(
            request.user,
            f'Status alterado: {status_anterior} → {novo_status}',
            'atendimento',
            atendimento_id,
            {'status_anterior': status_anterior, 'status_novo': novo_status,
             'cliente': atendimento.cliente.nome_completo}
        )

        return JsonResponse({
            'sucesso': True,
            'status_anterior': status_anterior,
            'status_novo': novo_status,
        })
    except json.JSONDecodeError:
        return JsonResponse({'erro': 'Dados inválidos'}, status=400)
    except Exception as e:
        logger.error(f'Erro ao atualizar status: {e}', exc_info=True)
        return JsonResponse({'erro': 'Ocorreu um erro interno. Tente novamente.'}, status=500)


# Nota: a antiga view setup_seed(request) foi substituida pelo management
# command `python manage.py seed` (app_shivazen/management/commands/seed.py),
# eliminando token em query param e execucao remota via URL.
