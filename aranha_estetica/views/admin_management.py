"""Views para features pendentes: bloqueios, procedimentos, clientes detalhe,
lista de espera, NPS web, termos de consentimento."""
import logging
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError, IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
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
from ..services.termos import (
    Q_NOTIF_TERMO,
    aceita_assinatura,
    obter_link_termo,
    termos_pendentes,
)
from ..utils.audit import registrar_log
from ..utils.datas import hoje
from ..utils.parse import id_int
from ..utils.security import safe_next
from ..validators import normalizar_telefone

logger = logging.getLogger(__name__)

NPS_TOKEN_EXPIRY = timedelta(days=7)
# Link do termo: vale ate o fim do atendimento (services.termos.aceita_assinatura)


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
        prof_id = request.POST.get('profissional_id', '')
        # 'todos' = bloqueio global (profissional nulo): vale p/ a agenda inteira
        profissional = None if prof_id == 'todos' else get_object_or_404(Profissional, pk=prof_id)
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
        alvo = profissional.nome if profissional else 'todos os profissionais'
        registrar_log(request.user, f'Criou bloqueio para {alvo}', 'bloqueio_agenda', bloqueio.pk, request=request)
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
    # profissional nulo = bloqueio global ('Todos os profissionais')
    alvo = bloqueio.profissional.nome if bloqueio.profissional_id else 'todos os profissionais'
    registrar_log(request.user, f'Excluiu bloqueio de {alvo}', 'bloqueio_agenda', bloqueio_id, request=request)
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


# Teto dos campos de valor (DecimalField max_digits=10, decimal_places=2)
VALOR_MAX = Decimal('99999999.99')


def _parse_preco(valor):
    """'1350,50' / '1350.50' -> Decimal em [0, VALOR_MAX]; None se vazio. ValueError se invalido.

    O teto e conferido ANTES do quantize: '1e30' estourava o contexto decimal
    (InvalidOperation, que nao e ValueError -> 500) e '123456789' passava e
    so falhava no banco (numeric field overflow no Postgres).
    """
    valor = (valor or '').strip()
    if not valor:
        return None
    try:
        preco = Decimal(valor.replace(',', '.'))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError('preco invalido') from exc
    if not preco.is_finite() or preco < 0 or preco > VALOR_MAX:
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

        registrar_log(request.user, f'Criou procedimento: {proc.nome}', 'procedimento', proc.pk, request=request)
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

        registrar_log(request.user, f'Editou procedimento: {proc.nome}', 'procedimento', proc.pk, request=request)
        messages.success(request, f'Procedimento "{proc.nome}" atualizado!')
    except (DatabaseError, ValidationError) as e:
        logger.error(f'Erro ao editar procedimento: {e}', exc_info=True)
        messages.error(request, 'Erro ao editar procedimento.')

    return redirect('aranha:admin_procedimentos')


# ═══════════════════════════════════════
#   DETALHE / EDICAO DE CLIENTE
# ═══════════════════════════════════════

def _normalizar_telefone_br(valor):
    """Somente digitos; remove DDI 55 colado (autofill '+55 17 9...')."""
    digitos = normalizar_telefone(valor)
    if len(digitos) in (12, 13) and digitos.startswith('55'):
        digitos = digitos[2:]
    return digitos


def _contexto_cliente(cliente):
    from django.db.models import Avg, Count, Max, Sum

    from ..models import CompraPacote, ConsumoSessao
    from ..services.termos import ids_com_termo_procedimento_pendente
    from ..utils.saude import alertas_saude

    atendimentos = list(
        Atendimento.objects.filter(cliente=cliente)
        .select_related('profissional', 'procedimento').order_by('-data_hora_inicio')[:20]
    )
    sem_termo = ids_com_termo_procedimento_pendente(atendimentos)
    de_pacote = set(
        ConsumoSessao.objects.filter(atendimento_id__in=[a.pk for a in atendimentos])
        .values_list('atendimento_id', flat=True)
    )
    for at in atendimentos:
        at.termo_pendente = at.pk in sem_termo
        at.termo_link = at.termo_pendente and aceita_assinatura(at)
        at.de_pacote = at.pk in de_pacote

    # Compras de pacote: saldo por procedimento + validade (recepcao responde
    # "quantas sessoes me restam e ate quando" sem abrir o Django admin)
    hoje_local = hoje()
    pacotes = list(
        CompraPacote.objects.filter(cliente=cliente)
        .select_related('pacote').prefetch_related('pacote__itens__procedimento')
        .order_by('-criado_em')
    )
    for compra in pacotes:
        compra.saldo = compra.saldo_por_procedimento()
        compra.vencido = bool(
            compra.status == 'ATIVO' and compra.data_expiracao and compra.data_expiracao < hoje_local
        )

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
        'alertas': alertas_saude(cliente),
        'pacotes': pacotes,
        'indicacao_travada': _indicacao_gerou_cashback(cliente),
    }


