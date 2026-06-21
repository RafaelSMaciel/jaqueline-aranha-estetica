# Arquitetura & Checkpoint — Shiva Zen

> **Documento vivo / check-point.** Sempre que concluir qualquer item, marque `[x]` na
> seção **6. Progresso** com commit/data. Objetivo: não nos perdermos entre banco,
> front-end público e app admin PWA.
>
> Specs detalhadas: banco em [`docs/specs/remodelagem-banco-v2.md`](specs/remodelagem-banco-v2.md);
> regras de negócio em [`docs/REGRAS-DE-NEGOCIO.md`](REGRAS-DE-NEGOCIO.md).

---

## 1. Visão geral do produto

Sistema de gestão para clínica de estética (Jaqueline Aranha, biomédica esteta).
Backend **Django 5.2 + PostgreSQL** (prod Railway) servindo **duas superfícies**:

```
            ┌──────────────── Django (backend + templates) ────────────────┐
            │                                                               │
   SUPERFÍCIE A — SITE PÚBLICO                 SUPERFÍCIE B — ADMIN PWA
   marketing + agendamento online             app instalado da Jaqueline/recepção
   (a cliente vê)                             (operação: agenda, clientes, financeiro)
```

Decisão central do redesign: **um único design system (tokens) → duas skins** —
site (editorial) e admin PWA (denso/app-shell) compartilham marca, divergem em componentes.

---

## 2. Banco de dados — CONCLUÍDO (remodelagem v2.1)

Branch `remodelagem-v2`. Migrations `0027`–`0038`. Validação: 163 testes Django + 16 pytest
verdes em cada fase; `makemigrations --check` sem drift.

### Entregue
- **50 → 33 tabelas de domínio** (−34%). Alvo final 32 após Fase 7 (agenda 3→2, adiada).
- **Naming PT-BR** singular consistente; renomeações de 8 tabelas + ~15 colunas.
- **Cortes:** 8 tabelas mortas, RBAC-teatro (perfil/funcionalidade → `usuario.papel`),
  workflow engine (deferido), EAV do prontuário → JSONB, OTP unificado hasheado.
- **Invariantes movidas para o banco:**
  - Anti double-booking: `EXCLUDE USING gist` em `atendimento` (constraint `excl_atendimento_sobreposicao`).
  - UNIQUE parciais: telefone/email/cpf ativos, termo vigente, preço por vigência, espera ativa, consumo por atendimento.
  - CHECK: nps 0–10, promoção desconto 0–100 XOR preço, carteira saldo ≥ 0, telefone/cpf regex, OTP tentativas ≤ teto.
  - Trigger ledger imutável em `movimento_carteira` (append-only).
  - Collation ICU pt-BR em colunas `nome`; `COMMENT ON TABLE` nas tabelas centrais.

### Fases (status no spec do banco)
| Fase | Conteúdo | Status |
|---|---|---|
| 1a–1d | Cortes (mortas, OTP, RBAC, workflow) | ✅ |
| 2a–2c | Renames coluna/tabela + `cliente.nome` | ✅ |
| 3 | Constraints integridade + telefone canônico | ✅ `0034` |
| 4 | ExclusionConstraint booking | ✅ `0035` |
| 5 | EAV → JSONB prontuário | ✅ `0036` |
| 6 | Aceite unificado + trigger + collation + comments | ✅ `0037`/`0038` |
| 7 | Agenda 3→2 (`agenda_horario` + `agenda_excecao`) | ⏳ PENDENTE — PR dedicada (mexe no cálculo de slots) |

### Gaps achados na auditoria (a fechar — não bloqueiam merge)
- **Média (3):** falta UNIQUE "1 retorno por origem"; falta CHECK `jsonb_typeof(respostas_extras)='object'`; falta trigger `atualizado_em`. → 1 migration corretiva (`0039`).
- **Baixa (4):** `db_comment` só 13/33 tabelas; collation só 3/8 colunas `nome`; docs README/PROJECT desatualizados; doc-drift menor (spec:75 rename fantasma, label `configuracao_sistema` em 4 views).
- **Operacional:** migrations sem `lock_timeout`/`NOT VALID` — adotar na Fase 7 + futuras.

