# Setup Produção — Jaqueline Aranha Estética

Variáveis e contas externas que dependem de você criar/configurar.

## ✅ Já configurado

| Item | Status |
|---|---|
| Railway projeto | `jaqueline-aranha-estetica` |
| GitHub source | `RafaelSMaciel/jaqueline-aranha-estetica` (main → prod, dev → dev) |
| Service web | rodando ambos envs |
| Postgres prod + Postgres-Dev | ambos c/ volume persistente |
| VAPID Web Push keys | geradas + setadas (dev + prod) |
| CLINIC_NAME / brand vars | setadas |
| 2FA TOTP | habilitado |
| Audit log | ativo |

## 🔴 Pendentes — você precisa criar conta + setar env vars

### 1. Sentry (monitoramento erros)

1. Criar conta gratuita: https://sentry.io/signup/
2. New Project → Django
3. Copiar DSN
4. Setar Railway:
   ```bash
   railway variables --service web --environment production --set "SENTRY_DSN=<dsn>" --skip-deploys
   railway variables --service web --environment dev --set "SENTRY_DSN=<dsn>" --skip-deploys
   ```

### 2. Cloudflare Turnstile (captcha)

1. Login https://dash.cloudflare.com/
2. Turnstile → Add site
3. Domain: `web-production-465af.up.railway.app` (e dev)
4. Copiar SITE_KEY + SECRET_KEY
5. Setar:
   ```bash
   railway variables --service web --environment production \
     --set "TURNSTILE_SITE_KEY=<site>" \
     --set "TURNSTILE_SECRET_KEY=<secret>" \
     --skip-deploys
   ```

### 3. WhatsApp Business API (Meta)

1. https://business.facebook.com → Whatsapp Business
2. Criar app + número
3. Submeter templates pra aprovação (24-72h):
   - Nome: `lembrete_d1` — body: "Olá {{1}}, lembrete: você tem agendamento amanhã às {{2}} com {{3}}."
   - Nome: `nps` — body: "Olá {{1}}, como foi seu atendimento? Avalie de 0 a 10: {{2}}"
   - Nome: `confirmacao` — body: "Confirmação: {{1}} em {{2}} com {{3}}."
4. Setar:
   ```bash
   railway variables --service web --environment production \
     --set "WHATSAPP_TOKEN=<token>" \
     --set "WHATSAPP_PHONE_ID=<phone_id>" \
     --set "WHATSAPP_NUMERO=<seu_numero>" \
     --skip-deploys
   ```

### 4. Email SMTP (Gmail / SendGrid / SES)

Gmail (simples):
1. https://myaccount.google.com/apppasswords → criar app password
2. Setar:
   ```bash
   railway variables --service web --environment production \
     --set "EMAIL_HOST_USER=<seu_email>" \
     --set "EMAIL_HOST_PASSWORD=<app_password>" \
     --set "EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend" \
     --skip-deploys
   ```

### 5. Zenvia SMS (OTP)

1. https://www.zenvia.com → criar conta
2. API key → setar `ZENVIA_API_KEY` (verifique código `aranha_estetica/utils/sms.py` p/ nome correto)

### 6. Google Calendar OAuth (sync)

1. https://console.cloud.google.com → New Project
2. APIs & Services → Calendar API → Enable
3. Credentials → OAuth Client ID (Web)
4. Authorized redirect URIs:
   - `https://web-production-465af.up.railway.app/painel/integrations/google/callback/`
   - `https://web-dev-1a30.up.railway.app/painel/integrations/google/callback/`
5. Setar:
   ```bash
   railway variables --service web --environment production \
     --set "GOOGLE_OAUTH_CLIENT_ID=<id>" \
     --set "GOOGLE_OAUTH_CLIENT_SECRET=<secret>" \
     --set "GOOGLE_OAUTH_REDIRECT_URI=https://web-production-465af.up.railway.app/painel/integrations/google/callback/" \
     --skip-deploys
   ```

## 🔴 Backups DB (CRÍTICO)

### Opção A — Railway Pro snapshot (US$5/mês)

1. Upgrade Railway → Pro plan
2. Settings → Backup → habilitar daily snapshot

### Opção B — Cron-job.org + dump S3 (free)

1. Criar bucket S3 (AWS, Cloudflare R2 free tier 10GB, ou Backblaze B2)
2. Endpoint dump pode ser:
   ```bash
   railway run --service Postgres pg_dump $DATABASE_URL > backup-$(date +%Y%m%d).sql
   ```
3. Cron-job.org → POST `https://web-production-465af.up.railway.app/cron/run/backup/?token=$CRON_TOKEN` (precisa criar endpoint)

## 🟡 LGPD avançado — encriptação at-rest PII

Status atual: PII (CPF, RG, telefone, email) em plaintext Postgres.

LGPD não exige encriptação at-rest explicitamente, mas é boa prática.

Implementação futura:
1. `pip install django-cryptography`
2. Substituir `models.CharField` por `EncryptedCharField` em campos sensíveis
3. **Cuidado**: queries `filter(cpf='123')` deixam de funcionar — precisa adaptar buscas
4. Migration custom p/ encriptar dados existentes

Esforço: ~4-8h c/ rewrite views que buscam por CPF/email.

Recomendação: deferir até crescimento da base que justifique.

## Comandos úteis

```bash
# Listar todas vars
railway variables --service web --environment production --kv

# Ver logs prod
railway logs --service web --environment production

# Conectar shell Django prod
railway run --service web --environment production python manage.py shell

# Forçar redeploy
railway redeploy --service web

# Status atual
railway status
```

## Token Railway

Token salvo em ambiente local. Se rotacionar:
1. Railway dashboard → Account → Tokens
2. Substituir `RAILWAY_API_TOKEN` em scripts/CI

## URLs

- Prod: https://web-production-465af.up.railway.app/
- Dev: https://web-dev-1a30.up.railway.app/
- Login admin: `/admin-login/`
- Painel: `/painel/`
