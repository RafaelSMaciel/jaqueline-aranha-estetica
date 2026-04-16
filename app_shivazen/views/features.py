"""Views para features pendentes: bloqueios, procedimentos, clientes detalhe,
lista de espera, NPS web, termos de consentimento."""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.core.paginator import Paginator
from django.db.models import Q
from datetime import datetime
import json
import logging

from django_ratelimit.decorators import ratelimit

from ..models import (
    BloqueioAgenda, Profissional, Procedimento, Preco,
    Cliente, Atendimento, ListaEspera, AvaliacaoNPS,
    VersaoTermo, AceitePrivacidade, AssinaturaTermoProcedimento,
)
from ..decorators import staff_required
from ..utils.audit import registrar_log

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════
#   BLOQUEIO DE AGENDA
# ═══════════════════════════════════════

@staff_required
def admin_bloqueios(request):
    """Lista e cria bloqueios de agenda."""
    bloqueios = BloqueioAgenda.objects.select_related(
        'profissional'
    ).order_by('-data_hora_inicio')

    profissionais = Profissional.objects.filter(ativo=True)

    paginator = Paginator(bloqueios, 30)
    page = request.GET.get('page', 1)
    bloqueios_page = paginator.get_page(page)

    context = {
        'bloqueios': bloqueios_page,
        'profissionais': profissionais,
    }
    return render(request, 'painel/admin_bloqueios.html', context)


@staff_required
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_criar_bloqueio(request):
    """Cria bloqueio de agenda via POST."""
    if request.method != 'POST':
        return redirect('shivazen:admin_bloqueios')

    try:
        profissional = get_object_or_404(Profissional, pk=request.POST.get('profissional_id'))
        data_inicio = datetime.fromisoformat(request.POST.get('data_hora_inicio', ''))
        data_fim = datetime.fromisoformat(request.POST.get('data_hora_fim', ''))
        motivo = request.POST.get('motivo', '').strip()

        if data_fim <= data_inicio:
            messages.error(request, 'Data fim deve ser posterior a data inicio.')
            return redirect('shivazen:admin_bloqueios')

        bloqueio = BloqueioAgenda.objects.create(
            profissional=profissional,
            data_hora_inicio=data_inicio,
            data_hora_fim=data_fim,
            motivo=motivo,
        )
        registrar_log(request.user, f'Criou bloqueio para {profissional.nome}', 'bloqueio_agenda', bloqueio.pk)
        messages.success(request, 'Bloqueio criado com sucesso!')
    except Exception as e:
        logger.error(f'Erro ao criar bloqueio: {e}', exc_info=True)
        messages.error(request, 'Erro ao criar bloqueio.')

    return redirect('shivazen:admin_bloqueios')


@staff_required
def admin_excluir_bloqueio(request, bloqueio_id):
    """Exclui bloqueio de agenda via POST."""
    if request.method != 'POST':
        return redirect('shivazen:admin_bloqueios')

    bloqueio = get_object_or_404(BloqueioAgenda, pk=bloqueio_id)
    registrar_log(request.user, f'Excluiu bloqueio de {bloqueio.profissional.nome}', 'bloqueio_agenda', bloqueio_id)
    bloqueio.delete()
    messages.success(request, 'Bloqueio excluido!')
    return redirect('shivazen:admin_bloqueios')


# ═══════════════════════════════════════
#   CRUD DE PROCEDIMENTOS
# ═══════════════════════════════════════

@staff_required
def admin_procedimentos(request):
    """Lista e gerencia procedimentos."""
    procedimentos = list(
        Procedimento.objects.all().order_by('-ativo', 'categoria', 'nome')
    )

    # Precos base (profissional=NULL) por procedimento — um único query
    preco_map = {
        p.procedimento_id: p.valor
        for p in Preco.objects.filter(
            procedimento_id__in=[pr.pk for pr in procedimentos],
            profissional__isnull=True,
        )
    }
    for proc in procedimentos:
        proc.preco_base = preco_map.get(proc.pk)

    paginator = Paginator(procedimentos, 30)
    page = request.GET.get('page', 1)
    procs_page = paginator.get_page(page)

    context = {
        'procedimentos': procs_page,
        'categorias': Procedimento.CATEGORIA_CHOICES,
    }
    return render(request, 'painel/admin_procedimentos.html', context)