---

## 3. Front-end — A FAZER (redesign + padronização)

### Direção visual escolhida
**Direção 2 — "Clínico-premium confiança"**: serif clássica (display) + sans limpa,
luz/off-white, dourado da marca como sistema, barra de credencial, trust tokens,
hierarquia que afunila para o agendamento. (Concepts em `static/_design_tmp/` — temporário, apagar após decisão.)

### Stack alvo (substitui o template comprado)
| Camada | Hoje | Alvo |
|---|---|---|
| Interatividade servidor | jQuery (morto) + reload | **HTMX 2.x** |
| Estado UI local | DOM na mão | **Alpine.js 3** |
| CSS | Bootstrap 232KB + main.css 6996 linhas + 26 `<style>` inline | **Tailwind CSS v4** (1 token config) |
| Componentes | monolitos + duplicação | **django-cotton** |
| Ícones | bootstrap-icons + fontawesome (2 libs) | **1 set SVG inline** |
| Build | assets soltos | **Vite + django-vite** (ou Tailwind CLI) |
| Mantém | Whitenoise, CSP+nonce, CSRF, server-render, PWA | idem |

**Por quê:** um `tailwind.config` alimenta site **e** admin — trocar cor da marca = 1 linha.
Remove a causa de ~30 dos 46 achados da auditoria de front (sem build→não poda; sem
estado→wizard quebra; sem componentes→monolito; `innerHTML`→XSS; inline→CSP fraca).

### Escopo — 67 telas, mas ~16 arquétipos
| Superfície | Telas | Arquétipos únicos |
|---|---|---|
| Site público (`estrutura/base`) | 37 | ~10 |
| Admin PWA (`painel/base`) | 30 | ~6 |
| E-mails (`email/base_email`) | 10 (+1 base) | 1 |
| **Total** | **67 telas + 10 e-mails** | **~16 padrões + 2 cascas** |

Arquétipos públicos: home, wizard booking, catálogo de serviço, página de marca,
auto-serviço cliente, formulário público, artigo/legal, sucesso/obrigado, auth, erro.
Arquétipos admin: **app-shell PWA**, lista/tabela+filtros, detalhe/registro, formulário,
dashboard, calendário, auth/2FA.

### Achados da auditoria de front a corrigir junto (resumo)
- **Alta:** form de contato quebrado (view ignora POST); stored-XSS no toast (`innerHTML`);
  wizard cards = `<div>` sem teclado (WCAG); wizard perde estado em rejeição; hero LCP lazy+AOS.
- **Média/baixa:** jQuery morto; swiper/glightbox em 31/32 págs sem usar; 2 libs de ícone;
  5 fontes (2 mortas); contraste do dourado falha AA; 3 h1 na home; cookie-banner órfão;
  hreflang /en /es 404; CDN sem SRI; `window.confirm()` 21×; 2 dark modes desconectados.

---

## 4. Ordem de execução (ondas)

**Onda 0 — Pré-requisitos** (destrava o resto): cores reais da marca, referências, fotos reais, decisão final de stack (A reskin / B migração — recomendado **B**).

**Onda 1 — Fundação** 🔴: token system (Tailwind) + casca do site (header/footer) + app-shell do PWA (bottom-nav, header, offline) + biblioteca de componentes (botões, cards, forms, tabela, badges, toasts). *Destrava as 67 telas.*

**Onda 2 — Site público** 🔴: home + wizard booking (corrigindo a11y/estado) → serviços/marca → cauda (sucesso/legal/auth).

**Onda 3 — Admin PWA** 🔴: 6 arquétipos (tabela, detalhe, form, dashboard, calendar, auth) → 30 telas cascateiam.

**Onda 4 — E-mails** 🟡: 10 templates no novo visual.

**Transversal — Quick wins da auditoria** (cabe na Onda 1): contato POST, toast `textContent`, jQuery fora, hero LCP, fontes, cookie-banner, hreflang, SRI.

---

## 5. Decisões fixadas (ADR-lite)

