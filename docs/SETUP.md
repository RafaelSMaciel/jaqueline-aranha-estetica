# Setup — shivazen-app

## Requisitos

- Python 3.12+
- PostgreSQL 16 (prod) ou SQLite (dev)
- Redis 7 (cache + Celery broker)
- Node não é necessário (assets buildados em `static/`)

## Setup local (sem Docker)

```bash
git clone <repo>
cd shivazen-app
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env       # editar segredos
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Servidor em http://localhost:8000.

## Setup com Docker

```bash
docker compose up --build
```

Sobe `db` (postgres), `redis`, `web`, `worker`, `beat`. Migrate roda automático.

## Variáveis de ambiente

Veja `.env.example`. Chaves críticas:

| Var | Default | Notas |
|-----|---------|-------|
| `DJANGO_SECRET_KEY` | — | Obrigatório em prod |
| `DJANGO_ENV` | `dev` | `prod` em deploy |
| `DATABASE_URL` | sqlite | `postgres://...` em prod |
| `REDIS_URL` | LocMem | `redis://...` em prod |
| `ALLOWED_HOSTS` | `*` em dev | CSV em prod |
| `EMAIL_*` | console | SMTP em prod |
| `SENTRY_DSN` | — | Opcional |
| `CLINIC_*` | — | Branding white-label |

## Comandos úteis

```bash
# Rodar testes
python manage.py test app_shivazen

# Rodar com coverage
coverage run --source=app_shivazen manage.py test
coverage report
coverage html  # gera htmlcov/

# Celery worker (dev)
celery -A shivazen worker --loglevel=info

# Celery beat (dev)
celery -A shivazen beat --loglevel=info

# Shell Django
python manage.py shell

# Coletar estáticos (prod)
python manage.py collectstatic --noinput
```

## Estrutura

```
shivazen/
  settings/
    __init__.py    # seleciona dev|prod via DJANGO_ENV
    base.py        # config comum
    dev.py         # debug, sem SSL
    prod.py        # HSTS, SSL, sessão 30min
  celery.py
  urls.py

app_shivazen/
  models/          # divididos por domínio
  views/           # divididos por domínio
  forms/           # ModelForms com validators
  services/        # lógica de negócio extraída
  utils/           # security, helpers
  middleware.py    # SecurityHeaders + CSP nonce
  validators.py    # CPF, telefone, datas, valor
  templates/
  static/
  tests/
```
