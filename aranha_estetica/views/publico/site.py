import logging

from django.db import OperationalError, ProgrammingError
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
from .contato import destino_contato
from ...utils.precos import _promocao_aplicavel, aplicar_promocao, preco_base_map, promocao_vigente

logger = logging.getLogger(__name__)


def _juntar_ou(itens: list[str]) -> str:
    """['a'] -> 'a'; ['a', 'b', 'c'] -> 'a, b ou c'."""
    if len(itens) <= 1:
        return ''.join(itens)
    return f"{', '.join(itens[:-1])} ou {itens[-1]}"


def _faq_canais(branding: dict) -> tuple[str, str]:
    """Respostas do FAQ que citam canais: so os que existem de verdade (FAQ vai p/ o JSON-LD).

    Retorna (como_agendar, como_pedir_exclusao).
    """
    agendar = [txt for txt, valor in (
        ('pelo WhatsApp', branding.get('WHATSAPP_NUMERO')),
        ('por telefone', branding.get('CLINIC_PHONE')),
    ) if valor]
    como_agendar = 'Você pode agendar online pelo nosso site (disponível 24h)'
    if agendar:
        como_agendar += f', {_juntar_ou(agendar)}' if len(agendar) > 1 else f' ou {agendar[0]}'
    como_agendar += '. No site, é só escolher o procedimento, o dia e o horário.'

    exclusao = [txt for txt, valor in (
        ('pelo WhatsApp', branding.get('WHATSAPP_NUMERO')),
        ('por telefone', branding.get('CLINIC_PHONE')),
        ('por e-mail', branding.get('CLINIC_EMAIL')),
    ) if valor]
    if destino_contato() and email_configurado():
        exclusao.append('pelo formulário de contato')
    exclusao.append('presencialmente na clínica')
    como_excluir = (
        'Sim. Pela área "Meus dados" (link no rodapé) você consulta e baixa suas '
        f'informações. Para pedir a exclusão, fale com a gente {_juntar_ou(exclusao)}.'
    )
    return como_agendar, como_excluir


