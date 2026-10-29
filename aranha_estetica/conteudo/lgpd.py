"""Politica de Privacidade v1.0 e a declaracao do tratamento (LGPD arts. 9, 18, 19 II e 20).

Fonte UNICA de: o texto do termo LGPD (``TERMO_LGPD_CONTEUDO``, gravado na VersaoTermo pela 0045 e
pelo seed, e renderizado em /politica-de-privacidade/) e a secao ``sobre_o_tratamento`` do
"baixar meus dados" (services/lgpd). Modulo puro (sem Django): a migration importa daqui.

REVISAO JURIDICA: o texto foi escrito a partir do que o sistema faz, para uma clinica pequena de
estetica; a dona da clinica (e, se possivel, um advogado) precisa revisar antes do go-live. Nada
aqui identifica a clinica (razao social, CNPJ, endereco, encarregado): isso vem da tela Branding
e a pagina da politica emoldura o texto com esses dados.

Formato do texto (o filtro ``texto_termo`` renderiza; no wizard ele aparece cru, legivel assim):
bloco separado por linha em branco; bloco de uma linha "N. Titulo" = secao; linhas "- " = lista.

Mudou o texto? Depois do go-live, so com versao nova do termo (Painel > Termos): a versao aceita e
imutavel e o SHA-256 dela e a prova do aceite. Os prazos de ``PRAZOS_RETENCAO`` precisam bater com
settings.RETENCAO_* (check aranha.W016 confere o texto da versao vigente).
"""

TERMO_LGPD_VERSAO = '1.0'
TERMO_LGPD_TITULO = 'Política de Privacidade e tratamento de dados pessoais (LGPD)'

# (finalidade, dados, base legal) — art. 9 I/II e art. 7º/11
TRATAMENTOS = (
    ('Agendar, confirmar, remarcar, cancelar e realizar o seu atendimento, com os e-mails de '
     'confirmação, cancelamento e lembrete',
     'nome, celular, e-mail (se informado), procedimento, profissional, data e horário',
     'execução de contrato e procedimentos preliminares a seu pedido (art. 7º, V)'),
    ('Confirmar que você tem 18 anos ou mais e avaliar a segurança do procedimento',
     'data de nascimento (obrigatória no agendamento)',
     'execução de contrato (art. 7º, V)'),
    ('Confirmar, por código enviado por SMS, que o celular informado é seu',
     'celular e o código de confirmação',
     'execução de contrato (art. 7º, V) e prevenção a fraudes (art. 7º, IX)'),
    ('Emitir nota fiscal e formalizar a venda de pacotes de sessões',
     'nome, CPF, CEP e endereço, só quando necessários',
     'obrigação legal e execução de contrato (art. 7º, II e V)'),
    ('Ficha de avaliação (anamnese), prontuário e anotações dos atendimentos, para avaliar a '
     'segurança do procedimento e cuidar do seu atendimento',
     'dados de saúde (alergias, contraindicações, histórico, medicamentos em uso, gestação e o que '
     'você responder na ficha)',
     'consentimento específico e destacado (art. 11, I), revogável'),
    ('Lembrete do agendamento e pesquisa de satisfação por WhatsApp',
     'nome, celular, procedimento e horário',
     'consentimento (art. 7º, I), revogável'),
    ('Promoções, novidades e felicitação de aniversário por e-mail',
     'nome, e-mail e data de nascimento',
     'consentimento (art. 7º, I), revogável'),
    ('Publicar o seu depoimento no site, com o primeiro nome e a inicial do sobrenome',
     'nome, nota e comentário da pesquisa de satisfação',
     'consentimento (art. 7º, I), revogável'),
    ('Registrar a nota e o comentário da pesquisa de satisfação, para melhorar o atendimento',
     'nota e comentário',
     'legítimo interesse (art. 7º, IX); responder é opcional'),
    ('Lista de espera e formulário de contato',
     'nome, celular, e-mail e mensagem',
     'procedimentos preliminares a seu pedido (art. 7º, V)'),
    ('Programa de indicação',
     'código de indicação, quem indicou e o crédito gerado',
     'execução de contrato (art. 7º, V)'),
    ('Guardar os registros dos atendimentos, dos termos aceitos e dos consentimentos',
     'histórico de atendimentos, versão do termo aceito, data, hora e endereço IP',
     'obrigação legal e exercício regular de direitos (art. 7º, II e VI; art. 11, II, "d")'),
    ('Segurança do site e prevenção de fraudes e abusos',
     'endereço IP, data, hora e dados técnicos do navegador',
     'legítimo interesse (art. 7º, IX)'),
)

