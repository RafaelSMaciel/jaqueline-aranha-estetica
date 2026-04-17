# Arquitetura — shivazen-app

## Visão geral

Django 5.2 monolito modular para clínica de estética. White-label via env vars (`CLINIC_*`). Frontend com templates Django + Bootstrap 5 + AOS + Swiper. PWA com manifest + service worker.

## Camadas

```
┌──────────────────────────────────────┐
│ Templates (Django + Bootstrap)       │  Apresentação
├──────────────────────────────────────┤
│ Views (públicas, admin, API)         │  HTTP boundary
├──────────────────────────────────────┤
│ Forms (ModelForm + validators)       │  Validação de input
├──────────────────────────────────────┤
│ Services (regras de negócio)         │  Lógica de domínio
├──────────────────────────────────────┤
│ Models (Django ORM + mixins)         │  Persistência
├──────────────────────────────────────┤
│ DB (PostgreSQL prod / SQLite dev)    │
└──────────────────────────────────────┘
```

Regra: **views chamam services**, **services chamam models**. Forms validam input antes de chegar no service. Signals só para integrações secundárias (notificação, audit).

## Domínios principais

- **Clientes** (`models/clientes.py`) — soft delete, LGPD opt-in/opt-out, unsubscribe_token, índice por telefone
- **Profissionais** (`models/profissionais.py`) — disponibilidade semanal, especialidades
- **Procedimentos** (`models/procedimentos.py`) — duração, categoria, preços por profissional
- **Agendamentos / Atendimentos** (`models/agendamentos.py`) — fluxo agendado→realizado/cancelado/faltou, token cancelamento, constraints de horário
- **Pacotes** (`models/pacotes.py`) — saldo de sessões, débito automático via service
- **Notificações** (`models/notificacoes.py`) — registro de envios (e-mail, WhatsApp)
- **Sistema** (`models/sistema.py`) — LogAuditoria com IP + masking PII

## Serviços

- `AgendamentoService` — criar/cancelar/reagendar com `@transaction.atomic` e check de colisão
- `PacoteService` — débito de sessão por atendimento (extraído de signal)
- `NotificacaoService` — registrar/marcar enviada/falhou
- `LgpdService` — DSAR (export JSON), unsubscribe por token, anonimização (esquecer)
- `AuditoriaService` — log com IP + sanitização automática de PII

## Segurança

- **CSP per-request nonce** (`ContentSecurityPolicyMiddleware`) — script-src/style-src usam `nonce-{{ csp_nonce }}`
- **SecurityHeadersMiddleware** — Permissions-Policy, COOP, CORP, Referrer-Policy
- **HSTS 1 ano** em prod, SSL redirect, cookies Secure
- **Rate limit** (django-ratelimit) em endpoints sensíveis
- **PII masking** automático em `LogAuditoria` para chaves sensíveis
- **safe_str_compare** (`hmac.compare_digest`) para tokens/OTP

## LGPD

- DSAR endpoint `/lgpd/meus-dados/` com OTP
- Opt-out por token único `/lgpd/unsubscribe/<token>/`
- Cookie consent banner com persistência server-side
- Anonimização (`LgpdService.esquecer_cliente`) preserva dados estatísticos

## Performance

- `list_select_related` em todos os admins com FK
- `select_related`/`prefetch_related` nas views de listagem
- Índices: `(profissional, data_hora_inicio)` em Atendimento; `(usuario, -criado_em)` em LogAuditoria; `unsubscribe_token` em Cliente
- Cache Redis em prod, LocMem em dev

## Async (Celery)

- Worker: notificações de e-mail/WhatsApp, expiração de pacotes
- Beat: jobs periódicos (lembretes, expiração)
- Broker: Redis

## Deploy

- **Railway** (Nixpacks) — `railway.json` + `Procfile` (web/worker/beat/release)
- **Docker** — `Dockerfile` (python:3.12-slim, user não-root) + `docker-compose.yml`
- **Healthcheck** `/health/` valida DB
