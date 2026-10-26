from datetime import datetime, time, timedelta

from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, Q, Subquery
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache

from ..models import (
    AgendaHorario, AvaliacaoNPS, Cliente, CompraPacote, Atendimento, LinkAcesso, Profissional,
)
from ..decorators import staff_required
from ..services import financeiro, links
from ..utils.busca import filtrar_clientes
from ..utils.nps import resumo_nps
from ..utils.datas import fmt_local, hoje as hoje_local
from ..utils.parse import id_int
from ..utils.saude import alertas_saude


def _inicio_do_dia(d):
    """Meia-noite local (aware) de uma data — limite para lookups em DateTimeField."""
    return timezone.make_aware(datetime.combine(d, time.min))


def _brl(valor) -> str:
    return f"{valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')


def _anexar_alertas_saude(atendimentos):
    """Seta `atend.alertas_saude` (utils.saude.alertas_saude) em cada item.

    Alerta visivel de alergia/contraindicacao — do prontuario E das fichas que a
    cliente respondeu no booking. Uma consulta por cliente distinto da pagina.
    """
    cache_cliente = {}
    for atend in atendimentos:
        if atend.cliente_id not in cache_cliente:
            cache_cliente[atend.cliente_id] = alertas_saude(atend.cliente)
        atend.alertas_saude = cache_cliente[atend.cliente_id]
    return atendimentos


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
    # Papel sem tela propria (ex.: recepcao ainda sem permissoes): encerra a sessao.
    auth_logout(request)
    return redirect('aranha:inicio')


@never_cache  # pendentes/proximos trazem alerta de saude
@staff_required
def painel_overview(request):
    """Dashboard principal — Overview com estatísticas"""
    hoje = hoje_local()  # data local — timezone.now().date() vira o dia seguinte apos 21h BRT
    inicio_semana = hoje - timedelta(days=hoje.weekday())
    fim_semana = inicio_semana + timedelta(days=6)
    inicio_mes = hoje.replace(day=1)
    inicio_mes_dt = _inicio_do_dia(inicio_mes)

    agendamentos_hoje = Atendimento.objects.filter(
        data_hora_inicio__date=hoje,
        status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO']
    ).count()

    agendamentos_semana = Atendimento.objects.filter(
        data_hora_inicio__date__range=[inicio_semana, fim_semana],
        status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO']
    ).count()

    total_clientes = Cliente.objects.filter(ativo=True).count()
    novos_clientes = Cliente.objects.filter(criado_em__gte=inicio_mes_dt).count()

    # Receita (D20, services/financeiro — mesma regra do financeiro e da ficha):
    # avulsos faturados (sem retorno, sem valor zero e sem sessao de pacote, que
    # ja entrou na venda do pacote) + pacotes vendidos no mes, liquidos do reembolso.
    avulsos_mes = financeiro.resumo_avulsos(Atendimento.objects.filter(data_hora_inicio__gte=inicio_mes_dt))
    pacotes_mes = financeiro.resumo_pacotes(CompraPacote.objects.filter(criado_em__gte=inicio_mes_dt))
    receita_mensal = _brl(avulsos_mes['total'] + pacotes_mes['total'])
    ticket_medio = _brl(avulsos_mes['ticket_medio'])

    realizados_semana = Atendimento.objects.filter(
        data_hora_inicio__date__range=[inicio_semana, fim_semana],
        status='REALIZADO',
    ).count()
    # Cada AgendaHorario representa uma janela de UM dia da semana (campo
    # dia_semana), entao a soma de todas as janelas ativas ja e a capacidade
    # semanal de slots. Usa total_seconds() (nao .seconds, que zera o
    # componente de dias) para janelas que possam cruzar/exceder 24h.
    slots_semana = 0
    for d in AgendaHorario.objects.filter(profissional__ativo=True):
        duracao = datetime.combine(hoje, d.hora_fim) - datetime.combine(hoje, d.hora_inicio)
        minutos_janela = int(duracao.total_seconds()) // 60
        slots_semana += minutos_janela // 30
    taxa_ocupacao = round((realizados_semana / slots_semana) * 100, 1) if slots_semana else 0

    limite_90d = timezone.now() - timedelta(days=90)
    clientes_ativos_90d = Cliente.objects.filter(
        atendimento__data_hora_inicio__gte=limite_90d,
        atendimento__status__in=['REALIZADO', 'CONFIRMADO'],
    ).distinct().count()

    # NPS de verdade (%promotores - %detratores), mesma formula de relatorios.painel_nps
    # (utils/nps: meio p/ cima, = fn_nps_periodo do Postgres)
    limite_30d = timezone.now() - timedelta(days=30)
    nps_30d = resumo_nps(AvaliacaoNPS.objects.filter(criado_em__gte=limite_30d))['nps']

    proximos_agendamentos = _anexar_alertas_saude(list(Atendimento.objects.filter(
        data_hora_inicio__gte=timezone.now(),
        status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO']
    ).select_related('cliente', 'profissional', 'procedimento').order_by('data_hora_inicio')[:10]))

    # Pendentes de aprovação (todos) — com alerta de saude visivel p/ quem aprova
    pendentes_aprovacao = _anexar_alertas_saude(list(Atendimento.objects.filter(
        status='PENDENTE',
        data_hora_inicio__gte=timezone.now(),
    ).select_related('cliente', 'profissional', 'procedimento').order_by('data_hora_inicio')[:15]))
    total_pendentes = Atendimento.objects.filter(
        status='PENDENTE',
        data_hora_inicio__gte=timezone.now(),
    ).count()

    # --- Dados para os Gráficos (1 query instead of 7) ---
    dias_semana_list = [(inicio_semana + timedelta(days=i)).strftime('%d/%m') for i in range(7)]
    agendamentos_por_dia = dict(
        Atendimento.objects.filter(
            data_hora_inicio__date__range=[inicio_semana, fim_semana],
            status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO', 'REALIZADO']
        ).annotate(
            dia=TruncDate('data_hora_inicio')
        ).values('dia').annotate(total=Count('pk')).values_list('dia', 'total')
    )
    dados_grafico_list = [
        agendamentos_por_dia.get((inicio_semana + timedelta(days=i)), 0) for i in range(7)
    ]

    rotulos_status = dict(Atendimento.STATUS_CHOICES)
    agendamentos_por_status = list(Atendimento.objects.filter(
        data_hora_inicio__gte=inicio_mes_dt
    ).values('status').annotate(total=Count('pk')).order_by('status'))

    context = {
        'agendamentos_hoje': agendamentos_hoje,
        'agendamentos_semana': agendamentos_semana,
        'total_clientes': total_clientes,
        'novos_clientes': novos_clientes,
        'receita_mensal': receita_mensal,
        'ticket_medio': ticket_medio,
        'taxa_ocupacao': taxa_ocupacao,
        'clientes_ativos_90d': clientes_ativos_90d,
        'nps_30d': nps_30d,
        'proximos_agendamentos': proximos_agendamentos,
        # Listas cruas: o template serializa via |json_script (json.dumps aqui
        # codificava duas vezes e o Chart.js recebia uma string).
        'dias_semana': dias_semana_list,
        'dados_grafico_semana': dados_grafico_list,
        'status_labels': [rotulos_status.get(s['status'], s['status']) for s in agendamentos_por_status],
        'status_totais': [s['total'] for s in agendamentos_por_status],
        'pendentes_aprovacao': pendentes_aprovacao,
        'total_pendentes': total_pendentes,
    }

    return render(request, 'painel/overview.html', context)


