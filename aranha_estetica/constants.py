"""Constantes de dominio com uso real no codigo.

Nao importar Django models aqui (mantem modulo puro). Limites que o dono
ajusta pelo painel vivem em Configuracao (ex.: MAX_FALTAS_BLOQUEIO); os
valores abaixo sao o default quando a chave nao existe.
"""
from datetime import timedelta
from decimal import Decimal


# ─── Reagendamento self-service ────────────────────────────────────────
JANELA_MINIMA_REAGENDAMENTO = timedelta(hours=24)


# ─── No-show ───────────────────────────────────────────────────────────
# Default de Cliente.registrar_falta; sobrescrito por Configuracao
# chave='MAX_FALTAS_BLOQUEIO' (tela Configuracoes do painel).
MAX_FALTAS_ANTES_BLOQUEIO = 3


# ─── Fidelidade / Cashback (F-CSB) ─────────────────────────────────────
VALOR_CASHBACK_INDICACAO = Decimal('50.00')


# ─── Aniversario ───────────────────────────────────────────────────────
# Percentual inteiro de EXIBICAO (15 = "15%"), interpolado em copy de
# email/WhatsApp — nao usar em aritmetica monetaria.
DESCONTO_ANIVERSARIO_PERCENTUAL = 15


# Prazos de retencao (OTP, notificacao, auditoria) vivem em settings.RETENCAO_*
# (ajustaveis por env) — ver clinica/settings/base.py e tasks_manutencao.py.
