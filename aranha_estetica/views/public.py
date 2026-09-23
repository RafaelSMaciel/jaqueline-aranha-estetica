import logging
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.db import IntegrityError, OperationalError, ProgrammingError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django_ratelimit.decorators import ratelimit

from ..models import (
    AvaliacaoNPS,
    Cliente,
    ListaEspera,
    Procedimento,
    Profissional,
    Promocao,
)
from ..utils import datas
from ..utils.branding import get_branding
from ..utils.email import email_configurado
from ..utils.pii import mask_email
from ..utils.precos import preco_base_map
from ..validators import normalizar_telefone, validate_telefone_br

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
                 'a': 'Sim. Cancelamentos e remarcações podem ser feitos com até 24h de antecedência, sem custo, pela área "Meus agendamentos" (link no rodapé do site).'},
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
                 'a': 'Algumas. Por isso preenchemos uma ficha de avaliação antes do atendimento — assim garantimos sua segurança e o melhor resultado.'},
            ],
        },
        {
            'slug': 'privacidade',
            'nome': 'Privacidade e Dados',
            'itens': [
                {'q': 'Como meus dados são armazenados?',
                 'a': 'Seguimos a LGPD. Seus dados ficam armazenados de forma segura e são usados apenas para o seu atendimento e a comunicação com você.'},
                {'q': 'Posso acessar ou excluir meus dados?',
                 'a': 'Sim. Pela área "Meus dados" (link no rodapé) você consulta e baixa suas informações. Para pedir a exclusão, fale com a gente pelo WhatsApp ou e-mail.'},
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


def favicon(request):
    """/favicon.ico -> PNG estatico (evita 404 renderizando a pagina inteira)."""
    return redirect(static('assets/favicon.png'), permanent=True)


def limite_excedido(request, exception=None):
    """Pagina amigavel de rate-limit (settings.RATELIMIT_VIEW)."""
    return render(request, '429.html', status=429)


# ─── Contato ───

ASSUNTOS_CONTATO = {
    'agendamento': 'Agendamento',
    'duvida': 'Dúvidas sobre procedimentos',
    'orcamento': 'Solicitar orçamento',
    'parceria': 'Parcerias',
    'outros': 'Outros',
}


def _destino_contato() -> str:
    """Caixa que recebe o formulario: Branding/env CLINIC_EMAIL -> DEFAULT_FROM_EMAIL (se nao for noreply)."""
    destino = (get_branding().get('CLINIC_EMAIL') or getattr(settings, 'CLINIC_EMAIL', '') or '').strip()
    if destino:
        return destino
    padrao = (getattr(settings, 'DEFAULT_FROM_EMAIL', '') or '').strip()
    local = padrao.split('<')[-1].split('@')[0].lower()
    if not padrao or local.startswith(('noreply', 'no-reply', 'nao-responda', 'naoresponda')):
        return ''
    return padrao


@ratelimit(key='ip', rate='5/m', method='POST', block=True)
def agenda_contato(request):
    """Pagina de contato publico com form POST funcional (PRG).

    Se o e-mail nao puder ser entregue, NAO finge sucesso: re-renderiza o
    formulario com os dados preenchidos e oferece o WhatsApp.
    """
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()[:150]
        email = request.POST.get('email', '').strip()[:254]
        phone = request.POST.get('phone', '').strip()[:30]
        subject = request.POST.get('subject', '').strip()
        message = request.POST.get('message', '').strip()[:5000]
        privacy = request.POST.get('privacy', '')
        marketing = request.POST.get('marketing', '') == '1'

        form_data = {
            'name': name, 'email': email, 'phone': phone,
            'subject': subject, 'message': message, 'marketing': marketing,
        }

        faltando = not all([name, email, subject, message, privacy])
        if faltando:
            messages.error(request, 'Preencha todos os campos obrigatórios.')
            return render(request, 'agenda/contato.html', {'form_data': form_data})

        try:
            validate_email(email)
        except ValidationError:
            messages.error(request, 'Informe um e-mail válido.')
            return render(request, 'agenda/contato.html', {'form_data': form_data})

        assunto = ASSUNTOS_CONTATO.get(subject, 'Outros')
        destino = _destino_contato()
        corpo = (
            f'Nome: {name}\n'
            f'E-mail: {email}\n'
            f'Telefone: {phone or "não informado"}\n'
            f'Assunto: {assunto}\n'
            f'Aceita receber novidades e promoções: {"sim" if marketing else "não"}\n\n'
            f'Mensagem:\n{message}'
        )

        enviado = 0
        # console/dummy fora de DEBUG 'enviam' (retornam 1) mas nada chega: falha fechado
        if destino and email_configurado():
            try:
                enviado = EmailMessage(
                    subject=f'Contato site: {assunto}',
                    body=corpo,
                    from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                    to=[destino],
                    reply_to=[email],
                ).send(fail_silently=False)
            except Exception:  # noqa: BLE001 — qualquer falha de envio vira aviso honesto
                logger.exception(
                    'contato_email_falha',
                    extra={'from_email': mask_email(email), 'subject': subject},
                )
        else:
            logger.error(
                'contato_email_indisponivel',
                extra={'tem_destino': bool(destino), 'from_email': mask_email(email)},
            )

        if not enviado:
            messages.error(
                request,
                'Não conseguimos enviar sua mensagem agora. Seus dados continuam no '
                'formulário: tente de novo mais tarde ou use outro canal de contato desta página.',
            )
            return render(request, 'agenda/contato.html', {
                'form_data': form_data,
                'envio_falhou': True,
            })

        messages.success(request, 'Mensagem enviada! Entraremos em contato em breve.')
        return redirect('aranha:agenda_contato')

    return render(request, 'agenda/contato.html')


# ─── Promocoes ───

def _preco_final(promo):
    """Valor a pagar: preco fixo da promo OU preco base com o desconto %."""
    if promo.preco_promocional is not None:
        return float(promo.preco_promocional)
    if promo.desconto_percentual and promo.preco_original is not None:
        return round(promo.preco_original * (1 - float(promo.desconto_percentual) / 100), 2)
    return None


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

        # Enriquecer com preço original/final — preços resolvidos em lote (1 query)
        proc_ids = [promo.procedimento_id for promo in promos if promo.procedimento_id]
        precos = preco_base_map(proc_ids)
        for promo in promos:
            valor = precos.get(promo.procedimento_id) if promo.procedimento_id else None
            promo.preco_original = float(valor) if valor is not None else None
            promo.preco_final = _preco_final(promo)
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

        # Mesmo resolvedor do booking/promocoes: preco base (sem profissional) primeiro.
        proc_ids = [p.pk for p in procedimentos]
        preco_map = (
            {k: float(v) for k, v in preco_base_map(proc_ids).items()} if proc_ids else {}
        )

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


def _nome_publico(nome: str) -> str:
    """'Maria da Silva' -> 'Maria S.' (depoimento publico: primeiro nome + inicial)."""
    partes = (nome or '').split()
    if not partes:
        return 'Cliente'
    if len(partes) == 1:
        return partes[0]
    return f'{partes[0]} {partes[-1][0].upper()}.'


def depoimentos(request):
    """Depoimentos NPS publicados: so com opt-in do cliente E aprovacao da clinica (LGPD)."""
    avaliacoes = []
    try:
        avaliacoes = list(
            AvaliacaoNPS.objects
            .filter(nota__gte=9, autoriza_publicacao=True, aprovado_publicacao=True)
            .filter(~Q(comentario__isnull=True))
            .exclude(comentario__exact='')
            .select_related('atendimento__cliente')
            .order_by('-criado_em')[:30]
        )
    except (OperationalError, ProgrammingError):
        logger.warning('Tabela AvaliacaoNPS nao encontrada.')

    for av in avaliacoes:
        av.autor = _nome_publico(av.atendimento.cliente.nome if av.atendimento_id else '')
        av.estrelas = int(av.nota or 0) // 2  # escala 0-10 -> 0-5

    return render(request, 'publico/depoimentos.html', {'avaliacoes': avaliacoes})


def galeria(request):
    """Pagina publica com galeria estatica da clinica."""
    # Apenas fotos genuinamente esteticas/spa (curadoria de marca).
    fotos = [
        {'src': 'assets/clinica/espaco-2.webp', 'titulo': 'Recepção'},
        {'src': 'assets/clinica/consulta-1.webp', 'titulo': 'Sala de avaliação'},
        {'src': 'assets/clinica/facial-2.webp', 'titulo': 'Tratamento facial com LED'},
        {'src': 'assets/clinica/facial-3.webp', 'titulo': 'Máscara facial'},
    ]
    return render(request, 'publico/galeria.html', {'fotos': fotos})


# ─── Lista de espera ───

TURNOS = [('MANHA', 'Manhã'), ('TARDE', 'Tarde'), ('NOITE', 'Noite')]


def _telefone_br(valor: str) -> str:
    """Digitos do celular, sem DDI 55 (autofill costuma trazer +55)."""
    digitos = normalizar_telefone(valor)
    if len(digitos) in (12, 13) and digitos.startswith('55'):
        digitos = digitos[2:]
    return digitos


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def lista_espera_publica(request):
    """Formulario publico para cliente se inscrever na lista de espera.

    Form anonimo (sem OTP): NUNCA altera dados de um Cliente que ja existe —
    so cria o cadastro quando o telefone e novo.
    """
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

    context = {
        'procedimentos': procedimentos,
        'profissionais': profissionais,
        'turnos': TURNOS,
        'data_minima': datas.hoje().isoformat(),
        'form_data': {},
    }

    if request.method != 'POST':
        return render(request, 'publico/lista_espera.html', context)

    nome = request.POST.get('nome', '').strip()[:150]
    email = request.POST.get('email', '').strip()[:254]
    telefone_bruto = request.POST.get('telefone', '').strip()
    telefone = _telefone_br(telefone_bruto)
    procedimento_id = request.POST.get('procedimento', '')
    profissional_id = request.POST.get('profissional', '')
    data_desejada = request.POST.get('data_desejada', '')
    turno = request.POST.get('turno', '') or None

    context['form_data'] = {
        'nome': nome, 'email': email, 'telefone': telefone_bruto[:20],
        'procedimento': procedimento_id, 'profissional': profissional_id,
        'data_desejada': data_desejada, 'turno': turno or '',
    }

    def _erro(msg):
        messages.error(request, msg)
        return render(request, 'publico/lista_espera.html', context)

    if not all([nome, telefone_bruto, email, procedimento_id, data_desejada]):
        return _erro('Preencha nome, celular, e-mail, procedimento e data.')

    # Valida antes do banco: o CHECK do Postgres (10-11 digitos) viraria 500.
    try:
        if not telefone:
            raise ValidationError('sem dígitos')
        validate_telefone_br(telefone)
    except ValidationError:
        return _erro('Informe um celular válido com DDD, ex.: (17) 99999-9999.')

    try:
        validate_email(email)
    except ValidationError:
        return _erro('Informe um e-mail válido.')

    try:
        procedimento = Procedimento.objects.get(pk=procedimento_id, ativo=True)
    except (Procedimento.DoesNotExist, ValueError):
        return _erro('Procedimento inválido.')

    profissional = None
    if profissional_id:
        try:
            profissional = Profissional.objects.get(pk=profissional_id, ativo=True)
        except (Profissional.DoesNotExist, ValueError):
            profissional = None

    try:
        data_obj = datetime.strptime(data_desejada, '%Y-%m-%d').date()
    except ValueError:
        return _erro('Data inválida.')

    if data_obj < datas.hoje():
        return _erro('Escolha uma data a partir de hoje.')

    if turno and turno not in {k for k, _ in TURNOS}:
        turno = None

    try:
        with transaction.atomic():
            cliente = Cliente.objects.filter(telefone=telefone).first()
            if cliente is None:
                # E-mail ja usado por outro cadastro: cria sem e-mail (UNIQUE parcial)
                email_livre = not Cliente.objects.filter(email__iexact=email).exists()
                cliente = Cliente.objects.create(
                    nome=nome, telefone=telefone, ativo=True,
                    email=email if email_livre else None,
                )

            ja_inscrito = ListaEspera.objects.filter(
                cliente=cliente,
                procedimento=procedimento,
                data_desejada=data_obj,
                notificado=False,
            ).exists()

            if not ja_inscrito:
                ListaEspera.objects.create(
                    cliente=cliente,
                    procedimento=procedimento,
                    profissional_desejado=profissional,
                    data_desejada=data_obj,
                    turno_desejado=turno,
                )
    except IntegrityError:
        logger.warning('lista_espera_integridade', exc_info=True)
        return _erro('Não foi possível concluir sua inscrição agora. Tente novamente em instantes.')

    # Mesma pagina de sucesso p/ nova inscricao ou repetida (nao revela cadastro alheio).
    return redirect('aranha:lista_espera_sucesso')


def lista_espera_sucesso(request):
    """Confirmacao de inscricao na lista de espera."""
    return render(request, 'publico/lista_espera_sucesso.html')


def servico_detalhe(request, slug):
    """Pagina de detalhe individual de um procedimento pelo slug."""
    procedimento = get_object_or_404(Procedimento, slug=slug, ativo=True)

    # Mesmo resolvedor de /especialidades/ e do booking (base primeiro).
    valor = preco_base_map([procedimento.pk]).get(procedimento.pk)
    preco = float(valor) if valor is not None else None

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


# ─── Servicos por categoria (paginas institucionais estaticas) ───

def servicos_faciais(request):
    return render(request, 'servicos/faciais.html')


def servicos_corporais(request):
    return render(request, 'servicos/corporais.html')
