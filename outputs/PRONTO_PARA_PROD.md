# Checklist Pronto Para Produção — Jaqueline Aranha Estética

Data: 2026-04-27 · Branch: `main` (HEAD `481cc35`)

Foco: lacunas reais entre código atual e operação **dia 1** com paciente real. NÃO repete REVIEW.md (técnico) — aqui é gap "go-live".

---

## 1. Bloqueadores absolutos 🔴

Sem esses, **NÃO PODE ir pra prod**. Risco de perda de dados, lockout, ou exposição.

### 1.1 Auto-deploy Railway funcionando
- **Estado:** Railway não está disparando deploy ao push (commits `c29ce88`, `f39a376`, `e8892e2`, `07b9739`, `fa734d8`, `481cc35` em main+dev sem reflexo no site)
- **Ação:** abrir Railway Dashboard → Service → Settings → Source → confirmar branch + auto-deploy ligado. Ou reconectar GitHub.
- **Quem:** Rafael (ops)
- **Estimativa:** 30 min - 2h (varia se for desconectar/reconectar webhook)

### 1.2 2FA admin ativado em prod
- **Estado:** código merged (commit `f39a376`), faltam env vars + bootstrap
- **Ação:**
  1. Setar `TWO_FACTOR_REMEMBER_COOKIE_AGE` (opcional, default 30d) no Railway
  2. Após deploy: `railway run python manage.py setup_2fa <email-admin>`
  3. Escanear QR no Google Authenticator/Authy
  4. Anotar 10 backup tokens em cofre seguro
  5. Validar login `/account/login/` antes de fechar sessão atual
- **Quem:** Dra (escanear QR) + Rafael (rodar comando)
- **Estimativa:** 30 min

### 1.3 Backups DB
- **Estado:** Railway Hobby NÃO inclui snapshots automáticos
- **Ação:**
  1. Endpoint `/cron/run/backup_db/` (criar — não existe ainda) que faz `pg_dump | gzip | upload R2`
  2. Provisionar Cloudflare R2 bucket
  3. Cron-job.org chamar 1x/dia com `X-Cron-Token`
  4. Retenção 30 dias
- **Quem:** Rafael (dev + ops)
- **Estimativa:** 4-6h