# (operador, para que, observacao) — art. 9 V e art. 33
OPERADORES = (
    ('Railway', 'hospedagem do site e do banco de dados', 'pode tratar dados fora do Brasil'),
    ('Zenvia', 'envio do código de confirmação por SMS', ''),
    ('Meta (WhatsApp Business)', 'mensagens de WhatsApp de lembrete, pesquisa e aviso de vaga, só com o '
     'seu consentimento', 'trata dados fora do Brasil'),
    ('Provedor de envio de e-mail contratado pela clínica', 'e-mails de confirmação, lembrete e, com o seu '
     'consentimento, promoções', 'pode tratar dados fora do Brasil'),
    ('Cloudflare (Turnstile)', 'verificação contra robôs nos formulários do site', 'recebe o endereço IP e '
     'dados técnicos do navegador; trata dados fora do Brasil'),
    ('Sentry, quando ativado', 'monitoramento de erros do site, sem dados de contato nem de saúde',
     'trata dados fora do Brasil'),
    ('Provedor de armazenamento da cópia de segurança', 'backup do banco de dados, gravado cifrado',
     'pode tratar dados fora do Brasil'),
    ('Aplicativo de calendário escolhido pela profissional (Google, Apple ou Microsoft)', 'agenda da '
     'profissional: data, horário, procedimento, primeiro nome e inicial do sobrenome', 'pode tratar dados '
     'fora do Brasil'),
    ('jsDelivr', 'bibliotecas usadas só no painel interno da equipe', 'não recebe dados de clientes'),
)

# (o que, chave em settings, valor padrao, unidade, complemento) — o padrao TEM que ser o do settings
PRAZOS_RETENCAO = (
    ('Ficha de avaliação, prontuário, anotações e fichas de atendimentos realizados',
     'RETENCAO_ATENDIMENTO_SAUDE_DIAS', 365 * 20, 'dias',
     'depois do último atendimento realizado ou da última atualização; depois, são eliminados'),
    ('Cadastro e histórico de agendamentos', 'RETENCAO_CLIENTE_INATIVO_DIAS', 365 * 5, 'dias',
     'sem nenhum atendimento; depois, o cadastro é pseudonimizado (sem nome, contato, documentos e '
     'indicação), salvo o registro de saúde dentro do prazo acima'),
    ('Pedido de exclusão do cadastro', 'RETENCAO_CARENCIA_SOFT_DELETE_DIAS', 30, 'dias',
     'para desfazer um engano; depois, o cadastro é pseudonimizado'),
    ('Pacote de sessões (contrato)', 'RETENCAO_PACOTE_APOS_EXPIRAR_DIAS', 365 * 5, 'dias',
     'depois de expirar ou ser cancelado'),
    ('Ficha preenchida para um agendamento que não aconteceu', 'RETENCAO_FICHA_SEM_ATENDIMENTO_DIAS', 90,
     'dias', ''),
    ('Texto das mensagens enviadas (lembretes e confirmações)', 'RETENCAO_NOTIFICACAO_DIAS', 365, 'dias', ''),
    ('Registros de auditoria, com endereço IP', 'RETENCAO_LOG_AUDITORIA_DIAS', 365 * 5, 'dias',
     'como prova de autoria e de consentimento'),
    ('Código de confirmação por SMS', 'RETENCAO_OTP_HORAS', 24, 'horas', ''),
    ('Lista de espera', None, None, '', 'até a data desejada passar ou até você agendar'),
)


def formatar_prazo(valor: int, unidade: str = 'dias') -> str:
    """365*20 dias -> '20 anos'; 365 -> '1 ano'; 90 -> '90 dias'; 24 horas -> '24 horas'."""
    if unidade == 'horas':
        return f'{valor} hora' if valor == 1 else f'{valor} horas'
    if valor and valor % 365 == 0:
        anos = valor // 365
        return '1 ano' if anos == 1 else f'{anos} anos'
    return '1 dia' if valor == 1 else f'{valor} dias'


def linha_prazo(rotulo: str, valor, unidade: str, complemento: str) -> str:
    """Linha da tabela de prazos ('- O que: prazo, complemento.'): a mesma na politica e no check."""
    if valor is None:
        return f'- {rotulo}: {complemento}.'
    prazo = formatar_prazo(valor, unidade)
    return f'- {rotulo}: {prazo}' + (f', {complemento}.' if complemento else '.')