@never_cache  # lista traz alertas de saude das clientes (template: marcadores_atendimentos)
@staff_required
def painel_agendamentos(request):
    """Gerenciamento de agendamentos"""
    status_filter = request.GET.get('status', 'all')
    data_filter = request.GET.get('data')
    profissional_filter = request.GET.get('profissional', '')

    agendamentos = Atendimento.objects.all().select_related(
        'cliente', 'cliente__prontuario', 'profissional', 'procedimento'
    ).order_by('-data_hora_inicio')

    if status_filter != 'all':
        agendamentos = agendamentos.filter(status=status_filter.upper())

    if data_filter:
        try:
            data = datetime.strptime(data_filter, '%Y-%m-%d').date()
            agendamentos = agendamentos.filter(data_hora_inicio__date=data)
        except ValueError:
            pass

    # ?profissional=abc / '²' (URL manipulada) nao pode virar 500
    profissional_id = id_int(profissional_filter)
    if profissional_id is not None:
        agendamentos = agendamentos.filter(profissional_id=profissional_id)

    paginator = Paginator(agendamentos, 50)
    page = request.GET.get('page', 1)
    agendamentos_page = paginator.get_page(page)

    profissionais = Profissional.objects.filter(ativo=True)

    context = {
        'agendamentos': agendamentos_page,
        'profissionais': profissionais,
        'status_filter': status_filter,
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
                atendimento__data_hora_inicio__gte=_inicio_do_dia(inicio_mes),
                atendimento__data_hora_inicio__lt=_inicio_do_dia(proximo_mes),
            )
        ),
        ics_ativo_desde=Subquery(link_ics.values('criado_em')[:1]),
    ).order_by('nome')

    paginator = Paginator(profissionais, 30)
    page = request.GET.get('page', 1)
    profissionais_page = paginator.get_page(page)
    migracao_links = links.aplicada_em_0049()
    for prof in profissionais_page:
        # link migrado do token antigo (antes da 0049): ativo, mas sem data real de criacao
        prof.ics_migrado = bool(
            prof.ics_ativo_desde and migracao_links and prof.ics_ativo_desde <= migracao_links
        )

    context = {'profissionais': profissionais_page}
    return render(request, 'painel/profissionais.html', context)


def _celula_texto_seguro(ws) -> None:
    """Impede formula injection na ultima linha escrita.

    O openpyxl grava como formula qualquer str iniciada por '='. Nome de
    cliente vem do booking publico (texto livre) — '=HYPERLINK(...)' viraria
    formula executada no Excel do admin. Forcar data_type 's' grava texto puro.
    """
    for cell in ws[ws.max_row]:
        if isinstance(cell.value, str) and cell.value.startswith('='):
            cell.data_type = 's'


@staff_required
def exportar_relatorio_excel(request):
    """Gera um relatório Excel dos últimos 30 dias de atendimentos"""
    import openpyxl

    data_limite = timezone.now() - timedelta(days=30)
    atendimentos = (
        Atendimento.objects.filter(data_hora_inicio__gte=data_limite)
        .select_related('cliente', 'profissional', 'procedimento')
        .order_by('data_hora_inicio')
    )

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="relatorio_atendimentos.xlsx"'

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Atendimentos Últimos 30 dias"

    # Header
    columns = ['ID', 'Data', 'Hora', 'Cliente', 'Profissional', 'Procedimento', 'Status', 'Valor (R$)']
    ws.append(columns)

    for at in atendimentos:
        valor = at.valor_cobrado or 0
        ws.append([
            at.pk,
            fmt_local(at.data_hora_inicio, '%d/%m/%Y'),  # hora local, nao UTC
            fmt_local(at.data_hora_inicio, '%H:%M'),
            at.cliente.nome,
            at.profissional.nome,
            at.procedimento.nome,
            at.get_status_display(),
            float(valor)
        ])
        _celula_texto_seguro(ws)

    wb.save(response)
    return response
