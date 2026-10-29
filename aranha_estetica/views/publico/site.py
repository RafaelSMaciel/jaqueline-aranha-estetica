import logging

from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static

from ...models import (
    AvaliacaoNPS,
    Procedimento,
    Profissional,
    Promocao,
)
from ...utils import datas
from ...utils.branding import get_branding
from ...utils.email import email_configurado
from ...utils.pii import nome_publico
from ...utils.precos import _promocao_aplicavel, aplicar_promocao, preco_base_map, promocao_vigente
from ...conteudo.faq import montar_faq
from .contato import destino_contato

logger = logging.getLogger(__name__)


def _canais(branding: dict) -> tuple[list[str], list[str]]:
    """(canais p/ agendar, canais p/ pedidos LGPD) que existem de verdade (o FAQ vai p/ o JSON-LD)."""
    agendar = [txt for txt, valor in (
        ('pelo WhatsApp', branding.get('WHATSAPP_NUMERO')),
        ('por telefone', branding.get('CLINIC_PHONE')),
    ) if valor]
    exclusao = [*agendar, *(['por e-mail'] if branding.get('CLINIC_EMAIL') else [])]
    if destino_contato() and email_configurado():
        exclusao.append('pelo formulário de contato')
    return agendar, exclusao


def home(request):
    from ...models import Cliente

    agendar, exclusao = _canais(get_branding())
    return render(request, 'publico/home.html', {
        'profissionais': list(Profissional.objects.filter(ativo=True).order_by('nome')[:6]),
        'faq_categorias': montar_faq(canais_agendar=agendar, canais_exclusao=exclusao,
                                     limite_faltas=Cliente.limite_faltas_bloqueio()),
    })


def termos_uso(request):
    return render(request, 'publico/termos_uso.html')


def quem_somos(request):
    return render(request, 'publico/quem_somos.html')


def favicon(request):
    """/favicon.ico -> PNG estatico (evita 404 renderizando a pagina inteira)."""
    return redirect(static('assets/favicon.png'), permanent=True)


def limite_excedido(request, exception=None):
    """Pagina amigavel de rate-limit (settings.RATELIMIT_VIEW)."""
    return render(request, '429.html', status=429)


# ─── Promocoes / precos de vitrine ───

def _promocao_do_agendamento(procedimento, dia, valor_cheio):
    """Promocao que o agendamento aplicaria no dia sobre esse valor.

    Mesma chamada de utils.precos.preco_com_promocao (valor_base = preco cobrado):
    o % incide sobre o preco do profissional quando o procedimento nao tem base.
    """
    return promocao_vigente(procedimento, dia, valor_base=valor_cheio)


def _preco_final(promo, valor_cheio, dia):
    """Valor a pagar com a promo — so quando e a promo que o agendamento aplica.

    Promo geral nao tem preco unico (vale p/ o catalogo): sem 'por R$'. Promo de
    procedimento so mostra preco se promocao_vigente (a mesma do booking) a
    escolhe sobre esse valor — senao a vitrine anunciaria um valor que o
    Atendimento nao grava (CDC art. 30/35).
    """
    if promo.procedimento_id is None or valor_cheio is None:
        return None
    if _promocao_do_agendamento(promo.procedimento, dia, valor_cheio) != promo:
        return None
    final = aplicar_promocao(valor_cheio, promo)
    return float(final) if final is not None else None


def _precos_vitrine(procedimentos) -> dict:
    """{pk: (valor_final, promocao|None, valor_cheio)} de HOJE p/ o 'A partir de'.

    valor_cheio = preco_base_map (base; sem base, o menor por profissional). A
    promocao e a que o agendamento aplicaria (utils.precos.promocao_vigente), p/ o
    valor da vitrine bater com o gravado no Atendimento.
    """
    procedimentos = list(procedimentos)
    if not procedimentos:
        return {}
    ids = [p.pk for p in procedimentos]
    cheios = preco_base_map(ids)
    hoje = datas.hoje()
    vigentes = Promocao.objects.filter(ativa=True, data_inicio__lte=hoje, data_fim__gte=hoje)
    ha_geral = vigentes.filter(procedimento__isnull=True).exists()
    com_promo = set(vigentes.filter(procedimento_id__in=ids).values_list('procedimento_id', flat=True))

    out = {}
    for proc in procedimentos:
        cheio = cheios.get(proc.pk)
        if cheio is None:
            continue
        promo = _promocao_do_agendamento(proc, hoje, cheio) if (ha_geral or proc.pk in com_promo) else None
        out[proc.pk] = (aplicar_promocao(cheio, promo), promo, cheio)
    return out


def promocoes(request):
    """Lista de promoções ativas e vigentes (data no fuso local)."""
    hoje = datas.hoje()
    promos = list(Promocao.objects.filter(
        ativa=True,
        data_inicio__lte=hoje,
        data_fim__gte=hoje
    ).select_related('procedimento').order_by('-data_inicio'))
    # So o que o agendamento honra: promo geral de preco fixo e ignorada no booking.
    promos = [p for p in promos if _promocao_aplicavel(p)]

    # Enriquecer com preço original/final — preços resolvidos em lote (1 query)
    proc_ids = [promo.procedimento_id for promo in promos if promo.procedimento_id]
    precos = preco_base_map(proc_ids)
    visiveis = []
    for promo in promos:
        valor = precos.get(promo.procedimento_id) if promo.procedimento_id else None
        if promo.procedimento_id and _promocao_do_agendamento(promo.procedimento, hoje, valor) is None:
            continue  # o agendamento nao aplicaria desconto nenhum: nao anunciar
        promo.preco_original = float(valor) if valor is not None else None
        promo.preco_final = _preco_final(promo, valor, hoje)
        visiveis.append(promo)
    return render(request, 'publico/promocoes.html', {'promocoes': visiveis})


