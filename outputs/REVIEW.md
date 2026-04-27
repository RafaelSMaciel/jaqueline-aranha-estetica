# Review do Projeto — shivazen-app (Dra. Jaqueline Aranha)

Data: 2026-04-27 · Branch: `claude/quizzical-grothendieck-3d4b34` (HEAD = `c29ce88`, sincronizado com `origin/main` e `origin/dev`)

---

## 1. Segurança

### OK
- `DJANGO_SECRET_KEY` obrigatória em prod ([base.py:34-37](shivazen/settings/base.py:34))
- `DEBUG=False` em prod ([prod.py:6](shivazen/settings/prod.py:6))
- HSTS 1 ano + preload + includeSubDomains ([prod.py:16-18](shivazen/settings/prod.py:16))
- Cookies `Secure` em prod ([prod.py:12-13](shivazen/settings/prod.py:12))
- `SECURE_PROXY_SSL_HEADER` correto p/ Railway ([prod.py:19](shivazen/settings/prod.py:19))
- CSP com nonce per-request ([middleware.py](app_shivazen/middleware.py))
- `django-axes` (lockout) + `django-ratelimit` em endpoints sensíveis (login, OTP, admin POSTs)
- `CRON_TOKEN` via `X-Cron-Token` em endpoints cron ([cron.py:30](app_shivazen/views/cron.py:30))
- Rate limits granulares: login `5/m` por user + `10/m` por IP, promoções `5/h`

### Pendências

**🔴 [CRÍTICO] 2FA admin NÃO implementado**
Grep `django.otp|two_factor|django_otp|totp|2fa` retorna **0 hits**. Memória `S164` (23/abr) afirmava ter implementado — **falso positivo**. Roadmap correto: pendente.
- **Esforço:** ~8h (django-two-factor-auth)
- **Impacto:** alto — admin sem 2FA é maior vetor risco

**🟡 CSP `'unsafe-inline'` em script-src + style-src** ([middleware.py:45,55](app_shivazen/middleware.py:45))
TODO declarado. Migrar templates legados pra nonce/hash exclusivo.
- **Esforço:** M (12-20h)
- **Impacto:** médio — XSS exploitable se inline injetado

**🟡 `SECURE_SSL_REDIRECT` opt-out via `USE_HTTPS=False`** ([prod.py:9](shivazen/settings/prod.py:9))
Default `True`. Deixar fixo `True` em prod, sem env var.

**🟢 Secrets no histórico:** scan limpo. Apenas `senha123`/`admin123` em fixtures de teste — aceitável.

**🟢 LGPD:** completo (services/lgpd.py, DSAR endpoints, anonimização agendada via Celery, consent granular por canal).

---

## 2. Confiabilidade / Operação

### OK
- Healthcheck `/healthz/` ([railway.json:10](railway.json:10))
- Sentry SDK wired ([base.py:16-30](shivazen/settings/base.py:16))
- Logging estruturado JSON em prod ([base.py:282+](shivazen/settings/base.py:282))
- Cron HTTP endpoint substituiu Celery Beat (free tier)

### Pendências

**🔴 Backups DB ausentes**
Sem snapshot Railway agendado nem dump S3. Risco perda permanente.
- **Ação:** Railway Pro snapshot + cron-job.org chamando endpoint dump → S3
- **Esforço:** S (2-4h)

**🔴 Sentry DSN não confirmado em prod**
Código pronto. Variável `SENTRY_DSN` precisa estar setada no Railway dashboard.
- **Ação:** criar projeto sentry.io, copiar DSN, setar no Railway
- **Esforço:** S (15min)

**🟡 Auto-deploy Railway desconectado / em branch errada**
Push em `main` E `dev` (ambos `c29ce88`) — site não atualizou após 5min. Confirmar dashboard.

**🟡 Sem rollback documentado**
Sem `RUNBOOK.md`. Em incidente: como reverter? Trocar Railway pra deploy anterior é UI manual.

**🟡 `seed.py` raiz não existe**
Comando `python manage.py seed` falha silencioso ([seed.py:32-38](app_shivazen/management/commands/seed.py)). Memória S172 cita 23 procedimentos seed — perdido em algum branch/reset.

---

## 3. Performance

**🟢 N+1 candidates baixo:** apenas 2 hits suspeitos
- [admin_management.py:119](app_shivazen/views/admin_management.py:119) — `for p in Preco.objects.filter(...)` — verificar se está dentro de outro loop
- [prontuario.py:79](app_shivazen/views/prontuario.py:79) — `for r in ProntuarioResposta.objects.filter(...)`
- **Ação:** adicionar `select_related`/`prefetch_related` se confirmado

**🟡 Cache queries:** sem `cache.get_or_set` em queries pesadas (procedimentos públicos, horários disponíveis). Free Redis Railway disponível.

**🟡 Imagens:** sem WebP/AVIF, sem `srcset`. Roadmap deferido.

