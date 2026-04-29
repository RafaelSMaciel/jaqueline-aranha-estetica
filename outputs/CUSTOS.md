# Cobertura de Custos / Serviços Externos — Jaqueline Aranha Estética

Data: 2026-04-27 · Valores 2025-2026 (USD/BRL aproximados, USD≈R$5,30)

---

## Resumo executivo

Volume operacional realista informado: **até 100 pacientes/mês**.

| Cenário | Custo mensal aproximado |
|---------|-------------------------|
| **Free tier (atual)** | **~R$ 0 a R$ 30** |
| **Operação real (100 pacientes/mês)** | **~R$ 130 a R$ 250** |
| **Crescimento (500 pacientes/mês — referência)** | **~R$ 280 a R$ 610** |

Variável principal: volume de **SMS Zenvia** (OTP) e **WhatsApp Meta** (lembretes + NPS) — ambos lineares com pacientes/mês.

---

## 1. Infraestrutura

### Railway (web app + Postgres + cron interno)
- **Uso:** Django + gunicorn (1 worker, 4 threads), Postgres managed, deploys auto
- **Tier atual:** Free Hobby ($5 crédito/mês)
- **Limitações free:** sleep ~5min sem tráfego, RAM 512MB, 1GB DB, sem snapshots auto
- **Recomendado prod:** Hobby plan ($5/mês fixo) cobre 100 pacientes/mês confortavelmente
- **Estimativa:** **R$ 30 a R$ 100/mês** (Hobby)

### Domínio
- Não configurado ainda — site em subdomínio Railway
- **Registro.br** `.com.br`: ~R$ 40/ano = **R$ 3,30/mês**
- **GoDaddy/Cloudflare** `.com`: $10-15/ano = **R$ 5-7/mês**

### Cloudflare (CDN + WAF + DDoS + Turnstile)
- **Uso atual:** Turnstile captcha (free) — formulários públicos
- **Tier:** Free unlimited
- **Custo:** **R$ 0/mês**

---

## 2. Comunicação

### Zenvia SMS (OTP de verificação)
- **Modelo:** pré-pago, por mensagem
- **Custo unitário 2025-2026:** ~**R$ 0,12 a R$ 0,18 por SMS**
- **Volume estimado (100 pacientes/mês):**
  - 100 pacientes × ~1,2 SMS médio (login + reagendamento) = **~120 SMS/mês**
  - 120 × R$ 0,15 médio = **~R$ 18/mês**
- **Volume referência (500 pacientes/mês):** **~R$ 90/mês**
- **Otimização:** rate limit já configurado (3 SMS/hora por telefone — anti-abuse)

### WhatsApp Business API (Meta Cloud)
- **Modelo:** Conversation pricing (Brasil)
  - **Utility (lembrete, NPS, confirmação):** ~R$ 0,04 a R$ 0,08 por conversa
  - **Marketing:** ~R$ 0,15 a R$ 0,30 por conversa (opt-in only, LGPD)
  - **Service (paciente inicia):** primeiras 1000 conversas/mês **free**
- **Volume estimado (100 pacientes/mês):**
  - 100 pacientes × ~3 conversas (confirmação + lembrete D-1 + NPS) = **~300 utility/mês**
  - 300 × R$ 0,06 = **~R$ 18/mês**
  - Promo marketing opt-in (~30% base): 30 × R$ 0,20 = **R$ 6/mês**
  - **Total: ~R$ 24/mês**
- **Volume referência (500 pacientes/mês):** **~R$ 100-150/mês**
- **Bloqueador:** templates `lembrete_d1`, `nps`, `confirmacao` precisam aprovação Meta Business Manager (24-72h, gratuito)

### Email transacional
- **Atual:** SMTP Gmail — limite ~500/dia, sem confiabilidade volume + envia de @gmail
- **Recomendado:**
  - **Resend** — 3000 emails/mês free → cobre 100 pacientes folgado
  - **SendGrid** — 100/dia free, $19,95/mês 50k
  - **Amazon SES** — $0,10/1000 emails (mais barato em volume)
- **Volume estimado (100 pacientes/mês):** confirmações + cancel + alertas + promo + aniversário ≈ **~500-700 emails/mês**
- **Estimativa:** **R$ 0/mês (Resend free tier suficiente)**
- **Volume referência (500 pacientes/mês):** **~R$ 0-30/mês**

---

## 3. Observabilidade

### Sentry (error tracking)
- **Tier:** Free (5k events/mês, 1 user, retenção 30 dias)
- **5k events/mês cobre confortavelmente** uma clínica baixo tráfego
- **Custo:** **R$ 0/mês**

### Cron-job.org
- **Uso:** dispara endpoints Railway (lembrete D-1, NPS, expiração pacotes, anonimização LGPD)
- **Tier:** Free
- **Custo:** **R$ 0/mês**

---

## 4. Segurança / CAPTCHA

