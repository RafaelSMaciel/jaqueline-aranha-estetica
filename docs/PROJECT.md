# Jaqueline Aranha Estética — Documentação Consolidada

> **Single source of truth** — setup, arquitetura, segurança, LGPD, integrações.
> Para overview rápido leia `README.md`. Este arquivo é referência completa.

---

## 📋 Sumário

- [1. Stack & Estrutura](#1-stack--estrutura)
- [2. Setup Local](#2-setup-local)
- [3. Setup Produção (Railway)](#3-setup-produção-railway)
- [4. Arquitetura](#4-arquitetura)
- [5. Segurança](#5-segurança)
- [6. LGPD / DPIA](#6-lgpd--dpia)
- [7. WhatsApp Business Templates](#7-whatsapp-business-templates)
- [8. Endpoints & API](#8-endpoints--api)
- [9. Banco de Dados](#9-banco-de-dados)
- [10. Contribuindo](#10-contribuindo)
- [11. Audit & Refactor (2026-05-13)](#11-audit--refactor-2026-05-13)

---

## 1. Stack & Estrutura

| Camada | Tecnologia |
|---|---|
| Linguagem | Python 3.12+ |
| Framework | Django 5.2 (MVT) |
| Banco | PostgreSQL 14+ (47 tabelas, 24 migrations) |
| Cache / Broker | Redis 7 |
| Async | Celery 5.4 + Beat |
| Frontend | Bootstrap 5.3 + FullCalendar 6 + AOS + Swiper |
| API | DRF + drf-spectacular (OpenAPI) |
| Auth | django-axes + django-two-factor-auth (TOTP) |
| Push | pywebpush (VAPID) |
| Captcha | Cloudflare Turnstile |
| Notificações | WhatsApp Business API + Zenvia (SMS OTP) + SMTP Gmail |
| Hosting | Railway (Nixpacks) |
| Monitoring | Sentry |

```
jaqueline-aranha-estetica/
├── aranha_estetica/              # App Django principal
│   ├── api/                      # DRF v1 + OpenAPI
│   ├── domain/                   # Events + EventBus + handlers (NOVO arquitetura v2)
│   ├── models/                   # 14 arquivos por agregado
│   ├── views/                    # 30+ módulos por domínio
│   ├── services/                 # Lógica de negócio
│   ├── templates/                # Bootstrap 5 + PWA
│   ├── static/                   # CSS, JS, ícones PWA
│   ├── utils/                    # cache, structured_logging, captcha, etc.
│   ├── exceptions.py             # Hierarquia DomainError/IntegrationError
│   └── migrations/               # 24 migrations versionadas
├── clinica/                      # Settings Django + celery + urls
├── docs/
│   └── PROJECT.md                # ← este arquivo (single source of truth)
├── Dockerfile                    # Multi-stage build
├── Procfile                      # Railway processes (web/worker/beat/release)
├── railway.json                  # Railway config
├── requirements.txt
└── manage.py
```

---

## 2. Setup Local

### Requisitos
- Python 3.12+
- PostgreSQL 14+ (prod) ou SQLite (dev fallback)
- Redis 7 (cache + Celery broker; opcional em dev local)

### Passos

```bash
git clone https://github.com/RafaelSMaciel/jaqueline-aranha-estetica
cd jaqueline-aranha-estetica
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # editar SECRET_KEY + outros
python manage.py migrate
python manage.py seed_jaqueline    # seed dados iniciais
python manage.py createsuperuser
python manage.py runserver
```

Servidor em `http://127.0.0.1:8000`.

### Comandos úteis dev

```bash
python manage.py check             # validação Django
python manage.py test              # roda 135 tests (~5s)
python -m ruff check aranha_estetica clinica
python manage.py makemigrations
python manage.py shell             # REPL com models carregados
celery -A clinica worker -l info   # worker async (precisa Redis)
celery -A clinica beat -l info     # scheduler periódico
```

---

## 3. Setup Produção (Railway)

### ✅ Já configurado

| Item | Status |
|---|---|
| Railway projeto `jaqueline-aranha-estetica` | ✅ |
| GitHub source `RafaelSMaciel/jaqueline-aranha-estetica` (main→prod, dev→dev) | ✅ |
| 2 ambientes isolados (dev + prod) | ✅ |
| Postgres + Redis dedicados por ambiente | ✅ |
| VAPID Web Push keys geradas + setadas | ✅ |
| `CLINIC_*` brand vars setadas | ✅ |
| 2FA TOTP habilitado | ✅ |
| Audit log ativo | ✅ |

### 🔴 Pendentes — criar contas externas

#### 1. Sentry (monitoramento)
1. https://sentry.io/signup → New Project → Django
2. Copiar DSN
3. `railway variables --service web --environment production --set "SENTRY_DSN=<dsn>"`

#### 2. Cloudflare Turnstile (captcha)
1. https://dash.cloudflare.com → Turnstile → Add site
2. Domain: `web-production-465af.up.railway.app` (e dev)
3. Setar `TURNSTILE_SITE_KEY` + `TURNSTILE_SECRET_KEY`

#### 3. WhatsApp Business API (Meta)
1. https://business.facebook.com → WhatsApp Business
2. Criar app + número
3. Submeter 4 templates (24-72h aprovação) — ver seção 7
4. Setar `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_NUMERO`

#### 4. Email SMTP (Gmail)
1. https://myaccount.google.com/apppasswords → criar app password
2. Setar `EMAIL_HOST_USER` + `EMAIL_HOST_PASSWORD`
3. `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`

#### 5. Zenvia SMS (OTP)
1. https://zenvia.com → criar conta + comprar créditos
2. Setar `ZENVIA_API_KEY`

#### 6. Google Calendar OAuth (sync agenda)
1. https://console.cloud.google.com → New Project → Calendar API
2. OAuth Client ID (Web) com redirect URIs:
   - `https://web-production-465af.up.railway.app/painel/integrations/google/callback/`
   - `https://web-dev-1a30.up.railway.app/painel/integrations/google/callback/`
3. Setar `GOOGLE_OAUTH_CLIENT_ID` + `GOOGLE_OAUTH_CLIENT_SECRET` + `GOOGLE_OAUTH_REDIRECT_URI`

#### 7. Backups DB (CRÍTICO)
- **Opção A:** Upgrade Railway Pro (US$ 5/mês) → Settings → Backup → daily snapshot
- **Opção B:** cron-job.org POST endpoint `/cron/run/backup/?token=$CRON_TOKEN` (precisa criar handler)

### Comandos Railway úteis

```bash
railway variables --service web --environment production --kv      # listar
railway logs --service web --environment production                 # logs
railway run --service web --environment production python manage.py shell
railway redeploy --service web                                      # redeploy manual
```

### Pipeline CI/CD (push-to-deploy 2-3min)

```
git push origin <branch>
       ↓
GitHub webhook
       ↓
Railway detecta + dispara build
       ↓
Nixpacks: Python + requirements.txt → imagem OCI
       ↓
Release phase: collectstatic + migrate
       ↓
Restart atômico: gunicorn + worker + beat
       ↓
Sentry captura + healthchecks /health/ /healthz/
```

### URLs

- Prod: https://web-production-465af.up.railway.app/
- Dev: https://web-dev-1a30.up.railway.app/
- Login admin: `/admin-login/`
- Painel: `/painel/`
- API Swagger: `/api/schema/swagger/`

---

## 4. Arquitetura

### Camadas

```
┌──────────────────────────────────────────┐
│ Templates (Django + Bootstrap)           │  Apresentação
├──────────────────────────────────────────┤
│ Views (públicas + admin + API DRF)       │  HTTP boundary
├──────────────────────────────────────────┤
│ Forms (ModelForm + validators)           │  Input validation
├──────────────────────────────────────────┤
│ Services (regras de negócio + DTOs)      │  Domain logic
├──────────────────────────────────────────┤
│ Domain Events (EventBus)                 │  Handlers desacoplados
├──────────────────────────────────────────┤
│ Models (Django ORM + Manager methods)    │  Persistência
├──────────────────────────────────────────┤
│ DB PostgreSQL + Redis cache              │
└──────────────────────────────────────────┘
```

**Regra:** views chamam services, services chamam models. Forms validam input antes do service. EventBus dispara handlers desacoplados após `transaction.on_commit`.

### Domínios principais

| Agregado | Arquivo | Responsabilidade |
|---|---|---|
| Acesso | `models/acesso.py` | Usuario, Perfil, Funcionalidade, RBAC |
| Clientes | `models/clientes.py` | Cliente + soft-delete + LGPD opt-in/out |
| Profissionais | `models/profissionais.py` | Profissional + disponibilidade + exceções + bloqueios RRULE |
| Procedimentos | `models/procedimentos.py` | Procedimento + Preço + Promocao + modalidade (presencial/online/híbrido) |
| Agendamentos | `models/agendamentos.py` | Atendimento (FSM 7 estados) + Notificacao |
| Prontuário | `models/prontuario.py` | Prontuário + AnotacaoSessao |
| Anamnese | `models/anamnese.py` | FormularioAnamnese (schema JSON) + RespostaAnamnese |
| Pacotes | `models/pacotes.py` | Pacote + ItemPacote + PacoteCliente + SessaoPacote |
| Workflow | `models/workflow.py` | WorkflowRegra + WorkflowExecucao (UNIQUE dedup) |
| Push | `models/push.py` | WebPushSubscription |
| Termos | `models/termos.py` | VersaoTermo + AceitePrivacidade + AssinaturaTermoProcedimento |
| NPS | `models/nps.py` | AvaliacaoNPS |
| Sistema | `models/sistema.py` | LogAuditoria + ConfiguracaoSistema + OtpCode + Feriado |
| Extras | `models/extras.py` | PatchTest + FotoAntesDepois + Produto + MovimentoEstoque + Tag + Plano + Crédito |

### Services principais

- `AgendamentoService` — criar/cancelar/reagendar atomic + DTO + EventBus
- `WorkflowEngine` — avaliar regras periodicamente + dedup UNIQUE
- `OtpService` — gerar/validar códigos OTP via SMS Zenvia
- `NotificacaoService` — envio multi-canal (WhatsApp, email, push, SMS)
- `LgpdService` — DSAR (export JSON) + unsubscribe + anonimização
- `AuditoriaService` — log com IP + sanitização PII automática
- `GcalSync` — Google Calendar OAuth bidirecional

### Domain Events (arquitetura v2)

```python
from aranha_estetica.domain.event_bus import EventBus
from aranha_estetica.domain.events import AtendimentoRealizado

@EventBus.subscribe(AtendimentoRealizado)
def disparar_workflow_nps(event: AtendimentoRealizado):
    ...
```

Events disponíveis: AtendimentoCriado, AtendimentoConfirmado, AtendimentoRealizado, AtendimentoCancelado, AtendimentoFaltou, AtendimentoReagendado, ClienteCadastrado, ConsentRegistrado, PacoteVendido, SessaoConsumida.

### Exception hierarchy

```
DomainError (base)
├── BusinessRuleViolation (HTTP 409)
│   ├── SlotIndisponivelError
│   ├── ClienteBloqueadoError
│   └── TransicaoStatusInvalida
├── ResourceNotFound (404)
├── AuthorizationError (403)
└── ValidationError (400)
    ├── OtpInvalidoError
    └── OtpExpiradoError

IntegrationError (base — serviços externos)
├── TransientError (503 — retry-able)
└── PermanentError (502 — falha definitiva)
```

---

## 5. Segurança

### Autenticação & Autorização
- Custom `Usuario` (`AbstractBaseUser`) com email como identificador
- 2FA TOTP **obrigatório** para staff (`django-two-factor-auth`)
- `django-axes` lockout: 5 tentativas falhas/hora por IP+username
- RBAC granular via `Perfil` + `Funcionalidade` (matriz)
- `staff_required` + `profissional_required` decorators
- Profissional view com helper `_atendimento_do_profissional()` aplicando filter ownership direto na query (anti-BOLA)

### Headers HTTP
- `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`
- `Content-Security-Policy` com nonce per-request (`ContentSecurityPolicyMiddleware`)
- `X-Frame-Options: DENY` (anti-clickjacking)
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Cross-Origin-Opener-Policy: same-origin`

### Cookies
- `SESSION_COOKIE_HTTPONLY = True`
- `CSRF_COOKIE_HTTPONLY = True`
- `SameSite = Lax`
- `Secure` em produção
- `SESSION_COOKIE_AGE = 8h` (configurável via env)
- `SESSION_EXPIRE_AT_BROWSER_CLOSE = True`

### Anti-bot / Rate Limit
- Cloudflare Turnstile em formulários públicos
- `django-ratelimit` por IP em endpoints sensíveis (OTP, login, AJAX)
- DRF throttling: 60/h anônimo + 1000/h user

### Tokens
- `secrets.token_urlsafe(32)` p/ cancelamento, unsubscribe, OTP, DSAR
- `hmac.compare_digest` p/ comparação constant-time

### Inputs
- `FILE_UPLOAD_MAX_MEMORY_SIZE = 5 MB`
- `DATA_UPLOAD_MAX_NUMBER_FIELDS = 200`
- `bleach` whitelist em HTML user-controlled (email promocao)
- `json_script` (Django) em todos os JSON injetados em templates

### Secrets
- Zero hardcoded — tudo via env vars Railway
- `SECRET_KEY` raise RuntimeError se ausente em prod
- `.gitignore` cobre `.env`, `.claude/`, `*.pem`, `*.key`
- `.dockerignore` exclui secrets, tests, docs, venv

---

## 6. LGPD / DPIA

### Bases legais (LGPD art. 7 e 11)

| Tratamento | Base legal | Justificativa |
|---|---|---|
| Agendamento | Execução de contrato (art. 7-V) | Serviço solicitado pelo cliente |
| Prontuário | Tutela da saúde (art. 11-II-f) | Profissional habilitado |
| Lembrete D-1 | Legítimo interesse (art. 7-IX) | Reduz no-show |
| Marketing | Consentimento expresso (art. 7-I) | Opt-in granular |
| Auditoria | Cumprimento obrigação legal (art. 7-II) | Rastreabilidade fiscal/sanitária |

### Direitos dos titulares (art. 18) — implementação

| Direito | Endpoint / método |
|---|---|
| Confirmação + acesso | `/lgpd/meus-dados/?format=json` (DSAR via OTP) |
| Correção | Atualização pelo admin no `/painel/clientes/<id>/` |
| Anonimização | `LgpdService.esquecer_cliente()` + job semanal automático após 30d soft-delete |
| Portabilidade | Export JSON estruturado em DSAR |
| Revogação consent | `/lgpd/unsubscribe/<token>/` + cookie banner granular |
| Eliminação | Soft-delete + purge job |

### Retenção

| Dado | Prazo | Após prazo |
|---|---|---|
| Cliente ativo | Indeterminado | — |
| Cliente inativo (sem agendamento) | 5 anos | Anonimização automática |
| Atendimento (saúde) | 20 anos | CFM Resolução 1.821/2007 |
| LogAuditoria | 5 anos | Purga manual |
| Sessão web | 8h | Auto-expira |

### Compartilhamento com terceiros

- **WhatsApp Business API (Meta)** — telefone + mensagem template
- **SMTP Gmail** — email + conteúdo transacional
- **Zenvia SMS** — telefone + código OTP (validade 10min)
- **Sentry** — stack traces sem PII (`send_default_pii=False`)
- **Railway** — DB + arquivos hospedados
- **Sem compartilhamento comercial** com terceiros

### Procedimento incidente (art. 48)

1. Conter incidente (revogar tokens, isolar sistema afetado)
2. Avaliar impacto via `LogAuditoria`
3. Comunicar ANPD em até 72h (se risco material)
4. Comunicar titulares afetados via email registrado

---

## 7. WhatsApp Business Templates

Submeter no Meta Business Manager. Aprovação 24-72h.

### Template 1 — `confirmacao_d1` (lembrete 24h antes)

- Categoria: **UTILITY**
- Idioma: `pt_BR`
- Env var: `WHATSAPP_TEMPLATE_D1`

```
Oi {{1}}! Passando para lembrar do seu agendamento amanhã ({{2}} às {{3}}) no {clínica}.

- Procedimento: {{4}}
- Profissional: {{5}}

Confirmar presença: {{6}}
Preciso remarcar: {{7}}

Qualquer dúvida, responda esta mensagem.
```

Parâmetros: nome, data, hora, procedimento, profissional, link confirmar, link remarcar.

### Template 2 — `nps_pos_atendimento`

- Categoria: **UTILITY**
- Env var: `WHATSAPP_TEMPLATE_NPS`

```
Olá {{1}}, como foi seu atendimento? Avalie de 0 a 10: {{2}}
```

### Template 3 — `confirmacao` (agendamento criado)

- Categoria: **UTILITY**

```
Confirmação: {{1}} em {{2}} com {{3}}.
```

### Template 4 — `pesquisa_online` (Sprint 7, em submissão)

- Categoria: **UTILITY**
- Env var: `WHATSAPP_TEMPLATE_PESQUISA`

```
Olá {{1}}, agradecemos sua consulta online de {{2}}.
Pode nos ajudar com 2 minutos respondendo essa pesquisa?

{{3}}
```

### Observações para aprovação Meta

- Sem emojis nas variáveis (evita rejeição)
- URLs com token de 32 chars no BODY (não em BUTTONS — exige domain allowlist)
- Categoria UTILITY (transacional), não MARKETING

---

## 8. Endpoints & API

### REST API v1 (DRF read-only, requer staff)

| Método | Endpoint | Função |
|---|---|---|
| GET | `/api/v1/profissionais/` | Listar profissionais ativos |
| GET | `/api/v1/profissionais/{id}/` | Detalhes |
| GET | `/api/v1/procedimentos/` | Listar catálogo |
| GET | `/api/v1/procedimentos/{slug}/` | Detalhes (lookup por slug) |
| GET | `/api/v1/clientes/?q=` | Buscar (PII mascarada) |
| GET | `/api/v1/clientes/{id}/` | Detalhes (PII mascarada) |
| GET | `/api/v1/atendimentos/?status=` | Filtrar por status |
| GET | `/api/v1/atendimentos/{id}/` | Detalhes |
| GET | `/api/v1/atendimentos/hoje/` | Custom: dia atual |

### Documentação interativa

- OpenAPI 3.0: `/api/schema/`
- Swagger UI: `/api/schema/swagger/`
- ReDoc: `/api/schema/redoc/`

### Endpoints públicos relevantes

| Endpoint | Função |
|---|---|
| `/agendamento/` | Booking público (3 passos) |
| `/agendar/<slug>/` | Direto p/ profissional |
| `/embed/agendar/` | Versão iframe |
| `/agendamento/otp/solicitar/` | Solicitar OTP via SMS |
| `/agendamento/otp/verificar/` | Validar + criar atendimento |
| `/confirmar/<token>/` | Link mágico confirmar |
| `/reagendar/<token>/` | Link mágico reagendar |
| `/anamnese/<token>/` | Form anamnese pre-atendimento |
| `/pesquisa/<token>/` | Pesquisa pós-atendimento online |
| `/nps/<token>/` | Página NPS |
| `/agenda/<slug>/feed.ics` | Feed iCal token-signed |
| `/lgpd/meus-dados/` | DSAR export JSON |
| `/lgpd/unsubscribe/<token>/` | Opt-out one-click |

### Webhooks recebidos

- `POST /api/whatsapp/webhook/` — eventos Meta (mensagens + delivery)
- `POST /api/zenvia/webhook/` — relatórios SMS
- `POST /cron/run/<job>/?token=$CRON_TOKEN` — cron externo token-protected

### Healthchecks

- `/health/` — readiness (verifica DB + Redis + Celery opcional)
- `/healthz/` — liveness (200 sempre, processo vivo)

---

## 9. Banco de Dados

### Resumo

- **47 tabelas** em produção
- **24 migrations** versionadas
- **14 arquivos models** organizados por agregado
- **CHECK constraints** em campos enumerados (status, categoria, modalidade)
- **UNIQUE constraints** em campos naturais (CPF, token, email parcial)
- **FK policies explícitas:** RESTRICT (auditoria), CASCADE (compostos), SET NULL (opcionais)
- **87+ índices** (compostos para dashboard queries)

### Padrões adotados

- `criado_em` / `atualizado_em` em todos os models via auto_now_add/auto_now
- `deletado_em` (DateTimeField nullable) p/ soft-delete em entidades com PII
- `{Model}Manager` com QuerySet methods reutilizáveis (`Atendimento.objects.ativos()`, `.do_profissional()`, `.conflito_com()`)
- `db_table = '{nome_em_snake_case}'` explícito em todos os Meta
- `db_index=True` em campos de busca frequente (email, telefone, token)

### MER detalhado

Diagramas em `OneDrive/Projeto FAM - Shivazen/Diagramas/`.

---

## 10. Contribuindo

### Workflow

1. Branch `dev` para integração contínua (deploy auto Railway dev)
2. Branch `main` apenas via PR aprovado a partir de `dev` (deploy auto Railway prod)
3. Commits seguem [Conventional Commits](https://www.conventionalcommits.org/):
   - `feat(scope): ...`
   - `fix(scope): ...`
   - `refactor(scope): ...`
   - `docs(scope): ...`
   - `chore(scope): ...`

### Pre-commit checks (rodar antes de push)

```bash
python -m ruff check aranha_estetica clinica
python manage.py check
python manage.py test --keepdb
```

### Guidelines

- Lógica de negócio em `services/`, não em views
- Models com `Manager`/`QuerySet` para queries reutilizáveis
- Domain events em vez de signals quando o handler é desacoplado
- `transaction.atomic` em qualquer operação que mexa em 2+ tables
- Usar `exceptions.py` para erros tipados (DomainError + IntegrationError)
- Logs estruturados com `utils.structured_logging.log_event()`
- Cache via `utils.cache.cached()` decorator (não `cache.get/set` ad-hoc)
- Sem secrets no código — env vars Railway
- Migrations versionadas; nunca editar após push

### Tests

- `tests/` na app — usa `TestCase` Django
- 135+ tests cobrem models + services + views críticas
- Roda em `db_test.sqlite3` (SQLite) — rápido (~5s)

### Roadmap TCC (8º semestre)

1. App mobile nativo (Flutter/React Native + câmera + offline sync)
2. PWA → TWA (publicação Play Store)
3. Multi-tenant SaaS (Row-Level Security PostgreSQL + onboarding self-service)

## 11. Audit & Refactor (2026-05-13)

Audit completo em 2026-05-13 (rating 7/10). Plano de execução em 3 lotes:

### Lote 1 — Quick wins (em execução)
1. `DJANGO_SECRET_KEY` exigida em prod/Railway (settings/base.py)
2. `except Exception` → specific (`OperationalError`, `ProgrammingError`, `ImportError`) em middleware.py
3. `except Exception` → `(DatabaseError, IntegrityError)` em models/agendamentos.py
4. Rate limit GET em `agendamento_publico` (60/h por IP, anti-enumeration)
5. `FormularioAnamnese.clean()` valida `schema_json` (tipos, opcoes, keys unicas)
6. Reorder LGPD: `_registrar_consents` ANTES de `Atendimento.objects.create()`
7. Pin exato em `python-dateutil` e `python-json-logger`
8. Test matrix `ComissaoService.resolver_regra` (4 niveis + inativa + outro prof/proc)
9. Cleanup: `tmp_req/`, `seed_jaqueline.py`, `seed_pesquisa_online_v1.py`

### Lote 2 — Refactor (concluido)
- Split `booking.py` → `booking_public.py` + `booking_otp.py` + `booking_reagendar.py` (3 arquivos, ~250 linhas cada)
- Remover sync fallback em `agendamento_service.py` (sempre-async via Celery)
- `utils/pii.py`: `mask_email`/`mask_telefone`/`mask_cpf` + Sentry `before_send` filtra PII de extras + scrub `request.data`
- PII mascarada em logs de booking (booking_public, booking_otp)
- Squash migrations 0001-0010: **adiado** (Railway prod ainda lê migrations individuais; coordenar com freeze antes)

### Lote 3 — CSP unsafe-inline (parcial - concluido)
**Removido `'unsafe-inline'` de:** `script-src` e `style-src` (todos `<script>`/`<style>` inline tem nonce).
**Mantido `'unsafe-inline'` em:** `script-src-attr` e `style-src-attr` (34 handlers `onclick=` + 645 `style=""` em 69 templates - refactor adiado para Lote 3.5).

Audit Lote 3:
- 53 `<script>` total: 23 nonced + 30 src= externos + 5 data scripts (nonce adicionado) = 0 inline executavel sem nonce
- 28 `<style>` blocks: 26 nonced + 2 em email templates (CSP nao aplica)
- Novo `aranha_estetica/tests/test_csp.py`: 4 regressao tests garantem que `'unsafe-inline'` nao volta a script-src/style-src

### Lote 3.5 — Inline handlers + styles (futuro)
- Migrar 34 handlers inline (`onclick=`, `onchange=`, etc) para `addEventListener` em script blocks nonced
- Migrar 645 `style=""` attrs para classes utilitarias / CSS modulares
- Remover `'unsafe-inline'` de `script-src-attr` e `style-src-attr`
- Templates afetados: 19 (handlers) + 69 (styles, inclui 13 email - skip)

---

**Mantenedor:** Rafael Maciel — `rafael-sebastiao@hotmail.com`
**Última atualização:** 2026-05-13
