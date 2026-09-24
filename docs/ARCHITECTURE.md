# Arquitetura & Checkpoint — Shiva Zen

> **Documento vivo / check-point.** Decisões (ADR-lite), estado de cada frente e as
> pendências **reais** (conferidas no código). Ao concluir algo, marque `[x]` na seção 6 com
> commit/data. Referência técnica completa em [`PROJECT.md`](PROJECT.md); banco em
> [`specs/remodelagem-banco-v2.md`](specs/remodelagem-banco-v2.md); front em
> [`specs/fundacao-front-design.md`](specs/fundacao-front-design.md); regras em
> [`REGRAS-DE-NEGOCIO.md`](REGRAS-DE-NEGOCIO.md).

---

## 1. Visão geral do produto

Sistema de gestão da clínica de estética Jaqueline Aranha (biomédica esteta). Backend
**Django 5.2 + PostgreSQL 18** (Railway), server-render, servindo:

```
            ┌──────────────── Django (backend + templates) ────────────────┐
            │                                                               │
   SUPERFÍCIE A — SITE PÚBLICO                 SUPERFÍCIE B — EQUIPE (PWA)
   vitrine + agendamento online               painel ADMIN (app-shell) +
   + "Meus agendamentos" + LGPD                portal do PROFISSIONAL
   (a cliente vê; identidade = celular)       (login + 2FA)
```

Um único design system (tokens Tailwind v4) → duas "peles": site editorial e painel denso.

---

## 2. Banco de dados — CONCLUÍDO (remodelagem v2.1 + auditoria pré-produção)