### 1.4 Domínio definitivo configurado
- **Estado:** site em `web-production-465af.up.railway.app` (subdomain genérico)
- **Ação:**
  1. Registrar `jaquelineearanha.com.br` (ou similar) no Registro.br
  2. Apontar DNS pra Railway (CNAME)
  3. Adicionar custom domain no Railway Service → Settings
  4. Atualizar `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `SITE_URL` env vars
  5. Atualizar context_processor `SITE_URL` default
- **Quem:** Rafael (registro + DNS) + Dra (decidir nome)
- **Estimativa:** 1-2h dev + 24-48h propagação DNS

### 1.5 Política privacidade + termos publicados
- **Estado:** **OK em código** ✓ — templates `politica_privacidade.html`, `termos_uso.html`, `termo_assinatura.html` existem e linkados em rodapé. Conteúdo gerado, mas pode precisar revisão jurídica.
- **Ação:** revisar conteúdo com advogado/responsável compliance, ajustar dados específicos (CNPJ, endereço, DPO)
- **Quem:** Dra + Rafael (ajustar campos finais)
- **Estimativa:** 2h revisão + ajustes

### 1.6 Email transacional com domínio próprio
- **Estado:** atual envia de `noreply@clinica.com.br` (genérico) via Gmail SMTP
- **Ação:**
  1. Conta Resend.com (gratuito até 3k/mês)
  2. Verificar domínio `jaquelineearanha.com.br` (DKIM + SPF + DMARC records)
  3. Trocar env Railway: `EMAIL_HOST=smtp.resend.com`, `EMAIL_HOST_USER=resend`, `EMAIL_HOST_PASSWORD=<api-key>`, `DEFAULT_FROM_EMAIL=contato@jaquelineearanha.com.br`
  4. Testar envio em produção (template confirmação agendamento)
- **Quem:** Rafael
- **Estimativa:** 1-2h

---

## 2. Pré-lançamento 🟡

Alta prioridade, **não-bloqueante** mas fortemente recomendado antes de divulgar.

### 2.1 Sentry DSN ativo
- **Ação:** sentry.io → Create project Django → copiar DSN → setar `SENTRY_DSN` no Railway
- **Estimativa:** 15 min · Rafael

### 2.2 WhatsApp templates Meta aprovados
- **Ação:** business.facebook.com → Submeter `lembrete_d1`, `nps`, `confirmacao_agendamento` (24-72h aprovação)
- **Estimativa:** 30 min submissão + espera · Rafael ou Dra (acesso Meta)

### 2.3 Cookie banner — verificar gating de tracking
- **Estado:** banner existe (templates/partials/cookie_consent.html). Verificar se Google Analytics SÓ carrega após consent.
- **Ação:** auditar template head — bloquear `gtag()` até cookie aceito
- **Estimativa:** 1h · Rafael

### 2.4 Smoke tests E2E manuais documentados
- **Ação:** criar `outputs/SMOKE_TESTS.md` com checklist:
  - [ ] Site carrega em desktop + mobile
  - [ ] Agendamento público completo (fluxo OTP SMS → confirmação)
  - [ ] Email confirmação chega na caixa
  - [ ] Reagendamento via link mágico
  - [ ] Cancelamento via link mágico
  - [ ] Login admin painel + 2FA
  - [ ] Aprovação atendimento pendente
  - [ ] Cadastro paciente manual
  - [ ] Listar/buscar pacientes
  - [ ] Cron lembrete D-1 dispara (rodar manualmente endpoint)
  - [ ] Sitemap.xml + robots.txt respondem 200
- **Estimativa:** 2h documentar + 2h executar · Rafael+Dra

### 2.5 Setup completo da Dra
- **Ação:**
  1. Criar usuário admin no painel (`Perfil=Administrador`)
  2. Criar usuário profissional (Dra Jaqueline) ligado ao perfil
  3. Subir foto perfil + logo clínica
  4. Cadastrar 23 procedimentos do catálogo (seed `setup_seed` quebrado — recriar via painel ou seed.py)
  5. Definir preços iniciais
  6. Configurar horário de atendimento padrão
  7. Definir bloqueios (almoço, folgas)
  8. Criar pelo menos 1 promoção e 1 pacote pra demo
- **Estimativa:** 4-6h · Dra (com guia) ou Rafael

### 2.6 Robots.txt + sitemap.xml respondendo
- **Estado:** **OK em código** ✓ — confirmado que estão registrados em `clinica/urls.py`
- **Ação:** validar em prod com `curl https://<dominio>/robots.txt` e `curl https://<dominio>/sitemap.xml` após deploy
- **Estimativa:** 5 min · Rafael

---

## 3. Operação dia 1 (logística não-código) 🟢

### 3.1 Onboarding da Dra
- **Ação:** criar guia rápido (PDF ou vídeo curto 5-10 min) cobrindo:
  - Como logar (com 2FA)
  - Como aprovar agendamento pendente
  - Como cadastrar paciente manualmente
  - Como atender (marcar realizado)
  - Como cadastrar pacote vendido
  - Como criar promoção e disparar email
  - Como exportar dados paciente (DSAR)
- **Quem:** Rafael (gravar) ou Dra (ler com Rafael)
- **Estimativa:** 3-4h

### 3.2 Backup de configurações (.env de prod)
- **Ação:** salvar em cofre (1Password, Bitwarden) cópia das env vars Railway: `DJANGO_SECRET_KEY`, `DATABASE_URL`, `WHATSAPP_TOKEN`, `ZENVIA_*`, `TURNSTILE_*`, `SENTRY_DSN`, `CRON_TOKEN`. Sem essas, recriar prod do zero é dor.
- **Estimativa:** 30 min · Rafael

