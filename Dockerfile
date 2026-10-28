# ─── Stage 0: front (Vite + Tailwind -> aranha_estetica/static/dist) ────
# static/dist e gitignored: sem este stage a imagem sai sem manifest e todo
# template com {% vite_asset %} da 500. Tailwind v4 exige Node >= 20.
# Imagens base fixadas por digest (multi-arch; o Dependabot atualiza): tag movida nao muda a imagem.
FROM node:22-slim@sha256:de170b39439eb0e5a3444ad7d60b015614a381e10fecae2e30ec9e735122f38d AS front

WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY vite.config.js ./
COPY aranha_estetica ./aranha_estetica
RUN npm run build && test -f aranha_estetica/static/dist/manifest.json


# ─── Stage 1: builder (compila wheels) ──────────────────────────────────
FROM python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
# requirements.lock fixa as transitivas (cryptography, django-otp, pywebpush...):
# a imagem sai igual ao que o CI testou, build apos build do mesmo commit.
COPY requirements.txt requirements.lock ./
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt -c requirements.lock


# ─── Stage 2: runtime (imagem final, leve) ──────────────────────────────
FROM python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_ENV=prod

# libpq5 (runtime). Sem curl: o HEALTHCHECK usa o Python da imagem (curl so ajudaria quem
# explorasse o processo a baixar ferramenta ou exfiltrar dado). Usuario de runtime sem shell.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Instala wheels pré-compilados do builder (sem build-essential)
COPY --from=builder /wheels /wheels
RUN pip install --no-index --no-cache /wheels/* \
    && rm -rf /wheels

# Smoke do QR do cadastro de 2FA (obrigatorio p/ ADMIN): a imagem NAO tem
# Pillow, entao o QR e SVG (views/admin_2fa). Falha o build se esse caminho
# passar a exigir PIL — antes o setup dava 500 e trancava o painel.
RUN python -c "import io, qrcode, qrcode.image.svg; qrcode.make('otpauth://totp/smoke', image_factory=qrcode.image.svg.SvgPathFillImage).save(io.BytesIO())"

# Codigo e estaticos do root, so leitura para o usuario app: um RCE no gunicorn nao reescreve .py
# nem template para persistir ate o proximo deploy (nada em runtime escreve em /app).
COPY . .
COPY --from=front /src/aranha_estetica/static/dist ./aranha_estetica/static/dist

# Estaticos no build (whitenoise CompressedManifest em /app/staticfiles; o
# static/src do Vite fica fora via clinica.apps.StaticFilesSemFonteConfig).
# Sem `|| true`: qualquer falha derruba o build. O check reprova manifest do
# Vite ausente (django_vite.W001). DJANGO_SECRET_KEY de build nao vai p/ runtime.
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput --verbosity 1 \
    && DJANGO_SECRET_KEY=build-only python manage.py check --tag staticfiles --fail-level WARNING

USER app

EXPOSE 8080

# Liveness (/healthz/): processo vivo + bundle do front presente, sem banco. Vale para docker run e
# compose; o Railway usa o healthcheckPath do railway.json. Host do healthcheck do Railway: sempre
# em ALLOWED_HOSTS (prod.py). urlopen levanta em resposta != 2xx (sai != 0).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request as u; u.urlopen(u.Request('http://localhost:%s/healthz/' % os.environ.get('PORT', '8080'), headers={'Host': 'healthcheck.railway.app'}), timeout=4)" || exit 1

# 1 worker x 4 threads: cache LocMem (rate-limit, lock de slot, quota de SMS) e
# por processo; mais workers so com REDIS_URL. Migrations rodam no pre-deploy
# (railway.json: migrate_atomico + bootstrap_admin), nao no start.
# LoggerSemTokens: access log com tokens de link magico/reset redigidos
# ([token]). O startCommand do railway.json sobrescreve este CMD: mudar os dois.
CMD ["sh", "-c", "exec gunicorn clinica.wsgi --bind 0.0.0.0:${PORT:-8080} --workers ${WEB_CONCURRENCY:-1} --threads ${WEB_THREADS:-4} --timeout ${WEB_TIMEOUT:-120} --access-logfile - --error-logfile - --logger-class clinica.gunicorn_logger.LoggerSemTokens"]