def _secao(numero: int, titulo: str, *blocos: str) -> str:
    return '\n\n'.join((f'{numero}. {titulo}', *blocos))


def _lista(linhas) -> str:
    return '\n'.join(linhas)


def montar_politica(prazos: dict | None = None) -> str:
    """Texto completo da politica. prazos: {chave do settings: valor} (padrao = PRAZOS_RETENCAO)."""
    valores = prazos or {}
    tabela_prazos = _lista(
        linha_prazo(rotulo, valores.get(chave, padrao) if chave else None, unidade, complemento)
        for rotulo, chave, padrao, unidade, complemento in PRAZOS_RETENCAO
    )
    return '\n\n'.join((
        'Esta Política explica quais dados pessoais a clínica trata quando você usa o site, agenda um '
        'atendimento ou é atendida, para que eles servem, com quem são compartilhados, por quanto tempo '
        'ficam guardados e como você exerce os seus direitos pela Lei Geral de Proteção de Dados '
        '(LGPD, Lei nº 13.709/2018).',

        _secao(
            1, 'Quem cuida dos seus dados',
            'A clínica responsável por este site é a controladora dos dados pessoais tratados no site e nos '
            'atendimentos. A identificação da clínica (nome, razão social, CNPJ e endereço) e o contato do '
            'encarregado pelo tratamento de dados (art. 41) estão na página da Política de Privacidade '
            '(/politica-de-privacidade/) e no rodapé do site.',
        ),
        _secao(
            2, 'Quais dados tratamos',
            _lista((
                '- Identificação e contato: nome e celular (obrigatórios para agendar) e e-mail (opcional).',
                '- Data de nascimento: obrigatória no agendamento, para confirmar que você tem 18 anos ou mais '
                'e avaliar a segurança do procedimento. A felicitação de aniversário só é enviada se você '
                'aceitar receber promoções.',
                '- CPF, CEP e endereço: só quando necessários para a nota fiscal ou o contrato de pacote de '
                'sessões. Não coletamos RG nem profissão.',
                '- Agendamentos: procedimento, profissional, data, horário, histórico, faltas e cancelamentos.',
                '- Dados de saúde: a ficha de avaliação (anamnese), o prontuário e as anotações dos '
                'atendimentos. São dados pessoais sensíveis e recebem proteção reforçada.',
                '- Termos e preferências: o registro dos termos aceitos (versão, data, hora e endereço IP) e '
                'das suas preferências de comunicação.',
                '- Pesquisa de satisfação: nota, comentário e, se você autorizar, a publicação do depoimento.',
                '- Lista de espera e contato: o que você informa nesses formulários.',
                '- Programa de indicação: o seu código, quem indicou você e o crédito gerado.',
                '- Dados técnicos: endereço IP, data, hora e dados do navegador.',
            )),
            'Não pedimos senha nem dados de cartão no site: o agendamento é feito sem conta, com '
            'confirmação por código enviado por SMS.',
        ),
        _secao(
            3, 'Para que usamos e com qual base legal',
            _lista(f'- {finalidade}: {base}.' for finalidade, _dados, base in TRATAMENTOS),
        ),
        _secao(
            4, 'Dados de saúde',
            'Os dados de saúde são tratados com o seu consentimento específico e destacado (art. 11, I), '
            'dado ao preencher a ficha de avaliação, e servem só para avaliar a segurança do procedimento '
            'e cuidar do seu atendimento. Ficam gravados cifrados e só a equipe que cuida do seu '
            'atendimento tem acesso.',
            'Você pode revogar esse consentimento a qualquer momento, na área "Meus agendamentos" do site '
            'ou pedindo à clínica. A revogação vale dali em diante: o registro já feito continua guardado '
            'pelo prazo indicado na seção 11, para a sua segurança e para o exercício regular de direitos '
            '(art. 7º, VI; art. 11, II, "d"; art. 16). Um novo atendimento pede uma nova ficha e um novo '
            'consentimento; sem a ficha, alguns procedimentos não podem ser feitos com segurança.',
        ),
        _secao(
            5, 'Mensagens que enviamos',
            _lista((
                '- E-mails de confirmação, cancelamento e lembrete do agendamento fazem parte do serviço '
                '(art. 7º, V) e são enviados quando você informa um e-mail.',
                '- Lembrete e pesquisa de satisfação por WhatsApp só com o seu consentimento. Para parar, '
                'responda PARAR ou SAIR no WhatsApp ou desmarque a opção em "Meus agendamentos".',
                '- Promoções, novidades e aniversário por e-mail só com o seu consentimento. Todo e-mail de '
                'divulgação traz um link para cancelar o recebimento.',
                '- O código por SMS serve só para confirmar que o celular é seu.',
            )),
        ),
        _secao(
            6, 'Decisões automatizadas',
            'Depois de um número de faltas seguidas definido pela clínica (padrão: 3), o agendamento '
            'online é suspenso automaticamente. O cancelamento feito com menos de 24 horas de '
            'antecedência conta como falta. Você continua podendo agendar pela recepção e pode pedir, a '
            'qualquer momento, que uma pessoa da equipe revise a suspensão (art. 20). Pedidos de quem já '
            'foi atendida podem ser confirmados automaticamente; os demais são aprovados pela '
            'profissional.',
        ),
        _secao(
            7, 'Com quem compartilhamos',
            'Não vendemos os seus dados. Eles são compartilhados apenas com fornecedores que ajudam a '
            'prestar o serviço (operadores), que só podem usá-los para essa finalidade:',
            _lista(
                f'- {nome}: {servico}' + (f' ({obs})' if obs else '') + '.'
                for nome, servico, obs in OPERADORES
            ),
            'Quando um fornecedor trata dados fora do Brasil, a transferência internacional segue o '
            'art. 33 da LGPD. Também compartilhamos dados quando exigido por lei ou por ordem de autoridade '
            'competente.',
        ),
        _secao(
            8, 'Cookies, fontes e mapa',
            'Usamos apenas cookies essenciais: sessão, proteção contra envios falsos, preferência de tema '
            'claro ou escuro e o registro de que você viu o aviso de cookies. Não usamos cookies de '
            'publicidade nem de análise de navegação. As fontes são servidas pelo próprio site, e o mapa '
            'da página de contato é uma imagem do próprio site: o Google Maps só abre se você clicar em '
            '"Abrir no Google Maps".',
        ),
        _secao(
            9, 'Segurança',
            'Os dados trafegam com criptografia (HTTPS). A ficha de avaliação, o prontuário e as anotações '
            'ficam gravados cifrados, e a cópia de segurança também é cifrada. O acesso ao painel da '
            'clínica exige login individual com segundo fator, e cada acesso ao prontuário fica registrado.',
        ),
        _secao(
            10, 'Seus direitos',
            'Você pode, a qualquer momento e sem custo: confirmar se tratamos os seus dados e acessá-los; '
            'corrigir dados incompletos, inexatos ou desatualizados; pedir a anonimização, o bloqueio ou a '
            'eliminação do que for desnecessário ou tratado em desconformidade; pedir a portabilidade; '
            'saber com quem compartilhamos; ser informada sobre a possibilidade de não consentir e as '
            'consequências; revogar consentimentos; e pedir a revisão de decisões automatizadas (arts. 18 '
            'e 20).',
            'Como pedir: na área "Meus agendamentos" do site você muda as suas preferências de '
            'comunicação, revoga o consentimento de saúde, pede correção ou exclusão e baixa os seus dados '
            '(a ficha de saúde vai ao e-mail cadastrado ou é entregue mediante pedido na clínica). Também '
            'atendemos pelos canais de contato da clínica. Respondemos em até 15 dias. Você também pode '
            'apresentar reclamação à Autoridade Nacional de Proteção de Dados (ANPD).',
        ),
        _secao(11, 'Por quanto tempo guardamos', tabela_prazos),
        _secao(
            12, 'Alterações nesta política',
            'Se esta política mudar, uma nova versão será publicada com a nova data de vigência, e o que '
            'depender do seu consentimento será pedido novamente.',
        ),
    ))


TERMO_LGPD_CONTEUDO = montar_politica()

# Declaracao do tratamento (art. 19 II) no "baixar meus dados": mesma fonte da politica
BASES_LEGAIS = {
    'tratamentos': [
        {'finalidade': finalidade, 'dados': dados, 'base_legal': base}
        for finalidade, dados, base in TRATAMENTOS
    ],
    'operadores': [
        {'operador': nome, 'servico': servico, 'observacao': obs} for nome, servico, obs in OPERADORES
    ],
}