### 3.3 Plano de comunicação se sistema cair
- **Ação:** definir canal alternativo. Sugestão:
  - Página estática `/manutencao/` ativada via env var `MAINTENANCE_MODE=True`
  - WhatsApp pessoal Dra como fallback de agendamento manual
  - Cliente prejudicado recebe SMS automático: "estamos passando por instabilidade, ligue (XX) XXXX"
- **Estimativa:** 2h · Rafael

### 3.4 Suporte técnico
- **Ação:** definir SLA pessoal:
  - Quem é o "DRI" (Rafael?)
  - Horário disponível (ex: seg-sex 9h-18h, fim-semana sob demanda)
  - Como Dra contata (WhatsApp pessoal direto?)
  - Tempo resposta esperado: crítico (site fora) <30min, normal <4h
- **Estimativa:** 30 min conversa · Rafael+Dra

---

## 4. Pós-lançamento (1-2 semanas pós go-live) 🔵

### 4.1 Métricas a monitorar
- **Conversion rate:** visitantes / agendamentos (GA4 + funil custom)
- **Taxa abandono OTP:** quantos solicitam código mas não validam (sinal de fricção SMS)
- **No-show rate:** atendimentos `FALTOU` / total realizados
- **NPS médio:** dashboard painel já mostra
- **Tempo aprovação admin:** delta entre criação e aprovação atendimento

### 4.2 Ajustes UX baseado em uso real
- Sentry: monitorar erros JS no front-end real (smartphones diversos)
- Rastrear cliques em botões CTA pra ajustar copy
- Heatmap (Hotjar/Microsoft Clarity free) — opcional

### 4.3 Sanity check de carga
- Mesmo com 100 pacientes/mês, picos podem ocorrer (campanha promo)
- Sentry performance traces, Railway metrics (CPU, RAM, response time)
- Alarme manual se p95 response time > 2s

---

## Checklist final de bloqueadores 🔴

| # | Item | Estimativa | Quem |
|---|------|------------|------|
| 1 | Auto-deploy Railway funcionando | 0,5-2h | Rafael |
| 2 | Bootstrap 2FA + env Railway | 0,5h | Rafael+Dra |
| 3 | Backup DB automatizado (cron + R2) | 4-6h | Rafael |
| 4 | Domínio próprio + DNS | 1-2h dev + 48h propagação | Rafael+Dra |
| 5 | Revisão termos/privacidade conteúdo | 2h | Dra+Rafael |
| 6 | Email Resend com domínio verificado | 1-2h | Rafael |

**Total bloqueadores:** ~9-15h de dev + decisões/ações da Dra (escolher domínio, escanear QR 2FA, revisar texto legal).

**Pré-lançamento (não-bloqueante mas pré-go-live):** Sentry DSN, WhatsApp templates, cookie consent, smoke tests, setup procedimentos/preços, onboarding Dra. ~10-15h adicionais.

---

## Sequência sugerida de execução

```
Dia 1 (Rafael):
  - Resolver Railway deploy (#1)
  - Domínio + DNS (#4) — inicia propagação
  - Sentry DSN (#2.1)
  - Email Resend (#6)

Dia 2 (Rafael):
  - Backup DB (#3) com R2
  - Cookie consent gating GA (#2.3)
  - Bootstrap 2FA local + valida (#2)

Dia 3 (Dra+Rafael):
  - Submeter WhatsApp templates Meta (#2.2)
  - Revisão termos privacidade (#5)
  - Setup procedimentos/preços/horários (#2.5) — começo

Dia 4-5:
  - Smoke tests E2E (#2.4)
  - Onboarding Dra com guia (#3.1)
  - Backup .env (#3.2) + plano comunicação fallback (#3.3)

Dia 6-7:
  - WhatsApp templates aprovados (espera Meta) — ativa
  - Soft launch com 5-10 pacientes conhecidos
  - Monitor Sentry/logs

Semana 2:
  - Lançamento público (instagram/site)
  - Acompanhar métricas
```

Custos de referência (CUSTOS.md): **R$ 130-250/mês** com 100 pacientes.