| # | Decisão | Razão |
|---|---|---|
| D1 | Backend Django server-render **mantido** | Já remodelado e sólido; SEO grátis; 1 mantenedora |
| D2 | **Não vira SPA** (HTMX+Alpine) | Evita 2ª stack/API duplicada; conversão+SEO sem Node-SSR |
| D3 | **Tailwind** (não Bootstrap) | 1 token config p/ site+admin; mata 2 dark modes/tokens duplicados |
| D4 | Direção visual **"2 — Clínico-premium"** | Confiança/segurança = o que estética vende; converte p/ booking |
| D5 | Admin redesenhado como **PWA app-shell** | É app instalado; precisa UX de app, não tabela desktop |
| D6 | Fase 7 do banco (agenda) = **PR dedicada** | Mexe no cálculo de slots (área crítica) |

---

## 6. Progresso (marcar ao concluir)

### Banco
- [x] Fases 1–6 (migrations `0027`–`0038`) — branch `remodelagem-v2`
- [ ] Migration corretiva `0039` (UNIQUE retorno + CHECK jsonb + trigger atualizado_em)
- [ ] Doc-drift: README/PROJECT, spec:75, label `configuracao_sistema` (4 views)
- [ ] Fase 7 — agenda 3→2 (`agenda_horario` + `agenda_excecao`)
- [ ] Merge `remodelagem-v2` → `main` (deploy roda as migrations)

### Front — Onda 0 (pré-requisitos)
- [ ] Cores reais da marca recebidas
- [ ] Referências visuais recebidas
- [ ] Fotos reais (clínica/profissional/procedimentos)
- [ ] Decisão final stack (A reskin / **B migração**)

### Front — Onda 1 (fundação) — spec [`specs/fundacao-front-design.md`](specs/fundacao-front-design.md) ✅ · plano [`plans/fundacao-front-fatia1.md`](plans/fundacao-front-fatia1.md)
Decisões: Tailwind v4 + HTMX + @alpinejs/csp (F8) + cotton + Vite; dark opcional/padrão claro; lean incremental.
- [x] **Fatia 1 — branch `front-fundacao`** (NÃO mergeada; sem push): toolchain Vite+Tailwind · django-vite+cotton · tokens light/dark · tema via cookie sem FOUC + toggle Alpine CSP-safe · casca `base_v2` + header/footer · 6 componentes (botão/card/campo/badge/toast/modal) · `/v2-prova` (DEBUG-only). **Verificada ao vivo no browser** (light/dark, Alpine, assets). 182 testes + guardas de regressão. Verificação visual pegou 4 bugs de pipeline que os testes não viam (static_url_prefix, @source, CSP style dev, seletor `[data-theme=escuro]`).
- [ ] **T9 — calibração de marca:** trocar paleta placeholder + fontes self-hosted — **gated nas cores reais** (pendência do dono)
- [ ] App-shell PWA admin (nav, bottom-nav mobile, offline, install) — Onda 3
- [ ] Componentes do wizard (stepper, slot, calendário) — Onda 2
- [ ] Quick wins auditoria: contato POST · toast textContent · jQuery fora · hero LCP · fontes · cookie-banner · hreflang · SRI

### Front — Onda 2 (site público) — ✅ COMPLETA — plano wizard [`plans/onda2-wizard.md`](plans/onda2-wizard.md)
**ZERO templates ainda em `estrutura/base.html` (Bootstrap morto).** Todo o site público + profissional migrado pra base_v2/Tailwind/tokens. 200 testes, sem drift, verificado no browser.
- [x] **Home ✅** (rebuild D2: 1 h1 [era 3], hero `fetchpriority` sem lazy [LCP fix], 7 seções, FAQ/loops preservados)
- [x] **Wizard booking ✅** (W1-W5): JS externalizado CSP-safe · re-skin base_v2 · a11y teclado (cards/dias→`<button>`) · estado sessionStorage + re-hidrata no reject (bug B) · guardas regressão. Contrato preservado.
- [x] **Serviços ✅** (faciais/corporais/produtos rebuild padrão compartilhado · especialidades [tabs CSS-only CSP-safe] · servico_detalhe [JSON-LD preservado] — verificado browser)
- [x] **Páginas de marca ✅** (quem_somos, equipe [loop prof], depoimentos [swiper→grid], galeria, promoções [loop] — rebuild base_v2)
- [x] **Auto-serviço ✅** (meus_agendamentos 894L: 3 steps OTP + lista + modal · reagendar · confirmar_presença — 2 onclick→addEventListener, modal a11y, toast textContent)
- [x] **Formulários públicos ✅** (contato [bug alta: POST/email/PRG], lista_espera, nps_web, pesquisa+anamnese — contratos preservados)
- [x] **Cauda/legal ✅** (politica_privacidade, termos_uso, lgpd_*, *_sucesso/_obrigado, termo_assinatura/obrigado, 404 → base_v2; DSAR form preservado)
- [x] **Auth ✅** (login + 4 reset → `base_auth` mínima, sem chrome) · **Profissional ✅** (agenda [5 onclick→0, 3 forms] · anotar)
- [ ] embed.html (widget standalone — deixado minimal, sem chrome do site)

