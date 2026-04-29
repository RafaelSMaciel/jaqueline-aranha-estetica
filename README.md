# Jaqueline Aranha Estética

Sistema de agendamento online e gestão para clínica de estética da biomédica Jaqueline Aranha. Django 5.2 + PostgreSQL + Redis + Celery, com painel administrativo, PWA, web push e integrações externas (Google Calendar, WhatsApp Business, MercadoPago opcional).

> **Nota técnica:** módulos Python renomeados em 2026-04-29 (`app_shivazen` → `aranha_estetica`, `shivazen` → `clinica`). O `app_label` interno permanece `app_shivazen` para preservar `django_migrations` e `content_type` rows sem mexer no DB. O nome do repositório local pode ainda aparecer como `shivazen-app/` — pode ser renomeado livremente. Marca de produto: **Jaqueline Aranha Estética**, configurável via env var `CLINIC_NAME`.

## Visão Geral

Plataforma single-tenant white-label para clínica estética, atendimento exclusivo de uma biomédica. Pacientes agendam pelo site público; o painel administrativo cobre gestão completa da operação (agenda, prontuário, financeiro, notificações).

### Funcionalidades

**Site público / paciente**
- Agendamento online em 3 etapas (procedimento → data/horário → confirmação)
- Filtro por categoria (Facial, Corporal, Capilar, Outro)
- Verificação por OTP (SMS / e-mail) + Cloudflare Turnstile
- "Meus Agendamentos" via OTP — sem login tradicional
- Reagendamento self-service por link mágico (24h de antecedência mínima)
- Anamnese pré-agendamento (formulários dinâmicos por procedimento/categoria)
- Lista de espera + página de promoções
- PWA instalável (manifest + service worker com offline fallback)
- Embed widget `<iframe>` para Linktree/Instagram bio (`/embed/agendar/`)

**Painel administrativo**
- Dashboard com 8 KPIs (agendamentos, ticket médio, ocupação, ativos 90d, NPS, etc.)
- Calendário visual (FullCalendar) com drag-drop reagendamento
- Gestão de agendamentos, pacientes, profissionais, procedimentos, pacotes, promoções, lista de espera
- Ficha do paciente com timeline visual + LTV + procedimentos preferidos
- Workflows configuráveis (regra → trigger → ação) substituem tasks hardcoded
- Bloqueios de agenda + recorrência (RRULE iCal)
- Exceções por data (folga ou horário diferente)
- Buffer entre atendimentos + min-notice/max-advance por profissional
- Web Push notifications (VAPID) para profissional ao novo agendamento
- ICS feed assinado por profissional (sincroniza com Google Cal/Outlook)
- Integração Google Calendar OAuth (push outbound + pull eventos externos)
- Auditoria detalhada (LogAuditoria)
- 2FA TOTP (django-two-factor-auth)
- LGPD: consentimentos granulares por canal, unsubscribe one-click, soft delete, anonimização

**Notificações multi-canal**
- WhatsApp Business API (lembrete D-1, NPS pós-atendimento) — 2 templates
- E-mail (OTP, confirmação, cancelamento, fila, pacotes, aniversário, promoções, alertas)
- SMS (OTP primário com Zenvia)
- Web push (VAPID/pywebpush) para staff
- Cron HTTP autenticado (`X-Cron-Token`) substitui Celery Beat em free tier

**Regras de negócio destacadas**
- 3-strike: 3 faltas consecutivas bloqueiam agendamento online
- FSM transitions em `Atendimento` (PENDENTE → AGENDADO → CONFIRMADO → REALIZADO/CANCELADO/FALTOU)
- Workflow engine c/ deduplicação (UNIQUE regra+atendimento)
- Slot generator: feriado → max-advance → exceção → semanal → min-notice → buffer → bloqueios
- Reset automático de faltas ao marcar REALIZADO
- Notificação de fila de espera ao liberar vaga

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | Django 5.2, Python 3.12+ |
| DB | PostgreSQL 14+ |
| Cache / Tasks | Redis + Celery 5.4 |
| Servidor | Gunicorn + WhiteNoise |
| Frontend | Bootstrap 5.3, Vanilla JS, FullCalendar 6, AOS, Swiper |
| Auth | django-axes, django-two-factor-auth, OTP TOTP |
| Push | pywebpush (VAPID) |
| Notificações | WhatsApp Business API, Zenvia (SMS), SMTP |
| Captcha | Cloudflare Turnstile |
| Deploy | Railway (Nixpacks) |
| Monitoramento | Sentry |

## Estrutura

