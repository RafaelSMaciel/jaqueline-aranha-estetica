"""Settings da suite (manage.py test e pytest) — escolhido em settings/__init__.py.

E a unica deteccao de "estou em teste" (antes: sys.argv em base.py e em dev.py). Mesmo
ponto de partida do dev (DEBUG da env, sem HTTPS), sem debug-toolbar e com hasher rapido.
"""
# ruff: noqa: F405  (star import intencional)
import os

import dj_database_url

from .base import *  # noqa: F401,F403

DEBUG = True  # o runner troca p/ False na execucao; aqui vale p/ o que e lido no import
INTERNAL_IPS = ['127.0.0.1', 'localhost']
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# PBKDF2 com 1M de iteracoes custava ~1/3 da suite (cada create_user/login). Producao nao muda.
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# Banco: SQLite em arquivo por padrao — `--keepdb` reaproveita as migrations entre execucoes
# (um modulo de teste: ~15 s -> ~2 s). No Windows/macOS o `--parallel` so funciona com o banco em
# memoria (o Django clona p/ os workers "spawn" so a partir dele): TESTE_SQLITE_MEMORIA=1.
# TEST_DATABASE_URL=postgres://... roda no Postgres (CI e docker-compose): DDL, CHECK, EXCLUDE,
# trigger e procedures das migrations so existem la.
_test_db_url = os.environ.get('TEST_DATABASE_URL', '').strip()
if _test_db_url:
    # mesmas OPTIONS de producao (application_name, statement_timeout): o CI roda com elas
    DATABASES = {'default': opcoes_postgres(dj_database_url.parse(_test_db_url))}
else:
    _arquivo = os.environ.get('TESTE_SQLITE_MEMORIA', '').strip() != '1'
    DATABASES = {'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db_test.sqlite3',
        'TEST': {'NAME': BASE_DIR / 'db_test.sqlite3' if _arquivo else None},
    }}

# vite_asset sem manifest.json no disco: as tags apontam p/ o dev server. TESTE_VITE_BUILD=1 usa
# o bundle do `npm run build` (testes de navegador no CI: tests/test_wizard_browser.py).
DJANGO_VITE['default']['dev_mode'] = os.environ.get('TESTE_VITE_BUILD', '').strip() != '1'

# SMS so logado (o runner roda sem ZENVIA_*; fora disso utils/sms falha fechado).
# Env + setting: vale p/ leitura no import.
os.environ.setdefault('SMS_DEV_LOG_ONLY', 'true')
SMS_DEV_LOG_ONLY = True

# O runner roda os checks com DEBUG=False: os avisos de config de producao (aranha/checks.py)
# viram ruido em toda execucao. Testados direto em tests/test_deploy_config.py.
SILENCED_SYSTEM_CHECKS = [f'aranha.W{i:03d}' for i in range(1, 14)]
# COMMENT ON (db_comment/db_table_comment, padrao SQL §2) so existe no Postgres; o SQLite dos
# testes ignora e avisaria em toda execucao.
SILENCED_SYSTEM_CHECKS += ['fields.W163', 'models.W046']