def _indicacao_gerou_cashback(cliente):
    """Indicacao que ja creditou cashback nao muda mais (o credito ficaria
    com a indicadora errada)."""
    from ..models import MovimentoCarteira
    return bool(cliente.indicado_por_id) and MovimentoCarteira.objects.filter(
        origem='CASHBACK_INDICACAO', tipo='CREDITO', atendimento__cliente=cliente,
    ).exists()


def _buscar_indicadora(termo, cliente):
    """(indicadora|None, erro|None) pelo codigo de indicacao ou pelo nome."""
    termo = (termo or '').strip()[:150]
    if not termo:
        return None, 'Informe o código de indicação ou o nome de quem indicou.'
    # Codigo (gerado em maiusculas) tem prioridade; senao busca pelo nome.
    candidatas = list(Cliente.objects.filter(codigo_indicacao=termo.upper())[:1])
    if not candidatas:
        por_nome = Cliente.objects.filter(nome__icontains=termo)
        candidatas = list(por_nome.filter(nome__iexact=termo)[:2]) or list(por_nome[:2])
    if not candidatas:
        return None, 'Nenhuma cliente encontrada com esse código ou nome.'
    if len(candidatas) > 1:
        return None, 'Mais de uma cliente com esse nome: use o código de indicação de quem indicou.'
    if candidatas[0].pk == cliente.pk:
        return None, 'Uma cliente não pode indicar a si mesma.'
    return candidatas[0], None


def _salvar_indicacao(request, cliente):
    """Grava/remove Cliente.indicado_por (cashback de indicacao) + auditoria."""
    destino = redirect('aranha:admin_cliente_detalhe', pk=cliente.pk)
    if _indicacao_gerou_cashback(cliente):
        messages.error(request, 'Essa indicação já gerou cashback para quem indicou e não pode ser alterada.')
        return destino
    anterior = cliente.indicado_por_id
    if request.POST.get('remover') == '1':
        indicadora = None
    else:
        indicadora, erro = _buscar_indicadora(request.POST.get('indicado_por_busca'), cliente)
        if erro:
            messages.error(request, erro)
            return destino
    novo = indicadora.pk if indicadora is not None else None
    if novo == anterior:
        messages.info(request, 'Indicação sem alteração.')
        return destino
    try:
        with transaction.atomic():
            # update(): nao passa pelo save() do cadastro nem por concorrencia do form
            Cliente.objects.filter(pk=cliente.pk).update(indicado_por=indicadora, atualizado_em=timezone.now())
    except IntegrityError:  # chk_cliente_nao_indica_si_mesmo
        messages.error(request, 'Indicação inválida.')
        return destino
    # sem nomes no texto: o log sobrevive ao esquecimento (LGPD); os pks bastam
    registrar_log(
        request.user, 'Removeu indicacao' if indicadora is None else 'Registrou indicacao',
        'cliente', cliente.pk, detalhes={'indicado_por': novo, 'indicado_por_anterior': anterior},
        request=request,
    )
    if indicadora is None:
        messages.success(request, 'Indicação removida.')
        return destino
    messages.success(request, f'Indicação registrada: {indicadora.nome}.')
    ja_pagou = Atendimento.objects.filter(
        cliente=cliente, status=Atendimento.STATUS_REALIZADO, eh_retorno=False, valor_cobrado__gt=0,
    ).exists()
    if ja_pagou:
        messages.warning(
            request,
            'Esta cliente já tem atendimento pago: o cashback de indicação só é gerado no '
            '1º atendimento pago, então não haverá crédito para quem indicou.',
        )
    return destino


