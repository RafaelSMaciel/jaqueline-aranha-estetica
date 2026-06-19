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

### Front — Onda 2 (site público) — plano wizard [`plans/onda2-wizard.md`](plans/onda2-wizard.md)
Abordagem wizard: pragmática (re-skin Tailwind + externalizar JS + corrigir 2 bugs; sem reescrita Alpine).
- [ ] Home (hero D2 + seções) — **gated nas cores/fotos reais**
- [x] **Wizard booking ✅** (W1-W5, verificado ao vivo no browser): W1 JS externalizado CSP-safe · W2 re-skin base_v2+Tailwind/tokens (Bootstrap fora, dark ok) · W3 a11y teclado (proc-card/cal-day→`<button>` focável) · W4 estado em sessionStorage + re-hidrata no reject (bug B: usuário volta ao step 3 com tudo, vê o erro) · W5 guardas de regressão. 185 testes. Contrato de booking preservado.
- [ ] Catálogo serviços (faciais/corporais/produtos/especialidades/detalhe)
- [ ] Páginas de marca (quem somos/equipe/galeria/depoimentos)
- [ ] Auto-serviço (meus agendamentos/reagendar/confirmar presença)
- [~] Formulários públicos — **contato ✅** (base_v2 + form POST funcional/email/PRG/rate-limit + a11y labels/iframe title — **bug alta da auditoria resolvido**, verificado browser); pendente lista espera/anamnese/pesquisa/nps
- [x] **Cauda/legal ✅** (politica_privacidade, termos_uso, lgpd_meus_dados+unsubscribe, agendamento_sucesso, nps/pesquisa_obrigado, lista_espera_sucesso → base_v2; DSAR form preservado) — falta 404
- [x] **Auth ✅** (login + 4 telas reset → `base_auth` mínima Tailwind/tokens, sem chrome; contrato+erros preservados, verificado browser)

### Front — Onda 3 (admin PWA)
- [ ] Lista/tabela + filtros (15 telas: agendamentos, clientes, …)
- [ ] Detalhe/registro (cliente_detalhe, prontuario_detalhe, …)
- [ ] Formulário criar/editar (usuario_form, anamnese_form, …)
- [ ] Dashboard (overview, dashboard_financeiro)
- [ ] Calendário (calendar)
- [ ] Auth/2FA (2fa_challenge, 2fa_setup)

### Front — Onda 4 (e-mails)
- [ ] 10 templates de e-mail no novo visual

### Regras de negócio (registry — spec [`specs/regras-negocio-registry.md`](specs/regras-negocio-registry.md))
- [x] Revisão das regras + referências de mercado + catálogo (2026-06-14)
- [x] Spec aprovada (fonte única + 5 simplificações; escopo = simplificar)
- [ ] Implementação 5.1 — Registry (fonte única; itens 4+5)
- [ ] Implementação 5.2 — Estados + auto-aprovação (itens 1+2)
- [ ] Implementação 5.3 — Identidade/sessão do cliente (item 3)

---

_Última atualização: 2026-06-14 — + spec de regras de negócio (registry)._
