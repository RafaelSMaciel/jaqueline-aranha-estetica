# Roadmap — shivazen-app

Itens que requerem setup externo, contas pagas, ou redesign arquitetural.
Ordenados por valor/esforco.

## Sprint 1 (1-2 semanas, baixa friccao)

### Backups DB
- **Acao:** Configurar Railway Postgres snapshot diario + dump semanal pra S3
- **Externo:** Railway dashboard + AWS S3 (ou R2)
- **Custo:** ~$5/mes S3
- **Risco:** ALTO se nao implementado (perda permanente)

### Sentry ativo
- **Acao:** Criar projeto Sentry, setar `SENTRY_DSN` no Railway
- **Externo:** sentry.io (free tier 5k events/mes)
- **Custo:** $0 (inicial)
- **Codigo:** ja wired em `settings/base.py:17-30`

### 2FA admin
- **Acao:** Instalar `django-otp` + `django-two-factor-auth`
- **Codigo:** Requer redesign fluxo login (template novo, settings, urls)
- **Esforco:** ~8h
- **Externo:** —

### Cloudflare CDN
- **Acao:** Apontar dominio via Cloudflare, ativar proxy
- **Beneficio:** WAF, cache estaticos, DDoS protection, HTTPS auto
- **Custo:** $0 (free tier)
- **Externo:** cloudflare.com

### Staging Railway
- **Acao:** Branch `dev` deploy automatico em projeto Railway separado
- **Custo:** +$5/mes (segundo ambiente)
- **Externo:** Railway dashboard

## Sprint 2 (mes 1, media friccao)

### WhatsApp templates Meta
- **Acao:** Submeter templates Meta Business Manager (lembrete, NPS, confirmacao)
- **Externo:** business.facebook.com (aprovacao 24-72h)
- **Custo:** Por mensagem enviada
- **Bloqueador:** Sem templates aprovados, mensagens vao como sessao (24h limite)

### Encryption at rest DB
- **Acao:** Validar Railway Postgres com encryption (default em planos pagos)
- **Externo:** Railway docs/support
- **Custo:** Plano paid

### VPN/IP allowlist admin
- **Acao:** Cloudflare Access ou Tailscale na frente do `/admin/` e `/painel/`
- **Custo:** $0 (Cloudflare free 50 users)
- **Externo:** cloudflare.com Access

### Mutation testing
- **Acao:** `pip install mutmut` + rodar em CI weekly
- **Esforco:** ~4h setup
- **Codigo:** Pode rodar local

## Sprint 3 (mes 2-3, alta friccao)

### Multi-tenancy
- **Acao:** Refator pra schema-per-tenant (django-tenants) ou row-level
- **Esforco:** ~80h (toques em todas queries + permissoes + admin)
- **Quando:** So se vender pra mais clinicas
- **Risco:** ALTO (refator profundo)

### Mobile app
- **Acao:** React Native consumindo API DRF (`/api/v1/`)
- **Esforco:** ~200h MVP
- **Externo:** Apple Developer Program ($99/ano), Google Play ($25 unico)
- **Pre-req:** Ampliar API REST (CRUD agendamento, push tokens)

### Chat interno (WS)
- **Acao:** Django Channels + Redis + frontend WS
- **Esforco:** ~40h
- **Codigo:** Adiciona async, reorg ASGI

### AI features
- **Acao:** Sugestao horario (LLM), classificacao NPS sentimento, sumarizacao prontuario
- **Esforco:** ~20h por feature
- **Externo:** provedor LLM (~$50/mes)
- **Compliance:** **CUIDADO LGPD** — dados saude para 3rd-party requer consentimento explicito

## Frontend/admin — deferido (requer asset pipeline ou redesenho)

### Frontend publico
- **WebP/AVIF + srcset** — conversao lote das imagens `assets/health/*` (Pillow ou cwebp externo)
- **Critical CSS inline** — extrair above-the-fold (~14KB) via critters/critical
- **Self-host Google Fonts subset** — baixar woff2 subset latin, servir de `static/fonts/`
- **Tree-shake Bootstrap** — bundle custom so com componentes usados
- **Image sitemap** — `ImageSitemap` separado em `sitemaps.py`
- **Google Reviews widget / social proof** — componente com API externa
- **A/B testing hero CTA** — django-waffle + variantes
- **Antes/depois galeria interativa** — library compare-slider
- **Calculadora pacote** — formulario dinamico preco

### Admin PWA
- **FullCalendar drag-drop** — substituir lista por calendar view (~20h)
- **Push notifications VAPID** — backend envio + SW subscribe UI
- **Command palette cmd+k** — fuzzy search global (kbar-like)
- **IndexedDB offline-first** — cache agendamentos + sync queue
- **WebAuthn biometric login** — opcional extra MFA
- **Virtualizacao listas** — react-window ou intersection-observer lazy
- **Inline edit / quick view modal** — edicao direta sem navegar
- **Bulk actions expandido** — select-all + multi-action
- **Chart.js cohort/funil/heatmap** — relatorios avancados
- **IP allowlist UI** — tabela gerenciar IPs permitidos (complementa Cloudflare Access)

## Backlog (sem prazo)

- **TISS integration** — convenios saude, padronizacao ANS
- **Loyalty/cashback** — pontos por atendimento
- **GraphQL endpoint** (strawberry-django) — alternativa REST
- **Storybook design system** — componentes templates documentados
- **django-prometheus** + Grafana dashboards
- **Renovate bot** alternativa Dependabot
- **Release-please** — release notes automaticas
- **PWA push notifications** (VAPID web push)
- **Calendario drag-drop** FullCalendar.js
- **Export PDF relatorios** weasyprint (binario instalado externo)

## Itens ja implementados (sumario)

Ver commits `dev` para detalhes:
- ✅ Settings split base/dev/prod
- ✅ CSP nonce per-request
- ✅ HSTS + SSL + cookies seguros
- ✅ Validators CPF/telefone/data
- ✅ Soft-delete + LGPD service
- ✅ Cookie banner granular
- ✅ DSAR endpoint
- ✅ Anonimizacao agendada (Celery weekly)
- ✅ django-axes lockout
- ✅ django-ratelimit em endpoints publicos
- ✅ DRF API + OpenAPI schema
- ✅ Sitemap dinamico + robots.txt
- ✅ PWA: manifest + SW workbox-style
- ✅ Lazy loading imagens
- ✅ i18n base (pt-br/en/es)
- ✅ django-storages config (S3/R2)
- ✅ CI workflow (lint+test+security)
- ✅ Pre-commit hooks
- ✅ Dependabot
- ✅ PR/issue templates + CODEOWNERS
- ✅ Smoke test script
- ✅ Dockerfile + docker-compose
- ✅ DPIA documentado