@never_cache  # alertas de saude + CPF/RG/endereco: fora do cache do navegador
@staff_required
def admin_cliente_detalhe(request, pk):
    """Detalhe e edicao de cliente."""
    cliente = get_object_or_404(Cliente, pk=pk)

    if request.method == 'POST' and request.POST.get('acao') == 'indicacao':
        return _salvar_indicacao(request, cliente)

    if request.method == 'POST':
        # ClientePainelForm: normaliza telefone (+55)/CPF com mascara, valida e
        # checa unicidade entre ativos como erro de formulario; nao toca nos
        # consents LGPD (campo ausente no POST nao zera opt-in).
        from ..forms import ClientePainelForm
        form = ClientePainelForm(request.POST, instance=cliente)
        erros = []
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save()
            except IntegrityError:
                # corrida com outro cadastro entre a checagem e o save
                erros = ['Telefone, e-mail ou CPF já cadastrado para outro cliente.']
        else:
            rotulos = {'nome': 'Nome', 'telefone': 'Telefone', 'email': 'E-mail', 'cpf': 'CPF',
                       'data_nascimento': 'Data de nascimento'}
            for campo, lista in form.errors.items():
                prefixo = f'{rotulos.get(campo, campo.replace("_", " ").capitalize())}: ' if campo != '__all__' else ''
                erros.extend(f'{prefixo}{erro}' for erro in lista)
        if erros:
            # Nada gravado: re-renderiza com o que foi digitado + erros
            for erro in erros:
                messages.error(request, erro)
            return render(request, 'painel/cliente_detalhe.html', _contexto_cliente(cliente))
        # sem o nome no texto: o log sobrevive ao esquecimento (LGPD); o pk basta
        registrar_log(request.user, 'Editou cliente', 'cliente', cliente.pk, request=request)
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
        # O form publico da lista e anonimo: o e-mail digitado pode nao ser da
        # dona do telefone. Nome do cadastro so vai p/ o e-mail do proprio cadastro.
        cadastro = (item.cliente.email or '').strip().lower()
        nome = item.cliente.nome if cadastro and destino.strip().lower() == cadastro else ''
        try:
            enviado = bool(enviar_fila_espera_email(destino, {
                'nome': nome,
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
        'lista_espera', item.pk, request=request,
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

    erro = None
    if request.method == 'POST' and not ja_respondeu:
        nota_str = request.POST.get('nota', '').strip()
        comentario = request.POST.get('comentario', '').strip()

        # fullmatch: isdigit() aceitava '²' (int() -> 500) e '07'
        if re.fullmatch(r'10|[0-9]', nota_str):
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
        erro = 'Escolha uma nota de 0 a 10.'

    context = {
        'atendimento': atendimento,
        'ja_respondeu': ja_respondeu,
        'notas_range': range(11),  # 0..10 (escala NPS real)
        'erro': erro,
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
        # id_int: isdigit() aceitava '²' e o int() dava 500
        proc_pk = id_int(proc_id)
        if proc_pk is None:
            messages.error(request, 'Procedimento inválido.')
            return redirect('aranha:admin_termos')
        procedimento = Procedimento.objects.filter(pk=proc_pk).first()
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
            # filter(procedimento=None) vira IS NULL — escopo global do tipo.
            # LGPD e sempre global: arquiva tambem LGPD legada presa a procedimento.
            vigentes = VersaoTermo.objects.filter(tipo=tipo, ativa=True)
            if tipo == 'PROCEDIMENTO':
                vigentes = vigentes.filter(procedimento=procedimento)
            arquivadas = vigentes.update(ativa=False)
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
        {'arquivadas': arquivadas}, request=request,
    )
    if arquivadas:
        messages.success(request, 'Nova versão publicada; a versão anterior foi arquivada.')
    else:
        messages.success(request, 'Termo publicado!')
    return redirect('aranha:admin_termos')


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def termo_assinatura(request, token):
    """Pagina publica p/ a cliente aceitar os termos pendentes do atendimento.

    - So token de Notificacao do termo (tipo TERMO; LEMBRETE+EMAIL legado):
      token de NPS/lembrete WhatsApp nao abre esta pagina.
    - Vale ate o fim do atendimento; cancelado/realizado/faltou/reagendado
      ou ja encerrado -> 410 sem gravar nada.
    - POST exige o aceite de TODOS os termos pendentes (validado no servidor;
      o `required` do checkbox e so conveniencia) e grava pela
      AceiteTermo.registrar (IP, user-agent, SHA-256 do texto) + auditoria.
    """
    notif = get_object_or_404(
        Notificacao.objects.select_related(
            'atendimento__cliente', 'atendimento__procedimento', 'atendimento__profissional',
        ),
        Q_NOTIF_TERMO,
        token=token,
    )
    atendimento = notif.atendimento
    cliente = atendimento.cliente

    if not aceita_assinatura(atendimento):
        ativo = atendimento.status in Atendimento.STATUS_ATIVOS
        return render(request, 'publico/termo_obrigado.html', {
            'cliente': cliente,
            # ativo mas ja encerrado = link vencido; senao o atendimento nao existe mais
            'expirado': ativo,
            'indisponivel': not ativo,
        }, status=410)

    pendentes = termos_pendentes(cliente, atendimento.procedimento)
    context = {
        'atendimento': atendimento,
        'cliente': cliente,
        'termos_pendentes': pendentes,
    }

    if request.method == 'POST':
        # Versao publicada entre o GET e o POST tambem entra em `pendentes`
        # e, sem o aceite dela no POST, cai aqui (nada e gravado).
        faltando = [t for t in pendentes if request.POST.get(f'aceite_{t.pk}') != '1']
        if faltando:
            context['erro'] = 'Para concluir, marque "Li e concordo" em todos os termos abaixo.'
            return render(request, 'publico/termo_assinatura.html', context, status=400)

        if pendentes:
            with transaction.atomic():
                for termo in pendentes:
                    AceiteTermo.registrar(cliente, termo, request=request, atendimento=atendimento)
                Notificacao.objects.filter(pk=notif.pk).update(respondido_em=timezone.now())
            registrar_log(
                None, 'Cliente aceitou termos', 'aceite_termo', cliente.pk,
                {'versoes': [t.pk for t in pendentes], 'atendimento': atendimento.pk,
                 'notificacao': notif.pk},
                request=request,
            )
        return render(request, 'publico/termo_obrigado.html', {'cliente': cliente})

    return render(request, 'publico/termo_assinatura.html', context)


@staff_required
@require_POST
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def admin_gerar_link_termo(request, pk):
    """Gera (ou reusa) o link do termo do atendimento p/ a recepcao enviar.

    O link vale ate o fim do atendimento. A tela mostra o link p/ copiar,
    WhatsApp/e-mail ja com o link no texto e "abrir aqui" (aceite no balcao,
    no tablet da clinica).
    """
    atendimento = get_object_or_404(
        Atendimento.objects.select_related('cliente', 'procedimento', 'profissional'), pk=pk,
    )
    if not aceita_assinatura(atendimento):
        messages.error(
            request,
            'Este atendimento não aceita mais termo (cancelado, concluído ou horário já encerrado).',
        )
        return _voltar(request)
    pendentes = termos_pendentes(atendimento.cliente, atendimento.procedimento)
    if not pendentes:
        messages.info(request, f'{atendimento.cliente.nome} já aceitou todos os termos deste atendimento.')
        return _voltar(request)

    notif, link, criado = obter_link_termo(atendimento, request.POST.get('canal', 'WHATSAPP'))
    registrar_log(
        request.user, 'Gerou link do termo' if criado else 'Consultou link do termo',
        'atendimento', atendimento.pk,
        {'notificacao': notif.pk, 'versoes': [t.pk for t in pendentes]},
        request=request,
    )
    return render(request, 'painel/termo_link.html', {
        'atendimento': atendimento,
        'cliente': atendimento.cliente,
        'link': link,
        'pendentes': pendentes,
        'telefone_wa': _normalizar_telefone_br(atendimento.cliente.telefone or ''),
    })


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
    # Mesmo contexto do envio real (utils.email): aniversario so felicita (sem
    # desconto) e a promocao usa variaveis de primeiro nivel (sem cupom).
    'aniversario': {
        'template': 'email/aniversario.html',
        'contexto': {'dados': {'nome': 'Maria Silva'},
                     'clinic_name': 'Jaqueline Aranha Estética',
                     'unsub_url': '#preview',
                     'preheader': 'Um carinho da nossa equipe para o seu dia'},
    },
    'promocao': {
        'template': 'email/promocao.html',
        'contexto': {
            'nome': 'Maria Silva', 'corpo_html': '<p>Oferta especial do mês.</p>',
            'validade': '30/05/2026',
            'clinic_name': 'Jaqueline Aranha Estética', 'unsub_url': '#preview',
        },
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
            'link_nps': '#preview',
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
    # site_url como no envio real (utils.email faz setdefault): CTAs absolutos
    # e sem VariableDoesNotExist em `{{ x|default:site_url }}`
    contexto = {'site_url': settings.SITE_URL.rstrip('/'), **fx['contexto']}
    html = render_to_string(fx['template'], contexto)
    # A previa e servida sob a CSP do painel (style-src com nonce): sem o
    # nonce o navegador descarta o <style> do e-mail (media queries, dark mode).
    nonce = getattr(request, 'csp_nonce', '')
    if nonce:
        html = html.replace('<style', f'<style nonce="{nonce}"')
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
        transitou = AgendamentoService().aprovar(atendimento, by_user=request.user, request=request)
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
        transitou = AgendamentoService().rejeitar(atendimento, by_user=request.user, request=request)
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
                    ok = service.aprovar(at, by_user=request.user, request=request)
                else:
                    ok = service.rejeitar(at, motivo='Rejeitado em lote', by_user=request.user, request=request)
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
