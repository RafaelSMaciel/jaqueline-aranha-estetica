# Cobertura de Custos / Serviços Externos — Plataforma Dra. Jaqueline Aranha

Data: 2026-04-27 · Valores 2025-2026 (USD/BRL aproximados, USD≈R$5,30)

---

## Resumo executivo

| Cenário | Custo mensal aproximado |
|---------|-------------------------|
| **Free tier (atual)** | **~R$ 0 a R$ 30** |
| **Operação básica (estável, ~500 atendimentos/mês)** | **~R$ 250 a R$ 450** |
| **Operação plena (templates Meta + SMS volume + backup)** | **~R$ 600 a R$ 900** |

Variável principal: volume de **SMS Zenvia** (OTP) e **WhatsApp Meta** (lembretes + NPS).

---

## 1. Infraestrutura

### Railway (web app + Postgres + cron interno)
- **Uso:** Django + gunicorn (1 worker, 4 threads), Postgres managed, deploys automáticos
- **Tier atual:** Free (Hobby plan ~$5 crédito/mês)
- **Limitações free:** sleep após ~5min sem tráfego, RAM 512MB, 1GB DB, sem snapshots automáticos
- **Custo recomendado prod:**
  - **Hobby plan ($5/mês fixo)** — adequado pra MVP atual
  - **Pro plan ($20/mês + uso)** — mantém serviço sempre ligado, RAM ampla, snapshots Postgres, ~$20-40/mês total com DB
- **Estimativa real:** **R$ 30 a R$ 200/mês** dependendo do plano

### Domínio
- Uso: domínio próprio (futuro — atualmente subdomínio Railway)
- **Registro.br** `.com.br`: ~R$ 40/ano = **R$ 3,30/mês**
- **GoDaddy/Cloudflare** `.com`: $10-15/ano = **R$ 5-7/mês**

### Cloudflare (CDN + WAF + DDoS)
- **Uso atual:** Turnstile captcha (free) — formulários públicos
- **Tier atual:** Free
- **Recomendado:** ativar proxy DNS (free) — adiciona WAF, cache estáticos, HTTPS
- **Custo:** **R$ 0** (free tier suficiente)
- **Cloudflare Access** (VPN/IP allowlist em /admin/) também **free** até 50 users

---

## 2. Comunicação

### Zenvia SMS (OTP)
- **Uso:** OTP de 6 dígitos para verificação de cliente no agendamento
- **Modelo:** pré-pago, por mensagem
- **Custo unitário 2025:** ~**R$ 0,12 a R$ 0,18 por SMS** (volumes baixos), pode chegar a R$ 0,08 com contrato volume
- **Estimativa volume:**
  - 500 agendamentos/mês × 1,2 SMS médio (alguns reenviam) = **~600 SMS = R$ 72 a R$ 108/mês**
  - 1000 agendamentos/mês = **R$ 144 a R$ 216/mês**
- **Otimização:** rate limit já configurado (3 SMS/hora por telefone — anti-abuse)

### WhatsApp Business API (Meta Cloud)
- **Uso:** lembrete D-1, NPS pós-atendimento, possível confirmação
- **Modelo:** **Conversation pricing** — Brasil
  - **Marketing:** ~R$ 0,15 a R$ 0,30 por conversa
  - **Utility (transacional, lembrete, confirmação):** ~R$ 0,04 a R$ 0,08 por conversa
  - **Service (cliente inicia):** geralmente **gratuito** (1000 conversas/mês free)
  - Janela 24h gratuita após contato cliente
- **Estimativa volume (500 atendimentos/mês):**
  - Lembretes D-1 (utility): 500 × R$ 0,06 = **R$ 30/mês**
  - NPS (utility): 500 × R$ 0,06 = **R$ 30/mês**
  - Promoções massa (marketing, opt-in only): 200 × R$ 0,20 = **R$ 40/mês**
  - **Total: ~R$ 100/mês**
- **Bloqueador atual:** templates `lembrete_d1`, `nps`, `confirmacao` precisam aprovação Meta Business Manager (24-72h, gratuito)

### Email transacional
- **Uso atual:** SMTP Gmail (configurado em `.env.example`) — limite ~500/dia, sem confiabilidade pra volume
- **Tier atual:** Free Gmail (limite restritivo)
- **Recomendado pra produção:**
  - **Resend** — 3000 emails/mês free, depois $20/mês 50k → **R$ 0 a R$ 110/mês**
  - **SendGrid** — 100/dia free, $19,95/mês 50k → **R$ 0 a R$ 110/mês**
  - **Amazon SES** — $0,10/1000 emails (mais barato em volume): **R$ 5-25/mês** em volume
- **Estimativa volume (500 atendimentos/mês):** confirmações + cancel + alertas + promo + aniversário ≈ 2500 emails/mês → **R$ 0 (free tier) a R$ 30/mês**

---

## 3. Observabilidade

### Sentry (erro tracking)
- **Tier:** Free (5k events/mês, 1 user, retenção 30 dias)
- **Pago:** Team $26/mês (50k events, 90 dias retenção)
- **Recomendado:** **Free é suficiente pra clínica** (5k events/mês cobre confortavelmente apps de baixo tráfego)
- **Estimativa:** **R$ 0/mês** (free)

