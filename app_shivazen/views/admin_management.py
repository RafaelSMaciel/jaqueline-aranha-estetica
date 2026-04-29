"""Views para features pendentes: bloqueios, procedimentos, clientes detalhe,
lista de espera, NPS web, termos de consentimento."""
import logging
from datetime import datetime, timedelta

NPS_TOKEN_EXPIRY = timedelta(days=7)

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from ..decorators import staff_required
from ..models import (
    AceitePrivacidade,
    AssinaturaTermoProcedimento,
    Atendimento,
    AvaliacaoNPS,
    BloqueioAgenda,
    Cliente,
    ListaEspera,
    Notificacao,
    Preco,
    Procedimento,
    Profissional,
    VersaoTermo,
)
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
    return render(request, 'painel/bloqueios.html', context)


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
    return render(request, 'painel/procedimentos.html', context)


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
        cliente.rg = request.POST.get('rg', '').strip() or None
        cliente.profissao = request.POST.get('profissao', '').strip() or None
        cliente.cep = request.POST.get('cep', '').strip() or None
        cliente.endereco = request.POST.get('endereco', '').strip() or None
        data_nasc = request.POST.get('data_nascimento', '')
        if data_nasc:
            try:
                cliente.data_nascimento = datetime.strptime(data_nasc, '%Y-%m-%d').date()
            except ValueError:
                pass
        cliente.ativo = request.POST.get('ativo') == '1'
        cliente.aceita_comunicacao = request.POST.get('aceita_comunicacao') == '1'
        cliente.save()
        registrar_log(request.user, f'Editou cliente: {cliente.nome_completo}', 'cliente', cliente.pk)
        messages.success(request, 'Paciente atualizado!')
        return redirect('shivazen:admin_cliente_detalhe', pk=pk)

    atendimentos = Atendimento.objects.filter(
        cliente=cliente
    ).select_related('profissional', 'procedimento').order_by('-data_hora_inicio')[:20]

    realizados_qs = Atendimento.objects.filter(cliente=cliente, status='REALIZADO')
    from django.db.models import Sum, Count, Avg, Max
    agg = realizados_qs.aggregate(
        total=Sum('valor_cobrado'),
        qtd=Count('pk'),
        ticket_medio=Avg('valor_cobrado'),
        ultima=Max('data_hora_inicio'),
    )
    proc_top = realizados_qs.values(
        'procedimento__nome'
    ).annotate(c=Count('pk')).order_by('-c').first()

    cliente_stats = {
        'ltv': agg['total'] or 0,
        'qtd_realizados': agg['qtd'] or 0,
        'ticket_medio': agg['ticket_medio'] or 0,
        'ultima_visita': agg['ultima'],
        'procedimento_mais_frequente': proc_top['procedimento__nome'] if proc_top else None,
        'no_show_count': Atendimento.objects.filter(cliente=cliente, status='FALTOU').count(),
        'cancelados_count': Atendimento.objects.filter(cliente=cliente, status='CANCELADO').count(),
    }

    context = {
        'cliente': cliente,
        'atendimentos': atendimentos,
        'cliente_stats': cliente_stats,
    }
    return render(request, 'painel/cliente_detalhe.html', context)


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
    return render(request, 'painel/lista_espera.html', context)


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

@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def nps_web(request, token):
    """Pagina publica para coletar NPS via link (email/SMS)."""
    notif = get_object_or_404(Notificacao, token=token, tipo='NPS')
    atendimento = notif.atendimento

    idade_token = timezone.now() - notif.criado_em
    if idade_token > NPS_TOKEN_EXPIRY:
        return render(request, 'publico/nps_obrigado.html', {
            'expirado': True,
            'cliente': atendimento.cliente,
        }, status=410)

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
    return render(request, 'painel/termos.html', context)


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


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def termo_assinatura(request, token):
    """Pagina publica para cliente assinar termo de consentimento."""
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
        from ..utils.security import client_ip
        ip = client_ip(request)

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


# ═══════════════════════════════════════
#   EMAIL PREVIEW (staff debug)
# ═══════════════════════════════════════