### Cloudflare Turnstile
- **Uso:** captcha invisível em formulários públicos
- **Tier:** Free unlimited
- **Custo:** **R$ 0/mês**

---

## 5. Storage / Mídia (futuro)

### Cloudflare R2 / AWS S3 (backups DB + uploads prontuário)
- **Uso atual:** **não configurado** — uploads ficam no FS local (perdem em redeploy)
- **Recomendado:** **Cloudflare R2** — 10GB free + $0.015/GB depois, **sem egress fees**
- **Volume 100 pacientes/mês:** ~5-10GB com fotos prontuário acumuladas → **R$ 0/mês (free tier)**
- **Crescimento longo prazo:** **R$ 0 a R$ 25/mês**

---

## 6. Analytics

### Google Analytics 4
- **Custo:** **R$ 0/mês** (free tier ilimitado)
- **⚠️ LGPD:** GA precisa estar atrás do cookie banner (consent obrigatório)

---

## 7. Tabela consolidada — 100 pacientes/mês (cenário real)

| Serviço | Uso | Tier | Custo mensal |
|---------|-----|------|--------------|
| Railway (web + DB) | hosting Django+Postgres | Hobby ($5) | **R$ 30 a R$ 100** |
| Domínio `.com.br` | site público | Registro.br | **R$ 4** |
| Cloudflare (CDN + WAF + Turnstile) | proxy + captcha | Free | **R$ 0** |
| **Zenvia SMS (OTP)** | ~120 SMS/mês | Pré-pago | **R$ 18** |
| **WhatsApp Cloud (utility + promo)** | ~330 conversas/mês | Conversation pricing | **R$ 24** |
| **Email (Resend)** | ~600 emails/mês | Free tier (3k/mês) | **R$ 0** |
| Sentry | error tracking | Free | **R$ 0** |
| Cron-job.org | scheduler externo | Free | **R$ 0** |
| Cloudflare R2 (futuro) | backups + uploads | Free tier (10GB) | **R$ 0** |
| Google Analytics 4 | métricas | Free | **R$ 0** |
| **TOTAL ESTIMADO** | | | **R$ 75 a R$ 145/mês** |

Margem por imprevistos / picos: **+R$ 50 a R$ 100/mês** ⇒ **planejar R$ 130-250/mês**.

---

## 8. Tabela referência — 500 pacientes/mês (crescimento futuro)

| Serviço | Custo mensal |
|---------|--------------|
| Railway (Pro plan recomendado em volume) | R$ 100-200 |
| Domínio | R$ 4 |
| Cloudflare | R$ 0 |
| Zenvia SMS (~600/mês) | R$ 90 |
| WhatsApp Cloud (~1500 conversas) | R$ 100-150 |
| Email (Resend pago em volume) | R$ 0-110 |
| Sentry | R$ 0 |
| R2 (~50GB acumulado) | R$ 25 |
| **TOTAL** | **R$ 320 a R$ 580/mês** |

---

## 9. Custos NÃO recorrentes (one-time / opcional)

- **Apple Developer Program** (PWA → app store): $99/ano = **~R$ 525/ano**
- **Google Play Developer**: $25 único = **~R$ 130 único**
- **Domínio premium** (`.com` curto): variável
- **Certificado SSL EV**: $80-300/ano (geralmente NÃO necessário — Let's Encrypt grátis via Railway)

---

## 10. Recomendações de redução de custo (volume 100/mês)

1. **Manter Railway Hobby** ($5 fixo) — Pro só faz sentido a partir de ~300 pacientes/mês
2. **Email no Resend free tier** (3000/mês) — cobre folgado o cenário 100 pacientes
3. **Cloudflare R2 free 10GB** — backups + uploads cabem por ano+ sem custo
4. **Zenvia rate limit 3/hora** já configurado — controla escalation custos SMS
5. **Migrar OTP futuro pra WhatsApp** (template `otp_login` aprovado) — corta R$ 18/mês SMS
6. **Sentry free 5k events** suficiente — não precisa upgrade até alto volume erros
7. **Cron-job.org free** evita Celery Beat + Redis pago

---

## 11. O que falta o user fazer pra ativar custos previstos

| Ação | Onde | Quando | Esforço |
|------|------|--------|---------|
| Submeter templates Meta | business.facebook.com | imediato (24-72h aprovação) | S |
| Criar projeto Sentry + setar `SENTRY_DSN` | sentry.io | hoje (15min) | S |
| Conta Zenvia + creditar saldo | zenvia.com | imediato (já integrado) | S |
| Conta Resend + setar SMTP env | resend.com | quando trocar de Gmail (volume baixo) | S |
| Registrar domínio `.com.br` | registro.br | quando quiser branding URL | S |
| Configurar Cloudflare DNS | cloudflare.com | após domínio próprio | S |
| Provisionar Cloudflare R2 | dash.cloudflare.com | quando começar uploads prontuário | M |
| Trocar Railway Hobby → Pro | dashboard.railway.app | só quando ultrapassar limites (~300+ pacientes) | S |
