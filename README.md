# Jaqueline Aranha Estética

Sistema de agendamento online e gestão para clínica de estética da biomédica Jaqueline Aranha. Django 5.2 + PostgreSQL + Redis + Celery, com painel administrativo, PWA, web push e integrações externas (WhatsApp Business, Google Calendar, Zenvia SMS, Sentry, Cloudflare Turnstile).

> **Nota técnica:** módulos Python: `aranha_estetica` (app Django) e `clinica` (project Django). Marca de produto: **Jaqueline Aranha Estética**, configurável via env var `CLINIC_NAME`. Plataforma single-tenant white-label — o mesmo código atende outras clínicas via configuração.

---

## Visão Geral

Plataforma single-tenant white-label para clínica de estética. Pacientes agendam pelo site público (sem cadastro tradicional, OTP via SMS); o painel administrativo cobre toda a operação — agenda, prontuário, anamnese dinâmica, pacotes, promoções, NPS, workflow engine, LGPD.

### Funcionalidades

**Site público / paciente**
- Agendamento online em 3 etapas (procedimento → data/horário → confirmação)
- Filtro por categoria (Facial, Corporal, Capilar, Outro)
- Verificação por OTP via SMS Zenvia + Cloudflare Turnstile (anti-bot)
- "Meus Agendamentos" via OTP — sem login tradicional
- Reagendamento self-service por link mágico (24h de antecedência mínima)
- Anamnese pré-agendamento dinâmica (schema JSON, 8 tipos de campo)
- Pesquisa pós-atendimento online via WhatsApp (formulário token-based)
- Lista de espera + página de promoções
- PWA instalável (manifest + service worker com offline fallback)
- Embed widget `<iframe>` para Linktree/Instagram bio (`/embed/agendar/`)

**Painel administrativo**
- Dashboard com 8 KPIs (agendamentos, ticket médio, ocupação, ativos 90d, NPS, etc.)
- Calendário visual (FullCalendar) com drag-drop reagendamento
- Gestão de agendamentos, pacientes, profissionais, procedimentos, pacotes, promoções, lista de espera
- Ficha do paciente com timeline visual + LTV + procedimentos preferidos
- Workflow engine configurável (regra → trigger → ação) — substitui tasks hardcoded
- Bloqueios de agenda + recorrência (RRULE iCal)
- Exceções por data (folga ou horário diferente)
- Buffer entre atendimentos + min-notice/max-advance por profissional
- Web Push notifications (VAPID) para profissional ao novo agendamento
- ICS feed assinado por profissional (sincroniza com Google Cal/Outlook)
- Integração Google Calendar OAuth (push outbound + pull eventos externos)
- Auditoria detalhada (LogAuditoria) — atende Art. 37 LGPD
- 2FA TOTP (django-two-factor-auth)
- LGPD: consentimentos granulares por canal, unsubscribe one-click, soft delete, anonimização após 30d, exportação JSON
- REST API v1 (DRF read-only) + OpenAPI/Swagger/ReDoc

**Notificações multi-canal**
- WhatsApp Business API (confirmação, lembrete D-1, NPS pós-atendimento, pesquisa pós-online) — 3 templates aprovados Meta + 1 em submissão
- E-mail (OTP, confirmação, cancelamento, fila, pacotes, aniversário, promoções, alertas)
- SMS (OTP via Zenvia)
- Web push (VAPID/pywebpush) para staff
- Cron HTTP autenticado (`X-Cron-Token`) substitui Celery Beat em free tier

**Regras de negócio destacadas**
- 3-strike: 3 faltas consecutivas bloqueiam agendamento online
- FSM com 7 estados em `Atendimento` (PENDENTE → AGENDADO → CONFIRMADO → REALIZADO/CANCELADO/FALTOU/REAGENDADO) e 14 transições válidas
- Workflow engine c/ deduplicação (UNIQUE regra+atendimento)
- Slot generator: 7 fontes (feriado → max-advance → exceção → semanal → min-notice → buffer → bloqueios)
- Reset automático de faltas ao marcar REALIZADO
- Notificação de fila de espera ao liberar vaga
- 3 modalidades de procedimento: presencial, online, híbrido