**🟢 Bundle estáticos:** WhiteNoise serve compressed; OK pra escala atual.

---

## 4. UX / Front-end

**🟢 PWA SW estratégia híbrida correta** ([sw.js](app_shivazen/templates/pwa/sw.js))
- HTML: network-first ✓ (não causa cache versão antiga)
- Static: stale-while-revalidate ✓
- Image: cache-first com TTL/limit ✓
- Bypass `/admin/`, `/painel/`, `/api/`, `/health` ✓
- `VERSION = 'v3'` — bump em deploy invalidate caches

**🟡 Versão antiga "Shiva Zen" no celular do usuário NÃO é SW** — é deploy Railway que não atualizou (item 2).

**🟢 Acessibilidade básica:** grep `<img` sem `alt=` em templates públicos = 0 hits. Bom.

**🟢 SEO:** `sitemaps.py` + `robots.txt` presentes. Verificar `og:*` tags em templates publico — usuário viu `og:site_name="Shiva Zen"`, vai atualizar pós-deploy.

**🟡 SW VERSION manual:** dev precisa lembrar bump `VERSION` em mudanças sensíveis. Considerar gerar via build hash.

---

## 5. Integrações

**🔴 WhatsApp templates Meta NÃO submetidos**
Code envia via `graph.facebook.com/v18.0`. Sem templates aprovados, mensagens fora da janela 24h falham. Bloqueador real.
- **Ação:** business.facebook.com → submeter templates `lembrete_d1`, `nps`, `confirmacao` (24-72h aprovação)

**🟢 Email sync** com spinner UX ([commit eb1427b](https://github.com/RafaelSMaciel/shivazen-app/commit/eb1427b)) — solução pragmática free tier.

**🟡 SMS Zenvia:** `SMS_MAX_POR_HORA=3` rate limit por telefone ([sms.py:34](app_shivazen/utils/sms.py:34)). Custo por mensagem — adicionar alarme se uso explodir.

**🟢 GA / pixel:** sem hits no grep — provável que ainda não tem ou está só em template head. LGPD exige consentimento prévio para tracking — verificar cookie banner gating GA.

---

## 6. Dívidas técnicas e housekeeping

**🟡 Branches `main` vs `dev` divergem historicamente**
Ambas agora em `c29ce88`. Decidir: única branch ou git-flow disciplinado.

**🟢 Tests:** 144 coletados (precisa `DJANGO_SETTINGS_MODULE=shivazen.settings.dev`). Sem `pytest.ini` / `pyproject.toml [tool.pytest]` — adicionar pra remover boilerplate.

**🟡 Migrations:** 18 arquivos. Squash candidato pós-MVP.

**🟡 Deps desatualizadas (críticas):**
- Django 5.2.1 → 6.0.4 (LTS jump)
- gunicorn 21.2 → 25.3 (várias releases)
- django-jazzmin 2.6 → 3.0
- redis 7.3 → 7.4
- requests 2.32 → 2.33
- Total: 28 outdated. Rodar `pip-compile` + testes antes upgrade.

**🟡 Warning Django 6:** `CheckConstraint.check` deprecated → migrar p/ `condition` ([sistema.py:34](app_shivazen/models/sistema.py:34)). Outros models também.

**🟢 README.md** decente. Considerar atualizar nome cliente (ainda diz "ShivaZen" como product name).

---

## 7. Próximos passos sugeridos (top 5)

| # | Ação | Esforço | Impacto |
|---|------|---------|---------|
| 1 | **Confirmar deploy Railway funcionando** (dashboard, branch, auto-deploy) | S | 🔴 Bloqueia todo deploy |
| 2 | **Backups DB** (Railway snapshot + dump S3) | S | 🔴 Risco existencial |
| 3 | **Sentry DSN** ativar em prod (env var Railway) | S | 🔴 Visibilidade incidentes |
| 4 | **2FA admin** (django-two-factor-auth) | M | 🔴 Segurança admin |
| 5 | **WhatsApp Meta templates** submeter aprovação | M | 🟡 Desbloqueia D-1/NPS |

Bonus rápidos:
- Atualizar SW `VERSION` em cada deploy crítico
- Adicionar `pytest.ini` com `DJANGO_SETTINGS_MODULE`
- Recriar `seed.py` raiz com 23 procedimentos catalogo

---

## Anexo: comandos úteis

```bash
# Confirmar deploy Railway
railway login && railway status && railway logs --tail 100

# Rodar tests local
DJANGO_SETTINGS_MODULE=shivazen.settings.dev pytest app_shivazen/tests/

# Bump SW cache
sed -i "s/VERSION = 'v3'/VERSION = 'v4'/" app_shivazen/templates/pwa/sw.js

# Cleanup branches duplicadas (após decidir estratégia)
git push origin --delete dev   # OU manter ambas e definir gitflow

# Deps update seguro
pip list --outdated > outdated.txt
# revisar uma a uma
```