def equipe(request):
    """Pagina publica com a equipe de profissionais ativos."""
    profissionais = list(Profissional.objects.filter(ativo=True).order_by('nome'))
    return render(request, 'publico/equipe.html', {'profissionais': profissionais})


# Titulo editorial de cada categoria do catalogo; categoria nova sem titulo usa o rotulo do model
TITULOS_CATEGORIA = {
    'FACIAL': 'Tratamentos Faciais',
    'CORPORAL': 'Tratamentos Corporais',
    'CAPILAR': 'Tratamentos Capilares',
    'OUTRO': 'Outros Serviços',
}


def _cartoes(procedimentos) -> list[dict]:
    """Cartao de vitrine de cada procedimento: preco de hoje (base + promocao do booking)."""
    vitrine = _precos_vitrine(procedimentos)

    def _item(p):
        final, promo, cheio = vitrine.get(p.pk, (None, None, None))
        return {
            'id': p.pk,
            'nome': p.nome,
            'slug': p.slug,
            'descricao': p.descricao or '',
            'duracao_minutos': p.duracao_minutos,
            'preco': float(final) if final is not None else 0,
            'preco_cheio': float(cheio) if promo is not None and cheio != final else None,
            'promocao': promo.nome if promo is not None and cheio != final else '',
        }
    return [_item(p) for p in procedimentos]


def especialidades(request):
    """Procedimentos ativos agrupados por categoria (Procedimento.CATEGORIA_CHOICES, na ordem do model)."""
    procedimentos = list(Procedimento.objects.filter(ativo=True).order_by('nome'))
    cartoes = {c['id']: c for c in _cartoes(procedimentos)}
    grupos = []
    for chave, rotulo in Procedimento.CATEGORIA_CHOICES:
        itens = [cartoes[p.pk] for p in procedimentos if p.categoria == chave]
        if itens:
            grupos.append({'key': chave, 'label': TITULOS_CATEGORIA.get(chave, rotulo), 'itens': itens})
    return render(request, 'publico/especialidades.html', {'grupos': grupos})


def depoimentos(request):
    """Depoimentos NPS publicados: so com opt-in do cliente E aprovacao da clinica (LGPD)."""
    avaliacoes = list(
        AvaliacaoNPS.objects
        .filter(nota__gte=9, autoriza_publicacao=True, aprovado_publicacao=True)
        .filter(~Q(comentario__isnull=True))
        .exclude(comentario__exact='')
        # Titular excluido/pseudonimizado (LGPD art. 18): depoimento sai do ar
        .filter(atendimento__cliente__deletado_em__isnull=True)
        .exclude(atendimento__cliente__nome__startswith='[ANONIMIZADO-')
        .select_related('atendimento__cliente')
        .order_by('-criado_em')[:30]
    )

    for av in avaliacoes:
        av.autor = nome_publico(av.atendimento.cliente.nome if av.atendimento_id else '')
        av.estrelas = int(av.nota or 0) // 2  # escala 0-10 -> 0-5

    return render(request, 'publico/depoimentos.html', {'avaliacoes': avaliacoes})


def galeria(request):
    """Pagina publica com galeria estatica da clinica."""
    # Apenas fotos genuinamente esteticas/spa (curadoria de marca).
    fotos = [
        {'src': 'assets/clinica/espaco-2.webp', 'titulo': 'Recepção'},
        {'src': 'assets/clinica/atendimento-1.webp', 'titulo': 'Sala de avaliação'},
        {'src': 'assets/clinica/facial-2.webp', 'titulo': 'Tratamento facial com LED'},
        {'src': 'assets/clinica/facial-3.webp', 'titulo': 'Máscara facial'},
    ]
    return render(request, 'publico/galeria.html', {'fotos': fotos})


# ─── Lista de espera ───


def servico_detalhe(request, slug):
    """Pagina de detalhe individual de um procedimento pelo slug."""
    procedimento = get_object_or_404(Procedimento, slug=slug, ativo=True)

    # Mesmo resolvedor de /especialidades/ e do booking (base primeiro + promocao de hoje).
    final, promo, cheio = _precos_vitrine([procedimento]).get(procedimento.pk, (None, None, None))
    preco = float(final) if final is not None else None
    em_promocao = promo is not None and cheio != final

    # Profissionais que executam esse procedimento
    profissionais = list(
        procedimento.profissionais.filter(ativo=True).order_by('nome')
    )

    # Outros procedimentos da mesma categoria (sugestao)
    relacionados = list(
        Procedimento.objects
        .filter(ativo=True, categoria=procedimento.categoria)
        .exclude(pk=procedimento.pk)
        .order_by('nome')[:4]
    )

    context = {
        'procedimento': procedimento,
        'preco': preco,
        'preco_cheio': float(cheio) if em_promocao else None,
        'promocao': promo if em_promocao else None,
        'profissionais': profissionais,
        'relacionados': relacionados,
    }
    return render(request, 'publico/servico_detalhe.html', context)


# ─── Servicos por categoria: cartoes do catalogo, texto editorial no template (decisao 46) ───

def _servicos_da_categoria(request, categoria, template):
    procedimentos = list(Procedimento.objects.filter(ativo=True, categoria=categoria).order_by('nome'))
    return render(request, template, {'servicos': _cartoes(procedimentos)})


def servicos_faciais(request):
    return _servicos_da_categoria(request, 'FACIAL', 'publico/servicos/faciais.html')


def servicos_corporais(request):
    return _servicos_da_categoria(request, 'CORPORAL', 'publico/servicos/corporais.html')
