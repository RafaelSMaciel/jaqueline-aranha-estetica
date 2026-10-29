from datetime import timedelta

from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, Q, Subquery
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache

from ...models import (
    AvaliacaoNPS, Cliente, Atendimento, LinkAcesso, Profissional,
)
from ...models.agendamentos import STATUS_ATIVOS, StatusAtendimento
from ...constants import TOLERANCIA_DESFECHO
from ...decorators import staff_required
from ...services import financeiro
from ...services.alertas import marcar_atendimentos, registrar_leitura_saude
from ...services.retorno import devidos as retornos_devidos
from ...services.retorno import devidos_na_lista as retorno_devidos_na_lista
from ...utils.busca import filtrar_clientes
from ...services.nps import resumo_nps
from ...services.indicadores import ocupacao_semana
from ...utils.auditoria import registrar_log
from ...utils.datas import dias_locais, hoje as hoje_local, inicio_do_dia, ler_data
from ...utils.parse import id_int


def _marcar(request, atendimentos, tela, *, valores=False):
    """Marcadores da linha (services.alertas.marcar_atendimentos: alerta de saude, termo, ficha e,
    na agenda, pacote e credito) em lote, e a leitura de dado de saude na trilha (LGPD)."""
    lista = marcar_atendimentos(atendimentos, valores=valores)
    registrar_leitura_saude(request, (at.cliente_id for at in lista if at.alertas_saude), tela)
    return lista


def _limite_desfecho():
    """Realizado/Faltou so a partir do inicio (menos a folga): o select esconde as opcoes antes."""
    return timezone.now() + TOLERANCIA_DESFECHO


@login_required
def painel(request):
    """Porta de entrada pos-login: cada papel cai na sua tela."""
    user = request.user
    if user.is_staff:
        return redirect('aranha:painel_overview')
    # Qualquer nao-staff com profissional ativo vai ao portal (mesma regra do
    # staff_required); nao depende do papel gravado.
    prof = getattr(user, 'profissional', None)
    if prof is not None and prof.ativo:
        return redirect('aranha:profissional_agenda')
    # Profissional sem cadastro ativo vinculado nao tem tela: encerra a sessao.
    auth_logout(request)
    return redirect('aranha:inicio')