**Achados da auditoria de front resolvidos na Onda 2:** contato quebrado · wizard a11y teclado + perda de estado · home 3h1→1 + hero LCP · jQuery/Bootstrap/AOS/purecounter/swiper fora do público · ~todos onclick inline→addEventListener (CSP).

**Limpeza do público ✅:** removidos 9 arquivos mortos (`estrutura/base.html`+`baserodape`, partials head/cabecalho/rodape/toasts/cookie_consent/mobile_nav, `static/css/main.css`) · bordas de form control no token (`@layer base` — corrige currentColor em dark, verificado). **Pendente:** calibrar paleta real (T9) · **agora desbloqueado** (admin 30/30): remover `painel/base.html` + `static/css/base.css` + `static/vendor/` + `partials/_empty_state.html` (verificar 0 refs antes).

### Front — Onda 3 (admin PWA) — ✅ COMPLETA (30/30 telas)
**ZERO templates admin em `painel/base.html`.** Todo o painel migrado pra base_v2/Tailwind/tokens/Alpine CSP. 216 testes, verificado no browser (drawer, modais, charts, FullCalendar, busca instantânea).
- [x] **App-shell `painel/base_v2.html` ✅** (sidenav Alpine CSP drawer `classeDrawer()`, Vite bundle, tokens, **sem jQuery/Bootstrap**, tema unificado c/ público — mata os 2 dark modes; 18 nav links; drawer verificado ao vivo)
- [x] **Lista/tabela + filtros ✅** (clientes, profissionais, notificacoes, lista_espera, auditoria, anamneses, bloqueios, usuarios, termos, excecoes, prontuario, agendamentos — loops/forms/paginação/bulk preservados; busca instantânea `admin-search.js` bundled em app.js)
- [x] **Detalhe/registro ✅** (cliente_detalhe [timeline+status dinâmico via `<style nonce>` mínimo], prontuario_detalhe [modal anotação via fetch CSP-safe], anamnese_respostas, termos_compliance, editar_profissional)
- [x] **Formulário criar/editar ✅** (usuario_form, anamnese_form, cadastro_profissional [day-toggle peer-checked], configuracoes [data-confirm global], branding [sync hex ao vivo])
- [x] **CRUD c/ modais ✅** (procedimentos/promocoes/pacotes — modais Bootstrap→Alpine CSP `x-data=modal`/`pacoteCriar`; edit/create/venda por item + FAB; `data-confirm` global p/ excluir)
- [x] **Dashboard ✅** (overview [Chart.js via CDN — host na CSP; status via change delegado + reload], dashboard_financeiro [cards+tabelas])
- [x] **Calendário ✅** (calendar — FullCalendar 6.1 via CDN; JS já CSP-safe; estilos FC/modal em `<style nonce>`) · **2FA ✅** (2fa_challenge, 2fa_setup) · **branding/config ✅**
- [x] **`_status_badge.html` rebuild ✅** (Tailwind + SVG inline, sem Bootstrap Icons — corrige badge quebrado em meus_agendamentos)
- **Padrões CSP novos em `app.js`:** `anotacaoModal` (fetch), `pacoteCriar` (clonar item), `data-confirm` global delegado, import `admin-search.js`. Toda FontAwesome/Bootstrap-icons → SVG inline; onchange/onclick inline → delegados.
- **Bug pré-existente corrigido:** `views/pacotes.py` usava `Count('pacotecliente')` (rename do remodel 0032 quebrou a página) → `Count('comprapacote')`.

