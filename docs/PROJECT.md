# Jaqueline Aranha Estética (Shiva Zen) — Documentação do Projeto

> **Fonte viva textual do projeto.** Reflete o código do branch `front-fundacao`
> (migrations `0001`–`0046`). Mudou model, rota, env var, job ou deploy? Atualize aqui
> no mesmo commit. Regras funcionais detalhadas: [`REGRAS-DE-NEGOCIO.md`](REGRAS-DE-NEGOCIO.md).
> Acesso/login: [`AUTENTICACAO-E-ACESSO.md`](AUTENTICACAO-E-ACESSO.md). Decisões e
> pendências: [`ARCHITECTURE.md`](ARCHITECTURE.md). Requisitos/UML do TCC:
> [`gerar_requisitos.py`](gerar_requisitos.py) → `Requisitos-ShivaZen.docx/.pdf`.

## Sumário

1. [Visão geral](#1-visão-geral)
2. [Stack](#2-stack)
3. [Arquitetura](#3-arquitetura)
4. [Setup local e comandos](#4-setup-local-e-comandos)
5. [Banco de dados](#5-banco-de-dados)
6. [Rotas](#6-rotas)
7. [Integrações externas](#7-integrações-externas)
8. [Segurança](#8-segurança)
9. [LGPD](#9-lgpd)
10. [Jobs periódicos](#10-jobs-periódicos)
11. [Variáveis de ambiente](#11-variáveis-de-ambiente)
12. [Testes e CI](#12-testes-e-ci)
13. [Deploy (Railway)](#13-deploy-railway)
14. [Go-live / Operação (runbook)](#14-go-live--operação-runbook)
15. [Convenções de código](#15-convenções-de-código)
16. [Histórico](#16-histórico)

---

## 1. Visão geral

Sistema single-tenant da clínica de estética Jaqueline Aranha (São José do Rio Preto/SP).
Um único projeto Django, renderizado no servidor, com duas superfícies:

| Superfície | Quem usa | Base de template |
|---|---|---|
| **Site público** — vitrine, agendamento online, "Meus agendamentos", lista de espera, NPS, termos, LGPD | Clientes (sem conta/senha; identidade = celular + OTP por SMS) | `estrutura/base_v2.html` |
| **Painel da equipe** — agenda, clientes, ficha/prontuário, pacotes, promoções, comissões, NPS, termos, auditoria, Branding | ADMIN (painel completo, 2FA obrigatório) | `painel/base_v2.html` (app-shell PWA) |
| **Portal do profissional** — agenda própria, aprovar/rejeitar pedidos, "Realizado", anotações, ficha das próprias clientes | PROFISSIONAL | `profissional/*.html` sobre `estrutura/base_v2.html` |

O papel **RECEPCAO** existe no modelo, mas ainda não tem telas nem login (ver
[`AUTENTICACAO-E-ACESSO.md`](AUTENTICACAO-E-ACESSO.md)).

---

## 2. Stack

| Camada | Tecnologia (versões em `requirements.txt` / `package.json`) |
|---|---|
| Linguagem | Python 3.12 (`.python-version`), Node 22 no build (`.nvmrc`; Tailwind v4 exige ≥ 20) |
| Web | Django 5.2 (MVT, server-render), gunicorn 23, WhiteNoise 6 (estáticos `CompressedManifest` em prod) |
| Banco | PostgreSQL 18 em produção e CI; SQLite (`db_dev.sqlite3` / `db_test.sqlite3`) em dev e testes rápidos |
| Front-end | Vite 6 + Tailwind CSS v4 (`@tailwindcss/vite`) + Alpine.js via `@alpinejs/csp` + django-vite 3 + django-cotton 2. **Sem HTMX, Bootstrap ou jQuery** |
| Libs no CDN (com SRI) | FullCalendar 6.1.11 (`painel/calendar.html`), Chart.js 4.4.0 (`painel/overview.html`), Swagger UI 5.32.15 (`/api/schema/swagger/`) |
| Fontes | Playfair Display + Lato (Google Fonts) — tokens em `static/src/css/tokens.css` |
| Assíncrono | Celery 5.4. Em produção roda **eager** (sem worker) salvo `CELERY_WORKER_ENABLED=true`; jobs periódicos via cron HTTP |
| Cache / sessão | LocMemCache + sessão no banco (padrão, 1 worker). `REDIS_URL` troca para django-redis + sessão em cache |
| Auth | Usuário próprio (`AbstractBaseUser`, e-mail), django-axes 8, django-otp + django-two-factor-auth (só models/TOTP; rotas `/account/*` **não** publicadas), django-ratelimit 4 |
| API | Django REST Framework 3.17 (somente leitura, só ADMIN) + drf-spectacular 0.29 (Swagger, sem ReDoc) |
| Integrações | Zenvia SMS (OTP), WhatsApp Cloud API (Meta), e-mail (backend Django), pywebpush (VAPID), Google Calendar (OAuth, opcional), Cloudflare Turnstile, Sentry |
| Outros | bleach (HTML de promoção), openpyxl (Excel), python-dateutil (RRULE), python-json-logger (logs JSON em prod), qrcode (QR SVG do 2FA, sem Pillow) |
| Deploy | Railway (Dockerfile multi-stage + `railway.json`) |
| CI | GitHub Actions `.github/workflows/ci.yml` |

`requirements.lock` fixa as dependências transitivas (usado com `-c` no CI e no Docker).

---

## 3. Arquitetura

### 3.1 Estrutura de pastas

```
aranha_estetica/
├── models/          acesso, clientes, profissionais, procedimentos, agendamentos, prontuario,
│                    anamnese, termos, nps, pacotes, sistema, push, extras (carteira/comissão), mixins
├── views/           public, booking_public/_otp/_reagendar/_api, agenda_publica, anamnese_publica,
│                    notificacoes (confirmar presença), lgpd, profissional, prontuario, auth, admin_2fa,
│                    dashboard, admin*, pacotes, relatorios, cron, health, whatsapp, webpush_views
├── services/        agendamento_service, disponibilidade (SlotService), otp, lgpd, termos, alertas,
│                    comissao_service, fidelidade_service, retorno_service, lista_espera_service,
│                    anamnese, auditoria, push, gcal
├── domain/          event_bus.py (EventBus síncrono, best-effort), events.py, handlers.py (logging)
├── utils/           branding, email, sms, whatsapp, precos, saude, fichas, security, dois_fatores,
│                    pii, captcha, datas, parse, busca, audit
├── forms/           cliente.py (ClientePainelForm)
├── api/             DRF v1 + schema/Swagger
├── management/commands/  seed, bootstrap_admin, migrate_atomico, setup_2fa, carregar_feriados
├── templatetags/    painel_tags, atendimento_tags, gcal_tags, dict_extras
├── templates/       estrutura/, cotton/, publico/, agenda/, servicos/, painel/, profissional/,
│                    partials/, email/, usuario/, pwa/, api/, erros (400/403/403_csrf/404/429/500),
│                    axes_bloqueio.html, robots.txt
├── static/src/      fonte do Vite (css/tokens.css, css/app.css, js/app.js) — fora do collectstatic
├── static/js/       wizard.js, admin-search.js, webpush.js (servidos direto pelo staticfiles)
├── static/dist/     build do Vite (gitignored; gerado no Docker/CI)
├── signals.py       efeitos do post_save de Atendimento + cache do Branding
├── tasks.py / tasks_manutencao.py   jobs Celery (chamados pelo cron HTTP)
├── middleware.py    Enforce2FAMiddleware, SecurityHeadersMiddleware, ContentSecurityPolicyMiddleware
├── checks.py        system checks de produção aranha.W001–W009
├── context_processors.py · decorators.py · validators.py · constants.py · sitemaps.py · admin.py
clinica/             settings/{base,dev,prod}.py, urls.py, celery.py, wsgi.py, apps.py, gunicorn_logger.py
```

### 3.2 Camadas e fluxo de uma requisição

```
Navegador ──HTTPS──▶ gunicorn (1 worker × 4 threads)
  └─ middleware: Security → GZip → WhiteNoise → Session → CSRF → Auth → OTP → Messages
                 → XFrame → CSP (nonce) → SecurityHeaders (no-store/no-referrer) → Enforce2FA
                 → Axes → Ratelimit
      └─ view (validação + permissão) ──▶ services/ e utils/ (regra de negócio)
            └─ models (FSM do Atendimento, managers, constraints) ──▶ PostgreSQL
                  ├─ signals.py (post_save do Atendimento): faltas, débito de pacote,
                  │   lista de espera, push/Google Calendar no on_commit
                  └─ EventBus (após a transição da FSM): comissão, cashback, retorno
```

- **FSM do atendimento** (`models/agendamentos.py`): toda mudança de status passa por
  `aprovar/confirmar/cancelar/marcar_realizado/marcar_falta/marcar_reagendado`, que travam a
  linha (`select_for_update`), validam `TRANSICOES`, gravam `LogAuditoria` e publicam o
  evento. `status = X; save()` pula tudo isso e é proibido no código.
- **Signals** (efeitos que valem para qualquer caminho de gravação, inclusive Django admin):
  contador de faltas (3-strike), débito de sessão de pacote (nunca de retorno; validade pela
  data da sessão), aviso da lista de espera (CANCELADO/REAGENDADO), push ao profissional e
  sync do Google Calendar no `on_commit` de um atendimento novo, invalidação do cache do
  Branding.
- **EventBus** (`domain/`): `AtendimentoRealizado` → comissão, cashback de indicação e
  retorno obrigatório; `AtendimentoCancelado` → estorno de comissão e de cashback;
  `AtendimentoConfirmado` → só log. Eventos existentes: `AtendimentoConfirmado`,
  `AtendimentoRealizado`, `AtendimentoCancelado`, `AtendimentoFaltou`, `RetornoSugerido`,
  `CashbackLiberado`, `CashbackEstornado`, `ComissaoCalculada`. Síncrono, in-process e
  best-effort (falha de handler é logada, não desfaz a transição).
- **Disponibilidade**: fonte única `services/disponibilidade.py::SlotService` (slots de 30
  min; expediente semanal, exceções, feriados, bloqueios pontuais/recorrentes do profissional
  ou globais, buffers, `min_notice_horas`, `max_advance_dias`). Usada pelo wizard, pelo
  reagendamento, pelo agendamento interno e pela validação do servidor.
- **Preço**: `utils/precos.py` — preço vigente por data (profissional > base) e promoção
  vigente na data; `preco_com_promocao()` é o valor gravado no atendimento e o mesmo que a
  vitrine (`/promocoes/`, `/especialidades/`, cards do wizard) mostra.
- **Branding**: `utils/branding.get_branding()` — precedência **tela Branding (tabela
  `configuracao`) > env > padrão**; contatos vazios escondem o item no site.
- **Celery**: `send_email_async` e os `job_*` são tasks, mas em produção rodam eager (dentro do
  request/cron), sem retry síncrono (`CELERY_TASK_ANNOTATIONS max_retries=0`).
- **Front**: `static/src/js/app.js` registra componentes Alpine CSP-safe (`temaToggle`,
  `modal`, `navMenu`, `cookieConsent`, `adminShell`, `alertaDismiss`, `anotacaoModal`,
  `pacoteCriar`) + `data-confirm` global e a busca instantânea do painel. Componentes cotton:
  `<c-botao>`, `<c-card>`, `<c-campo>` e o chrome do site (`cabecalho`, `rodape`, `mensagens`,
  `cookie_consent`). Tema claro/escuro por cookie `tema` (`data-theme="claro|escuro"`) sem FOUC.
- **PWA**: `/manifest.json` + `/sw.js` (service worker v7: cacheia só páginas públicas e
  estáticos; nunca HTML privado) e `/painel/manifest.json` para o app da equipe.

---

## 4. Setup local e comandos

Passo a passo em [`README.md`](../README.md#rodando-localmente). Resumo:

```bash
pip install -r requirements-dev.txt -c requirements.lock
cp .env.example .env                    # DEBUG=True
python manage.py migrate
python manage.py seed --demo
ADMIN_EMAIL=... ADMIN_PASSWORD=... python manage.py bootstrap_admin
npm ci && npm run dev                   # terminal 1 (Vite :5173)
python manage.py runserver              # terminal 2
```

### Comandos de gerenciamento próprios

| Comando | O que faz |
|---|---|
| `seed [--demo] [--force]` | Idempotente e não destrutivo: profissional Jaqueline Aranha, catálogo real (23 procedimentos, com preço base quando há valor — um é "sob consulta"), habilitações, agenda seg–sex 9h–18h e sáb 9h–15h, termo LGPD v1.0 (se não houver), anamnese padrão e feriados. **Não cria usuários.** `--demo` = 3 clientes e atendimentos fictícios (recusado em produção). Em produção exige `--force`. |
| `bootstrap_admin [--reset-senha]` | Cria/reativa o ADMIN a partir de `ADMIN_EMAIL`/`ADMIN_PASSWORD`/`ADMIN_NOME` (e-mail em minúsculas; senha passa pelos validadores). Senha de conta existente só muda se estava desativada/sem senha ou com `ADMIN_PASSWORD_RESET=true`. Reativação apaga o 2FA antigo da conta. Nunca derruba o pre-deploy; ao final escreve **ERRO** em stderr se não sobrar ADMIN utilizável. |
| `migrate_atomico [--noinput]` | `migrate` numa transação única no Postgres (tudo ou nada), `SET LOCAL lock_timeout = '5s'`, `SET CONSTRAINTS ALL IMMEDIATE` entre migrations. Fora do Postgres vira `migrate` normal. Usado no pre-deploy e na CI. |
| `setup_2fa <email> [--force] [--name]` | Cria TOTP confirmado, imprime URI `otpauth://` + QR ASCII e 10 códigos de backup. `--force` = reset completo (apaga todos os devices OTP do usuário; auditado). Recuperação de acesso da equipe. |
| `carregar_feriados [--ano N]` | Feriados nacionais (fixos + móveis via Páscoa), idempotente; sem `--ano` = ano atual e seguinte. |

Úteis do Django/libs: `check`, `check --deploy`, `check --database default` (roda também o
W006), `makemigrations --check --dry-run`, `createsuperuser` (cria ADMIN), `axes_reset`
(limpa bloqueios do django-axes), `clearsessions`.

---

## 5. Banco de dados

### 5.1 Resumo

- **34 tabelas de domínio** (+ tabelas do Django, django-otp, django-axes). PK `BigAutoField`.
- Nomes PT-BR no singular, `db_table` explícito, textos nunca nulos (exceto colunas sob UNIQUE
  parcial), dinheiro em `Decimal(10,2)`, status como `CharField` + CHECK + FSM na aplicação.
- Migrations `0001`–`0046`. Produção está na **`0026`**: o próximo deploy aplica `0027`→`0046`
  de uma vez (ver §14 e [`specs/remodelagem-banco-v2.md`](specs/remodelagem-banco-v2.md)).

### 5.2 Tabelas

| Domínio | Tabela (model) | Para que serve |
|---|---|---|
| Acesso | `usuario` (Usuario) | Equipe. Login por e-mail; `papel` ADMIN/PROFISSIONAL/RECEPCAO (CHECK); `is_staff` = ADMIN; 1:1 opcional com Profissional |
| | `assinatura_push` (AssinaturaPush) | Inscrições Web Push (VAPID) por usuário |
| | `log_auditoria` (LogAuditoria) | Trilha LGPD art. 37: autor + snapshot `usuario_nome` ("Nome <email>"), ação, tabela, registro, detalhes JSON (PII mascarada), IP |
| Pessoas | `cliente` (Cliente) | Cliente da clínica. Telefone só dígitos (10–11) é a chave natural; e-mail/CPF opcionais; consentimentos granulares com data e IP; faltas/bloqueio online; soft delete (`deletado_em`); `indicado_por` (cashback) |
| | `profissional` (Profissional) | Quem atende. `min_notice_horas` (0–720, padrão 2), `max_advance_dias` (1–365, padrão 60), `ics_token` do feed, campos do Google Calendar |
| Catálogo | `procedimento` (Procedimento) | Serviço: duração, buffer (0–120 min), categoria, modalidade, retorno obrigatório (`exige_retorno` + janela em dias + duração do retorno) |
| | `habilitacao` (Habilitacao) | Profissional × procedimento (M2M through) |
| | `preco` (Preco) | Preço versionado por `vigente_desde`, base (sem profissional) ou por profissional |
| | `promocao` (Promocao) | Desconto 0–100% **XOR** preço fixo, período, procedimento (nulo = geral, só percentual vale) |
| Agenda | `disponibilidade_profissional` (DisponibilidadeProfissional) | Expediente semanal (1=Dom … 7=Sáb) |
| | `excecao_disponibilidade` (ExcecaoDisponibilidade) | FOLGA ou HORARIO_DIFERENTE numa data |
| | `bloqueio_agenda` (BloqueioAgenda) | Bloqueio pontual ou recorrente (RRULE), de um profissional ou global (profissional nulo = "Todos") |
| | `feriado` (Feriado) | Feriado/recesso; `bloqueia_agendamento` |
| | `atendimento` (Atendimento) | Agendamento/sessão. FSM de 7 estados, preço gravado (`valor_cobrado`, `valor_original`, `promocao`, `descricao_preco`), retorno (`eh_retorno` + `atendimento_origem`), `reagendado_de`, `token_cancelamento` |
| | `notificacao` (Notificacao) | Mensagem por atendimento: tipo (LEMBRETE, LEMBRETE_2H, CONFIRMACAO, CANCELAMENTO, NPS, PESQUISA, APROVACAO, TERMO), canal, status PENDENTE/ENVIADO/FALHOU, token do link |
| | `lista_espera` (ListaEspera) | Pedido de aviso de vaga (procedimento, data, turno, profissional opcional, `email_contato`). `token_reserva`/`expira_em` são legado sem uso |
| Clínico | `prontuario` (Prontuario) | Ficha base 1:1 com a cliente: alergias, contraindicações, histórico, medicamentos, observações + `respostas_extras` (JSONB, perguntas configuráveis) |
| | `prontuario_versao` (ProntuarioVersao) | **Histórico append-only** da ficha: foto do estado anterior a cada edição, com autor (0046) |
| | `anotacao_sessao` (AnotacaoSessao) | Nota clínica por atendimento; autor PROTECT + snapshot `autor_nome` |
| | `formulario_anamnese` (FormularioAnamnese) | Modelo de questionário (ANAMNESE/PESQUISA) em `schema_json`, com escopo e `obrigatorio` |
| | `resposta_anamnese` (RespostaAnamnese) | Ficha respondida (JSON) por cliente/atendimento; token do link público (60 dias) |
| LGPD | `versao_termo` (VersaoTermo) | Termo versionado (LGPD ou PROCEDIMENTO); 1 ativo por escopo; imutável depois do 1º aceite |
| | `aceite_termo` (AceiteTermo) | Prova do aceite: cliente, versão, atendimento, IP, user-agent, `conteudo_sha256`, data. Imutável (trigger) |
| | `codigo_otp` (CodigoOtp) | Desafio OTP (HMAC-SHA256 com a SECRET_KEY), propósito AGENDAMENTO/LOGIN_CLIENTE/DSAR, TTL, tentativas |
| NPS | `avaliacao_nps` (AvaliacaoNPS) | Nota 0–10 + comentário, `autoriza_publicacao` (opt-in da cliente) e `aprovado_publicacao` (moderação) |
| Pacotes | `pacote` / `item_pacote` | Pacote (preço, validade em meses) e seus itens (procedimento × sessões) |
| | `compra_pacote` (CompraPacote) | Venda a uma cliente: `valor_pago`, status ATIVO/FINALIZADO/CANCELADO/EXPIRADO, `data_expiracao` |
| | `consumo_sessao` (ConsumoSessao) | 1 sessão consumida por atendimento (OneToOne) |
| Financeiro | `carteira` / `movimento_carteira` | Saldo da cliente (CHECK ≥ 0) e ledger append-only (trigger) — hoje só cashback de indicação e estorno |
| | `regra_comissao` / `movimento_comissao` | Regra (percentual 0–100 XOR valor fixo) e comissão por atendimento (PENDENTE/PAGA/ESTORNADA) |
| Infra | `configuracao` (Configuracao) | Chave-valor: campos do Branding, `email_admin`, `prontuario_perguntas`, `MAX_FALTAS_BLOQUEIO` (opcional) |

### 5.3 Invariantes garantidas pelo banco

| Invariante | Objeto | Migration |
|---|---|---|
| Profissional não atende 2 clientes ao mesmo tempo (status PENDENTE/AGENDADO/CONFIRMADO) | `excl_atendimento_sobreposicao` — EXCLUDE gist com `btree_gist`, DEFERRABLE (**só Postgres**) | 0035 |
| Telefone/e-mail/CPF únicos entre clientes ativos | `uniq_cliente_telefone_ativo`, `uniq_cliente_email_ativo` (LOWER), `uniq_cliente_cpf_ativo` | 0034 |
| Formato de telefone (10–11 dígitos) e CPF (11) | `chk_cliente_telefone_digits`, `chk_cliente_cpf_digits` (**só Postgres**) | 0034 |
| 1 termo ativo por escopo; 1 preço por vigência; 1 espera ativa por cliente/procedimento/data | `uniq_termo_ativo_*`, `uniq_preco_*vigencia`, `uniq_espera_ativa` | 0034 |
| Promoção: desconto 0–100, preço ≥ 0, desconto XOR preço | `chk_promocao_*` | 0034 |
| Carteira nunca negativa; ledger imutável | `chk_carteira_saldo_nao_negativo`; trigger `trg_movimento_carteira_imutavel` (**só PG**) | 0034 / 0038 |
| Valor pago de pacote ≥ 0; nota NPS 0–10; comissão 0–100% | `chk_compra_pacote_valor_pago`, `chk_avaliacao_nps_nota`, `chk_regra_comissao_percentual_0_100` | 0039 / 0044 |
| No máximo 1 retorno vivo por atendimento de origem | `uniq_retorno_por_origem` | 0043 |
| JSON no formato certo (objeto/lista) | `chk_prontuario_extras_objeto`, `chk_resposta_anamnese_objeto`, `chk_formulario_schema_lista` — **NOT VALID** (só PG; validar depois, §14.h) | 0043 |
| Prova de aceite imutável; termo aceito imutável; histórico da ficha imutável | triggers `trg_aceite_termo_imutavel`, `trg_versao_termo_imutavel`, `trg_prontuario_versao_imutavel` (**só PG**) | 0046 |
| Ordenação pt-BR de nomes | collation ICU `pt_br` em `cliente/profissional/procedimento.nome` (**fora do estado do Django**) | 0038 |
| Uma cobrança de comissão ativa e um cashback por atendimento | `uniq_comissao_ativa_por_atendimento`, `uniq_cashback_indicacao_por_atendimento` | 0025/0032 |

Além disso: FKs `PROTECT` em tudo que é histórico clínico/financeiro (prontuário, anotação,
ficha, compra de pacote, carteira, aceite), índice GIN em `prontuario.respostas_extras`,
`COMMENT ON TABLE` nas tabelas centrais. Detalhe por migration:
[`specs/remodelagem-banco-v2.md`](specs/remodelagem-banco-v2.md).

---

## 6. Rotas

Namespace `aranha` (`aranha_estetica/urls.py`) + `clinica/urls.py`. Rotas removidas nesta
versão: `/account/*` (django-two-factor), `/api/schema/redoc/`, `/servicos/produtos/`,
`/ajax/verificar-telefone/`, `/ajax/buscar-procedimentos/`, `/ajax/buscar-horarios/`,
`/painel/cancelar-agendamento/`.

### 6.1 Site público

| Rota | Função |
|---|---|
| `/`, `/quem-somos/`, `/equipe/`, `/especialidades/`, `/galeria/`, `/depoimentos/`, `/promocoes/` | Páginas institucionais; depoimentos só com opt-in + aprovação; promoções com o mesmo preço do agendamento |
| `/servicos/faciais/`, `/servicos/corporais/`, `/servicos/detalhe/<slug>/` | Catálogo |
| `/contato/` | Formulário de contato (envia ao e-mail do Branding) |
| `/termos-de-uso/`, `/politica-de-privacidade/` | Textos legais |
| `/lista-espera/` (+ `/sucesso/`) | Pedido de aviso de vaga |
| `/sitemap.xml`, `/robots.txt`, `/manifest.json`, `/sw.js`, `/favicon.ico` | SEO/PWA |

### 6.2 Agendamento e auto-atendimento da cliente

| Rota | Função |
|---|---|
| `/agendamento/` · `/agendar/<slug>/` · `/embed/agendar/` | Wizard (geral, direto por profissional, widget para iframe — `EMBED_FRAME_ANCESTORS`) |
| `/ajax/dias-disponiveis/` · `/ajax/horarios-disponiveis/` | Dias do mês e horários livres (SlotService) |
| `POST /agendamento/otp/solicitar/` · `POST /agendamento/otp/verificar/` | OTP do celular (Turnstile quando configurado) |
| `POST /agendamento/confirmar/` · `/agendamento/sucesso/` | Grava o atendimento (PENDENTE) |
| `/meus-agendamentos/` (+ `otp/enviar/`, `otp/verificar/`, `sair/`) | Portal da cliente (login por celular ou e-mail; código sempre ao celular) |
| `POST /ajax/cancelar-agendamento/` | Cancelar pelo `token_cancelamento` |
| `/reagendar/<token>/` (+ `horarios/`) | Reagendar (até 24h antes) |
| `/confirmar/<token>/` | Confirmar presença / cancelar a partir do lembrete D-1 |
| `/termo/<token>/` | Aceitar termo(s) do atendimento (vale até o fim do atendimento) |
| `/nps/<token>/` | NPS (link de 7 dias) + opt-in de depoimento |
| `/anamnese/<token>/` · `/pesquisa/<token>/` (+ `obrigado/`) | Ficha/pesquisa por link (60 dias; ficha de saúde exige consentimento art. 11). **Mantidas, hoje sem produtor de link** (decisão pendente do dono: remover × gerar o link) |
| `/lgpd/meus-dados/` · `/lgpd/unsubscribe/<token>/` · `/lgpd/aceitar-cookies/` | DSAR com OTP, descadastro, registro do aviso de cookies |
| `/agenda/<slug>/feed.ics?token=` | Feed iCal do profissional (token rotacionável no painel) |

### 6.3 Equipe

| Rota | Função |
|---|---|
| `/admin-login/` · `/admin-logout/` (logout só via POST; GET mostra confirmação) · `/admin-login/recuperar/...` | Login da equipe e reset de senha |
| `/painel/seguranca/2fa/` (+ `verificar/`, `challenge/`) | Cadastro e desafio TOTP |
| `/profissional/` · `/profissional/atendimento/<pk>/{realizado,anotar,aprovar,rejeitar}/` | Portal do profissional |
| `/painel/`, `/painel/overview/` | Dashboard |
| `/painel/agendamentos/` (+ `novo/`, `bulk/`, `<pk>/aprovar/`, `<pk>/rejeitar/`, `<pk>/termo-link/`, `<pk>/valor/`), `/painel/atualizar-status/` | Agenda em lista, agendamento interno, aprovação, link do termo, valor cobrado, mudança de status (409 `termo_pendente` sem o override) |
| `/painel/calendario/` (+ `eventos/`, `mover/`) | Calendário FullCalendar com arrastar-e-soltar |
| `/painel/clientes/`, `/painel/clientes/<pk>/` | Clientes e ficha do cliente (pacotes, alertas, histórico) |
| `/painel/prontuario/` (+ `<cliente_id>/`, `<cliente_id>/salvar/`), `/painel/anotacao/<id>/salvar/` | Prontuário com histórico de versões e anotações |
| `/painel/profissionais/` (+ `<pk>/ics/novo-token/`, `<prof_id>/excecoes/...`), `/painel/cadastrar-profissional/`, `/painel/editar-profissional/<pk>/` | Profissionais, exceções, novo link do ICS |
| `/painel/procedimentos/...`, `/painel/pacotes/...` (+ `vender/`, `compras/<pk>/cancelar/`), `/painel/promocoes/...` (+ `<pk>/disparar/`) | Catálogo, pacotes, promoções |
| `/painel/bloqueios/...`, `/painel/lista-espera/` (+ `<pk>/notificar/`) | Bloqueios de agenda e lista de espera |
| `/painel/financeiro/`, `/painel/comissoes/` (+ `<pk>/pagar/`), `/painel/nps/` (+ `<pk>/publicacao/`), `/painel/exportar-relatorio/` | Relatórios, comissões, NPS e moderação de depoimentos, Excel |
| `/painel/anamneses/...`, `/painel/termos/` (+ `criar/`, `compliance/`) | Formulários e termos |
| `/painel/notificacoes/`, `/painel/auditoria/`, `/painel/usuarios/...`, `/painel/configuracoes/...`, `/painel/branding/`, `/painel/email-preview/[<nome>/]` | Sistema |
| `/painel/integrations/google/{connect,callback,pull}/...` | Google Calendar (UI escondida sem `GOOGLE_OAUTH_*`) |
| `/webpush/{public-key,subscribe,unsubscribe}/` | Web Push da equipe |
| `/django-admin-sv/` | Django admin (só ADMIN com sessão 2FA verificada) |

### 6.4 API, webhooks e infraestrutura

| Rota | Função |
|---|---|
| `/api/v1/{profissionais,procedimentos,clientes,atendimentos}/` (+ `atendimentos/hoje/`) | DRF somente leitura, só ADMIN (PII mascarada em clientes); throttling anon 60/h, user 1000/h |
| `/api/schema/`, `/api/schema/swagger/` | OpenAPI + Swagger (SplitView, CSP com nonce), só ADMIN |
| `GET/POST /api/whatsapp/webhook/` | Meta: handshake `hub.challenge` (`WHATSAPP_VERIFY_TOKEN`) e eventos assinados (`X-Hub-Signature-256` com `WHATSAPP_APP_SECRET`); captura nota de NPS 0–10 |
| `POST /api/zenvia/webhook/` | Status de entrega de SMS (`ZENVIA_ALLOWED_IPS` e/ou HMAC `ZENVIA_WEBHOOK_SECRET`; recusa tudo em prod sem nenhum dos dois) |
| `POST /cron/run/<job>/` | Jobs periódicos (§10), header `X-Cron-Token` |
| `/healthz/` | Liveness (processo vivo + manifest do Vite); healthcheck do Railway |
| `/health/` | Readiness: banco + cache + manifest (503 se algo falha); `?celery=1` com `X-Cron-Token` pinga o worker |

---

## 7. Integrações externas

| Serviço | Uso | Configuração | Sem configuração |
|---|---|---|---|
| **Zenvia SMS** | OTP do agendamento, do "Meus agendamentos" e do DSAR. Só para **celular**. Quotas: 3/h por telefone, 10/h por IP, 60/h global (alerta no log a 80%) | `ZENVIA_API_TOKEN`, `ZENVIA_FROM` (+ `SMS_MAX_*`, `ZENVIA_API_URL`, webhook) | Falha fechada: sem agendamento online; CTAs "Agendar" viram WhatsApp (`AGENDAR_WHATSAPP`) |
| **WhatsApp Cloud API (Meta)** | Templates aprovados: `confirmacao_d1` (7 parâmetros: nome, data, hora, procedimento, profissional, link confirmar, link cancelar), `nps_pos_atendimento` (nome, procedimento, link), `lista_espera_vaga` (nome, procedimento, data/hora, link). Categoria UTILITY; links no corpo | `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_TEMPLATE_*`, `WHATSAPP_API_VERSION` (padrão v23.0) | Nada é enviado nem marcado como enviado |
| **E-mail** (backend Django) | Transacional (aprovação/rejeição, cancelamento, termos, fila de espera, pacote expirando, aprovação ao profissional, reset/convite de senha, contato) e marketing (aniversário, promoções) com `List-Unsubscribe` (RFC 8058) e HTML de promoção sanitizado (bleach) | `EMAIL_BACKEND` + credenciais, `DEFAULT_FROM_EMAIL` | Prod usa backend *dummy*: nada sai; reset de senha/convites recusam (aviso W003) |
| **Botão WhatsApp** (`wa.me`) | Contato, fallback do agendamento, link do termo pela recepção | `WHATSAPP_NUMERO` (tela Branding > env) | Botões escondidos (aviso W004) |
| **Web Push (VAPID)** | Aviso de novo agendamento ao profissional | `WEBPUSH_VAPID_*` | Botão de ativar escondido |
| **Google Calendar** | Push do atendimento novo + pull de eventos (OAuth por profissional) | `GOOGLE_OAUTH_CLIENT_ID/SECRET/REDIRECT_URI` | UI escondida |
| **Cloudflare Turnstile** | Captcha no pedido de OTP (wizard e portal) | `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | Sem captcha (aviso W009 se o SMS for real) |
| **Sentry** | Erros; `send_default_pii=False` + `before_send` que mascara PII e tokens de URL | `SENTRY_DSN`, `SENTRY_TRACES_SAMPLE_RATE` | Desligado |
| **Armazenamento S3/R2** (opcional) | Mídia | `AWS_*` (exige instalar `django-storages`, que não está no requirements) | Disco local |

---

## 8. Segurança

Detalhe de login, 2FA, OTP e tokens em [`AUTENTICACAO-E-ACESSO.md`](AUTENTICACAO-E-ACESSO.md).

- **Equipe:** e-mail + senha (mín. 10, validadores do Django), django-axes (5 falhas → bloqueio de
  1h por IP+usuário, página pt-BR 429), rate-limit no login, **2FA TOTP obrigatório para ADMIN**
  (opcional para PROFISSIONAL via env), conferido a cada request.
- **Cliente:** sem senha; OTP de 6 dígitos por SMS preso ao telefone (HMAC-SHA256, 10 min, 5
  tentativas, reenvio ≥ 60 s), propósitos isolados.
- **Headers:** CSP com nonce por request (sem `unsafe-inline` em `script-src`/`style-src`, sem
  handlers inline; `style-src-attr 'unsafe-inline'` ainda permitido), jsdelivr liberado só nos
  caminhos das libs usadas, `frame-ancestors 'none'` (exceto o widget), HSTS 1 ano com preload,
  X-Frame-Options DENY, nosniff, COOP/CORP, Permissions-Policy.
- **Privacidade no navegador:** `/painel/`, `/profissional/`, `/meus-agendamentos/`, `/lgpd/`,
  `/api/`, `/django-admin-sv/` saem com `Cache-Control: no-store` (e views sensíveis com
  `@never_cache`); rotas com token no path usam `Referrer-Policy: no-referrer`; logout envia
  `Clear-Site-Data: "cache"`; o access log do gunicorn redige tokens (`LoggerSemTokens`).
- **Cookies:** HttpOnly, SameSite=Lax, Secure em prod; sessão da equipe 8h deslizantes que
  expira ao fechar o navegador.
- **IP do cliente:** `utils/security.client_ip` lê `CLIENT_IP_HEADER` (prod: `HTTP_X_REAL_IP`
  do Railway; nunca X-Forwarded-For). Rate-limit, axes e auditoria usam a mesma função.
- **Entradas:** uploads ≤ 5 MB, ≤ 200 campos, IDs só com dígitos ASCII (`utils/parse.id_int`),
  células de Excel com texto forçado (anti-fórmula), JSON injetado via `json_script`.
- **Segredos:** só em env; `DJANGO_SECRET_KEY` obrigatória fora de DEBUG/no Railway;
  `DJANGO_ENV` inválido derruba o boot.

---

## 9. LGPD

### 9.1 Bases legais (política de privacidade e termo LGPD v1.0)

| Tratamento | Base legal |
|---|---|
| Agendar, confirmar, lembrar e realizar o atendimento | Execução de contrato (art. 7º, V) |
| Ficha de avaliação e histórico de cuidados (dado de saúde) | Consentimento específico e tutela da saúde (art. 11, I e II "f") |
| Registros de atendimento e de aceites | Obrigação legal e exercício de direitos (art. 7º, II e VI) |
| Pesquisa de satisfação, segurança do site | Legítimo interesse (art. 7º, IX) |
| Depoimentos no site, novidades e promoções | Consentimento (art. 7º, I), revogável |

### 9.2 Consentimentos e provas

- **Política de Privacidade:** aceite obrigatório no wizard → `AceiteTermo` da versão LGPD
  vigente com IP, user-agent e SHA-256 do texto. Sem termo LGPD ativo o aceite não é gravado
  (garanta um — §14.e).
- **Dado de saúde (art. 11):** ficha do wizard e ficha pública (`/anamnese/<token>/`) só são
  gravadas com a caixa de consentimento destacada marcada; o texto (`TEXTO_CONSENTIMENTO_SAUDE`)
  vai para a `LogAuditoria` com IP.
- **Comunicação:** `consent_email_marketing`, `consent_whatsapp_confirmacao` (lembrete D-1 e
  aviso de vaga), `consent_whatsapp_nps` — nunca pré-marcados; cada um guarda data e IP; desmarcar
  (com o estado do cadastro exibido) revoga com data e IP.
- **Descadastro** (`/lgpd/unsubscribe/<token>/`): desliga marketing e pesquisas
  (`aceita_comunicacao`, e-mail marketing, NPS), **mantém o lembrete D-1** (transacional).
- **Depoimento:** só aparece no site com `autoriza_publicacao` (cliente) **e**
  `aprovado_publicacao` (equipe), nota ≥ 9 e comentário.
- **Imutabilidade:** aceite e termo já aceito não mudam (model + trigger 0046); editar o texto
  = publicar nova versão. Aceites anteriores à 0044 ficam com `conteudo_sha256` vazio.

### 9.3 Direitos do titular

| Direito | Como |
|---|---|
| Confirmação, acesso, portabilidade | `/lgpd/meus-dados/`: telefone → OTP DSAR → download JSON (cadastro, atendimentos, fichas, prontuário + versões, aceites, NPS, lista de espera, pacotes, carteira). Resposta idêntica exista ou não o cadastro; auditado |
| Correção | Pela equipe na ficha do cliente (`/painel/clientes/<pk>/`) |
| Revogação de consentimento | Link de descadastro; desmarcar no wizard |
| Anonimização/eliminação | Ação LGPD no Django admin (`LgpdService.esquecer_cliente`) e job semanal (§9.4) |

**Esquecimento** (`esquecer_cliente`): troca o nome por `[ANONIMIZADO-<pk>]` e zera CPF, RG,
e-mail, telefone, endereço, nascimento e consentimentos; soft delete; apaga OTPs, lista de
espera e texto das notificações; apaga fichas que não são de atendimento REALIZADO; tira o
depoimento do site e apaga o comentário do NPS; pseudonimiza o nome nos textos da
`LogAuditoria` e do histórico do Django admin. Atendimentos, aceites, prontuário e pacotes
ficam ligados a um titular não identificável.

### 9.4 Retenção

| Dado | Prazo | Mecanismo |
|---|---|---|
| Código OTP | 24 h (`RETENCAO_OTP_HORAS`) | `housekeeping` apaga |
| Sessões expiradas | — | `housekeeping` (`clearsessions`) |
| Texto das notificações | 12 meses (`RETENCAO_NOTIFICACAO_DIAS`) | `housekeeping` limpa `mensagem` |
| Log de auditoria | 5 anos (`RETENCAO_LOG_AUDITORIA_DIAS`) | `housekeeping` apaga |
| Logs do django-axes | 90 dias (`RETENCAO_AXES_LOG_DIAS`) | `housekeeping` apaga |
| Cliente sem atendimento | 5 anos (`LGPD_RETENCAO_CLIENTE_DIAS`) | `lgpd_purgar` anonimiza — **exceto** quem tem prontuário com conteúdo, pacote comprado ou atendimento REALIZADO nos últimos 20 anos |
| Cliente excluído (soft delete) | 30 dias | `lgpd_purgar` anonimiza (mesmas exceções) |
| Ficha de pedido não realizado (CANCELADO, PENDENTE vencido, convite não respondido) | 90 dias | `lgpd_purgar` apaga |
| Lista de espera com data passada | — | `lgpd_purgar` apaga |
| Atendimento realizado, prontuário e versões, aceites | ≥ 20 anos (registro de saúde) | nunca apagados; triggers impedem DELETE de aceite/versão |

### 9.5 Incidente (art. 48)

Conter (revogar tokens/sessões, `setup_2fa --force`, rotacionar `DJANGO_SECRET_KEY` — invalida
sessões e OTPs pendentes), avaliar pela `LogAuditoria`, comunicar a ANPD e os titulares
afetados quando houver risco relevante.

---

## 10. Jobs periódicos

Produção roda sem worker Celery: um **cron externo** chama
`POST /cron/run/<job>/` com o header `X-Cron-Token: $CRON_TOKEN` (nunca `?token=`).
Resposta `200 {"ok": true}`; `500 {"ok": false}` quando o job falha (o cron deve alertar);
`403` token; `404` job desconhecido (lista os disponíveis). O job roda **dentro do request**
(eager) e o gunicorn corta em 120 s. Os mesmos horários estão em `CELERY_BEAT_SCHEDULE`
(usado só se houver worker + beat).

```bash
curl -fsS -X POST -H "X-Cron-Token: $CRON_TOKEN" https://<dominio>/cron/run/<job>/
```

| Job (`<job>`) | Horário BRT | Cron (America/Sao_Paulo) | Cron (UTC) | O que faz |
|---|---|---|---|---|
| `pacote_expirar` | 00:30 | `30 0 * * *` | `30 3 * * *` | Compras ATIVO com `data_expiracao` < hoje → EXPIRADO |
| `pacote_expirando` | 07:00 | `0 7 * * *` | `0 10 * * *` | E-mail a quem tem pacote vencendo em 7 ou 1 dia com saldo |
| `lembrete_diario` | 08:00 | `0 8 * * *` | `0 11 * * *` | WhatsApp D-1 (template `confirmacao_d1`) para atendimentos **AGENDADO** de amanhã com `consent_whatsapp_confirmacao`; idempotente |
| `aniversario` | 09:00 | `0 9 * * *` | `0 12 * * *` | E-mail de felicitação (sem desconto) com consentimento de marketing; 29/02 comemora em 28/02 |
| `nps_24h` | 10:00 | `0 10 * * *` | `0 13 * * *` | WhatsApp de NPS para REALIZADO entre 24h e 7 dias, com consentimento; até 3 tentativas |
| `detrator_alerta` | 10:30 | `30 10 * * *` | `30 13 * * *` | E-mail de NPS ≤ 6 para `email_admin` > `ADMIN_EMAIL` > `CLINIC_EMAIL` |
| `limpeza_status` | 23:00 | `0 23 * * *` | `0 2 * * *` | PENDENTE vencido há 24h → CANCELADO (FSM); AGENDADO/CONFIRMADO sem desfecho **só vão para o log** (a equipe decide falta/realizado) |
| `lgpd_purgar` | dom 03:00 | `0 3 * * 0` | `0 6 * * 0` | Anonimização por retenção, fichas de pedidos não realizados, lista de espera vencida |
| `housekeeping` | 04:00 | `0 4 * * *` | `0 7 * * *` | Sessões, OTP > 24h, texto de notificação > 12 meses, auditoria > 5 anos, axes > 90 dias (etapas independentes; falha de uma → 500 no fim) |
| `feriados` | dia 1, 04:30 | `30 4 1 * *` | `30 7 1 * *` | `carregar_feriados` (ano atual + seguinte) |

O Brasil está sem horário de verão desde 2019: BRT = UTC−3 o ano todo. `job_promocao_mensal`
não está no cron — é disparado pelo painel (Promoções > Disparar).

---

## 11. Variáveis de ambiente

Lista completa e comentada em [`.env.example`](../.env.example). Em produção:

### 11.1 Obrigatórias / pré-requisito do go-live

| Variável | Propósito |
|---|---|
| `DJANGO_SECRET_KEY` | Chave do Django (sessões, reset, HMAC do OTP). Sem ela o boot falha fora de DEBUG |
| `DJANGO_ENV=prod` | Carrega `settings/prod.py` (a imagem já define; qualquer valor fora de `dev`/`prod` derruba o boot) |
| `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | Domínio próprio (o `RAILWAY_PUBLIC_DOMAIN` e o host do healthcheck entram sozinhos) |
| `SITE_URL` | URL pública https sem barra final — links de e-mail/WhatsApp/termos/JSON-LD (W001) |
| `DATABASE_URL` | Injetada pelo Postgres do Railway |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `ADMIN_NOME` | **Pré-requisito do 1º deploy**: a `0042` desativa as contas demo; o `bootstrap_admin` cria o ADMIN real. Apagar `ADMIN_PASSWORD` depois |
| `CRON_TOKEN` | Autentica o cron HTTP (W005) |
| `ZENVIA_API_TOKEN`, `ZENVIA_FROM` | SMS/OTP. **Sem SMS não há agendamento online** (CTA vira WhatsApp) (W002) |
| `EMAIL_BACKEND` (+ `EMAIL_HOST*`/credenciais do provedor, `DEFAULT_FROM_EMAIL`) | Sem ele nada é entregue (W003). **SMTP de saída só no plano Pro do Railway**; no Hobby use provedor HTTP — o projeto não traz backend HTTP instalado (ex.: adicionar `django-anymail` ao requirements) |
| `WHATSAPP_NUMERO` (ou tela Branding) | Botões de WhatsApp e fallback do agendamento (W004) |
| `CLINIC_EMAIL` (ou tela Branding) | Canal do titular LGPD e fallback do alerta de detrator (W007) |

### 11.2 Recomendadas

| Variável | Propósito |
|---|---|
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_TEMPLATE_*` | Lembrete D-1, NPS e aviso de vaga |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | Captcha no pedido de OTP; com SMS real sem captcha poucos IPs esgotam a cota global (W009) |
| `SENTRY_DSN` (+ `SENTRY_TRACES_SAMPLE_RATE`) | Monitoramento de erros |
| `WEBPUSH_VAPID_PUBLIC_KEY`, `WEBPUSH_VAPID_PRIVATE_KEY`, `WEBPUSH_VAPID_CLAIMS_EMAIL` | Push ao profissional |
| `ADMIN_2FA_OBRIGATORIO` | Sem a env: ligado em prod. `false` = válvula de emergência (W008) |
| `PROFISSIONAL_2FA_OBRIGATORIO` | `true` exige 2FA também do PROFISSIONAL (lê alertas de saúde) |
| `EMBED_FRAME_ANCESTORS` | Origens que podem embutir `/embed/agendar/` (vazio = qualquer `https:`) |
| `LGPD_RETENCAO_CLIENTE_DIAS` | Retenção de cliente inativo (padrão 1825) |
| `REDIS_URL` | Opcional: cache/sessão compartilhados (obrigatório se `WEB_CONCURRENCY` > 1). **Não** liga worker |

### 11.3 Opcionais / ajuste fino

`CLINIC_NAME`, `CLINIC_SUBTITLE`, `CLINIC_PHONE`, `CLINIC_ADDRESS`, `CLINIC_HOURS`,
`INSTAGRAM_URL`, `THEME_COLOR` (a tela Branding vence a env) · `USE_HTTPS` ·
`CLIENT_IP_HEADER` (prod: `HTTP_X_REAL_IP`) · `WEB_CONCURRENCY` (1), `WEB_THREADS` (4),
`WEB_TIMEOUT` (120) · `SESSION_COOKIE_AGE` (28800) · `CELERY_WORKER_ENABLED`,
`CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` · `ADMIN_PASSWORD_RESET` · `SMS_MAX_POR_HORA` (3),
`SMS_MAX_POR_IP_HORA` (10), `SMS_MAX_GLOBAL_HORA` (60), `ZENVIA_API_URL`,
`ZENVIA_WEBHOOK_SECRET`, `ZENVIA_ALLOWED_IPS` · `OTP_TTL_SEGUNDOS` (600),
`OTP_MAX_TENTATIVAS` (5), `OTP_REENVIO_MINIMO_SEG` (60) · `WHATSAPP_API_URL`,
`WHATSAPP_API_VERSION`, `WHATSAPP_PHONE_NUMBER_ID` (alias) · `GOOGLE_OAUTH_*` · `AWS_*` ·
`AXES_FAILURE_LIMIT` (5), `AXES_COOLOFF_TIME_HOURS` (1), `PASSWORD_RESET_TIMEOUT_SECONDS`
(3600), `TWO_FACTOR_REMEMBER_COOKIE_AGE` · `RETENCAO_OTP_HORAS`, `RETENCAO_NOTIFICACAO_DIAS`,
`RETENCAO_LOG_AUDITORIA_DIAS`, `RETENCAO_AXES_LOG_DIAS` · `DJANGO_LOG_LEVEL` ·
`EMAIL_TIMEOUT` (10).

Só dev/testes: `DEBUG`, `SMS_DEV_LOG_ONLY` (em prod gera W002), `DB_ENGINE`/`DB_*`,
`TEST_DATABASE_URL`, `PG_EXIGE_ICU`.

**Legada — remover do Railway:** `STATIC_ROOT` (era `/tmp`; `settings/prod.py` ignora e usa
`/app/staticfiles`, preenchido no build). Também não existem mais `ZENVIA_API_KEY` (use
`ZENVIA_API_TOKEN` + `ZENVIA_FROM`) nem `SECRET_KEY` (use `DJANGO_SECRET_KEY`).

---

## 12. Testes e CI

- `python manage.py test aranha_estetica` — suíte Django (SQLite `db_test.sqlite3`, ~1.060
  testes em 52 arquivos). Nos testes: Celery eager com propagação, SMS em modo log, avisos
  `aranha.W00x` silenciados (testados à parte em `test_deploy_config.py`), django-vite em
  `dev_mode` (não precisa do build).
- `TEST_DATABASE_URL=postgres://... python manage.py test aranha_estetica` — mesma suíte no
  Postgres; `tests/test_pg_ddl.py` confere os objetos só-Postgres (EXCLUDE, CHECK regex,
  triggers, collation — com `PG_EXIGE_ICU=1` a falta de ICU reprova).
- `pytest aranha_estetica/tests/test_2fa.py` — o único arquivo estilo pytest.
- Guardas de regressão relevantes: `test_csp.py` (CSP x templates, allowlist do jsdelivr),
  `test_front_pipeline.py` (Vite/cotton/webpush), `test_deploy_config.py` (checks, migrate
  atômico, Dockerfile/railway.json), `test_slots_disponibilidade.py` (caracterização do
  SlotService).

**CI** (`.github/workflows/ci.yml`, push em `main`/`front-fundacao` e PRs):

| Job | Passos |
|---|---|
| `testes` | `check`, `makemigrations --check`, suíte Django (SQLite), arquivos pytest, `check --deploy --fail-level ERROR` com settings de prod |
| `postgres` | Postgres 18: `migrate_atomico` do zero, `bootstrap_admin`, suíte Django no Postgres (bloqueante) |
| `front` | `npm ci`, `npm run build`, confere o manifest |
| `docker` | `docker build` da imagem de produção |
| `auditoria-deps` | `pip-audit` no `requirements.lock` (informativo) |

---

## 13. Deploy (Railway)

- **Build** (`Dockerfile`, builder `DOCKERFILE` no `railway.json`; o Procfile/Nixpacks foi
  removido):
  1. stage `front` (`node:22-slim`): `npm ci` + `npm run build`; falha se não houver
     `static/dist/manifest.json`;
  2. stage `builder` (`python:3.12-slim`): wheels de `requirements.txt` com `-c requirements.lock`;
  3. stage `runtime`: usuário não-root `app`, `DJANGO_ENV=prod`, smoke do QR SVG do 2FA (a imagem
     não tem Pillow), `collectstatic` (WhiteNoise `CompressedManifest`) +
     `check --tag staticfiles --fail-level WARNING` no build, `HEALTHCHECK` em `/healthz/`.
- **Pre-deploy** (`railway.json`): `python manage.py migrate_atomico --noinput && python manage.py
  bootstrap_admin`. Se uma migration falha, a transação inteira é desfeita e o deploy anterior
  continua no ar com o banco intacto.
- **Start**: `gunicorn clinica.wsgi` com `WEB_CONCURRENCY=1` worker × `WEB_THREADS=4`,
  timeout 120 s, access log com tokens redigidos. O `startCommand` do `railway.json` sobrescreve
  o `CMD` do Dockerfile (mudar os dois juntos). 1 réplica; restart ON_FAILURE (5×).
- **Healthcheck**: `/healthz/` (timeout 120 s) — 503 se o bundle do Vite faltar.
- **Produção**: `DEBUG` sempre falso, HTTPS + HSTS, e-mail *dummy* sem `EMAIL_BACKEND`, SMS falha
  fechado sem Zenvia, Celery eager (`CELERY_WORKER_ENABLED=true` liga worker real), logs JSON.
- O deploy de produção sai do branch `main` (configuração do serviço no Railway). Esta versão
  vive em `front-fundacao` e vai para produção com o merge em `main`.

---

## 14. Go-live / Operação (runbook)

Primeiro deploy desta versão: produção sai da migration **`0026`** para a **`0046`** (20
migrations com renomeações, deduplicação de dados, EXCLUDE, triggers e desativação de contas
demo). Faça em horário sem movimento.

### a) Pré-deploy

1. **Backup do Postgres de produção** (obrigatório — é o único rollback):
   ```bash
   # URL pública do Postgres do Railway (variável DATABASE_PUBLIC_URL do serviço Postgres).
   # Use pg_dump da mesma versão major do servidor (18) ou mais nova.
   pg_dump "$DATABASE_PUBLIC_URL" --format=custom --no-owner --file=prod_0026_$(date +%F_%H%M).dump
   pg_restore --list prod_0026_*.dump | head      # confere que o arquivo abre
   ```
   Guarde o arquivo fora da máquina (é dado pessoal e de saúde: armazenamento cifrado).
2. **Ensaio**: restaure o dump num Postgres 18 local e rode a atualização:
   ```bash
   createdb shivazen_ensaio && pg_restore --no-owner -d shivazen_ensaio prod_0026_*.dump
   DATABASE_URL=postgres://.../shivazen_ensaio DEBUG=True python manage.py migrate_atomico --noinput
   ```
   As migrations que podem **abortar** (tudo é desfeito) e o que fazer:
   - `0035` — pares de atendimentos ativos sobrepostos do mesmo profissional. Ela mesma cancela
     (com log) o PENDENTE já vencido ou o retorno PENDENTE sugerido; o resto aborta listando os
     pares → cancelar/reagendar um de cada par. Pré-checagem no schema 0026:
     ```sql
     SELECT a.id, b.id, a.profissional_id, a.status, b.status, a.data_hora_inicio, b.data_hora_inicio
     FROM atendimento a JOIN atendimento b
       ON a.profissional_id = b.profissional_id AND a.id < b.id
      AND a.status IN ('PENDENTE','AGENDADO','CONFIRMADO')
      AND b.status IN ('PENDENTE','AGENDADO','CONFIRMADO')
      AND a.data_hora_inicio < b.data_hora_fim AND b.data_hora_inicio < a.data_hora_fim;
     ```
   - `0043` — dois retornos já aprovados/realizados para a mesma origem (duplicata PENDENTE é
     cancelada sozinha). Pré-checagem no schema 0026 (coluna ainda `is_retorno`):
     ```sql
     SELECT atendimento_origem_id, array_agg(id ORDER BY id), array_agg(status ORDER BY id)
     FROM atendimento
     WHERE is_retorno AND atendimento_origem_id IS NOT NULL
       AND status IN ('PENDENTE','AGENDADO','CONFIRMADO','REALIZADO')
     GROUP BY atendimento_origem_id HAVING count(*) > 1;
     ```
   - Sem ICU no Postgres a `0038` só avisa e pula a collation (não aborta).
   - A `0034` não aborta: normaliza telefone/CPF/e-mail, tira duplicatas (o cliente com mais
     atendimentos fica com o valor; os outros ficam ativos com o campo nulo) e registra cada caso
     na `LogAuditoria` (`migration 0034: ...`, com `telefone_original`) para mesclagem manual;
     promoção fora do CHECK é **desativada**.
3. **Variáveis no Railway** (§11): defina `ADMIN_EMAIL`/`ADMIN_PASSWORD`/`ADMIN_NOME` (senha ≥ 10,
   não comum), `CRON_TOKEN`, `SITE_URL`, `ZENVIA_*`, `EMAIL_BACKEND` + provedor, `WHATSAPP_*`,
   `TURNSTILE_*`, `SENTRY_DSN`, `CLINIC_EMAIL`, `WHATSAPP_NUMERO`; **remova `STATIC_ROOT`**.
   Confira no painel do Railway que não há *start command* manual antigo (`gunicorn
   shivazen.wsgi`) competindo com o `railway.json` (config-as-code: builder DOCKERFILE,
   pre-deploy, healthcheck `/healthz/`).
4. Merge `front-fundacao` → `main` (dispara o deploy).

### b) O que o deploy faz

1. Build da imagem (§13) — falha de front, collectstatic ou check derruba o build, nada muda.
2. Pre-deploy: `migrate_atomico` (0027→0046 numa transação; `lock_timeout` 5 s — se o deploy
   antigo segurar lock, falha e é só refazer) e `bootstrap_admin` (cria/reativa o ADMIN).
   Leia o log do pre-deploy: avisos `aranha.W00x`, `0042: ATENCAO ...` e
   `bootstrap_admin: ERRO — painel sem administrador ativo` indicam env faltando.
3. Container novo sobe; só recebe tráfego depois de `/healthz/` responder 200.
4. Até a troca, o código antigo roda contra o schema novo por alguns segundos (erros pontuais
   esperados — por isso o horário sem movimento).

### c) Depois do deploy — verificação

```bash
curl -fsS https://<dominio>/healthz/                  # {"status": "alive"}
curl -fsS https://<dominio>/health/                   # db/cache/front = true
railway ssh   # shell no container:
python manage.py check --database default             # lista W001–W009 pendentes
python manage.py showmigrations aranha_estetica | tail -3   # [X] 0046
```

### d) Jobs periódicos (cron externo)

Crie um agendamento por job da tabela do §10 (`POST`, header `X-Cron-Token`):
- **cron-job.org** (mais simples): um job por URL, método POST, header customizado
  `X-Cron-Token`, fuso `America/Sao_Paulo` (use a coluna BRT), timeout alto e alerta de falha
  por e-mail (a resposta 500 indica job com erro).
- **Railway Cron**: serviço separado (imagem com `curl`) com *Cron Schedule* e o comando
  `curl -fsS -X POST -H "X-Cron-Token: $CRON_TOKEN" https://<dominio>/cron/run/<job>/`; o
  agendamento do Railway é em **UTC** (use a coluna UTC); um serviço por horário.

Teste cada um manualmente uma vez (resposta `{"ok": true, ...}`).

### e) Primeiro acesso

1. Entre em `/admin-login/` com `ADMIN_EMAIL`/`ADMIN_PASSWORD`. O painel **obriga o cadastro do
   2FA**: escaneie o QR em `/painel/seguranca/2fa/` e confirme o código. Para ter códigos de
   backup, rode `python manage.py setup_2fa <email> --force` pelo `railway ssh` (gera novo TOTP
   + 10 códigos; guarde-os).
2. **Remova `ADMIN_PASSWORD` do Railway** (fica em texto puro; o comando não faz nada sem ela).
3. Rode `python manage.py axes_reset` (limpa bloqueios de login herdados do deploy antigo).
4. **Painel > Branding**: telefone, WhatsApp (DDI+DDD+número), e-mail de contato, endereço e
   horário reais — a tela vence a env; campo vazio esconde o item no site.
5. **Painel > Termos**: revise o **termo LGPD v1.0** criado pela `0045` (resumo da política de
   privacidade). Depois do primeiro aceite ele fica imutável — correções viram nova versão. Crie
   os termos de PROCEDIMENTO necessários.
6. Confira Configurações (`email_admin` do alerta de detrator), profissionais (expediente,
   `max_advance_dias`), procedimentos/preços e a `LogAuditoria` com `migration 0034:` (duplicatas
   a mesclar e telefones inválidos a corrigir).
7. Faça um agendamento real de ponta a ponta (SMS chega, e-mail sai, aprovação no painel).

Sem acesso ao painel (2FA perdido, lockout): `railway ssh` → `python manage.py setup_2fa <email>
--force` (novo QR ASCII + códigos) e/ou `python manage.py axes_reset`. Emergência extrema:
`ADMIN_2FA_OBRIGATORIO=false` + redeploy (gera W008; volte assim que possível).

### f) System checks (`aranha.W00x`)

Aparecem no log de todo `manage.py` fora de DEBUG (inclusive no pre-deploy). Os que consultam o
banco (W004/W007 via Branding, W006) só rodam com `check --database default` ou no `migrate`.

| Check | Significa | Como resolver |
|---|---|---|
| `W001` | `SITE_URL` não é https público | `SITE_URL=https://<dominio>` |
| `W002` | SMS sem provedor (ou `SMS_DEV_LOG_ONLY=true` em prod) — OTP falha | `ZENVIA_API_TOKEN` + `ZENVIA_FROM`; remover `SMS_DEV_LOG_ONLY` |
| `W003` | Backend de e-mail que não entrega (dummy/console/arquivo) | `EMAIL_BACKEND` real + credenciais |
| `W004` | WhatsApp vazio/inválido — botões e fallback ocultos | Branding ou `WHATSAPP_NUMERO` |
| `W005` | `CRON_TOKEN` ausente — nenhum job roda | Definir e configurar o cron |
| `W006` | Nenhum ADMIN ativo com senha — ninguém entra no painel | `ADMIN_EMAIL`/`ADMIN_PASSWORD` + redeploy |
| `W007` | `CLINIC_EMAIL` vazio — sem canal do titular LGPD | Branding ou env |
| `W008` | `ADMIN_2FA_OBRIGATORIO=false` em prod | Remover a env |
| `W009` | SMS real sem Turnstile — cota global de SMS vulnerável | `TURNSTILE_SITE_KEY` + `TURNSTILE_SECRET_KEY` |

Build: `django_vite.W001` (manifest ausente) reprova o `check --tag staticfiles` do Dockerfile.

### g) Rollback

**Rollback = restaurar o dump.** Os *reverse* das migrations não recuperam dados (renomeações,
dedup da 0034 — cujo reverse não recria o UNIQUE global de CPF —, contas demo desativadas,
termo criado, triggers). Não use `migrate aranha_estetica <anterior>`.

1. Railway → Deployments → *Redeploy* do último deployment anterior (código da `0026`).
2. Restaure o banco:
   ```bash
   pg_restore --clean --if-exists --no-owner -d "$DATABASE_PUBLIC_URL" prod_0026_<data>.dump
   ```
3. Confira `/health/` e um login. Tudo gravado entre o deploy e o rollback se perde (exporte
   antes o que for preciso).

### h) Operação contínua

- **CHECKs `NOT VALID` da 0043**: depois de inspecionar os dados, valide no Postgres:
  `ALTER TABLE prontuario VALIDATE CONSTRAINT chk_prontuario_extras_objeto;` (idem
  `resposta_anamnese.chk_resposta_anamnese_objeto` e `formulario_anamnese.chk_formulario_schema_lista`).
- **Collation `pt_br`** fica fora do estado do Django: um `AlterField` futuro em
  `cliente/profissional/procedimento.nome` volta para a collation padrão — reaplique com `RunSQL`
  (`ALTER TABLE <t> ALTER COLUMN nome TYPE varchar(N) COLLATE pt_br`; N = 150 cliente, 100 os outros).
- **Link do ICS vazado**: Painel > Profissionais > botão "Novo link" ao lado do feed (o antigo
  para de funcionar; auditado).
- **Escalar**: mais de 1 worker gunicorn exige `REDIS_URL` (rate-limit, axes, lock de slot e quota
  de SMS são por processo no LocMem).
- **Backups**: mantenha backup automático do Postgres (plano do Railway) ou `pg_dump` periódico.

---

## 15. Convenções de código

- Regra de negócio em `services/`/`utils/`, não em view; mudança de status só pela FSM.
- Toda escrita multi-tabela em `transaction.atomic`; I/O externo (e-mail, WhatsApp, push) só no
  `on_commit`, best-effort.
- Datas "hoje/amanhã" sempre no fuso da clínica (`utils/datas`), nunca `date.today()`.
- Ação sensível → `utils/audit.registrar_log(..., request=request)` (grava IP; sem nome de cliente
  no texto — use pk em `detalhes`).
- Templates: CSP estrita (sem `onclick`, `<script>`/`<style>` com `nonce`), lógica Alpine em
  `Alpine.data()` no `app.js`, tokens de cor do `tokens.css` (nunca cor crua). Lib nova no CDN =
  atualizar `JSDELIVR_PATHS` + SRI.
- Copy do site em tom de estética/bem-estar (cliente, atendimento, avaliação — nunca
  "paciente/consulta médica").
- Migrations: nunca editar migration já aplicada em produção; DDL só-Postgres via `RunPython`
  com checagem de vendor; teste no Postgres (CI) antes do merge.
- Commits: Conventional Commits (`feat(scope)`, `fix(scope)`, `docs(scope)`...).
- Docs: este arquivo + requisitos (`python docs/gerar_requisitos.py`) no mesmo PR da mudança.

---

## 16. Histórico

| Quando | Marco |
|---|---|
| 2026-05 | Auditoria inicial, CSP com nonce, split do booking |
| 2026-06 | Remodelagem do banco v2.1 (0027–0038: 50 → 33 tabelas, invariantes no banco); fundação do front (Vite/Tailwind/cotton/Alpine CSP) e migração de 100% das telas; auditoria SWE de backend (0039) |
| 2026-09-23 | Auditoria pré-produção completa (~450 correções em ~30 commits, `b5fd681..182e2e9`): OTP preso ao telefone em todo agendamento, consentimentos LGPD/art. 11 com prova, 2FA obrigatório do ADMIN, histórico do prontuário, prova de aceite imutável, deploy por Dockerfile + `migrate_atomico`, retenção LGPD efetiva, migrations 0040–0046. Detalhe em [`ARCHITECTURE.md`](ARCHITECTURE.md#6-progresso-checkpoint) |

**Mantenedor:** Rafael Maciel · **Última atualização:** 2026-09-23