@staff_required
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_criar_procedimento(request):
    """Cria procedimento via POST."""
    if request.method != 'POST':
        return redirect('shivazen:admin_procedimentos')

    try:
        proc = Procedimento.objects.create(
            nome=request.POST.get('nome', '').strip(),
            descricao=request.POST.get('descricao', '').strip(),
            duracao_minutos=int(request.POST.get('duracao_minutos', 30)),
            categoria=request.POST.get('categoria', 'OUTRO'),
            ativo=True,
        )

        # Preco base (sem profissional)
        preco = request.POST.get('preco', '')
        if preco:
            Preco.objects.create(procedimento=proc, valor=preco)

        registrar_log(request.user, f'Criou procedimento: {proc.nome}', 'procedimento', proc.pk)
        messages.success(request, f'Procedimento "{proc.nome}" criado!')
    except Exception as e:
        logger.error(f'Erro ao criar procedimento: {e}', exc_info=True)
        messages.error(request, 'Erro ao criar procedimento.')

    return redirect('shivazen:admin_procedimentos')


@staff_required
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_editar_procedimento(request, pk):
    """Edita procedimento via POST."""
    proc = get_object_or_404(Procedimento, pk=pk)
    if request.method != 'POST':
        return redirect('shivazen:admin_procedimentos')

    try:
        proc.nome = request.POST.get('nome', proc.nome).strip()
        proc.descricao = request.POST.get('descricao', '').strip()
        proc.duracao_minutos = int(request.POST.get('duracao_minutos', proc.duracao_minutos))
        proc.categoria = request.POST.get('categoria', proc.categoria)
        proc.ativo = request.POST.get('ativo') == '1'
        proc.save()

        # Atualiza preco base
        preco_val = request.POST.get('preco', '')
        if preco_val:
            preco_obj, _ = Preco.objects.get_or_create(
                procedimento=proc, profissional=None,
                defaults={'valor': preco_val}
            )
            if not _:
                preco_obj.valor = preco_val
                preco_obj.save()

        registrar_log(request.user, f'Editou procedimento: {proc.nome}', 'procedimento', proc.pk)
        messages.success(request, f'Procedimento "{proc.nome}" atualizado!')
    except Exception as e:
        logger.error(f'Erro ao editar procedimento: {e}', exc_info=True)
        messages.error(request, 'Erro ao editar procedimento.')

    return redirect('shivazen:admin_procedimentos')


# ═══════════════════════════════════════
#   DETALHE / EDICAO DE CLIENTE
# ═══════════════════════════════════════

@staff_required
def admin_cliente_detalhe(request, pk):
    """Detalhe e edicao de cliente."""
    cliente = get_object_or_404(Cliente, pk=pk)

    if request.method == 'POST':
        cliente.nome_completo = request.POST.get('nome_completo', cliente.nome_completo).strip()
        cliente.telefone = request.POST.get('telefone', cliente.telefone).strip()
        cliente.email = request.POST.get('email', '').strip() or None
        cliente.cpf = request.POST.get('cpf', '').strip() or None
        data_nasc = request.POST.get('data_nascimento', '')
        if data_nasc:
            try:
                cliente.data_nascimento = datetime.strptime(data_nasc, '%Y-%m-%d').date()
            except ValueError:
                pass
        cliente.ativo = request.POST.get('ativo') == '1'
        cliente.save()
        registrar_log(request.user, f'Editou cliente: {cliente.nome_completo}', 'cliente', cliente.pk)
        messages.success(request, 'Cliente atualizado!')
        return redirect('shivazen:admin_cliente_detalhe', pk=pk)

    atendimentos = Atendimento.objects.filter(
        cliente=cliente
    ).select_related('profissional', 'procedimento').order_by('-data_hora_inicio')[:20]

    context = {
        'cliente': cliente,
        'atendimentos': atendimentos,
    }
    return render(request, 'painel/admin_cliente_detalhe.html', context)


# ═══════════════════════════════════════
#   LISTA DE ESPERA
# ═══════════════════════════════════════

@staff_required
def admin_lista_espera(request):
    """Gerencia lista de espera."""
    espera = ListaEspera.objects.select_related(
        'cliente', 'procedimento', 'profissional_desejado'
    ).order_by('-criado_em')

    paginator = Paginator(espera, 30)
    page = request.GET.get('page', 1)
    espera_page = paginator.get_page(page)

    context = {'lista_espera': espera_page}
    return render(request, 'painel/admin_lista_espera.html', context)


@staff_required
def admin_notificar_espera(request, pk):
    """Marca item da lista de espera como notificado."""
    if request.method != 'POST':
        return redirect('shivazen:admin_lista_espera')

    item = get_object_or_404(ListaEspera, pk=pk)
    item.notificado = True
    item.save()
    messages.success(request, f'{item.cliente.nome_completo} marcado como notificado.')
    return redirect('shivazen:admin_lista_espera')