### Front — Onda 4 (e-mails) — ✅ passe de consistência (visual, não-cor)
**Constatação:** os 10 e-mails NÃO eram código velho — todos herdam `email/base_email.html` (shell email-safe: tabelas, inline, dark-mode media query, responsivo, MSO). Visual gold/serif já coeso. Auditoria paralela (10 agentes) → passe de consistência **não-cor** (cor final fica pro T9):
- [x] **dark-mode:** aplicadas classes `.text-main/.text-soft/.text-muted/.card-inner` da base nos textos/cards que tinham só cor inline (antes: texto escuro-sobre-escuro ilegível no dark). Classes só existem no `@media dark` → light idêntico, **zero mudança de cor**.
- [x] **acentos** PT-BR corrigidos; **header_sub** contextual (cancelamento/nps/termos/aprovacao); `<p>`→`<h1 hero-title>`; `<div>` card→`<table role=presentation>`; botão com fallback Outlook (`background-color` sólido + pill/shadow); `color:white`→`#ffffff`; otp media-query mobile corrigida. Commit `b8cc97e`. Verificado: hex/vars/hrefs idênticos, 10/10 renderizam, 216 testes.

**✅ Achados de BACKEND de e-mail (correção/segurança — CORRIGIDOS, commit `379420e`):**
1. **Unsubscribe** — `base_email.html` checava `unsubscribe_url` (nunca setado) → agora usa `unsub_url` (o que `utils/email.py` injeta); `promocao.html` tinha link duplicado/quebrado p/ `/unsubscribe/` (404) → removido (base renderiza o canônico `/lgpd/unsubscribe/`).
2. **Promo XSS + e-mail-em-branco** — `tasks.py:job_promocao_mensal` agora roteia por `enviar_promocao_email` (ganha bleach anti-XSS + header List-Unsubscribe RFC 8058); `enviar_promocao_email` passa contexto **flat** (template usa `{{ nome }}` top-level; antes embrulhava em `{'dados':...}` → branco) + novo param `assunto`. Verificado via locmem: `<script>` removido, vars resolvem, header+link canônicos, subject custom preservado.
3. **Não-issue:** `{{ site_url }}/path/` é seguro (`SITE_URL.rstrip('/')`).

### Regras de negócio (registry — spec [`specs/regras-negocio-registry.md`](specs/regras-negocio-registry.md))
- [x] Revisão das regras + referências de mercado + catálogo (2026-06-14)
- [x] Spec aprovada (fonte única + 5 simplificações; escopo = simplificar)
- [ ] Implementação 5.1 — Registry (fonte única; itens 4+5)
- [ ] Implementação 5.2 — Estados + auto-aprovação (itens 1+2)
- [ ] Implementação 5.3 — Identidade/sessão do cliente (item 3)

## Backend — Auditoria SWE (3 ondas)
Auditoria multi-agente (10 módulos) → **149 achados: 31 alta, 71 média, 47 baixa**. Detalhe completo no histórico git (commit `19b8e67`, doc removido). Testes andam junto com cada fix.

**✅ Onda 1 — 8 alta (commits `18eac8f`→`be20c98`, 213 testes):**
- [x] `prontuario_consentimento`: `Count('aceiteprivacidade')` (model deletado 0037) → `'aceites'` (página 500)
- [x] `admin_atualizar_status`: burlava FSM → métodos do model (valida transição + publica eventos)
- [x] `lista_espera_publica`: telefone cru no get_or_create → `normalizar_telefone`
- [x] `admin_2fa_verify`: +`@ratelimit 5/m` (brute-force TOTP, fora do axes)
- [x] `admin_2fa`: open-redirect `?next=` → `url_has_allowed_host_and_scheme`
- [x] `whatsapp_webhook` (Meta): +handshake GET `hub.challenge` (Meta nunca verificava)
- [x] `fidelidade.estornar_cashback`: closure late-binding (eventos com pk do último) → bindado
- [x] `pacotes` criar/editar: +`transaction.atomic` (pacote órfão em falha parcial)