EMAIL_PREVIEW_FIXTURES = {
    'otp': {
        'template': 'email/otp.html',
        'contexto': {'codigo': '123456', 'clinic_name': 'Dra. Jaqueline Aranha',
                     'preheader': 'Seu codigo de verificacao'},
    },
    'confirmacao': {
        'template': 'email/confirmacao.html',
        'contexto': {'dados': {
            'nome': 'Maria Silva', 'procedimento': 'Limpeza de Pele',
            'profissional': 'Dra. Jaqueline Aranha', 'data_hora': '20/04/2026 as 14:00',
            'valor': '180,00',
        }, 'clinic_name': 'Dra. Jaqueline Aranha'},
    },
    'aniversario': {
        'template': 'email/aniversario.html',
        'contexto': {'dados': {'nome': 'Maria Silva', 'desconto': 15},
                     'clinic_name': 'Dra. Jaqueline Aranha',
                     'unsub_url': '#preview', 'preheader': 'Presente de aniversario'},
    },
    'promocao': {
        'template': 'email/promocao.html',
        'contexto': {'dados': {
            'nome': 'Maria Silva', 'corpo_html': '<p>Desconto especial!</p>',
            'cupom': 'VIP15', 'validade': '30/05/2026',
        }, 'clinic_name': 'Dra. Jaqueline Aranha', 'unsub_url': '#preview'},
    },
    'cancelamento': {
        'template': 'email/cancelamento.html',
        'contexto': {'dados': {
            'nome': 'Maria Silva', 'procedimento': 'Limpeza de Pele',
            'data_hora': '20/04/2026 as 14:00', 'profissional': 'Dra. Jaqueline Aranha',
        }, 'clinic_name': 'Dra. Jaqueline Aranha'},
    },
    'nps': {
        'template': 'email/nps.html',
        'contexto': {'dados': {
            'nome': 'Maria Silva', 'procedimento': 'Limpeza de Pele',
            'link': '#preview',
        }, 'clinic_name': 'Dra. Jaqueline Aranha'},
    },
    'fila_espera': {
        'template': 'email/fila_espera.html',
        'contexto': {'dados': {
            'nome': 'Maria Silva', 'procedimento': 'Limpeza de Pele',
            'data': '20/04/2026',
        }, 'clinic_name': 'Dra. Jaqueline Aranha'},
    },
    'pacote_expirando': {
        'template': 'email/pacote_expirando.html',
        'contexto': {'dados': {
            'nome': 'Maria Silva', 'pacote': 'Facial Premium',
            'dias': 7, 'sessoes_restantes': 3,
        }, 'clinic_name': 'Dra. Jaqueline Aranha'},
    },
}


@staff_required
def admin_email_preview(request, nome=None):
    """Preview de templates de email para staff. Renderiza com fixture."""
    from django.http import HttpResponse
    from django.template.loader import render_to_string

    if nome is None or nome not in EMAIL_PREVIEW_FIXTURES:
        lista = '<br>'.join(
            f'<a href="/painel/email-preview/{k}/">{k}</a>' for k in EMAIL_PREVIEW_FIXTURES
        )
        return HttpResponse(f'<h1>Email Previews</h1>{lista}')

    fx = EMAIL_PREVIEW_FIXTURES[nome]
    html = render_to_string(fx['template'], fx['contexto'])
    return HttpResponse(html)


# ═══════════════════════════════════════
#   APROVACAO — STAFF / GERENTE
# ═══════════════════════════════════════

from django.views.decorators.http import require_POST


@staff_required
@require_POST
@ratelimit(key='user', rate='60/m', method='POST', block=True)
def admin_aprovar_agendamento(request, pk):
    """Gerente/recepção aprova agendamento PENDENTE → AGENDADO."""
    atendimento = get_object_or_404(
        Atendimento.objects.select_related('cliente', 'procedimento', 'profissional'),
        pk=pk,
    )
    if atendimento.status != 'PENDENTE':
        messages.warning(request, f'Atendimento já está como {atendimento.get_status_display().lower()}.')
        return redirect(request.META.get('HTTP_REFERER', 'shivazen:painel_agendamentos'))

    atendimento.status = 'AGENDADO'
    atendimento.save()
    registrar_log(request.user, 'Aprovou agendamento', 'atendimento', atendimento.pk)

    if atendimento.cliente.email:
        data_fmt = atendimento.data_hora_inicio.strftime('%d/%m/%Y as %H:%M')
        dados = {
            'nome': atendimento.cliente.nome_completo,
            'procedimento': atendimento.procedimento.nome,
            'profissional': atendimento.profissional.nome,
            'data_hora': data_fmt,
            'valor': f'R$ {float(atendimento.valor_cobrado):.2f}' if atendimento.valor_cobrado else 'A consultar',
        }
        from ..tasks import send_email_async
        try:
            send_email_async.delay('enviar_confirmacao_agendamento_email',
                                   atendimento.cliente.email, dados)
        except Exception:
            from ..utils.email import enviar_confirmacao_agendamento_email
            enviar_confirmacao_agendamento_email(atendimento.cliente.email, dados)

    messages.success(request, f'Agendamento de {atendimento.cliente.nome_completo} aprovado.')
    return redirect(request.META.get('HTTP_REFERER', 'shivazen:painel_agendamentos'))