# ═══════════════════════════════════════
#   NPS VIA WEB
# ═══════════════════════════════════════

def nps_web(request, token):
    """Pagina publica para coletar NPS via link (email/SMS)."""
    from ..models import Notificacao
    notif = get_object_or_404(Notificacao, token=token, tipo='NPS')
    atendimento = notif.atendimento

    ja_respondeu = AvaliacaoNPS.objects.filter(atendimento=atendimento).exists()

    if request.method == 'POST' and not ja_respondeu:
        nota_str = request.POST.get('nota', '')
        comentario = request.POST.get('comentario', '').strip()

        if nota_str.isdigit() and 0 <= int(nota_str) <= 10:
            nota = int(nota_str)

            AvaliacaoNPS.objects.create(
                atendimento=atendimento,
                nota=nota,
                comentario=comentario,
            )
            return render(request, 'publico/nps_obrigado.html', {
                'nota': nota,
                'cliente': atendimento.cliente,
            })

    context = {
        'atendimento': atendimento,
        'ja_respondeu': ja_respondeu,
        'notas_range': range(11),  # 0..10 (escala NPS real)
    }
    return render(request, 'publico/nps_web.html', context)


# ═══════════════════════════════════════
#   TERMOS DE CONSENTIMENTO (workflow)
# ═══════════════════════════════════════

@staff_required
def admin_termos(request):
    """Lista e gerencia versoes de termos."""
    termos = VersaoTermo.objects.select_related('procedimento').order_by('-ativa', '-vigente_desde')

    context = {
        'termos': termos,
        'procedimentos': Procedimento.objects.filter(ativo=True),
    }
    return render(request, 'painel/admin_termos.html', context)


@staff_required
def admin_criar_termo(request):
    """Cria nova versao de termo."""
    if request.method != 'POST':
        return redirect('shivazen:admin_termos')

    try:
        proc_id = request.POST.get('procedimento_id')
        procedimento = None
        if proc_id:
            procedimento = Procedimento.objects.get(pk=proc_id)

        VersaoTermo.objects.create(
            tipo=request.POST.get('tipo', 'LGPD'),
            procedimento=procedimento,
            titulo=request.POST.get('titulo', '').strip(),
            conteudo=request.POST.get('conteudo', '').strip(),
            versao=request.POST.get('versao', '1.0'),
            vigente_desde=request.POST.get('vigente_desde', timezone.now().date()),
            ativa=True,
        )
        messages.success(request, 'Termo criado com sucesso!')
    except Exception as e:
        logger.error(f'Erro ao criar termo: {e}', exc_info=True)
        messages.error(request, 'Erro ao criar termo.')

    return redirect('shivazen:admin_termos')


def termo_assinatura(request, token):
    """Pagina publica para cliente assinar termo de consentimento."""
    from ..models import Notificacao
    notif = get_object_or_404(Notificacao, token=token)
    atendimento = notif.atendimento
    cliente = atendimento.cliente

    # Buscar termos pendentes para o procedimento
    termos = VersaoTermo.objects.filter(
        Q(tipo='LGPD') | Q(procedimento=atendimento.procedimento),
        ativa=True,
    )

    # Filtrar os ja assinados
    assinados_ids = set()
    assinados_ids.update(
        AceitePrivacidade.objects.filter(cliente=cliente).values_list('versao_termo_id', flat=True)
    )
    assinados_ids.update(
        AssinaturaTermoProcedimento.objects.filter(cliente=cliente).values_list('versao_termo_id', flat=True)
    )

    termos_pendentes = [t for t in termos if t.pk not in assinados_ids]

    if request.method == 'POST':
        ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', ''))
        if ',' in ip:
            ip = ip.split(',')[0].strip()

        for termo in termos_pendentes:
            if request.POST.get(f'aceite_{termo.pk}') == '1':
                if termo.tipo == 'LGPD':
                    AceitePrivacidade.objects.get_or_create(
                        cliente=cliente, versao_termo=termo,
                        defaults={'ip': ip}
                    )
                else:
                    AssinaturaTermoProcedimento.objects.get_or_create(
                        cliente=cliente, versao_termo=termo,
                        defaults={'atendimento': atendimento, 'ip': ip}
                    )

        messages.success(request, 'Termos assinados com sucesso!')
        return render(request, 'publico/termo_obrigado.html', {'cliente': cliente})

    context = {
        'atendimento': atendimento,
        'cliente': cliente,
        'termos_pendentes': termos_pendentes,
    }
    return render(request, 'publico/termo_assinatura.html', context)