@never_cache  # pendentes/proximos trazem alerta de saude
@staff_required
def painel_overview(request):
    """Dashboard principal — Overview com estatísticas"""
    hoje = hoje_local()  # data local — timezone.now().date() vira o dia seguinte apos 21h BRT
    # semana CIVIL (seg-dom) p/ agenda e ocupacao; mes = a competencia do financeiro (mesma funcao)
    inicio_semana = hoje - timedelta(days=hoje.weekday())
    fim_semana = inicio_semana + timedelta(days=6)
    mes = financeiro.competencia(hoje)

    agendamentos_hoje = Atendimento.objects.filter(
        **dias_locais('data_hora_inicio', hoje),
        status__in=STATUS_ATIVOS
    ).count()

    agendamentos_semana = Atendimento.objects.filter(
        **dias_locais('data_hora_inicio', inicio_semana, fim_semana),
        status__in=STATUS_ATIVOS
    ).count()

    total_clientes = Cliente.objects.filter(ativo=True).count()
    novos_clientes = Cliente.objects.filter(**mes.filtro('criado_em')).count()

    # Receita do mes = a mesma conta e o mesmo periodo do financeiro (services/financeiro, D20)
    receita = financeiro.resumo_receita(mes)

    ocupacao = ocupacao_semana(inicio_semana, fim_semana)   # definicao em services/indicadores

    limite_90d = timezone.now() - timedelta(days=90)
    clientes_ativos_90d = Cliente.objects.filter(
        atendimento__data_hora_inicio__gte=limite_90d,
        atendimento__status__in=(StatusAtendimento.REALIZADO, StatusAtendimento.CONFIRMADO),
    ).distinct().count()

    # NPS de verdade (%promotores - %detratores), mesma formula da tela de NPS (views/painel/nps)
    # (services/nps: meio p/ cima, = fn_nps_periodo do Postgres)
    limite_30d = timezone.now() - timedelta(days=30)
    nps_30d = resumo_nps(AvaliacaoNPS.objects.filter(criado_em__gte=limite_30d))['nps']

    proximos_agendamentos = Atendimento.objects.filter(
        data_hora_inicio__gte=timezone.now(),
        status__in=STATUS_ATIVOS
    ).select_related('cliente', 'profissional', 'procedimento').order_by('data_hora_inicio')[:10]

    # Pendentes de aprovação (todos) — com alerta de saude visivel p/ quem aprova
    pendentes_aprovacao = Atendimento.objects.filter(
        status=StatusAtendimento.PENDENTE,
        data_hora_inicio__gte=timezone.now(),
    ).select_related('cliente', 'profissional', 'procedimento').order_by('data_hora_inicio')[:15]
    # marcadores das duas listas num lote so (mesmas consultas p/ 1 ou 25 linhas)
    marcados = _marcar(request, [*proximos_agendamentos, *pendentes_aprovacao], 'visao_geral')
    proximos_agendamentos, pendentes_aprovacao = marcados[:len(proximos_agendamentos)], marcados[len(proximos_agendamentos):]
    total_sem_desfecho = Atendimento.objects.sem_desfecho().count()
    total_retornos = len(retornos_devidos())
    total_pendentes = Atendimento.objects.filter(
        status=StatusAtendimento.PENDENTE,
        data_hora_inicio__gte=timezone.now(),
    ).count()

    # --- Dados para os Gráficos (1 query instead of 7) ---
    dias_semana_list = [(inicio_semana + timedelta(days=i)).strftime('%d/%m') for i in range(7)]
    agendamentos_por_dia = dict(
        Atendimento.objects.filter(
            **dias_locais('data_hora_inicio', inicio_semana, fim_semana),
            status__in=(*STATUS_ATIVOS, StatusAtendimento.REALIZADO)
        ).annotate(
            dia=TruncDate('data_hora_inicio')
        ).values('dia').annotate(total=Count('pk')).values_list('dia', 'total')
    )
    dados_grafico_list = [
        agendamentos_por_dia.get((inicio_semana + timedelta(days=i)), 0) for i in range(7)
    ]

    rotulos_status = dict(Atendimento.STATUS_CHOICES)
    agendamentos_por_status = list(Atendimento.objects.filter(
        **mes.filtro('data_hora_inicio')
    ).values('status').annotate(total=Count('pk')).order_by('status'))

    context = {
        'agendamentos_hoje': agendamentos_hoje,
        'agendamentos_semana': agendamentos_semana,
        'total_clientes': total_clientes,
        'novos_clientes': novos_clientes,
        'mes': mes,
        'receita_mes': receita['total'],
        'ticket_medio': receita['ticket_medio'],
        'taxa_ocupacao': ocupacao['percentual'],
        'ocupacao': ocupacao,
        'clientes_ativos_90d': clientes_ativos_90d,
        'nps_30d': nps_30d,
        'proximos_agendamentos': proximos_agendamentos,
        # Listas cruas: o template serializa via |json_script (json.dumps aqui
        # codificava duas vezes e o Chart.js recebia uma string).
        'dias_semana': dias_semana_list,
        'dados_grafico_semana': dados_grafico_list,
        'status_labels': [rotulos_status.get(s['status'], s['status']) for s in agendamentos_por_status],
        'status_totais': [s['total'] for s in agendamentos_por_status],
        # cor de cada fatia = token do selo do status (--st-<status>-texto), lida no template
        'status_codigos': [s['status'] for s in agendamentos_por_status],
        'pendentes_aprovacao': pendentes_aprovacao,
        'total_pendentes': total_pendentes,
        'total_sem_desfecho': total_sem_desfecho,
        'total_retornos': total_retornos,
        'limite_desfecho': _limite_desfecho(),
    }

    return render(request, 'painel/overview.html', context)


