"""Perguntas frequentes do site (/duvidas/, as em destaque na home, e o JSON-LD FAQPage do
partials/faq_accordion.html).

As respostas que sao regra saem das constantes que o sistema aplica — a janela de 24 h para
remarcar, o cancelamento tardio que conta falta, o retorno combinado, o credito de indicacao —, para o
FAQ nao contradizer o sistema. Os canais (WhatsApp, telefone, e-mail) chegam da Branding pela view:
so aparece canal que existe de verdade (o FAQ tambem vai para o JSON-LD).
"""
from ..constants import JANELA_MINIMA_ALTERACAO, MAX_FALTAS_ANTES_BLOQUEIO, VALOR_CASHBACK_INDICACAO


def _juntar_ou(itens: list[str]) -> str:
    """['a'] -> 'a'; ['a', 'b', 'c'] -> 'a, b ou c'."""
    if len(itens) <= 1:
        return ''.join(itens)
    return f"{', '.join(itens[:-1])} ou {itens[-1]}"


def _horas(delta) -> str:
    return f'{int(delta.total_seconds() // 3600)} h'


def _brl(valor) -> str:
    return f'R$ {valor:.2f}'.replace('.', ',')


def montar_faq(*, canais_agendar: list[str], canais_exclusao: list[str],
               limite_faltas: int = MAX_FALTAS_ANTES_BLOQUEIO) -> list[dict]:
    """[{slug, nome, itens: [{q, a, destaque?}]}]. canais_*: os que existem; limite_faltas: o efetivo
    (painel). destaque=True: pergunta que tambem aparece na pagina inicial (em_destaque)."""
    janela = _horas(JANELA_MINIMA_ALTERACAO)
    como_agendar = 'Você pode agendar online pelo nosso site (disponível 24h)'
    if canais_agendar:
        como_agendar += f', {_juntar_ou(canais_agendar)}' if len(canais_agendar) > 1 else f' ou {canais_agendar[0]}'
    como_agendar += '. No site, é só escolher o procedimento, o dia e o horário.'
    como_excluir = (
        'Sim. Na área "Meus agendamentos" você baixa os seus dados, muda as suas preferências de '
        'comunicação e pede correção ou exclusão. Se preferir, fale com a gente '
        f'{_juntar_ou([*canais_exclusao, "presencialmente na clínica"])}.'
    )
    return [
        {
            'slug': 'agendamento',
            'nome': 'Agendamento',
            'itens': [
                {'q': 'Como faço para agendar um atendimento?', 'a': como_agendar, 'destaque': True},
                {'q': 'Preciso criar uma conta para agendar?', 'destaque': True,
                 'a': 'Não. O agendamento é sem cadastro: você se identifica pelo celular e confirma com um '
                      'código enviado por SMS. Rápido e sem senha.'},
                {'q': 'Posso cancelar ou remarcar meu horário?', 'destaque': True,
                 'a': f'Sim, pela área "Meus agendamentos" (link no rodapé do site). Remarcar online vale até '
                      f'{janela} antes do horário. Cancelar é sempre possível — o horário volta para quem está '
                      f'na lista de espera —, mas com menos de {janela} de antecedência o cancelamento conta '
                      f'como falta: depois de {limite_faltas} faltas seguidas, o agendamento online '
                      'fica suspenso e você agenda pela recepção.'},
                {'q': 'Não achei horário no dia que eu queria. E agora?',
                 'a': 'Entre na lista de espera escolhendo o dia e o turno. Se um horário compatível vagar, '
                      'avisamos você; você continua na lista até a data passar ou até agendar.'},
                {'q': 'Como funciona a avaliação inicial?', 'destaque': True,
                 'a': 'Antes de qualquer procedimento, fazemos uma avaliação individualizada para entender seu '
                      'objetivo e indicar o cuidado mais adequado para você.'},
            ],
        },
        {
            'slug': 'pagamentos',
            'nome': 'Pagamentos e Pacotes',
            'itens': [
                {'q': 'Quais são as formas de pagamento?', 'destaque': True,
                 'a': 'Aceitamos PIX, cartão de débito, cartão de crédito (com parcelamento) e dinheiro.'},
                {'q': 'Como funcionam os pacotes de sessões?',
                 'a': 'Os pacotes reúnem um número de sessões por um valor fechado, com validade definida e '
                      'condições especiais em relação às sessões avulsas. A sessão do pacote aparece como '
                      '"Sessão do pacote" em Meus agendamentos, sem cobrança em dobro.'},
                {'q': 'Ganho algo indicando uma amiga?',
                 'a': f'Sim. Seu código de indicação fica em "Meus agendamentos": quando a amiga informar o '
                      f'código e fizer o primeiro atendimento, você ganha {_brl(VALOR_CASHBACK_INDICACAO)} de '
                      'crédito para usar no seu próximo cuidado.'},
            ],
        },
        {
            'slug': 'tratamentos',
            'nome': 'Tratamentos',
            'itens': [
                {'q': 'Os tratamentos servem para todos os tipos de pele?',
                 'a': 'Cada pessoa é única. Por isso fazemos uma avaliação individualizada antes do procedimento '
                      'para indicar o cuidado mais adequado ao seu tipo de pele.'},
                {'q': 'Quanto tempo dura cada sessão?',
                 'a': 'Depende do procedimento escolhido — a duração estimada aparece na hora de agendar, junto '
                      'com os horários disponíveis.'},
                {'q': 'Alguns procedimentos têm retorno?',
                 'a': 'Sim. Quando o cuidado prevê acompanhamento, o retorno é sem custo e combinado com você: '
                      'o prazo aparece em "Meus agendamentos" e a clínica marca o melhor dia.'},
                {'q': 'Existem contraindicações?',
                 'a': 'Algumas. Por isso preenchemos uma ficha de avaliação antes do atendimento — assim '
                      'garantimos sua segurança e o melhor resultado.'},
            ],
        },
        {
            'slug': 'privacidade',
            'nome': 'Privacidade e Dados',
            'itens': [
                {'q': 'Como meus dados são armazenados?',
                 'a': 'Seguimos a LGPD. Seus dados ficam guardados com segurança, a ficha de saúde é gravada '
                      'cifrada e tudo é usado apenas para o seu atendimento e a comunicação que você autorizou. '
                      'Os detalhes estão na Política de Privacidade.'},
                {'q': 'Posso acessar ou excluir meus dados?', 'a': como_excluir},
                {'q': 'Vou receber mensagens de divulgação?',
                 'a': 'Apenas se você autorizar. Você escolhe o que deseja receber e pode cancelar a qualquer '
                      'momento, em "Meus agendamentos" ou pelo link de descadastro de cada mensagem de divulgação.'},
            ],
        },
    ]


def em_destaque(categorias: list[dict]) -> list[dict]:
    """As perguntas da pagina inicial (destaque=True), na ordem do FAQ; a lista completa fica em
    /duvidas/. O JSON-LD de cada pagina leva so as perguntas que ela mostra."""
    return [item for cat in categorias for item in cat['itens'] if item.get('destaque')]
