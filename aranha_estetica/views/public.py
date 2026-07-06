import logging
from datetime import datetime

from django.contrib import messages
from django.db import OperationalError, ProgrammingError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from ..models import (
    AvaliacaoNPS,
    Cliente,
    ListaEspera,
    Preco,
    Procedimento,
    Profissional,
    Promocao,
)

logger = logging.getLogger(__name__)


def home(request):
    profissionais = []
    try:
        profissionais = list(
            Profissional.objects.filter(ativo=True).order_by('nome')[:6]
        )
    except (OperationalError, ProgrammingError):
        pass
    faq_categorias = [
        {
            'slug': 'agendamento',
            'nome': 'Agendamento',
            'itens': [
                {'q': 'Como faço para agendar um atendimento?',
                 'a': 'Você pode agendar online pelo nosso site (disponível 24h), pelo WhatsApp ou por telefone. No site, é só escolher o procedimento, o dia e o horário.'},
                {'q': 'Preciso criar uma conta para agendar?',
                 'a': 'Não. O agendamento é sem cadastro: você se identifica pelo telefone e confirma com um código enviado por SMS. Rápido e sem senha.'},
                {'q': 'Posso cancelar ou remarcar meu horário?',
                 'a': 'Sim. Cancelamentos e remarcações podem ser feitos com até 24h de antecedência, sem custo, pela área "Meus Agendamentos".'},
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
                {'q': 'Existe benefício por indicação?',
                 'a': 'Sim. Ao indicar uma amiga, você ganha um crédito na sua carteira quando ela realiza o primeiro atendimento — para usar nos seus próximos cuidados.'},
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
                 'a': 'Sim. Quando o cuidado prevê acompanhamento, o retorno é sinalizado e agendado para você, sem custo adicional.'},
                {'q': 'Existem contraindicações?',
                 'a': 'Algumas. Por isso preenchemos uma ficha de anamnese antes do atendimento — assim garantimos sua segurança e o melhor resultado.'},
            ],
        },
        {
            'slug': 'privacidade',
            'nome': 'Privacidade e Dados',
            'itens': [
                {'q': 'Como meus dados são armazenados?',
                 'a': 'Seguimos a LGPD. Seus dados ficam armazenados de forma segura e são usados apenas para o seu atendimento e a comunicação com você.'},
                {'q': 'Posso acessar ou excluir meus dados?',
                 'a': 'Sim. Pela área "Meus Dados" você consulta, baixa ou solicita a exclusão das suas informações quando quiser.'},
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


def politica_privacidade(request):
    return render(request, 'publico/politica_privacidade.html')


def quem_somos(request):
    return render(request, 'publico/quem_somos.html')


@ratelimit(key='ip', rate='5/m', method='POST', block=True)
def agenda_contato(request):
    """Pagina de contato publico com form POST funcional (PRG)."""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        subject = request.POST.get('subject', '').strip()
        message = request.POST.get('message', '').strip()
        privacy = request.POST.get('privacy', '')

        erros = []
        if not name:
            erros.append('nome')
        if not email:
            erros.append('e-mail')
        if not subject:
            erros.append('assunto')
        if not message:
            erros.append('mensagem')
        if not privacy:
            erros.append('aceite da política de privacidade')

        if erros:
            messages.error(request, 'Preencha todos os campos obrigatorios.')
            return render(request, 'agenda/contato.html', {
                'form_data': {
                    'name': name, 'email': email,
                    'phone': phone, 'subject': subject, 'message': message,
                },
            })

        # Enviar e-mail para a clinica
        from django.conf import settings
        destino = getattr(settings, 'CLINIC_EMAIL', None) or \
            getattr(settings, 'DEFAULT_FROM_EMAIL', 'contato@clinica.com.br')

        corpo = (
            f'Nome: {name}\n'
            f'E-mail: {email}\n'
            f'Telefone: {phone or "nao informado"}\n\n'
            f'Mensagem:\n{message}'
        )
        try:
            from django.core.mail import send_mail as _send_mail
            _send_mail(
                subject=f'Contato site: {subject}',
                message=corpo,
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                recipient_list=[destino],
                fail_silently=False,
            )
        except Exception:
            logger.exception(
                'contato_email_falha',
                extra={'from_email': email, 'subject': subject},
            )

        messages.success(request, 'Mensagem enviada! Entraremos em contato em breve.')
        return redirect('aranha:agenda_contato')

    return render(request, 'agenda/contato.html')


def promocoes(request):
    """Lista de promoções ativas e vigentes"""
    promos = []
    try:
        hoje = timezone.now().date()
        promos = list(Promocao.objects.filter(
            ativa=True,
            data_inicio__lte=hoje,
            data_fim__gte=hoje
        ).select_related('procedimento').order_by('-data_inicio'))

        # Enriquecer com preço original — preços resolvidos em lote (1 query)
        from ..utils.precos import preco_base_map
        proc_ids = [promo.procedimento_id for promo in promos if promo.procedimento_id]
        precos = preco_base_map(proc_ids)
        for promo in promos:
            valor = precos.get(promo.procedimento_id) if promo.procedimento_id else None
            promo.preco_original = float(valor) if valor is not None else None
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

        preco_map = {}
        proc_ids = [p.pk for p in procedimentos]
        if proc_ids:
            precos = Preco.objects.filter(
                procedimento_id__in=proc_ids
            ).order_by('profissional')
            for preco in precos:
                if preco.procedimento_id not in preco_map:
                    preco_map[preco.procedimento_id] = float(preco.valor)

        for cat_key, cat_label in categorias:
            itens = [
                {
                    'id': p.pk,
                    'nome': p.nome,
                    'descricao': p.descricao or '',
                    'duracao_minutos': p.duracao_minutos,
                    'preco': preco_map.get(p.pk, 0),
                }
                for p in procedimentos
                if p.categoria == cat_key
            ]
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
    """Pagina publica com avaliacoes NPS (promotores) em carousel."""
    avaliacoes = []
    try:
        avaliacoes = list(
            AvaliacaoNPS.objects
            .filter(nota__gte=9)
            .filter(~Q(comentario__isnull=True))
            .exclude(comentario__exact='')
            .select_related('atendimento__cliente')
            .order_by('-criado_em')[:30]
        )
    except (OperationalError, ProgrammingError):
        logger.warning('Tabela AvaliacaoNPS nao encontrada.')

    return render(request, 'publico/depoimentos.html', {'avaliacoes': avaliacoes})


def galeria(request):
    """Pagina publica com galeria estatica da clinica (GLightbox)."""
    # Imagens usam o template_img ja presente em static/assets/
    # Apenas fotos genuinamente esteticas/spa (curadoria de marca).
    fotos = [
        {'src': 'assets/clinica/espaco-2.webp', 'titulo': 'Recepcao'},
        {'src': 'assets/clinica/consulta-1.webp', 'titulo': 'Sala de Avaliacao'},
        {'src': 'assets/clinica/facial-2.webp', 'titulo': 'Tratamento Facial com LED'},
        {'src': 'assets/clinica/facial-3.webp', 'titulo': 'Mascara Facial'},
    ]
    return render(request, 'publico/galeria.html', {'fotos': fotos})


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def lista_espera_publica(request):
    """Formulario publico para cliente se inscrever na lista de espera."""
    procedimentos = []
    profissionais = []
    try:
        procedimentos = list(
            Procedimento.objects.filter(ativo=True).order_by('nome')
        )
        profissionais = list(
            Profissional.objects.filter(ativo=True).order_by('nome')
        )
    except (OperationalError, ProgrammingError):
        logger.warning('Tabelas de procedimento/profissional nao encontradas.')

    if request.method == 'POST':
        from ..validators import normalizar_telefone
        nome = request.POST.get('nome', '').strip()
        # Normaliza igual ao booking, senao get_or_create cria duplicado / 500 por unique
        telefone = normalizar_telefone(request.POST.get('telefone', ''))
        procedimento_id = request.POST.get('procedimento', '')
        profissional_id = request.POST.get('profissional', '')
        data_desejada = request.POST.get('data_desejada', '')
        turno = request.POST.get('turno', '') or None

        if not all([nome, telefone, procedimento_id, data_desejada]):
            messages.error(request, 'Preencha nome, telefone, procedimento e data.')
            return redirect('aranha:lista_espera_publica')

        try:
            procedimento = Procedimento.objects.get(pk=procedimento_id, ativo=True)
        except Procedimento.DoesNotExist:
            messages.error(request, 'Procedimento invalido.')
            return redirect('aranha:lista_espera_publica')

        profissional = None
        if profissional_id:
            try:
                profissional = Profissional.objects.get(pk=profissional_id, ativo=True)
            except Profissional.DoesNotExist:
                profissional = None

        try:
            data_obj = datetime.strptime(data_desejada, '%Y-%m-%d').date()
        except ValueError:
            messages.error(request, 'Data invalida.')
            return redirect('aranha:lista_espera_publica')

        if data_obj < timezone.now().date():
            messages.error(request, 'Escolha uma data futura.')
            return redirect('aranha:lista_espera_publica')

        if turno and turno not in {'MANHA', 'TARDE', 'NOITE'}:
            turno = None

        cliente, _created = Cliente.objects.get_or_create(
            telefone=telefone,
            defaults={'nome': nome, 'ativo': True},
        )
        if not _created and cliente.nome != nome and nome:
            cliente.nome = nome
            cliente.save(update_fields=['nome'])

        ja_inscrito = ListaEspera.objects.filter(
            cliente=cliente,
            procedimento=procedimento,
            data_desejada=data_obj,
            notificado=False,
        ).exists()

        if ja_inscrito:
            messages.info(
                request,
                'Voce ja esta na lista de espera para este procedimento e data.'
            )
            return redirect('aranha:lista_espera_sucesso')

        ListaEspera.objects.create(
            cliente=cliente,
            procedimento=procedimento,
            profissional_desejado=profissional,
            data_desejada=data_obj,
            turno_desejado=turno,
        )

        return redirect('aranha:lista_espera_sucesso')

    context = {
        'procedimentos': procedimentos,
        'profissionais': profissionais,
        'turnos': [('MANHA', 'Manha'), ('TARDE', 'Tarde'), ('NOITE', 'Noite')],
        'data_minima': timezone.now().date().isoformat(),
    }
    return render(request, 'publico/lista_espera.html', context)


def lista_espera_sucesso(request):
    """Confirmacao de inscricao na lista de espera."""
    return render(request, 'publico/lista_espera_sucesso.html')


def servico_detalhe(request, slug):
    """Pagina de detalhe individual de um procedimento pelo slug."""
    procedimento = get_object_or_404(Procedimento, slug=slug, ativo=True)

    # Buscar preco generico (profissional=NULL) ou primeiro disponivel
    preco_obj = (
        Preco.objects.filter(procedimento=procedimento, profissional__isnull=True).first()
        or Preco.objects.filter(procedimento=procedimento).first()
    )
    preco = float(preco_obj.valor) if preco_obj else None

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
        'profissionais': profissionais,
        'relacionados': relacionados,
    }
    return render(request, 'publico/servico_detalhe.html', context)


# ─── Servicos por categoria ───

def _get_procedimentos_com_preco(categoria='FACIAL'):
    """Retorna procedimentos de uma categoria enriquecidos com precos."""
    procedimentos_com_preco = []
    try:
        procedimentos = Procedimento.objects.filter(ativo=True, categoria=categoria)
        proc_ids = list(procedimentos.values_list('pk', flat=True))
        precos = Preco.objects.filter(procedimento_id__in=proc_ids).order_by('profissional')
        preco_map = {}
        for p in precos:
            if p.procedimento_id not in preco_map:
                preco_map[p.procedimento_id] = p.valor

        for proc in procedimentos:
            procedimentos_com_preco.append({
                'id': proc.pk,
                'nome': proc.nome,
                'descricao': proc.descricao or '',
                'duracao_minutos': proc.duracao_minutos,
                'preco': float(preco_map.get(proc.pk, 0)),
            })
    except (OperationalError, ProgrammingError):
        logger.warning('Tabelas nao encontradas para procedimentos.')
    return procedimentos_com_preco


def servicos_faciais(request):
    return render(request, 'servicos/faciais.html', {
        'procedimentos': _get_procedimentos_com_preco('FACIAL'),
    })


def servicos_corporais(request):
    return render(request, 'servicos/corporais.html', {
        'procedimentos': _get_procedimentos_com_preco('CORPORAL'),
    })


def servicos_produtos(request):
    return render(request, 'servicos/produtos.html', {})