```
shivazen-app/                   # Pasta local do repo (nome legado, pode ser renomeada)
├── aranha_estetica/             # App Django principal (label interno: app_shivazen)
│   ├── models/                  # Domínio modular (clientes, agendamentos, workflow, push, anamnese...)
│   ├── views/                   # Views por domínio (booking, admin_*, profissional, webpush, etc.)
│   ├── services/                # Lógica de negócio (workflow_engine, push, gcal, otp, notificacao)
│   ├── templates/
│   │   ├── publico/             # Home, sobre, serviços, equipe, depoimentos, galeria
│   │   ├── servicos/            # Faciais, corporais, produtos
│   │   ├── agenda/              # Booking público, embed, meus_agendamentos
│   │   ├── painel/              # Admin: overview, calendar, agendamentos, clientes, workflows, anamneses...
│   │   ├── profissional/        # Agenda + anotações
│   │   ├── email/               # Templates HTML de e-mail
│   │   └── pwa/                 # manifest.json + sw.js
│   ├── static/
│   │   ├── assets/clinica/      # Fotos contextualizadas estética
│   │   ├── css/                 # base.css
│   │   └── js/                  # admin-search, webpush, main
│   ├── tasks.py                 # Jobs Celery (lembretes, NPS, workflow_pendentes)
│   ├── signals.py               # Reativo (faltas, pacotes, fila, workflow ON_BOOK/CANCEL)
│   ├── decorators.py            # staff_required, etc.
│   ├── middleware.py            # CSP nonce, axes, etc.
│   ├── urls.py                  # Rotas da app (namespace: aranha)
│   ├── management/commands/
│   │   └── seed_jaqueline.py    # Seed da clínica + 23 procedimentos oficiais
│   └── migrations/              # 21 migrações
├── clinica/                     # Projeto Django (settings, celery, urls, wsgi)
├── docs/                        # Documentação técnica
├── outputs/                     # Reports (review, custos, regras, prontidão)
├── scripts/                     # Utilitários (fix_accents, rename_photo_paths, update_docs_brand)
├── tmp_req/                     # Geração de DOCX/PDF de requisitos
├── requirements.txt
├── Procfile
├── railway.json
└── manage.py
```

## ENV vars (produção)

```bash
# Brand
CLINIC_NAME="Jaqueline Aranha Estética"
CLINIC_SUBTITLE="Estética facial e corporal · Atendimento exclusivo"
CLINIC_EMAIL="contato@jaquelineearanha.com.br"
CLINIC_PHONE="(17) 99999-0000"
CLINIC_ADDRESS="..."
WHATSAPP_NUMERO="55179XXXXXXXX"
DEFAULT_FROM_EMAIL="noreply@jaquelineearanha.com.br"

# Segurança
PASSWORD_RESET_TIMEOUT_SECONDS=3600
CRON_TOKEN="<gerado>"

# Web Push (VAPID)
WEBPUSH_VAPID_PUBLIC_KEY=
WEBPUSH_VAPID_PRIVATE_KEY=
WEBPUSH_VAPID_CLAIMS_EMAIL="mailto:rafelsebas@gmail.com"

# Google Calendar OAuth (opcional)
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
GOOGLE_OAUTH_REDIRECT_URI="https://<dominio>/painel/integrations/google/callback/"

# Cloudflare Turnstile
TURNSTILE_SITE_KEY=
TURNSTILE_SECRET_KEY=
```

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

## Documentação

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — arquitetura
- [docs/SETUP.md](docs/SETUP.md) — setup local
- [docs/API.md](docs/API.md) — endpoints
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) — contribuição
- [docs/DPIA.md](docs/DPIA.md) — DPIA / LGPD
- [docs/erd.md](docs/erd.md) — modelo entidade-relacionamento
- [docs/ROADMAP.md](docs/ROADMAP.md) — roadmap geral
- [docs/ROADMAP_MELHORIAS.md](docs/ROADMAP_MELHORIAS.md) — sprints 1-5 (regras, PWA, ICS, workflow, anamnese...)
- [docs/whatsapp_templates.md](docs/whatsapp_templates.md) — templates WhatsApp Meta
- [docs/EMAIL_ASYNC_VS_SYNC.md](docs/EMAIL_ASYNC_VS_SYNC.md) — decisão arquitetural e-mail
- [docs/FOTOS_AUDIT.md](docs/FOTOS_AUDIT.md) — audit de fotos
- [outputs/REGRA_NEGOCIO.md](outputs/REGRA_NEGOCIO.md) — regras de negócio
- [outputs/PRONTO_PARA_PROD.md](outputs/PRONTO_PARA_PROD.md) — checklist produção
- [outputs/CUSTOS.md](outputs/CUSTOS.md) — custos serviços externos
- [outputs/REVIEW.md](outputs/REVIEW.md) — review técnico

## Segurança

- Autenticação customizada (`AbstractBaseUser`, e-mail como identificador)
- Senhas: PBKDF2 (Django default)
- 2FA TOTP obrigatório para staff (django-two-factor-auth)
- Reset de senha: token 1h + rate limit 3/15min por IP + audit log
- CSRF em todos os formulários
- ORM (anti-SQLi) + template escaping (anti-XSS)
- CSP com nonce por request
- django-axes (lockout brute-force)
- Cloudflare Turnstile no booking público
- Rate limit por IP em endpoints sensíveis
- LGPD: consentimentos granulares por canal, unsubscribe one-click, soft delete, anonimização

## Licença

[MIT](LICENSE).

---

Desenvolvido por Rafael Maciel.