@never_cache  # lista traz alertas de saude das clientes (services.alertas.marcar_atendimentos)
@staff_required
def painel_agendamentos(request):
    """Gerenciamento de agendamentos"""
    status_filter = request.GET.get('status', 'all')
    data_filter = request.GET.get('data')
    profissional_filter = request.GET.get('profissional', '')

    agendamentos = Atendimento.objects.all().select_related(
        'cliente', 'cliente__prontuario', 'profissional', 'procedimento'
    ).order_by('-data_hora_inicio')

    if status_filter == 'sem_desfecho':
        # ja terminou e ninguem marcou o desfecho: mais antigo primeiro (resolver a fila)
        agendamentos = agendamentos.sem_desfecho().order_by('data_hora_inicio')
    elif status_filter != 'all':
        agendamentos = agendamentos.filter(status=status_filter.upper())

    if data_filter:
        try:
            data = ler_data(data_filter)
            agendamentos = agendamentos.filter(**dias_locais('data_hora_inicio', data))
        except ValueError:
            pass

    # ?profissional=abc / '²' (URL manipulada) nao pode virar 500
    profissional_id = id_int(profissional_filter)
    if profissional_id is not None:
        agendamentos = agendamentos.filter(profissional_id=profissional_id)

    paginator = Paginator(agendamentos, 50)
    page = request.GET.get('page', 1)
    agendamentos_page = paginator.get_page(page)
    # alerta, termo, ficha, pacote e credito da pagina em lote; o template so le atributo
    agendamentos_page.object_list = _marcar(request, agendamentos_page.object_list, 'agendamentos', valores=True)
    retornos = retorno_devidos_na_lista(agendamentos_page.object_list)
    for at in agendamentos_page.object_list:
        at.retorno_ate = retornos.get(at.pk)

    profissionais = Profissional.objects.filter(ativo=True)

    context = {
        'agendamentos': agendamentos_page,
        'profissionais': profissionais,
        'status_filter': status_filter,
        'limite_desfecho': _limite_desfecho(),
    }

    return render(request, 'painel/agendamentos.html', context)


@staff_required
def painel_clientes(request):
    """Gerenciamento de clientes"""
    search = request.GET.get('search', '').strip()
    clientes = Cliente.objects.all().order_by('-criado_em')

    if search:
        clientes = filtrar_clientes(clientes, search)

    paginator = Paginator(clientes, 50)
    page = request.GET.get('page', 1)
    clientes_page = paginator.get_page(page)

    context = {
        'clientes': clientes_page,
        'search': search,
    }

    return render(request, 'painel/clientes.html', context)


@staff_required
def painel_profissionais(request):
    """Gerenciamento de profissionais"""
    # Mes corrente no fuso local (agora.month em UTC erra no fim do mes a noite)
    inicio_mes = hoje_local().replace(day=1)
    proximo_mes = (inicio_mes + timedelta(days=32)).replace(day=1)
    # Feed ICS: o token so existe na geracao (link_acesso guarda o hash) — a
    # lista mostra so se ha link vigente e desde quando.
    link_ics = LinkAcesso.objects.filter(
        profissional=OuterRef('pk'), finalidade=LinkAcesso.FEED_ICS, revogado_em__isnull=True,
    ).order_by('-criado_em', '-pk')
    profissionais = Profissional.objects.all().annotate(
        total_agendamentos=Count('atendimento'),
        agendamentos_mes=Count(
            'atendimento',
            filter=Q(
                atendimento__data_hora_inicio__gte=inicio_do_dia(inicio_mes),
                atendimento__data_hora_inicio__lt=inicio_do_dia(proximo_mes),
            )
        ),
        ics_ativo_desde=Subquery(link_ics.values('criado_em')[:1]),
    ).order_by('nome')

    paginator = Paginator(profissionais, 30)
    page = request.GET.get('page', 1)
    profissionais_page = paginator.get_page(page)

    context = {'profissionais': profissionais_page}
    return render(request, 'painel/profissionais.html', context)


@never_cache
@staff_required
def exportar_relatorio_excel(request):
    """Relatorio gerencial do MES (?mes=AAAA-MM; padrao = mes corrente): Resumo, Atendimentos,
    Pacotes e Comissoes — os mesmos numeros do financeiro (services/relatorios). Exportar nomes de
    clientes em lote fica na auditoria (LGPD art. 37)."""
    from ...services.relatorios import planilha_gerencial

    mes = financeiro.competencia_do_param(request.GET.get('mes'))
    wb, contagem = planilha_gerencial(mes)
    registrar_log(request.user, 'Exportou relatório gerencial', 'atendimento', None,
                  {'mes': mes.param, **contagem}, request=request)
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="relatorio_{mes.param}.xlsx"'
    wb.save(response)
    return response