---

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | Django 5.2, Python 3.12+ |
| Banco | PostgreSQL 14+ (47 tabelas, 22 migrations) |
| Cache / Tasks | Redis + Celery 5.4 |
| Servidor | Gunicorn + WhiteNoise |
| Frontend | Bootstrap 5.3, Vanilla JS, FullCalendar 6, AOS, Swiper |
| Auth | django-axes, django-two-factor-auth, OTP TOTP |
| Push | pywebpush (VAPID) |
| API | Django REST Framework + drf-spectacular (OpenAPI) |
| Notificações | WhatsApp Business API, Zenvia (SMS), SMTP |
| Captcha | Cloudflare Turnstile |
| Hospedagem | Railway (Nixpacks build) |
| Monitoramento | Sentry |
| CI/CD | GitHub → Railway via webhook (push-to-deploy 2-3min) |

---

## CI/CD — Push-to-Deploy

Pipeline contínuo com tempo médio **2 a 3 minutos** do commit ao serviço atualizado em produção.

```
git push origin <branch>
       ↓
GitHub webhook
       ↓
Railway detecta + dispara build
       ↓
Nixpacks: detecta Python + requirements.txt → gera imagem OCI
       ↓
Release phase: collectstatic + migrate
       ↓
Substituição atômica: gunicorn + worker Celery + beat reiniciam
       ↓
Sentry captura exceptions + métricas
       ↓
Healthchecks /health/ (readiness) + /healthz/ (liveness)
```

**Branches:**
- `main` → ambiente production (`web-production-465af.up.railway.app`)
- `dev` → ambiente development (`web-dev-1a30.up.railway.app`)

Ambos os ambientes têm Postgres + Redis dedicados isolados.

**Tests:**
- `python manage.py check` — pré-commit local
- Management commands de smoke test (`seed_jaqueline`, `seed_pesquisa_online_v1`)
- Django TestCase em models e services principais

**Secrets management:**
- Zero secrets no Git
- Todas as credenciais via env vars Railway
- `.env.example` documenta o conjunto sem expor valores

---

## Estrutura

```
jaqueline-aranha-estetica/
├── aranha_estetica/             # App Django principal
│   ├── models/                  # 14 arquivos por agregado de domínio
│   │   ├── acesso.py            # Usuario, Perfil, RBAC
│   │   ├── clientes.py          # Cliente + soft-delete LGPD
│   │   ├── profissionais.py     # Profissional, disponibilidade, exceções, bloqueios
│   │   ├── procedimentos.py     # Procedimento, Preço, Promoção (com modalidade)
│   │   ├── agendamentos.py      # Atendimento (FSM), Notificacao
│   │   ├── prontuario.py        # Prontuário + AnotacaoSessao
│   │   ├── anamnese.py          # FormularioAnamnese (schema JSON dinâmico)
│   │   ├── pacotes.py           # Pacote + sessões consumíveis
│   │   ├── workflow.py          # WorkflowRegra + WorkflowExecucao
│   │   ├── push.py              # WebPushSubscription
│   │   ├── termos.py            # VersaoTermo + AceitePrivacidade
│   │   ├── nps.py               # AvaliacaoNPS
│   │   ├── sistema.py           # Config, OtpCode, LogAuditoria, Feriado
│   │   └── extras.py            # PatchTest, FotoAntesDepois, Produto, Tag, Plano, Crédito
│   ├── views/                   # 30+ módulos por domínio
│   ├── services/                # Lógica de negócio (workflow_engine, otp, notificacao, gcal)
│   ├── api/                     # REST API v1 (DRF + OpenAPI)
│   ├── templates/
│   │   ├── publico/             # Home, sobre, serviços, equipe, depoimentos, galeria
│   │   ├── servicos/            # Faciais, corporais, produtos
│   │   ├── agenda/              # Booking público, embed, meus_agendamentos, pesquisa
│   │   ├── painel/              # Admin: overview, calendar, agendamentos, anamneses, workflows...
│   │   ├── profissional/        # Agenda + anotações
│   │   ├── email/               # Templates HTML de e-mail
│   │   └── pwa/                 # manifest.json + sw.js (público + admin)
│   ├── static/                  # CSS, JS, fotos, ícones PWA
│   ├── tasks.py                 # Jobs Celery
│   ├── signals.py               # Reativo (faltas, pacotes, fila, workflow)
│   ├── decorators.py            # staff_required, etc.
│   ├── middleware.py            # CSP nonce, axes, etc.
│   ├── urls.py                  # Rotas (namespace: aranha)
│   ├── management/commands/     # Seed + jobs cron
│   └── migrations/              # 22 migrations versionadas
├── clinica/                     # Projeto Django (settings, celery, urls, wsgi)
├── docs/                        # Documentação técnica (API, MER, DPIA, etc.)
├── scripts/                     # Utilitários
├── requirements.txt
├── Procfile
├── railway.json
└── manage.py
```

---

## ENV vars (produção)

