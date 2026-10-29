import logging

from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static

from ...models import (
    AvaliacaoNPS,
    Procedimento,
    Profissional,
)
from ...utils import datas
from ...utils.branding import get_branding
from ...utils.email import email_configurado
from ...utils.pii import nome_publico
from ...services.precos import candidatas_de, precos_de_vitrine, promocoes_vigentes
from ...services.prova_social import numeros_do_site
from ...utils.precos import melhor_promocao
from ...conteudo.faq import em_destaque, montar_faq
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


def _faq():
    from ...models import Cliente

    agendar, exclusao = _canais(get_branding())
    return montar_faq(canais_agendar=agendar, canais_exclusao=exclusao,
                      limite_faltas=Cliente.limite_faltas_bloqueio())


def _amostra_do_catalogo(limite=8) -> list:
    """Atalhos "Também oferecemos" da home: procedimentos ativos do catalogo (nunca lista escrita a
    mao), um por familia ('Botox 1 região' e 'Botox 2 regiões' viram um so), em ordem alfabetica."""
    vistos, amostra = set(), []
    for proc in (Procedimento.objects.filter(ativo=True).exclude(slug__isnull=True).exclude(slug='')
                 .order_by('nome').only('nome', 'slug')):
        familia = proc.nome.split()[0].lower()
        if familia not in vistos:
            vistos.add(familia)
            amostra.append(proc)
        if len(amostra) == limite:
            break
    return amostra


def home(request):
    return render(request, 'publico/home.html', {
        'profissionais': list(Profissional.objects.filter(ativo=True).order_by('nome')[:6]),
        'faq_destaque': em_destaque(_faq()),   # a lista completa fica em /duvidas/
        'numeros': numeros_do_site(),
        'amostra_catalogo': _amostra_do_catalogo(),
    })


def duvidas(request):
    """Todas as perguntas frequentes, por categoria (a home mostra so as em destaque)."""
    return render(request, 'publico/duvidas.html', {'faq_categorias': _faq()})


def termos_uso(request):
    return render(request, 'publico/termos_uso.html')


def quem_somos(request):
    return render(request, 'publico/quem_somos.html', {'numeros': numeros_do_site()})


def favicon(request):
    """/favicon.ico -> PNG estatico (evita 404 renderizando a pagina inteira)."""
    return redirect(static('assets/favicon.png'), permanent=True)


def limite_excedido(request, exception=None):
    """Pagina amigavel de rate-limit (settings.RATELIMIT_VIEW)."""
    return render(request, '429.html', status=429)


# ─── Promocoes / precos de vitrine ───

def promocoes(request):
    """Promocoes ativas no dia (fuso local) que o agendamento honra, com o preco que ele grava.

    Promo de procedimento so aparece se o agendamento aplicaria algum desconto naquele
    procedimento, e so mostra "por R$" quando e ela a escolhida (services/precos: a mesma conta
    da vitrine e do booking — CDC art. 30/35). Promo geral (percentual) nao tem preco unico.
    """
    hoje = datas.hoje()
    vigentes = promocoes_vigentes(hoje)
    vitrine = precos_de_vitrine({p.procedimento for p in vigentes if p.procedimento_id}, hoje)
    visiveis = []
    for promo in sorted(vigentes, key=lambda p: p.data_inicio, reverse=True):
        preco = vitrine.get(promo.procedimento_id) if promo.procedimento_id else None
        if promo.procedimento_id:
            # sem preco: so a promocao de preco fixo do proprio procedimento vale no agendamento
            escolhida = preco.promocao if preco else melhor_promocao(candidatas_de(promo.procedimento_id, vigentes), None)
            if escolhida is None:
                continue   # o agendamento nao aplicaria desconto nenhum: nao anunciar
        promo.preco_original = float(preco.cheio) if preco else None
        promo.preco_final = float(preco.final) if preco and preco.promocao == promo else None
        visiveis.append(promo)
    return render(request, 'publico/promocoes.html', {'promocoes': visiveis})


def equipe(request):
    """Equipe: com uma profissional, a pagina e "Sobre a profissional" (foto ou monograma,
    especialidade, bio e os tratamentos que ela realiza, do catalogo); com 2+, a grade."""
    profissionais = list(Profissional.objects.filter(ativo=True).order_by('nome'))
    tratamentos = []
    if len(profissionais) == 1:
        tratamentos = list(
            Procedimento.objects.filter(ativo=True, habilitacao__profissional=profissionais[0])
            .exclude(slug__isnull=True).exclude(slug='').order_by('categoria', 'nome').distinct()
        )
    return render(request, 'publico/equipe.html', {'profissionais': profissionais, 'tratamentos': tratamentos})