@staff_required
@require_POST
@ratelimit(key='user', rate='60/m', method='POST', block=True)
def admin_rejeitar_agendamento(request, pk):
    """Gerente/recepção rejeita agendamento PENDENTE → CANCELADO."""
    atendimento = get_object_or_404(
        Atendimento.objects.select_related('cliente', 'procedimento', 'profissional'),
        pk=pk,
    )
    if atendimento.status != 'PENDENTE':
        messages.warning(request, f'Atendimento já está como {atendimento.get_status_display().lower()}.')
        return redirect(request.META.get('HTTP_REFERER', 'shivazen:painel_agendamentos'))

    atendimento.status = 'CANCELADO'
    atendimento.save()
    registrar_log(request.user, 'Rejeitou agendamento', 'atendimento', atendimento.pk)

    if atendimento.cliente.email:
        data_fmt = atendimento.data_hora_inicio.strftime('%d/%m/%Y as %H:%M')
        dados = {
            'nome': atendimento.cliente.nome_completo,
            'procedimento': atendimento.procedimento.nome,
            'profissional': atendimento.profissional.nome,
            'data_hora': data_fmt,
        }
        from ..tasks import send_email_async
        try:
            send_email_async.delay('enviar_cancelamento_email', atendimento.cliente.email, dados)
        except Exception:
            from ..utils.email import enviar_cancelamento_email
            enviar_cancelamento_email(atendimento.cliente.email, dados)

    messages.success(request, f'Agendamento de {atendimento.cliente.nome_completo} rejeitado.')
    return redirect(request.META.get('HTTP_REFERER', 'shivazen:painel_agendamentos'))


@staff_required
@require_POST
@ratelimit(key='user', rate='10/m', method='POST', block=True)
def admin_bulk_agendamentos(request):
    """Aprovacao/rejeicao em massa de agendamentos PENDENTES.

    POST: ids=[...] + acao=aprovar|rejeitar
    """
    ids_raw = request.POST.getlist('ids') or []
    acao = request.POST.get('acao', '').strip().lower()
    redirect_to = request.META.get('HTTP_REFERER') or 'shivazen:painel_agendamentos'

    if acao not in ('aprovar', 'rejeitar'):
        messages.error(request, 'Acao invalida.')
        return redirect(redirect_to)

    try:
        ids = [int(x) for x in ids_raw]
    except (TypeError, ValueError):
        messages.error(request, 'IDs invalidos.')
        return redirect(redirect_to)

    if not ids:
        messages.warning(request, 'Nenhum agendamento selecionado.')
        return redirect(redirect_to)

    from ..tasks import send_email_async
    from ..utils.email import (
        enviar_confirmacao_agendamento_email, enviar_cancelamento_email,
    )

    novo_status = 'AGENDADO' if acao == 'aprovar' else 'CANCELADO'
    atendimentos = list(
        Atendimento.objects.select_related('cliente', 'procedimento', 'profissional')
        .filter(pk__in=ids, status='PENDENTE')
    )

    processados = 0
    for at in atendimentos:
        at.status = novo_status
        at.save(update_fields=['status', 'atualizado_em'])
        registrar_log(
            request.user,
            ('Aprovou agendamento (bulk)' if acao == 'aprovar'
             else 'Rejeitou agendamento (bulk)'),
            'atendimento', at.pk,
        )

        if at.cliente.email:
            data_fmt = at.data_hora_inicio.strftime('%d/%m/%Y as %H:%M')
            dados = {
                'nome': at.cliente.nome_completo,
                'procedimento': at.procedimento.nome,
                'profissional': at.profissional.nome,
                'data_hora': data_fmt,
            }
            if acao == 'aprovar':
                dados['valor'] = (
                    f'R$ {float(at.valor_cobrado):.2f}'
                    if at.valor_cobrado else 'A consultar'
                )
                try:
                    send_email_async.delay(
                        'enviar_confirmacao_agendamento_email',
                        at.cliente.email, dados,
                    )
                except Exception:
                    enviar_confirmacao_agendamento_email(at.cliente.email, dados)
            else:
                try:
                    send_email_async.delay(
                        'enviar_cancelamento_email',
                        at.cliente.email, dados,
                    )
                except Exception:
                    enviar_cancelamento_email(at.cliente.email, dados)

        processados += 1

    ignorados = len(ids) - processados
    msg_acao = 'aprovado(s)' if acao == 'aprovar' else 'rejeitado(s)'
    msg = f'{processados} agendamento(s) {msg_acao}.'
    if ignorados:
        msg += f' {ignorados} ignorado(s) (nao estavam PENDENTE).'
    messages.success(request, msg)
    return redirect(redirect_to)