**✅ Onda 2 — mecânica (9 alta, commits `f22a08f`→último, 213 testes):**
- [x] quota SMS: `pode_enviar` só checa; novo `registrar_envio` (atomic `cache.add+incr`) só após sucesso
- [x] débito de pacote no `signals.py`: `transaction.atomic` + `select_for_update` (over-debit/TOCTOU)
- [x] comissão: guard de idempotência total por atendimento (anti double-pay pós-ESTORNADA)
- [x] `datetime.fromisoformat`→`make_aware` se naive (booking_public ×2 + reagendar; TypeError 500)
- [x] corrida de slot: `IntegrityError`→erro de domínio nos 2 services de agendamento
- [x] NPS job: `distinct()` + status Notificação reflete envio (ENVIADO/FALHOU, sem órfã; FALHOU re-tentável)
- [x] `verificar_telefone`: cooldown `pode_reenviar` antes do SMS (anti-abuso/custo)

**🔵 Onda 3 — decisões de arquitetura + itens entrelaçados (PENDENTE do dono):**
1. `AgendamentoService` **duplicado** (legado `agendamento.py` exportado vs novo `agendamento_service.py` c/ Command/eventos) — qual é canônico?
2. `domain/` event-bus = handlers stub (lógica real no signal) — remover camada OU migrar lógica pra ela?
3. Senha por e-mail (texto plano) na criação de usuário → trocar por link de definição (como reset)?
4. Deploy: `settings/__init__` cai em `dev` por fallback + `Procfile` usa `django_celery_beat` não instalado · `cron.run_job` síncrono e `sms` `time.sleep` (dependem de ter worker Celery).
5. `get_horarios_disponiveis` fat-model (110 linhas) → extrair p/ `SlotService` · `decorators_2fa.staff_otp_required` dead (2 mecanismos 2FA paralelos).
6. **booking_public OTP-gate por telefone** — exigir OTP tb p/ cliente identificado por telefone (hoje só por e-mail). Entrelaçado com o fluxo OTP e-mail/SMS + front; precisa traçar com cuidado e testar o happy-path de booking.
7. **`job_limpeza`** marca PENDENTE→FALTOU, mas a FSM não permite essa transição — decidir a regra (PENDENTE vencido deve virar FALTOU? CANCELADO?) antes de usar `marcar_falta()`.
8. **`Cliente.delete()`** faz hard-delete (sem `SoftDeleteMixin`) — override p/ soft-delete muda semântica de cascatas/admin; avaliar impacto.

---

### Backend — progresso de correção
- **✅ 20 alta** (Onda 1: 8 · Onda 2: 9 · Onda 3 seguras: domain stubs, Procfile beat, senha-por-link).
- **✅ 71 média** (workflow particionado em 10 agentes, arquivos disjuntos; verificado: check + 213 testes + migrations; migração 0039 [validators/check]). Um teste fortalecido expôs bug real → +guard de data-passada no booking. ~6 médias puladas com motivo (refactor grande/decisão: `__str__` PII testado, métodos da Carteira, retry policy de e-mail).
- **⏳ ~7 alta + 47 baixa pendentes** (Onda 3 — refactor grande/decisão):
  1. `AgendamentoService` **duplicado** (legado+novo, ambos vivos) — escolher canônico + migrar callers (arriscado p/ booking).
  2. `get_horarios_disponiveis` **fat-model** (110 linhas) → extrair `SlotService` (refactor do core de slot).
  3. `Cliente.delete()` faz **hard-delete** → override soft (muda semântica de cascatas/admin).
  4. **booking_public OTP-gate por telefone** (entrelaçado com fluxo OTP e-mail/SMS + front).
  5. `job_limpeza` marca PENDENTE→FALTOU mas a **FSM não permite** — decidir a regra.
  6. `settings/__init__` default `dev` por fallback (deploy: setar `DJANGO_ENV=prod`).
  7. `decorators_2fa.staff_otp_required` dead (middleware é o gate canônico).

---

_Última atualização: 2026-06-21 — Backend: 20 alta + 71 média corrigidas e verificadas (213 testes, migração 0039). Restam ~7 alta de refactor-grande/decisão + 47 baixa._