# Titulo editorial de cada categoria do catalogo; categoria nova sem titulo usa o rotulo do model
TITULOS_CATEGORIA = {
    'FACIAL': 'Tratamentos Faciais',
    'CORPORAL': 'Tratamentos Corporais',
    'CAPILAR': 'Tratamentos Capilares',
    'OUTRO': 'Outros Serviços',
}


def _cartoes(procedimentos) -> list[dict]:
    """Cartao de vitrine de cada procedimento: a mesma vitrine do wizard (services/precos: preco base
    do dia + promocao que o booking aplica)."""
    vitrine = precos_de_vitrine(procedimentos)

    def _item(p):
        preco = vitrine.get(p.pk)
        promocao = preco.promocao if preco else None
        return {
            'id': p.pk,
            'nome': p.nome,
            'slug': p.slug,
            'descricao': p.descricao or '',
            'duracao_minutos': p.duracao_minutos,
            'preco': float(preco.final) if preco else 0,
            'preco_cheio': float(preco.cheio) if promocao else None,
            'promocao': promocao.nome if promocao else '',
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
    # Apenas fotos genuinamente esteticas/spa (curadoria de marca). nome = arquivo em
    # static/assets/clinica (sem extensao): o {% foto %} serve as variantes do scripts/imagens.py.
    fotos = [
        {'nome': 'espaco-2', 'titulo': 'Recepção'},
        {'nome': 'atendimento-1', 'titulo': 'Sala de avaliação'},
        {'nome': 'facial-2', 'titulo': 'Tratamento facial com LED'},
        {'nome': 'facial-3', 'titulo': 'Máscara facial'},
    ]
    return render(request, 'publico/galeria.html', {'fotos': fotos})


# ─── Lista de espera ───


def servico_detalhe(request, slug):
    """Pagina de detalhe individual de um procedimento pelo slug."""
    procedimento = get_object_or_404(Procedimento, slug=slug, ativo=True)

    # Mesma vitrine de /especialidades/ e do wizard (services/precos)
    vitrine = precos_de_vitrine([procedimento]).get(procedimento.pk)

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
        'preco': float(vitrine.final) if vitrine else None,
        'preco_cheio': float(vitrine.cheio) if vitrine and vitrine.em_promocao else None,
        'promocao': vitrine.promocao if vitrine else None,
        'profissionais': profissionais,
        'relacionados': relacionados,
    }
    return render(request, 'publico/servico_detalhe.html', context)


# ─── Servicos por categoria: cartoes do catalogo, texto editorial no template (decisao 46) ───

def _destaques(procedimentos, limite=4) -> list:
    """Bloco "em destaque" das paginas de servico: so procedimento ativo do catalogo, com descricao;
    primeiro os que tem foto propria (imagem_destaque) ou pagina detalhada (descricao_longa). O texto
    e o do catalogo — a pagina nunca anuncia tratamento que a clinica nao oferece."""
    com_texto = [p for p in procedimentos if (p.descricao or '').strip() and p.slug]
    ordem = sorted(com_texto, key=lambda p: (not p.imagem_destaque, not p.descricao_longa.strip(), p.nome))
    vistos, destaques = set(), []
    for proc in ordem:   # um por familia: 'Botox 1 região' e 'Botox 2 regiões' nao ocupam 2 lugares
        familia = proc.nome.split()[0].lower()
        if familia not in vistos:
            vistos.add(familia)
            destaques.append(proc)
    return destaques[:limite]


def _servicos_da_categoria(request, categoria, template):
    procedimentos = list(Procedimento.objects.filter(ativo=True, categoria=categoria).order_by('nome'))
    return render(request, template, {
        'servicos': _cartoes(procedimentos),
        'destaques': _destaques(procedimentos),
        'numeros': numeros_do_site(),
    })


def servicos_faciais(request):
    return _servicos_da_categoria(request, 'FACIAL', 'publico/servicos/faciais.html')


def servicos_corporais(request):
    return _servicos_da_categoria(request, 'CORPORAL', 'publico/servicos/corporais.html')