### Cron-job.org
- **Uso:** dispara endpoints HTTP do Railway pra rodar tarefas agendadas (lembrete D-1, NPS, expiração pacotes, anonimização LGPD)
- **Tier:** Free (sem limite documentado para uso razoável)
- **Custo:** **R$ 0/mês**

---

## 4. Segurança / CAPTCHA

### Cloudflare Turnstile
- **Uso:** captcha invisível em formulários públicos (agendamento, contato)
- **Tier:** Free unlimited
- **Custo:** **R$ 0/mês**

---

## 5. Storage / Mídia (futuro)

### AWS S3 / Cloudflare R2 (backups + uploads de imagens prontuário)
- **Uso atual:** **não configurado** ainda — uploads ficam no FS local (perdem em redeploy Railway)
- **Recomendado:**
  - **Cloudflare R2** — 10GB free + $0.015/GB depois, **sem egress fees** → **R$ 0 a R$ 25/mês**
  - **AWS S3** — $0.023/GB + egress $0.09/GB (caro em downloads) → **R$ 25-80/mês**
- **Estimativa:** **R$ 25/mês** (R2, ~50GB com fotos prontuário e backups DB)

---

## 6. Analytics

### Google Analytics 4
- **Uso atual:** detectado em CSP ([middleware.py](app_shivazen/middleware.py)) — `https://www.google-analytics.com` permitido
- **Custo:** **R$ 0/mês** (GA4 free tier ilimitado)
- **⚠️ LGPD:** GA precisa estar atrás do cookie banner (consent obrigatório). Verificar gating.

---

## 7. Tabela consolidada

| Serviço | Uso | Tier | Custo mensal típico |
|---------|-----|------|---------------------|
| Railway (web + DB) | hosting Django+Postgres | Pro recomendado | **R$ 100 a R$ 200** |
| Domínio `.com.br` | site público | Registro.br | **R$ 4** |
| Cloudflare (CDN + WAF + Turnstile) | proxy + captcha | Free | **R$ 0** |
| Zenvia SMS | OTP cliente | Pré-pago | **R$ 70 a R$ 200** |
| WhatsApp Cloud API | lembrete + NPS + promo | Conversation pricing | **R$ 80 a R$ 150** |
| Email (Resend/SES) | transacional | Free a paid | **R$ 0 a R$ 30** |
| Sentry | error tracking | Free | **R$ 0** |
| Cron-job.org | scheduler externo | Free | **R$ 0** |
| Cloudflare R2 (futuro) | backups + uploads | Pay-as-you-go | **R$ 25** |
| Google Analytics 4 | métricas | Free | **R$ 0** |
| **TOTAL ESTIMADO** | | | **R$ 280 a R$ 610/mês** |

---

## 8. Custos NÃO recorrentes (one-time / opcional)

- **Apple Developer Program** (PWA → app store): $99/ano = **~R$ 525/ano**
- **Google Play Developer**: $25 único = **~R$ 130 único**
- **Domínio premium** (se quiser `.com` curto): variável
- **Certificado SSL EV** (se exigido por convênio): $80-300/ano (geralmente NÃO necessário — Let's Encrypt free via Railway)

---

## 9. Recomendações de redução de custo

1. **Manter free tiers onde possível**: Sentry, Cloudflare, Cron-job.org, GA4, Turnstile — economia ~R$ 50-100/mês vs alternativas pagas
2. **Cloudflare R2 > AWS S3** para mídia: free 10GB + sem egress = mais barato em escala
3. **Resend free tier** cobre 3000 emails/mês — típico fica grátis até ~600 atendimentos/mês
4. **Negociar volume Zenvia** acima de 1000 SMS/mês — desconto para R$ 0,08
5. **Rate limit OTP já implementado** (3/hora por telefone) — controla escalation custos SMS
6. **WhatsApp utility (lembrete/NPS) cabe em ~R$ 0,06/conversa** — mais barato que SMS, considerar migrar OTP para WhatsApp futuro (template `otp_login` aprovado)
7. **Backup DB**: Railway Pro inclui snapshots gratis. R2 só para retenção longa.
8. **Domínio Registro.br** (~R$ 40/ano) é o mais barato e funciona perfeito.

---

## 10. O que falta o user fazer pra ativar custos previstos

| Ação | Onde | Quando |
|------|------|--------|
| Trocar Railway Hobby → Pro | dashboard.railway.app | quando ultrapassar limites free |
| Submeter templates Meta | business.facebook.com | imediato (24-72h aprovação) |
| Criar projeto Sentry + setar `SENTRY_DSN` | sentry.io | hoje (15min) |
| Registrar domínio `.com.br` | registro.br | quando quiser branding URL |
| Configurar Cloudflare DNS | cloudflare.com | após domínio próprio |
| Trocar SMTP Gmail → Resend | resend.com | quando volume passar 100/dia |
| Provisionar Cloudflare R2 | dash.cloudflare.com | quando começar uploads prontuário |
| Conta Zenvia + creditar saldo | zenvia.com | imediato (já está integrado no código) |