Migrations `0027`–`0046` (produção ainda na `0026`; o go-live aplica todas de uma vez —
runbook em [`PROJECT.md` §14](PROJECT.md#14-go-live--operação-runbook)).

- **50 → 34 tabelas de domínio** (33 na v2.1 + `prontuario_versao` na 0046). Alvo 33 após a
  Fase 7 (agenda 3→2).
- Naming PT-BR singular; RBAC → `usuario.papel`; workflow engine removido; EAV do prontuário →
  JSONB; OTP único hasheado.
- Invariantes no banco: EXCLUDE anti double-booking, UNIQUE parciais (telefone/e-mail/CPF
  ativos, termo vigente, preço por vigência, espera ativa, **1 retorno por origem**), CHECKs
  (NPS 0–10, promoção, carteira ≥ 0, formato de telefone/CPF, comissão 0–100%, JSON `NOT VALID`),
  triggers de imutabilidade (ledger da carteira, **prova de aceite, termo aceito, histórico do
  prontuário**), collation ICU `pt_br`, `COMMENT ON TABLE`.
- Upgrade de produção seguro: `migrate_atomico` (transação única + `lock_timeout`), migrations
  0034–0038 reescritas in-place antes de qualquer PG aplicá-las (dedup sem perda, datas de aceite
  preservadas, `%` no plpgsql), abortos explícitos com a lista de ids (0035, 0043).

| Fase | Conteúdo | Status |
|---|---|---|
| 1a–1d | Cortes (mortas, OTP, RBAC, workflow) | ✅ 0027–0030 |
| 2a–2c | Renames coluna/tabela + `cliente.nome` | ✅ 0031–0033 |
| 3 | Constraints + telefone canônico + dedup | ✅ 0034 |
| 4 | ExclusionConstraint booking | ✅ 0035 |
| 5 | EAV → JSONB prontuário | ✅ 0036 |
| 6 | Aceite unificado + trigger + collation + comments | ✅ 0037/0038 |
| — | Auditoria SWE (validators/CHECK) | ✅ 0039 |
| — | Auditoria pré-produção (depoimentos, PROTECT, contas demo, retorno único, prova de aceite, termo LGPD, histórico do prontuário) | ✅ 0040–0046 |
| 7 | Agenda 3→2 (`agenda_horario` + `agenda_excecao`) | ⏳ pendente — PR dedicada (mexe no SlotService) |

---

## 3. Front-end — CONCLUÍDO (Ondas 1–4)

Stack em uso: **Vite 6 + Tailwind v4 + Alpine.js (`@alpinejs/csp`) + django-cotton**. HTMX foi
avaliado e **removido** (nenhum `hx-*` no projeto e injetava `<style>` sem nonce). Bootstrap,
jQuery, AOS, Swiper, FontAwesome/Bootstrap Icons: fora. Ícones SVG inline.

- Site público 100% em `estrutura/base_v2.html`; painel 100% em `painel/base_v2.html`
  (app-shell com drawer Alpine, manifest próprio, busca instantânea).
- Componentes cotton atuais: `<c-botao>`, `<c-card>`, `<c-campo>` (atributo `class` mesclado via
  `c-vars`) + chrome (`cabecalho`, `rodape`, `mensagens`, `cookie_consent`). `<c-badge>`,
  `<c-toast>` e `<c-modal>` saíram — status usa `partials/_status_badge.html`, modais usam
  `x-data="modal"`, mensagens usam `cotton/chrome/mensagens.html`.
- CSP estrita: sem `unsafe-inline` em `script-src`/`style-src` e sem handlers inline (sem
  `script-src-attr`). Resta `style-src-attr 'unsafe-inline'` (~160 atributos `style=""` fora dos
  e-mails, onde a CSP não se aplica).
- Páginas de erro próprias (400/403/403_csrf/404/429/500), página pt-BR do bloqueio do axes.
- Service worker v7: cacheia só páginas públicas e estáticos. Rodapé com coluna "Para você".
- E-mails: 10 templates sobre `email/base_email.html` (email-safe, dark mode), sem cupom/desconto
  prometido sem mecanismo.

---

## 4. Ondas (histórico)

| Onda | Entrega | Status |
|---|---|---|
| 0 | Pré-requisitos (cores, fotos, stack) | ✅ cor da marca confirmada (#C9A84C); fotos reais em `static/assets/clinica/` |
| 1 | Fundação (tokens, casca, componentes, tema sem FOUC) | ✅ (T9 parcial — seção 6) |
| 2 | Site público (home, wizard, serviços, marca, auto-serviço, formulários, legal, auth) | ✅ |
| 3 | Painel/PWA (30 telas, 6 arquétipos) | ✅ |
| 4 | E-mails (consistência + dark mode) | ✅ |

O plano do wizard (`plans/onda2-wizard.md`) foi concluído e removido: o contrato preservado
(nomes de campos, ids `#step-1..3`, endpoints `api_dias_disponiveis`,
`api_horarios_disponiveis`, `solicitar_otp_agendamento`, `verificar_otp_agendamento`,
`confirmar_agendamento`) segue válido; `static/js/wizard.js` é JS estático (fora do Vite),
cards e dias são `<button>` (teclado) e o estado sobrevive a recusa do servidor
(`sessionStorage` + reidratação).

---

## 5. Decisões fixadas (ADR-lite)

| # | Decisão | Razão |
|---|---|---|
| D1 | Backend Django server-render mantido | Sólido, SEO grátis, 1 mantenedor |
| D2 | Não vira SPA; **Alpine CSP, sem HTMX** | Nenhuma tela precisava de parcial por HTMX; CSP estrita |
| D3 | Tailwind v4 (não Bootstrap) | 1 config de tokens para site + painel |
| D4 | Direção visual "Clínico-premium" (dourado #C9A84C, serif + sans) | Confiança; afunila para o agendamento |
| D5 | Painel como PWA app-shell | Uso como app instalado |
| D6 | Fase 7 do banco em PR dedicada | Mexe no cálculo de slots |
| D7 | **Todo agendamento público exige OTP do celular**; a identidade é o telefone, e-mail nunca é identidade | Anti-sequestro de cadastro; sem enumeração |
| D8 | Agendamento público nasce **PENDENTE** (clínica aprova); interno nasce AGENDADO | Estado atual do código; a auto-aprovação aprovada no registry (R5) não foi implementada — reabrir com o dono se quiser tirar o gargalo |
| D9 | **2FA obrigatório para ADMIN** (válvula `ADMIN_2FA_OBRIGATORIO=false`); PROFISSIONAL opt-in | Painel lê dado de saúde (LGPD art. 11/46) |
| D10 | Rotas do django-two-factor (`/account/*`) e ReDoc não publicadas | 2ª tela de login burlava o 2FA; ReDoc exige `unsafe-inline` |
| D11 | RECEPCAO sem login até existirem telas próprias | Papel sem superfície própria = acesso indevido ao painel |
| D12 | Prova de aceite imutável (SHA-256 do texto + trigger) e termo aceito imutável | Evidência legal (LGPD art. 8) |
| D13 | Histórico append-only do prontuário (`prontuario_versao`) | CFM 1.638/2002 — edição não apaga o anterior |
| D14 | `limpeza_status` não marca FALTOU sozinho | Falta é terminal e bloqueia online; só a equipe decide |
| D15 | Aniversário = felicitação sem desconto; promoção sem cupom; validade anunciada ≤ fim da promoção | Nada é prometido sem mecanismo que aplique |
| D16 | Deploy por Dockerfile + `migrate_atomico` no pre-deploy; **rollback = restaurar dump** | Upgrade 0026→0046 tudo-ou-nada; reverses não recuperam dados |
| D17 | Produção sem worker Celery (eager) + cron HTTP com `X-Cron-Token` | Custo; 1 serviço |
| D18 | `/anamnese/<token>/` e `/pesquisa/<token>/` mantidas, hoje sem produtor de link | Aguardando decisão do dono (remover × gerar o link); a ficha pública já exige consentimento art. 11 |
| D19 | Site só pt-BR (sem LocaleMiddleware); sessão da equipe 8h deslizantes | Sem traduções; 30 min fixos deslogavam no meio do atendimento |
| D20 | Faturamento = avulsos + venda de pacote; sessão de pacote não soma; comissão da sessão de pacote = `valor_pago / total de sessões` | Evita contar a receita duas vezes e inflar comissão |

---

## 6. Progresso (checkpoint)

### 6.1 Concluído na auditoria pré-produção (2026-09-23, `b5fd681..HEAD`, ~33 commits)

Três rodadas de auditoria multi-agente (achados em `audit_r1/r2/final`) + verificação de
fidelidade dos commits → ~460 correções em quatro ondas, com testes junto (1.144 testes; suíte
verde em SQLite e Postgres).

- **Deploy/infra:** Dockerfile multi-stage com Vite, `requirements.lock`, `migrate_atomico`,
  `bootstrap_admin`, healthcheck `/healthz/` que reprova sem o manifest, settings de prod
  coerentes (DEBUG forçado, e-mail dummy sem backend, SMS fail-closed, Celery eager sem retry
  síncrono, IP do cliente por `X-Real-IP`), system checks `aranha.W001–W009`, log do gunicorn
  sem tokens, CI com Postgres 18 bloqueante + build do front + `docker build`. Procfile removido.
- **Banco:** 0029 e 0034–0038/0045 corrigidas in-place; 0040–0047 (depoimentos com opt-in,
  PROTECT no histórico, contas demo desativadas, retorno único + CHECK jsonb, prova de aceite,
  termos LGPD v1.0 e SAUDE v1.0 sempre presentes, autoria na auditoria, `prontuario_versao`,
  triggers de imutabilidade, `valor_reembolsado` do pacote, promoção geral só percentual).
- **Auth:** 2FA obrigatório do ADMIN avaliado a cada request, `/account/*` fora, QR em SVG,
  "trocar de aparelho" exige código, códigos de backup aceitos no desafio, login do PROFISSIONAL
  em `/admin-login/` → `/profissional/`, logout só via POST + `Clear-Site-Data`, `no-store` nas
  áreas privadas, `no-referrer` nas rotas com token, rotação do link ICS.
- **Booking:** OTP preso ao telefone e exigido de todo agendamento (só celular), cota de SMS
  checada antes de gerar código, slots revalidados no servidor, preço com promoção da data,
  aceite LGPD + consentimento art. 11 + termos do procedimento no wizard, opt-ins nunca
  pré-marcados (desmarcar revoga), reagendamento preserva retorno e move a ficha, e-mail do
  cadastro substituído só com telefone provado.
- **Painel:** agendamento interno pela recepção, valor cobrado, link do termo por atendimento,
  "Termo pendente" + override auditado "Realizado sem termo aceito", pacotes na ficha do cliente
  (saldo, validade, cancelamento com reembolso auditado), pacote vendido com nome/itens
  congelados, bloqueio global "Todos os profissionais", alertas de saúde visíveis (portal,
  agendamentos, ficha), prontuário com acesso restrito por vínculo + trilha de leitura +
  histórico de versões, moderação de depoimentos, Branding sem upload de logo (Configuracao >
  env > padrão), Configurações só com `email_admin` e `prontuario_perguntas` sugeridas.
- **Serviços/LGPD:** canais falham fechado (nada marcado como enviado sem entrega), lista de
  espera em mecanismo único (signal → service, envio no `on_commit`, `notificado` só com
  entrega), retenção efetiva (fichas de pedido não realizado 90 dias, lista de espera vencida,
  exceções de saúde/pacote), esquecimento completo (fichas, NPS, pseudônimo na auditoria),
  descadastro mantém o D-1, sem WhatsApp de aniversário, webhooks robustos.
- **Site:** conteúdo honesto (sem promessas), FAQ revisado, depoimentos só com consentimento,
  vitrine = preço do agendamento, CTA "Agendar" vira WhatsApp sem SMS, SEO/PWA, aviso LGPD na
  lista de espera (e-mail digitado só na inscrição; cliente existente só com OTP).
- **Removidos:** HTMX, `/servicos/produtos/`, `<c-badge>/<c-toast>`, `NotificacaoService`,
  `EmailService`, `WhatsAppService`, `utils/cache`, `utils/structured_logging`, `exceptions.py`
  (hierarquia `DomainError`), `PacoteService`, eventos órfãos, `job_notificar_fila_espera`,
  `/ajax/verificar-telefone/`, `/ajax/buscar-procedimentos/`, `/ajax/buscar-horarios/`,
  `/painel/cancelar-agendamento/`, `/api/schema/redoc/`, `seed_jaqueline` (→ `seed`), constantes
  sem uso (`DESCONTO_ANIVERSARIO_PERCENTUAL`, `TTL_RESERVA_LISTA_ESPERA_MINUTOS`...).

### 6.2 Pendências reais (verificadas no código em 2026-09-23)

**Operação / go-live (fora do código)**
- [ ] Backup `pg_dump` + ensaio do upgrade 0026→0047 num Postgres 18 (runbook §14.a)
- [ ] Env no Railway (ADMIN_*, CRON_TOKEN, SITE_URL, ZENVIA_*, EMAIL_BACKEND + provedor,
      WHATSAPP_*, TURNSTILE_*, SENTRY_DSN, CLINIC_EMAIL) e remover `STATIC_ROOT`
- [ ] Provedor de e-mail: `django-anymail` já instalado — escolher o provedor (ex.: Resend) e
      definir `EMAIL_BACKEND` + chave + `DEFAULT_FROM_EMAIL` de domínio verificado (check W011)
- [x] Merge `front-fundacao` → `main` (fast-forward) — 2026-09-23
- [ ] Cron externo dos jobs (`/cron/run/<job>/` + `X-Cron-Token`, runbook)
- [ ] Pós-deploy: 2FA do ADMIN, apagar `ADMIN_PASSWORD`, `axes_reset`, Branding real, revisar
      termo LGPD v1.0, mesclar duplicatas logadas pela 0034, `VALIDATE CONSTRAINT` dos CHECKs
      `NOT VALID` da 0043

**Produto / código**
- [ ] **Fase 7** — agenda 3→2 (`disponibilidade_profissional` + `excecao_disponibilidade` +
      `bloqueio_agenda` → `agenda_horario` + `agenda_excecao`)
- [ ] **T9 — calibração de marca (parcial):** cor #C9A84C confirmada pelo dono e fontes
      definidas (Playfair Display + Lato); falta só o self-host das fontes (hoje Google Fonts,
      já declarado na política de privacidade) — opcional
- [x] ~~**Pacote cancelado some do faturamento:**~~ resolvido (0047 `valor_reembolsado`; receita = pago − reembolsado).
- [x] ~~**Janela do vínculo profissional ↔ prontuário**~~ resolvido (janela = max(60, `max_advance_dias`)).
- [ ] **Link da ficha de anamnese** no agendamento interno: o marcador "ficha pendente" existe,
      mas nada gera convite para `/anamnese/<token>/` (rev_painel-11; depende da decisão D18)
- [x] ~~**Purga LGPD × histórico do prontuário:**~~ resolvido (`candidatos_purga` retém quem tem `prontuario_versao`).
- [ ] **Cashback de indicação (parcial):** a ficha do cliente já registra "Indicada por"
      (crédito automático no 1º atendimento pago); falta fluxo de **uso** do saldo da carteira
- [x] ~~`Promocao.clean()` e promoção geral de preço fixo~~ resolvido (clean + CHECK na 0047).
- [x] ~~`migrate_atomico` sem aviso de falha~~ resolvido (escreve FALHOU + migration corrente).
- [x] ~~`tests/test_pg_ddl.py` sem os triggers da 0046~~ resolvido.
- [x] ~~Django admin: `ProntuarioVersao` somente leitura~~ resolvido (com trilha de leitura).
- [x] ~~Consentimento de saúde só em log~~ resolvido (termo SAUDE v1.0 + `AceiteTermo` com prova).
- [ ] Hardening opcional do 2FA: settings de teste com `ADMIN_2FA_OBRIGATORIO=False` e tirar a
      dependência do marcador `usuario_id` no `Enforce2FAMiddleware`
- [x] ~~Baixa: `isdigit()` aceita "²"~~ resolvido (`utils/parse.id_int` e nota NPS por regex).
- [ ] Baixa: remover `ListaEspera.token_reserva`/`expira_em` (legado sem uso); fallback
      `LEMBRETE/EMAIL` em `services/termos.Q_NOTIF_TERMO` pode sair depois da 0045 em prod;
      docstring de `utils/dois_fatores.verificar_token` ainda descreve o `--force` antigo
- [ ] Baixa: `painel/termo_link.html` oferece `mailto:` para o e-mail do cadastro (não verificado)
- [ ] `style-src-attr 'unsafe-inline'`: migrar ~160 atributos `style=""` (fora dos e-mails) para classes
- [ ] Registry de regras (spec 5.1) e sessão única de cliente verificado (5.3) — ver
      [`specs/regras-negocio-registry.md`](specs/regras-negocio-registry.md)
- [ ] RECEPCAO: telas próprias (hoje sem login)
- [ ] Decidido não fazer (por ora): trigger de `atualizado_em`; UNIQUE de regra de comissão ativa
      (quebraria o desempate por "mais recente"); consentimento de WhatsApp para marketing

### 6.3 Histórico anterior (resumo)

- **2026-06-11 → 06-14:** remodelagem v2.1 (0027–0038), revisão de regras, spec do front.
- **2026-06-14 → 06-21:** Ondas 1–4 do front; auditoria SWE de backend (149 achados: 31 alta,
  71 média, 47 baixa — todos endereçados; 0039). Decisões pendentes daquela auditoria foram
  resolvidas nesta rodada: `AgendamentoService` único, `domain/` só com os handlers reais,
  senha por link (nunca por e-mail), `DJANGO_ENV` explícito, SlotService extraído, 2FA num
  mecanismo só, OTP por telefone para todos, `Cliente.delete()` = soft delete, `limpeza_status`
  sem FALTOU automático.

---

_Última atualização: 2026-09-23 — auditoria pré-produção concluída; pendências da seção 6.2
conferidas no código._