def home(request):
    profissionais = []
    try:
        profissionais = list(
            Profissional.objects.filter(ativo=True).order_by('nome')[:6]
        )
    except (OperationalError, ProgrammingError):
        pass
    como_agendar, como_excluir = _faq_canais(get_branding())
    faq_categorias = [
        {
            'slug': 'agendamento',
            'nome': 'Agendamento',
            'itens': [
                {'q': 'Como faço para agendar um atendimento?', 'a': como_agendar},
                {'q': 'Preciso criar uma conta para agendar?',
                 'a': 'Não. O agendamento é sem cadastro: você se identifica pelo telefone e confirma com um código enviado por SMS. Rápido e sem senha.'},
                {'q': 'Posso cancelar ou remarcar meu horário?',
                 'a': 'Sim, sem custo, pela área "Meus agendamentos" (link no rodapé do site): a remarcação online vale até 24 h antes do horário e o cancelamento, até o horário marcado. Se não puder vir, avise o quanto antes — o horário volta para quem está na lista de espera.'},
                {'q': 'Como funciona a avaliação inicial?',
                 'a': 'Antes de qualquer procedimento, fazemos uma avaliação individualizada para entender seu objetivo e indicar o cuidado mais adequado para você.'},
            ],
        },
        {
            'slug': 'pagamentos',
            'nome': 'Pagamentos e Pacotes',
            'itens': [
                {'q': 'Quais são as formas de pagamento?',
                 'a': 'Aceitamos PIX, cartão de débito, cartão de crédito (com parcelamento) e dinheiro.'},
                {'q': 'Como funcionam os pacotes de sessões?',
                 'a': 'Os pacotes reúnem um número de sessões por um valor fechado, com validade definida e condições especiais em relação às sessões avulsas.'},
                {'q': 'Ganho algo indicando uma amiga?',
                 'a': 'Sim. Seu código de indicação fica em "Meus agendamentos": quando a amiga informar o código e fizer o primeiro atendimento, você ganha um crédito para usar no seu próximo cuidado.'},
            ],
        },
        {
            'slug': 'tratamentos',
            'nome': 'Tratamentos',
            'itens': [
                {'q': 'Os tratamentos servem para todos os tipos de pele?',
                 'a': 'Cada pessoa é única. Por isso fazemos uma avaliação individualizada antes do procedimento para indicar o cuidado mais adequado ao seu tipo de pele.'},
                {'q': 'Quanto tempo dura cada sessão?',
                 'a': 'Depende do procedimento escolhido — a duração estimada aparece na hora de agendar, junto com os horários disponíveis.'},
                {'q': 'Alguns procedimentos têm retorno?',
                 'a': 'Sim. Quando o cuidado prevê acompanhamento, o retorno é sem custo: você combina o melhor dia com a clínica dentro do prazo indicado em Meus agendamentos.'},
                {'q': 'Existem contraindicações?',
                 'a': 'Algumas. Por isso preenchemos uma ficha de avaliação antes do atendimento — assim garantimos sua segurança e o melhor resultado.'},
            ],
        },
        {
            'slug': 'privacidade',
            'nome': 'Privacidade e Dados',
            'itens': [
                {'q': 'Como meus dados são armazenados?',
                 'a': 'Seguimos a LGPD. Seus dados ficam armazenados de forma segura e são usados apenas para o seu atendimento e a comunicação com você.'},
                {'q': 'Posso acessar ou excluir meus dados?', 'a': como_excluir},
                {'q': 'Vou receber mensagens de divulgação?',
                 'a': 'Apenas se você autorizar. Você escolhe o que deseja receber e pode cancelar o recebimento a qualquer momento, com um clique.'},
            ],
        },
    ]
    return render(request, 'publico/home.html', {
        'profissionais': profissionais,
        'faq_categorias': faq_categorias,
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
    promos = []
    try:
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
        promos = visiveis
    except (OperationalError, ProgrammingError):
        logger.warning('Tabela de promoções não encontrada — exibindo página sem promoções.')

    context = {'promocoes': promos}
    return render(request, 'publico/promocoes.html', context)


def equipe(request):
    """Pagina publica com a equipe de profissionais ativos."""
    profissionais = []
    try:
        profissionais = list(
            Profissional.objects.filter(ativo=True).order_by('nome')
        )
    except (OperationalError, ProgrammingError):
        logger.warning('Tabela de profissionais nao encontrada.')

    return render(request, 'publico/equipe.html', {'profissionais': profissionais})


def especialidades(request):
    """Pagina publica com procedimentos agrupados por categoria em tabs."""
    categorias = [
        ('FACIAL', 'Tratamentos Faciais'),
        ('CORPORAL', 'Tratamentos Corporais'),
        ('CAPILAR', 'Tratamentos Capilares'),
        ('OUTRO', 'Outros Serviços'),
    ]

    grupos = []
    try:
        procedimentos = list(Procedimento.objects.filter(ativo=True).order_by('nome'))

        # Mesmo resolvedor do booking: preco base primeiro + promocao vigente hoje.
        vitrine = _precos_vitrine(procedimentos)

        def _item(p):
            final, promo, cheio = vitrine.get(p.pk, (None, None, None))
            return {
                'id': p.pk,
                'nome': p.nome,
                'descricao': p.descricao or '',
                'duracao_minutos': p.duracao_minutos,
                'preco': float(final) if final is not None else 0,
                'preco_cheio': float(cheio) if promo is not None and cheio != final else None,
                'promocao': promo.nome if promo is not None and cheio != final else '',
            }

        for cat_key, cat_label in categorias:
            itens = [_item(p) for p in procedimentos if p.categoria == cat_key]
            if itens:
                grupos.append({
                    'key': cat_key,
                    'label': cat_label,
                    'itens': itens,
                })
    except (OperationalError, ProgrammingError):
        logger.warning('Tabelas de procedimentos nao encontradas.')

    return render(request, 'publico/especialidades.html', {'grupos': grupos})


def depoimentos(request):
    """Depoimentos NPS publicados: so com opt-in do cliente E aprovacao da clinica (LGPD)."""
    avaliacoes = []
    try:
        avaliacoes = list(
            AvaliacaoNPS.objects
            .filter(nota__gte=9, autoriza_publicacao=True, aprovado_publicacao=True)
            .filter(~Q(comentario__isnull=True))
            .exclude(comentario__exact='')
            # Titular excluido/anonimizado (LGPD art. 18): depoimento sai do ar
            .filter(atendimento__cliente__deletado_em__isnull=True)
            .exclude(atendimento__cliente__nome__startswith='[ANONIMIZADO-')
            .select_related('atendimento__cliente')
            .order_by('-criado_em')[:30]
        )
    except (OperationalError, ProgrammingError):
        logger.warning('Tabela AvaliacaoNPS nao encontrada.')

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


# ─── Servicos por categoria (paginas institucionais estaticas) ───

def servicos_faciais(request):
    return render(request, 'publico/servicos/faciais.html')


def servicos_corporais(request):
    return render(request, 'publico/servicos/corporais.html')
