"""Views para features pendentes: bloqueios, procedimentos, clientes detalhe,
lista de espera, NPS web, termos de consentimento."""
import logging
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from ..decorators import staff_required
from ..models import (
    AceiteTermo,
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
from ..utils.datas import hoje
from ..utils.security import safe_next
from ..validators import (
    normalizar_cpf,
    normalizar_telefone,
    validate_cpf,
    validate_data_nascimento,
    validate_telefone_br,
)

logger = logging.getLogger(__name__)

NPS_TOKEN_EXPIRY = timedelta(days=7)
TERMO_TOKEN_EXPIRY = timedelta(days=7)


def _parse_datetime_aware(valor):
    """Parseia um datetime ISO da entrada do usuario garantindo tz-aware.

    Com USE_TZ=True, um datetime naive seria interpretado no fuso default e
    deslocaria o horario salvo; tornamos aware no fuso atual.
    """
    dt = datetime.fromisoformat(valor)
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


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
        return redirect('aranha:admin_bloqueios')

    try:
        profissional = get_object_or_404(Profissional, pk=request.POST.get('profissional_id'))
        data_inicio = _parse_datetime_aware(request.POST.get('data_hora_inicio', ''))
        data_fim = _parse_datetime_aware(request.POST.get('data_hora_fim', ''))
        motivo = request.POST.get('motivo', '').strip()

        if data_fim <= data_inicio:
            messages.error(request, 'Data fim deve ser posterior a data inicio.')
            return redirect('aranha:admin_bloqueios')

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

    return redirect('aranha:admin_bloqueios')


@staff_required
def admin_excluir_bloqueio(request, bloqueio_id):
    """Exclui bloqueio de agenda via POST."""
    if request.method != 'POST':
        return redirect('aranha:admin_bloqueios')

    bloqueio = get_object_or_404(BloqueioAgenda, pk=bloqueio_id)
    # profissional nulo = bloqueio global (criavel so pelo Django admin)
    alvo = bloqueio.profissional.nome if bloqueio.profissional_id else 'todos os profissionais'
    registrar_log(request.user, f'Excluiu bloqueio de {alvo}', 'bloqueio_agenda', bloqueio_id)
    bloqueio.delete()
    messages.success(request, 'Bloqueio excluído!')
    return redirect('aranha:admin_bloqueios')


# ═══════════════════════════════════════
#   CRUD DE PROCEDIMENTOS
# ═══════════════════════════════════════

def _preco_base_vigente_map(procedimento_ids):
    """{procedimento_id: Decimal} do preco base VIGENTE (vigente_desde <= hoje).

    Preco e versionado por vigente_desde: pega a vigencia mais recente ja em
    vigor (ignora reajustes futuros agendados)."""
    mapa = {}
    for p in Preco.objects.filter(
        procedimento_id__in=procedimento_ids,
        profissional__isnull=True,
        vigente_desde__lte=hoje(),
    ).order_by('procedimento_id', '-vigente_desde', '-pk'):
        mapa.setdefault(p.procedimento_id, p.valor)
    return mapa


def _parse_preco(valor):
    """'1350,50' / '1350.50' -> Decimal >= 0; None se vazio. ValueError se invalido."""
    valor = (valor or '').strip()
    if not valor:
        return None
    try:
        preco = Decimal(valor.replace(',', '.'))
    except InvalidOperation as exc:
        raise ValueError('preco invalido') from exc
    if not preco.is_finite() or preco < 0:
        raise ValueError('preco invalido')
    return preco.quantize(Decimal('0.01'))


def _parse_duracao(valor, padrao=30):
    try:
        duracao = int(valor if valor not in (None, '') else padrao)
    except (TypeError, ValueError) as exc:
        raise ValueError('duracao invalida') from exc
    if duracao < 5 or duracao > 600:
        raise ValueError('duracao invalida')
    return duracao


def _categoria_valida(valor, padrao='OUTRO'):
    validas = dict(Procedimento.CATEGORIA_CHOICES)
    return valor if valor in validas else padrao


@staff_required
def admin_procedimentos(request):
    """Lista e gerencia procedimentos."""
    procedimentos = list(
        Procedimento.objects.all().order_by('-ativo', 'categoria', 'nome')
    )

    # Preco base vigente (profissional=NULL) por procedimento — um único query
    preco_map = _preco_base_vigente_map([pr.pk for pr in procedimentos])
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
        return redirect('aranha:admin_procedimentos')

    nome = request.POST.get('nome', '').strip()
    if not nome:
        messages.error(request, 'Informe o nome do procedimento.')
        return redirect('aranha:admin_procedimentos')
    try:
        duracao = _parse_duracao(request.POST.get('duracao_minutos'))
        preco = _parse_preco(request.POST.get('preco'))
    except ValueError:
        messages.error(request, 'Duração ou preço inválido.')
        return redirect('aranha:admin_procedimentos')

    try:
        with transaction.atomic():
            proc = Procedimento.objects.create(
                nome=nome,
                descricao=request.POST.get('descricao', '').strip(),
                duracao_minutos=duracao,
                categoria=_categoria_valida(request.POST.get('categoria')),
                ativo=True,
            )
            # Preco base (sem profissional), vigente a partir de hoje
            if preco is not None:
                Preco.objects.create(procedimento=proc, valor=preco, vigente_desde=hoje())

        registrar_log(request.user, f'Criou procedimento: {proc.nome}', 'procedimento', proc.pk)
        messages.success(request, f'Procedimento "{proc.nome}" criado!')
    except (DatabaseError, ValidationError) as e:
        logger.error(f'Erro ao criar procedimento: {e}', exc_info=True)
        messages.error(request, 'Erro ao criar procedimento.')

    return redirect('aranha:admin_procedimentos')


@staff_required
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_editar_procedimento(request, pk):
    """Edita procedimento via POST.

    Preco e versionado (Preco.vigente_desde): mudar o valor cria uma nova
    vigencia a partir de hoje em vez de sobrescrever o historico (se ja
    houver vigencia de hoje, ela e ajustada).
    """
    proc = get_object_or_404(Procedimento, pk=pk)
    if request.method != 'POST':
        return redirect('aranha:admin_procedimentos')

    nome = request.POST.get('nome', proc.nome).strip()
    if not nome:
        messages.error(request, 'Informe o nome do procedimento.')
        return redirect('aranha:admin_procedimentos')
    try:
        duracao = _parse_duracao(request.POST.get('duracao_minutos'), proc.duracao_minutos)
        preco = _parse_preco(request.POST.get('preco'))
    except ValueError:
        messages.error(request, 'Duração ou preço inválido.')
        return redirect('aranha:admin_procedimentos')

    try:
        with transaction.atomic():
            proc.nome = nome
            proc.descricao = request.POST.get('descricao', '').strip()
            proc.duracao_minutos = duracao
            proc.categoria = _categoria_valida(request.POST.get('categoria'), proc.categoria)
            proc.ativo = request.POST.get('ativo') == '1'
            proc.save()

            if preco is not None:
                vigente = _preco_base_vigente_map([proc.pk]).get(proc.pk)
                if vigente is None or vigente != preco:
                    Preco.objects.update_or_create(
                        procedimento=proc, profissional=None, vigente_desde=hoje(),
                        defaults={'valor': preco},
                    )

        registrar_log(request.user, f'Editou procedimento: {proc.nome}', 'procedimento', proc.pk)
        messages.success(request, f'Procedimento "{proc.nome}" atualizado!')
    except (DatabaseError, ValidationError) as e:
        logger.error(f'Erro ao editar procedimento: {e}', exc_info=True)
        messages.error(request, 'Erro ao editar procedimento.')

    return redirect('aranha:admin_procedimentos')


# ═══════════════════════════════════════
#   DETALHE / EDICAO DE CLIENTE
# ═══════════════════════════════════════

# Limites dos CharFields de Cliente (evita DataError/500 no Postgres)
_CLIENTE_MAX = {'nome': 150, 'rg': 20, 'profissao': 100, 'cep': 10, 'endereco': 255}


def _normalizar_telefone_br(valor):
    """Somente digitos; remove DDI 55 colado (autofill '+55 17 9...')."""
    digitos = normalizar_telefone(valor)
    if len(digitos) in (12, 13) and digitos.startswith('55'):
        digitos = digitos[2:]
    return digitos


def _aplicar_post_cliente(request, cliente):
    """Aplica o POST no cliente EM MEMORIA e devolve a lista de erros.

    Valida telefone/CPF/e-mail/data e a unicidade entre clientes ativos
    (mesma condicao das UniqueConstraint parciais) antes do save, para
    responder com mensagem em vez de IntegrityError/500.
    """
    post = request.POST
    erros = []

    def _texto(campo):
        return (post.get(campo) or '').strip()

    nome = _texto('nome')
    if not nome:
        erros.append('Informe o nome do cliente.')
    for campo, maximo in _CLIENTE_MAX.items():
        if len(_texto(campo)) > maximo:
            erros.append(f'O campo {campo} aceita no máximo {maximo} caracteres.')

    telefone = _normalizar_telefone_br(_texto('telefone')) or None
    if telefone:
        try:
            validate_telefone_br(telefone)
        except ValidationError:
            erros.append('Telefone inválido: informe DDD + número (10 ou 11 dígitos).')
        else:
            if Cliente.objects.filter(telefone=telefone).exclude(pk=cliente.pk).exists():
                erros.append('Este telefone já pertence a outro cliente.')

    cpf = normalizar_cpf(_texto('cpf')) or None
    if cpf:
        try:
            validate_cpf(cpf)
        except ValidationError:
            erros.append('CPF inválido.')
        else:
            if Cliente.objects.filter(cpf=cpf).exclude(pk=cliente.pk).exists():
                erros.append('Este CPF já pertence a outro cliente.')

    email = _texto('email') or None
    if email:
        try:
            validate_email(email)
        except ValidationError:
            erros.append('E-mail inválido.')
        else:
            if Cliente.objects.filter(email__iexact=email).exclude(pk=cliente.pk).exists():
                erros.append('Este e-mail já pertence a outro cliente.')

    data_nascimento = None
    data_nasc_raw = _texto('data_nascimento')
    if data_nasc_raw:
        try:
            data_nascimento = datetime.strptime(data_nasc_raw, '%Y-%m-%d').date()
            validate_data_nascimento(data_nascimento)
        except (ValueError, ValidationError):
            erros.append('Data de nascimento inválida.')
            data_nascimento = cliente.data_nascimento

    cliente.nome = nome or cliente.nome
    cliente.telefone = telefone
    cliente.email = email
    cliente.cpf = cpf
    cliente.rg = _texto('rg') or None
    cliente.profissao = _texto('profissao') or None
    cliente.cep = _texto('cep') or None
    cliente.endereco = _texto('endereco') or None
    cliente.data_nascimento = data_nascimento
    cliente.ativo = post.get('ativo') == '1'
    cliente.aceita_comunicacao = post.get('aceita_comunicacao') == '1'
    return erros


def _contexto_cliente(cliente):
    from django.db.models import Avg, Count, Max, Sum

    atendimentos = Atendimento.objects.filter(
        cliente=cliente
    ).select_related('profissional', 'procedimento').order_by('-data_hora_inicio')[:20]

    realizados_qs = Atendimento.objects.filter(cliente=cliente, status='REALIZADO')
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
    return {
        'cliente': cliente,
        'atendimentos': atendimentos,
        'cliente_stats': cliente_stats,
    }


@staff_required
def admin_cliente_detalhe(request, pk):
    """Detalhe e edicao de cliente."""
    cliente = get_object_or_404(Cliente, pk=pk)

    if request.method == 'POST':
        erros = _aplicar_post_cliente(request, cliente)
        if not erros:
            try:
                with transaction.atomic():
                    cliente.save()
            except IntegrityError:
                # corrida com outro cadastro entre a checagem e o save
                erros = ['Telefone, e-mail ou CPF já cadastrado para outro cliente.']
        if erros:
            # Nada gravado: re-renderiza com o que foi digitado + erros
            for erro in erros:
                messages.error(request, erro)
            return render(request, 'painel/cliente_detalhe.html', _contexto_cliente(cliente))
        registrar_log(request.user, f'Editou cliente: {cliente.nome}', 'cliente', cliente.pk)
        messages.success(request, 'Cliente atualizado!')
        return redirect('aranha:admin_cliente_detalhe', pk=pk)

    return render(request, 'painel/cliente_detalhe.html', _contexto_cliente(cliente))


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
    """Avisa o cliente da lista de espera e marca o item como avisado.

    Se o cliente tem e-mail, envia o aviso de vaga (utils.email); o canal
    pode estar sem provedor em prod (retorna False) — a mensagem diz o que
    de fato aconteceu para a recepcao completar o contato pelo WhatsApp.
    """
    if request.method != 'POST':
        return redirect('aranha:admin_lista_espera')

    item = get_object_or_404(
        ListaEspera.objects.select_related('cliente', 'procedimento'), pk=pk,
    )
    enviado = False
    # e-mail do pedido da lista (quando o form publico coleta) > e-mail do cadastro
    destino = getattr(item, 'email_contato', None) or item.cliente.email
    if destino:
        from ..utils.email import enviar_fila_espera_email
        try:
            enviado = bool(enviar_fila_espera_email(destino, {
                'nome': item.cliente.nome,
                'procedimento': item.procedimento.nome,
                'data': item.data_desejada.strftime('%d/%m/%Y'),
            }))
        except Exception:  # pylint: disable=broad-except
            logger.warning('lista_espera_email_falhou', exc_info=True, extra={'espera_id': item.pk})

    item.notificado = True
    item.save(update_fields=['notificado'])
    registrar_log(
        request.user,
        f'Avisou lista de espera ({"e-mail enviado" if enviado else "contato manual"})',
        'lista_espera', item.pk,
    )
    if enviado:
        messages.success(request, f'Aviso de vaga enviado por e-mail para {item.cliente.nome}.')
    else:
        messages.info(
            request,
            f'{item.cliente.nome} marcado como avisado. Nenhum e-mail foi enviado — '
            'faça o contato pelo WhatsApp ou telefone.',
        )
    return redirect('aranha:admin_lista_espera')


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
                # opt-in explicito p/ virar depoimento publico (moderado no painel)
                autoriza_publicacao=request.POST.get('autoriza_publicacao') in ('1', 'on', 'true'),
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
    """Publica nova versao de termo.

    So 1 versao ativa por escopo (uniq_termo_ativo_*): a vigente do mesmo
    tipo/procedimento e arquivada na mesma transacao. Clientes que aceitaram
    a versao anterior passam a ter o termo novo pendente (versionamento).
    """
    if request.method != 'POST':
        return redirect('aranha:admin_termos')

    tipo = request.POST.get('tipo', 'LGPD')
    if tipo not in dict(VersaoTermo.TIPO_CHOICES):
        messages.error(request, 'Tipo de termo inválido.')
        return redirect('aranha:admin_termos')

    titulo = request.POST.get('titulo', '').strip()
    conteudo = request.POST.get('conteudo', '').strip()
    versao = request.POST.get('versao', '').strip() or '1.0'
    if not titulo or not conteudo:
        messages.error(request, 'Informe título e conteúdo do termo.')
        return redirect('aranha:admin_termos')
    if len(versao) > 20:
        messages.error(request, 'Versão deve ter no máximo 20 caracteres.')
        return redirect('aranha:admin_termos')

    procedimento = None
    proc_id = request.POST.get('procedimento_id', '')
    if tipo == 'PROCEDIMENTO' and proc_id:
        if not proc_id.isdigit():
            messages.error(request, 'Procedimento inválido.')
            return redirect('aranha:admin_termos')
        procedimento = Procedimento.objects.filter(pk=int(proc_id)).first()
        if procedimento is None:
            messages.error(request, 'Procedimento não encontrado.')
            return redirect('aranha:admin_termos')

    vigente_raw = request.POST.get('vigente_desde', '').strip()
    try:
        vigente_desde = datetime.strptime(vigente_raw, '%Y-%m-%d').date() if vigente_raw else hoje()
    except ValueError:
        messages.error(request, 'Data de vigência inválida.')
        return redirect('aranha:admin_termos')

    try:
        with transaction.atomic():
            # filter(procedimento=None) vira IS NULL — escopo global do tipo
            arquivadas = VersaoTermo.objects.filter(
                tipo=tipo, procedimento=procedimento, ativa=True,
            ).update(ativa=False)
            novo = VersaoTermo.objects.create(
                tipo=tipo,
                procedimento=procedimento,
                titulo=titulo,
                conteudo=conteudo,
                versao=versao,
                vigente_desde=vigente_desde,
                ativa=True,
            )
    except (IntegrityError, DatabaseError, ValidationError) as e:
        logger.error(f'Erro ao publicar termo: {e}', exc_info=True)
        messages.error(request, 'Não foi possível publicar o termo. Tente novamente.')
        return redirect('aranha:admin_termos')

    registrar_log(
        request.user, f'Publicou termo {tipo} v{versao}', 'versao_termo', novo.pk,
        {'arquivadas': arquivadas},
    )
    if arquivadas:
        messages.success(request, 'Nova versão publicada; a versão anterior foi arquivada.')
    else:
        messages.success(request, 'Termo publicado!')
    return redirect('aranha:admin_termos')


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def termo_assinatura(request, token):
    """Pagina publica para cliente assinar termo de consentimento."""
    notif = get_object_or_404(Notificacao, token=token)
    atendimento = notif.atendimento
    cliente = atendimento.cliente

    # SEGURANCA: limitar a janela de uso do link (analogo ao NPS_TOKEN_EXPIRY).
    # Sem isso, um token antigo abriria a assinatura de termos indefinidamente.
    if timezone.now() - notif.criado_em > TERMO_TOKEN_EXPIRY:
        return render(request, 'publico/termo_obrigado.html', {
            'cliente': cliente,
            'expirado': True,
        }, status=410)

    # Buscar termos pendentes para o procedimento
    termos = VersaoTermo.objects.filter(
        Q(tipo='LGPD')
        | Q(tipo='PROCEDIMENTO', procedimento=atendimento.procedimento)
        | Q(tipo='PROCEDIMENTO', procedimento__isnull=True),
        ativa=True,
    )

    # Filtrar os ja assinados
    assinados_ids = set(
        AceiteTermo.objects.filter(cliente=cliente).values_list('versao_termo_id', flat=True)
    )

    termos_pendentes = [t for t in termos if t.pk not in assinados_ids]

    if request.method == 'POST':
        from ..utils.security import client_ip
        ip = client_ip(request)

        for termo in termos_pendentes:
            if request.POST.get(f'aceite_{termo.pk}') == '1':
                if termo.tipo == 'LGPD':
                    AceiteTermo.objects.get_or_create(
                        cliente=cliente, versao_termo=termo,
                        defaults={'ip': ip}
                    )
                else:
                    AceiteTermo.objects.get_or_create(
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
        'contexto': {'codigo': '123456', 'clinic_name': 'Jaqueline Aranha Estética',
                     'preheader': 'Seu codigo de verificacao'},
    },
    'confirmacao': {
        'template': 'email/confirmacao.html',
        'contexto': {'dados': {
            'nome': 'Maria Silva', 'procedimento': 'Limpeza de Pele',
            'profissional': 'Jaqueline Aranha', 'data_hora': '20/04/2026 as 14:00',
            'valor': '180,00',
        }, 'clinic_name': 'Jaqueline Aranha Estética'},
    },
    'aniversario': {
        'template': 'email/aniversario.html',
        'contexto': {'dados': {'nome': 'Maria Silva', 'desconto': 15},
                     'clinic_name': 'Jaqueline Aranha Estética',
                     'unsub_url': '#preview', 'preheader': 'Presente de aniversario'},
    },
    'promocao': {
        'template': 'email/promocao.html',
        'contexto': {'dados': {
            'nome': 'Maria Silva', 'corpo_html': '<p>Desconto especial!</p>',
            'cupom': 'VIP15', 'validade': '30/05/2026',
        }, 'clinic_name': 'Jaqueline Aranha Estética', 'unsub_url': '#preview'},
    },
    'cancelamento': {
        'template': 'email/cancelamento.html',
        'contexto': {'dados': {
            'nome': 'Maria Silva', 'procedimento': 'Limpeza de Pele',
            'data_hora': '20/04/2026 as 14:00', 'profissional': 'Jaqueline Aranha',
        }, 'clinic_name': 'Jaqueline Aranha Estética'},
    },
    'nps': {
        'template': 'email/nps.html',
        'contexto': {'dados': {
            'nome': 'Maria Silva', 'procedimento': 'Limpeza de Pele',
            'link': '#preview',
        }, 'clinic_name': 'Jaqueline Aranha Estética'},
    },
    'fila_espera': {
        'template': 'email/fila_espera.html',
        'contexto': {'dados': {
            'nome': 'Maria Silva', 'procedimento': 'Limpeza de Pele',
            'data': '20/04/2026',
        }, 'clinic_name': 'Jaqueline Aranha Estética'},
    },
    'pacote_expirando': {
        'template': 'email/pacote_expirando.html',
        'contexto': {'dados': {
            'nome': 'Maria Silva', 'pacote': 'Facial Premium',
            'dias': 7, 'sessoes_restantes': 3,
        }, 'clinic_name': 'Jaqueline Aranha Estética'},
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


def _voltar(request):
    """Volta p/ a pagina de origem (Referer local) ou p/ a lista de agendamentos."""
    return redirect(safe_next(request, request.META.get('HTTP_REFERER'), 'aranha:painel_agendamentos'))


@staff_required
@require_POST
@ratelimit(key='user', rate='60/m', method='POST', block=True)
def admin_aprovar_agendamento(request, pk):
    """Gerente/recepção aprova agendamento PENDENTE → AGENDADO.

    View thin: delega regras + email + audit ao AgendamentoService.
    """
    atendimento = get_object_or_404(
        Atendimento.objects.select_related('cliente', 'procedimento', 'profissional'),
        pk=pk,
    )
    from ..services.agendamento_service import AgendamentoService
    try:
        transitou = AgendamentoService().aprovar(atendimento, by_user=request.user)
    except Atendimento.TransicaoInvalida:
        transitou = False
    if not transitou:
        messages.warning(request, f'Atendimento já está como {atendimento.get_status_display().lower()}.')
    else:
        messages.success(request, f'Agendamento de {atendimento.cliente.nome} aprovado.')
    return _voltar(request)


@staff_required
@require_POST
@ratelimit(key='user', rate='60/m', method='POST', block=True)
def admin_rejeitar_agendamento(request, pk):
    """Gerente/recepção rejeita agendamento PENDENTE → CANCELADO.

    View thin: delega regras + email + audit ao AgendamentoService.
    """
    atendimento = get_object_or_404(
        Atendimento.objects.select_related('cliente', 'procedimento', 'profissional'),
        pk=pk,
    )
    from ..services.agendamento_service import AgendamentoService
    try:
        transitou = AgendamentoService().rejeitar(atendimento, by_user=request.user)
    except Atendimento.TransicaoInvalida:
        transitou = False
    if not transitou:
        messages.warning(request, f'Atendimento já está como {atendimento.get_status_display().lower()}.')
    else:
        messages.success(request, f'Agendamento de {atendimento.cliente.nome} rejeitado.')
    return _voltar(request)


@staff_required
@require_POST
@ratelimit(key='user', rate='10/m', method='POST', block=True)
def admin_bulk_agendamentos(request):
    """Aprovacao/rejeicao em massa de agendamentos PENDENTES.

    POST: ids=[...] + acao=aprovar|rejeitar. Cada item passa pelo
    AgendamentoService (FSM + auditoria + e-mail pos-commit com hora local);
    item que mudou de estado no meio do caminho e ignorado sem abortar o lote.
    """
    ids_raw = request.POST.getlist('ids') or []
    acao = request.POST.get('acao', '').strip().lower()

    if acao not in ('aprovar', 'rejeitar'):
        messages.error(request, 'Ação inválida.')
        return _voltar(request)

    try:
        ids = [int(x) for x in ids_raw]
    except (TypeError, ValueError):
        messages.error(request, 'Seleção inválida.')
        return _voltar(request)

    if not ids:
        messages.warning(request, 'Nenhum agendamento selecionado.')
        return _voltar(request)

    from ..services.agendamento_service import AgendamentoService
    service = AgendamentoService()

    processados = 0
    with transaction.atomic():
        atendimentos = list(
            Atendimento.objects.select_related('cliente', 'procedimento', 'profissional')
            .filter(pk__in=ids, status='PENDENTE')
        )
        for at in atendimentos:
            try:
                if acao == 'aprovar':
                    ok = service.aprovar(at, by_user=request.user)
                else:
                    ok = service.rejeitar(at, motivo='Rejeitado em lote', by_user=request.user)
            except Atendimento.TransicaoInvalida:
                ok = False
            if ok:
                processados += 1

    ignorados = len(ids) - processados
    msg_acao = 'aprovado(s)' if acao == 'aprovar' else 'rejeitado(s)'
    msg = f'{processados} agendamento(s) {msg_acao}.'
    if ignorados:
        msg += f' {ignorados} ignorado(s) (não estavam pendentes).'
    messages.success(request, msg)
    return _voltar(request)
