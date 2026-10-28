"""Ambiente de testes PUBLICADO (Railway "dev", saindo da branch dev): producao + travas.

DJANGO_ENV=staging no ambiente dev do Railway. Tudo como em prod.py (DEBUG=False, HTTPS, Postgres,
cifragem, 2FA) e mais:
- AMBIENTE_TESTE=True: faixa "ambiente de testes" no site e no painel, robots/X-Robots-Tag noindex
  e `seed --demo` liberado (dados de demonstracao);
- SMS so em log: o codigo OTP sai no log `sms_dev_log` do Railway (quem tem acesso ao projeto
  conclui o agendamento sem gastar credito nem mandar SMS a terceiros);
- e-mail e WhatsApp so p/ DESTINATARIOS_TESTE (env, separados por virgula): o e-mail passa pelo
  BackendAmbienteTeste, que filtra os destinatarios e entrega pelo backend real
  (EMAIL_BACKEND da env); fora da lista nada sai (log envio_bloqueado_ambiente_teste).
"""
# ruff: noqa: F405  (star import intencional)
from .prod import *  # noqa: F401,F403

AMBIENTE_TESTE = True

SMS_DEV_LOG_ONLY = True

EMAIL_BACKEND_REAL = EMAIL_BACKEND
EMAIL_BACKEND = 'aranha_estetica.utils.email.BackendAmbienteTeste'

# W002 = SMS em modo log (aqui e o esperado)
SILENCED_SYSTEM_CHECKS = ['aranha.W002']
