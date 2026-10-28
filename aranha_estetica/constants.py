"""Constantes de dominio com uso real no codigo.

Nao importar Django models aqui (mantem modulo puro). Limites que o dono
ajusta pelo painel vivem em Configuracao (ex.: MAX_FALTAS_BLOQUEIO); os
valores abaixo sao o default quando a chave nao existe.
"""
from datetime import timedelta
from decimal import Decimal


# ─── Reagendamento self-service ────────────────────────────────────────
JANELA_MINIMA_REAGENDAMENTO = timedelta(hours=24)
# Realizado/Faltou so a partir do inicio do atendimento (menos esta folga p/ quem marca na hora):
# antes, um clique na agenda do mes seguinte gerava comissao, debitava pacote e contava falta.
TOLERANCIA_DESFECHO = timedelta(minutes=30)

# ─── Pedido pelo site (D8) ──────────────────────────────────────────────
# Cliente ja atendida, sem falta em aberto, com menos que isto de horarios futuros: o pedido
# nasce AGENDADO (sem fila de aprovacao). Primeira vez ou acima do limite: PENDENTE.
LIMITE_AUTOAPROVACAO = 2


# ─── Links publicos (link_acesso.expira_em, gravado na emissao) ───────────
# NPS: 7 dias do envio. Ficha/pesquisa: 60 dias do convite. Confirmacao D-1,
# termo e reagendar legado expiram no proprio atendimento (inicio/fim).
VALIDADE_LINK_NPS = timedelta(days=7)
VALIDADE_LINK_FORMULARIO = timedelta(days=60)


# ─── No-show ───────────────────────────────────────────────────────────
# Default de Cliente.registrar_falta; sobrescrito por Configuracao
# chave='MAX_FALTAS_BLOQUEIO' (tela Configuracoes do painel).
MAX_FALTAS_ANTES_BLOQUEIO = 3


# ─── Fidelidade / Cashback (F-CSB) ─────────────────────────────────────
VALOR_CASHBACK_INDICACAO = Decimal('50.00')


# ─── Trilhas (log_auditoria, historico_alteracao) ──────────────────────
# Idade minima que o Postgres aceita apagar (tg_*_imutavel, 0059): a purga de retencao
# (RETENCAO_LOG_AUDITORIA_DIAS) nao pode ser menor (aranha.W013; Marco Civil art. 15 = 6 meses).
TRILHA_RETENCAO_MINIMA_DIAS = 180


# ─── Termo LGPD padrao (v1.0) ───────────────────────────────────────────
# Resumo fiel da pagina /politica-de-privacidade/ (publico/politica_privacidade.html).
# Usado pela migration 0045 (banco em operacao sem termo LGPD) e pelo `seed` —
# nao remover. Mudou a politica? Publique nova versao em Painel > Termos: a
# versao ja aceita e imutavel (VersaoTermo.save).
TERMO_LGPD_VERSAO = '1.0'
TERMO_LGPD_TITULO = 'Política de Privacidade e tratamento de dados pessoais (LGPD)'
TERMO_LGPD_CONTEUDO = (
    'Este é o resumo da Política de Privacidade da clínica. A versão completa está em '
    '/politica-de-privacidade/.\n\n'
    '1. Dados que coletamos: nome, celular e, quando você informa, e-mail e data de '
    'nascimento; dados dos agendamentos (procedimento, profissional, data, horário, '
    'histórico, faltas e cancelamentos); a ficha de avaliação (anamnese), com '
    'informações de saúde — dado pessoal sensível, com proteção reforçada; o registro '
    'de aceite de termos e das suas preferências de comunicação; a nota e o comentário '
    'da pesquisa de satisfação; mensagens de contato e da lista de espera; e dados '
    'técnicos de acesso (endereço IP, data e hora).\n\n'
    '2. Para que usamos: agendar, confirmar, lembrar e realizar o atendimento '
    '(execução do serviço, art. 7º, V); ficha de avaliação e histórico de cuidados, '
    'para a segurança do procedimento (consentimento e tutela da saúde, art. 11, I e '
    'II, "f"); guardar registros de atendimento e de aceites (obrigação legal e '
    'exercício regular de direitos, art. 7º, II e VI); pesquisa de satisfação e '
    'segurança do site (legítimo interesse, art. 7º, IX). Depoimentos no site e '
    'novidades e promoções só com o seu consentimento (art. 7º, I), que pode ser '
    'revogado a qualquer momento.\n\n'
    '3. Compartilhamento: não vendemos seus dados. Eles são compartilhados apenas com '
    'fornecedores que ajudam a prestar o serviço — hospedagem em nuvem (com servidores '
    'que podem ficar fora do Brasil), envio de SMS, WhatsApp e e-mail, verificação '
    'contra robôs, monitoramento de erros, os calendários usados pela profissional e '
    'fontes e mapa do Google — ou quando exigido por lei ou autoridade competente.\n\n'
    '4. Cookies: apenas os essenciais (sessão, proteção contra envios falsos, '
    'preferência de tema e registro do aviso de cookies).\n\n'
    '5. Segurança: conexão criptografada (HTTPS); ficha de avaliação, histórico de '
    'saúde e anotações gravados cifrados; o acesso ao painel da clínica exige login '
    'individual com segundo fator e fica registrado; a ficha de avaliação só é vista '
    'pela equipe que cuida do seu atendimento.\n\n'
    '6. Seus direitos: confirmar o tratamento, acessar, corrigir, pedir portabilidade, '
    'anonimização ou exclusão do que não for obrigatório guardar, saber com quem '
    'compartilhamos e revogar consentimentos — pela área Meus dados do site ou pelos '
    'canais de contato da clínica. Respondemos em até 15 dias.\n\n'
    '7. Por quanto tempo guardamos: ficha de avaliação, histórico de saúde, registro dos '
    'atendimentos realizados e termos aceitos por 20 anos (registro de saúde); o '
    'cadastro, enquanto você for cliente — sem atendimento por 5 anos, ele é '
    'anonimizado; ficha de agendamento que não aconteceu, até 90 dias; texto das '
    'mensagens enviadas, 12 meses; registros técnicos de acesso, até 6 meses.\n\n'
    '8. Alterações: se a política mudar, uma nova versão será publicada com nova data '
    'de vigência, e o que depender do seu consentimento será pedido novamente.'
)


# Prazos de retencao (OTP, notificacao, auditoria) vivem em settings.RETENCAO_*
# (ajustaveis por env) — ver clinica/settings/base.py e jobs_manutencao.py.
