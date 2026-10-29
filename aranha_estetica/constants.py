"""Constantes de dominio com uso real no codigo.

Nao importar Django models aqui (mantem modulo puro). Limites que o dono
ajusta pelo painel vivem em Configuracao (ex.: MAX_FALTAS_BLOQUEIO); os
valores abaixo sao o default quando a chave nao existe.
"""
from datetime import timedelta
from decimal import Decimal


# ─── Alteracao pela cliente (decisao 10) ───────────────────────────────
# Remarcar online exige esta antecedencia; cancelar online e sempre permitido (a vaga volta p/ a
# lista de espera), mas com menos que isto vira cancelamento tardio e conta como falta
# (services/agenda.cancelar_pela_cliente). O FAQ do site le daqui (conteudo/faq.py).
JANELA_MINIMA_ALTERACAO = timedelta(hours=24)
# Realizado/Faltou so a partir do inicio do atendimento (menos esta folga p/ quem marca na hora):
# antes, um clique na agenda do mes seguinte gerava comissao, debitava pacote e contava falta.
TOLERANCIA_DESFECHO = timedelta(minutes=30)

# ─── Pedido pelo site (D8) ──────────────────────────────────────────────
# Cliente ja atendida, sem falta em aberto, com menos que isto de horarios futuros: o pedido
# nasce AGENDADO (sem fila de aprovacao). Primeira vez ou acima do limite: PENDENTE.
LIMITE_AUTOAPROVACAO = 2
# Teto de horarios futuros ativos (pendente/agendado/confirmado) p/ um novo pedido pelo site: cobre o
# pacote semanal sem abrir espaco p/ sequestro de agenda. Acima disso a recepcao marca pelo painel.
MAX_ATIVOS_ONLINE = 3
# Pedido sem decisao e cancelado ate esta folga antes do inicio (job limpeza_status, de hora em
# hora): a vaga volta p/ a lista de espera e a cliente sabe a tempo de escolher outro horario.
PRAZO_DECISAO_PENDENTE = timedelta(hours=1)
MSG_PEDIDO_EXPIRADO = (
    'Não conseguimos confirmar o seu pedido a tempo. Escolha outro horário — '
    'será um prazer receber você.'
)


# ─── Links publicos (link_acesso.expira_em, gravado na emissao) ───────────
# NPS: 7 dias do envio. Ficha/pesquisa: 60 dias do convite. Confirmacao D-1 e
# termo expiram no proprio atendimento (inicio/fim).
VALIDADE_LINK_NPS = timedelta(days=7)
VALIDADE_LINK_FORMULARIO = timedelta(days=60)


# ─── Comunicacao com a cliente (D19, D20) ─────────────────────────────────
# NPS: no maximo 1 pesquisa por cliente neste intervalo (e nunca em retorno): services/nps.
NPS_INTERVALO_DIAS = 90


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
# A Politica de Privacidade completa (texto, bases legais, operadores e prazos) mora em
# conteudo/lgpd.py e e a fonte unica do termo LGPD e da declaracao do DSAR (art. 19 II). Usado pela
# migration 0045/0078 (banco sem termo LGPD) e pelo `seed` — nao remover. Mudou a politica depois do
# go-live? Publique nova versao em Painel > Termos: a versao ja aceita e imutavel (VersaoTermo.save).
from .conteudo.lgpd import (  # noqa: E402,F401  (re-export; modulo puro, sem Django)
    BASES_LEGAIS, TERMO_LGPD_CONTEUDO, TERMO_LGPD_TITULO, TERMO_LGPD_VERSAO,
)

# Prazos de retencao vivem em settings.RETENCAO_* — os LGPD sao fixos (publicados no termo; check
# aranha.W016) e os de housekeeping (OTP, notificacao, auditoria) ajustaveis por env.
