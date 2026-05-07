"""Constantes de dominio — tempos, limites, thresholds, cache TTLs.

Centralizar evita magic numbers espalhados e facilita ajuste futuro.
Nao importar Django models aqui (mantém modulo puro).
"""
from datetime import timedelta
from decimal import Decimal


# ─── Janelas temporais (timedelta) ──────────────────────────────────────
JANELA_MINIMA_REAGENDAMENTO = timedelta(hours=24)
JANELA_MINIMA_CANCELAMENTO = timedelta(hours=24)
JANELA_LEMBRETE_D1 = timedelta(days=1)
JANELA_NPS_POS_ATENDIMENTO = timedelta(hours=24)
JANELA_PESQUISA_ONLINE = timedelta(hours=2)
TTL_TOKEN_LINK_MAGICO = timedelta(days=60)
ANTECEDENCIA_MINIMA_AGENDAMENTO = timedelta(hours=2)
JANELA_MAXIMA_AGENDAMENTO = timedelta(days=90)


# ─── Janelas temporais (segundos / minutos) ─────────────────────────────
TTL_OTP_MINUTOS = 10
TTL_OTP_SEGUNDOS = TTL_OTP_MINUTOS * 60


# ─── Limites de negocio ─────────────────────────────────────────────────
MAX_FALTAS_ANTES_BLOQUEIO = 3
MAX_TENTATIVAS_OTP = 3
MAX_OTP_POR_HORA_TELEFONE = 3
MAX_TENTATIVAS_LOGIN_PRE_LOCKOUT = 5


# ─── Cache TTLs (segundos) ──────────────────────────────────────────────
CACHE_TTL_BRANDING = 600       # 10min
CACHE_TTL_SLOTS = 60           # 1min
CACHE_TTL_FERIADOS = 86400     # 24h
CACHE_TTL_CONFIG = 600         # 10min


# ─── Financeiro ─────────────────────────────────────────────────────────
TAXA_NO_SHOW_PERCENTUAL = Decimal('0.50')


# ─── Limits de upload ───────────────────────────────────────────────────
TAMANHO_MAX_LOGO_MB = 5
TAMANHO_MAX_LOGO_BYTES = TAMANHO_MAX_LOGO_MB * 1024 * 1024
EXTENSOES_LOGO_PERMITIDAS = ('.png', '.jpg', '.jpeg', '.svg', '.webp')


# ─── Strings de notificacao (evita typos) ──────────────────────────────
NOTIF_TIPO_LEMBRETE = 'LEMBRETE'
NOTIF_TIPO_CONFIRMACAO = 'CONFIRMACAO'
NOTIF_TIPO_NPS = 'NPS'
NOTIF_TIPO_PESQUISA = 'PESQUISA'
NOTIF_TIPO_CANCELAMENTO = 'CANCELAMENTO'
NOTIF_TIPO_APROVACAO = 'APROVACAO'

NOTIF_CANAL_WHATSAPP = 'WHATSAPP'
NOTIF_CANAL_EMAIL = 'EMAIL'
NOTIF_CANAL_SMS = 'SMS'
NOTIF_CANAL_PUSH = 'PUSH'

NOTIF_STATUS_PENDENTE = 'PENDENTE'
NOTIF_STATUS_ENVIADO = 'ENVIADO'
NOTIF_STATUS_FALHOU = 'FALHOU'