```bash
# Brand white-label
CLINIC_NAME="Jaqueline Aranha Estética"
CLINIC_SUBTITLE="Estética facial e corporal · Atendimento exclusivo"
CLINIC_EMAIL="contato@jaquelineearanha.com.br"
CLINIC_PHONE="(11) XXXX-XXXX"
CLINIC_ADDRESS="..."
WHATSAPP_NUMERO="55119XXXXXXXX"
INSTAGRAM_URL="https://www.instagram.com/<perfil>/"
DEFAULT_FROM_EMAIL="noreply@jaquelineearanha.com.br"

# Segurança
SECRET_KEY=
DEBUG=False
ALLOWED_HOSTS=
PASSWORD_RESET_TIMEOUT_SECONDS=3600
CRON_TOKEN=

# Banco / Cache
DATABASE_URL=
REDIS_URL=

# Web Push (VAPID)
WEBPUSH_VAPID_PUBLIC_KEY=
WEBPUSH_VAPID_PRIVATE_KEY=
WEBPUSH_VAPID_CLAIMS_EMAIL=

# WhatsApp Business API
WHATSAPP_TOKEN=
WHATSAPP_PHONE_ID=
WHATSAPP_TEMPLATE_D1=confirmacao_d1
WHATSAPP_TEMPLATE_NPS=nps_pos_atendimento
WHATSAPP_TEMPLATE_PESQUISA=pesquisa_online

# Zenvia SMS (OTP)
ZENVIA_API_KEY=

# Email SMTP
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend

# Google Calendar OAuth (opcional)
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
GOOGLE_OAUTH_REDIRECT_URI=

# Cloudflare Turnstile
TURNSTILE_SITE_KEY=
TURNSTILE_SECRET_KEY=

# Sentry
SENTRY_DSN=
```

Lista completa em [docs/SETUP_PROD.md](docs/SETUP_PROD.md).

---

## Setup local

Ver [docs/SETUP.md](docs/SETUP.md). Resumo:

```bash
python -m venv .venv
source .venv/bin/activate  # ou .venv\Scripts\activate no Windows
pip install -r requirements.txt
cp .env.example .env  # ajustar valores
python manage.py migrate
python manage.py seed_jaqueline
python manage.py createsuperuser
python manage.py runserver
```

---

## Documentação

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — arquitetura
- [docs/SETUP.md](docs/SETUP.md) — setup local
- [docs/SETUP_PROD.md](docs/SETUP_PROD.md) — variáveis de produção + serviços externos
- [docs/API.md](docs/API.md) — endpoints
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) — contribuição
- [docs/DB_AUDIT.md](docs/DB_AUDIT.md) — auditoria comparativa do banco
- [docs/DPIA.md](docs/DPIA.md) — DPIA / LGPD
- [docs/erd.md](docs/erd.md) — modelo entidade-relacionamento
- [docs/ROADMAP_MELHORIAS.md](docs/ROADMAP_MELHORIAS.md) — sprints (regras, PWA, ICS, workflow, anamnese, extras, pesquisa pós-online)
- [docs/whatsapp_templates.md](docs/whatsapp_templates.md) — templates WhatsApp Meta

---

## Segurança

- Autenticação customizada (`AbstractBaseUser`, e-mail como identificador)
- Senhas: PBKDF2 (Django default)
- 2FA TOTP obrigatório para staff (django-two-factor-auth)
- Reset de senha: token 1h + rate limit 3/15min por IP + audit log
- CSRF em todos os formulários
- ORM (anti-SQLi) + template escaping (anti-XSS)
- CSP com nonce por request
- django-axes (lockout brute-force após 5 tentativas/h)
- Cloudflare Turnstile no booking público
- Rate limit por IP em endpoints sensíveis (Redis)
- HSTS 1 ano + SECURE_SSL_REDIRECT em produção
- Set-Cookie: Secure + HttpOnly + SameSite=Lax
- X-Frame-Options DENY (anti-clickjacking)
- LGPD: consentimentos granulares por canal, unsubscribe one-click, soft delete, anonimização após 30d, endpoint de exportação JSON

---

## Roadmap próximo (TCC 8º semestre)

1. Aplicativo móvel nativo dedicado (Flutter ou React Native) — câmera integrada para fotos antes/depois, sync offline
2. Evolução PWA → TWA (Trusted Web Activity) para publicação na Play Store
3. Multi-tenant SaaS — Row-Level Security PostgreSQL + onboarding self-service para outras clínicas

---

## Licença

[MIT](LICENSE).

---

Desenvolvido por Rafael Maciel.
