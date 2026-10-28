# Jaqueline Aranha Estética (Shiva Zen) — Documentação do Projeto

> **Fonte viva textual do projeto.** Reflete o código do branch `dev`
> (migrations `0001`–`0060`). Mudou model, rota, env var, job ou deploy? Atualize aqui
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
| Libs no CDN (com SRI) | FullCalendar 6.1.11 (`painel/calendar.html`), Chart.js 4.4.0 (`painel/overview.html`) |
| Fontes | Playfair Display + Lato (Google Fonts) — tokens em `static/src/css/tokens.css` |
| Assíncrono | Celery 5.4. Em produção roda **eager** (sem worker) salvo `CELERY_WORKER_ENABLED=true`; jobs periódicos via cron HTTP |
| Cache / sessão | LocMemCache + sessão no banco (padrão, 1 worker). `REDIS_URL` troca para django-redis + sessão em cache |
| Auth | Usuário próprio (`AbstractBaseUser`, e-mail), django-axes 8, django-otp + django-two-factor-auth (só models/TOTP; rotas `/account/*` **não** publicadas), django-ratelimit 4 |
| API | Sem API REST (a DRF saiu: não tinha consumidor). Endpoints JSON do próprio site (wizard, painel) são views Django |
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
├── models/          acesso, clientes, profissionais, agenda (AgendaHorario/AgendaExcecao), procedimentos,
│                    agendamentos, prontuario, anamnese, termos, nps, pacotes, sistema, push,
│                    extras (carteira/comissão), historico (trilha historico_alteracao), mixins
├── db/              peças de banco usadas por migrations (Indice, PadronizarNomes) — congelado
├── migrations/sql/  texto SQL das rotinas por versão (v0056 triggers, v0057 leitura/views, v0058
│                    procedures, v0059 trilhas append-only) — congelado, importado só pela migration da versão
├── views/           public, booking_public/_otp/_reagendar/_api, agenda_publica, anamnese_publica,
│                    notificacoes (confirmar presença), lgpd, profissional, prontuario, auth, admin_2fa,
│                    dashboard, admin*, pacotes, relatorios, cron, health, whatsapp, webpush_views
├── services/        agenda (reservar/reagendar/mover), agendamento_service, disponibilidade (SlotService),
│                    otp, lgpd, termos, alertas,
│                    comissao_service, fidelidade_service, retorno_service, lista_espera_service,
│                    anamnese, auditoria, push, gcal
├── domain/          event_bus.py (EventBus síncrono, na transação da FSM), events.py, handlers.py (logging)
├── utils/           branding, email, sms, whatsapp, precos, saude, fichas, security, dois_fatores,
│                    pii, captcha, datas, parse, busca (trigram no PG), audit, recorrencia (RRULE do
│                    subconjunto), nps (calculo unico, meio p/ cima), banco (GUCs app.* e CALL das
│                    procedures)
├── forms/           cliente.py (ClientePainelForm)
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
├── sinais_celery.py contexto da trilha (app.origem=celery:<task>) nas tasks do worker real
├── middleware.py    Enforce2FAMiddleware, SecurityHeadersMiddleware, ContentSecurityPolicyMiddleware,
│                    ContextoBancoMiddleware (autor/IP/origem p/ a trilha historico_alteracao)
├── checks.py        system checks de produção aranha.W001–W009
├── excecoes.py      RegraDeNegocio (mensagem + codigo), HorarioIndisponivel, TransicaoInvalida
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
                  └─ EventBus (na mesma transação da FSM): comissão, cashback, retorno
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
  `CashbackLiberado`, `CashbackEstornado`, `ComissaoCalculada`. Síncrono, in-process e **na
  mesma transação da transição**: handler que falha é logado e desfaz a transição inteira
  (status + efeitos gravam juntos ou nada grava); I/O externo vai em `on_commit`.
  `cancelar(..., pela_cliente=True)` só nos fluxos da própria cliente (link/portal).
- **Aviso de cancelamento** (`services/agendamento_service.avisar_cancelamento`): e-mail à
  cliente quando a clínica cancela — painel, rejeição, Django admin e pedido PENDENTE que venceu
  sem aprovação. Cancelamento pela própria cliente não avisa.
- **Disponibilidade**: fonte única `services/disponibilidade.py::SlotService` (slots de 30
  min; grade semanal `AgendaHorario` e exceções `AgendaExcecao` — folga/feriado/recesso,
  horário diferente, bloqueios pontuais/recorrentes do profissional ou globais —, buffers,
  `min_notice_horas`, `max_advance_dias`). Usada pelo wizard, pelo
  reagendamento, pelo agendamento interno e pela validação do servidor.
- **Agenda** (`services/agenda.py`): as 5 portas que gravam horário (wizard, agendamento pelo
  painel, reagendamento pelo portal/link, arrasto do calendário, retorno automático) passam por
  `validar_horario` (habilitação, horário futuro, feriado/recesso, SlotService; `detalhado=True`
  dá o motivo à equipe — conflito, bloqueio, folga, expediente — e o site vê a mensagem
  genérica), `reservar` (conflito com `select_for_update`, preço da data, INSERT em savepoint;
  corrida perdida para o EXCLUDE vira `HorarioIndisponivel`), `reagendar` (antigo REAGENDADO +
  novo com preço/promoção e ficha) e `mover`. O que é só de uma porta fica nela (OTP e
  consentimentos do wizard, valor combinado do painel, link e e-mails do portal).
- **Recusas** (`excecoes.py`): service diz "não" levantando `RegraDeNegocio(mensagem, codigo=)`
  (ou `HorarioIndisponivel`/`TransicaoInvalida`); a view só mostra `exc.mensagem` (ou
  `json_recusa(exc)`). `Atendimento.TransicaoInvalida` é a mesma classe.
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
| `migrate_atomico [--noinput]` | `migrate` numa transação única no Postgres (tudo ou nada), `SET LOCAL lock_timeout = '5s'`, `SET LOCAL app.origem = 'migracao'` (trilha `historico_alteracao`), `SET CONSTRAINTS ALL IMMEDIATE` entre migrations. Fora do Postgres vira `migrate` normal. Usado no pre-deploy e na CI. |
| `setup_2fa <email> [--force] [--name]` | Cria TOTP confirmado, imprime URI `otpauth://` + QR ASCII e 10 códigos de backup. `--force` = reset completo (apaga todos os devices OTP do usuário; auditado). Recuperação de acesso da equipe. |
| `carregar_feriados [--ano N]` | Feriados nacionais (fixos + móveis via Páscoa), idempotente; sem `--ano` = ano atual e seguinte. |
| `gerar_chave_cifragem` | Imprime uma chave nova para `CHAVES_CIFRAGEM`. |
| `rotacionar_chave_cifragem [--dry-run]` | Recifra tudo com a 1ª chave de `CHAVES_CIFRAGEM` (inclusive a trilha do prontuário), numa transação, com os triggers de usuário das tabelas desligados durante o UPDATE; auditado. Passo do meio da rotação (nova na frente → rodar → tirar a antiga). |

Úteis do Django/libs: `check`, `check --deploy`, `check --database default` (roda também o
W006), `makemigrations --check --dry-run`, `createsuperuser` (cria ADMIN), `axes_reset`
(limpa bloqueios do django-axes), `clearsessions`.

---

## 5. Banco de dados

### 5.1 Resumo

- **36 tabelas de domínio** (+ tabelas do Django, django-otp, django-axes). PK `BigAutoField`.
- Migrations `0001`–`0060`. Ainda não há produção: o primeiro deploy cria o banco do zero (§14).
  O Postgres do ambiente de desenvolvimento no Railway (na `0026`) está em stand-by. Histórico em
  [`specs/remodelagem-banco-v2.md`](specs/remodelagem-banco-v2.md) e, da `0048` em diante, o padrão
  SQL em [`specs/padrao-sql.md`](specs/padrao-sql.md).
- **Lógica no banco** (0056–0059, só Postgres — §5.4): 46 triggers (`atualizado_em`,
  imutabilidade, validação, trilha `historico_alteracao`), 23 funções `fn_*`, 9 views `vw_*` de
  relatório e 4 procedures `sp_*` que são a **fonte única** da escrita de retenção/LGPD. Exige as
  extensões `btree_gist`, `pg_trgm` e `unaccent` (o PG 18 do Railway tem).
- **Padrão SQL em todas as tabelas** (0054/0055): nomes ASCII snake_case PT-BR no singular;
  colunas `eh_`/`tem_` (booleano), `data_`/`hora_`/`_em`, `valor_`, `qtd_`, `url_`, `_hash`
  (`char(64)`), `_ref` (id externo), FK `<tabela>[_papel]_id` e autoria `usuario_autor_id`;
  objetos `pk_`/`fk_` (com `ON DELETE` real, `DEFERRABLE INITIALLY DEFERRED`)/`uq_`/`ck_`/`ix_`/
  `ex_`/`tg_`/`fn_`; sem índice `_like`; `COMMENT` em toda tabela; todo default também no banco
  (`db_default`); textos `NOT NULL DEFAULT ''` (exceto e-mail/CPF/telefone sob UNIQUE parcial e
  dado pessoal opcional: ausente = `NULL`, nunca `''`). Conformidade testada no Postgres por
  `tests/test_nomes_padrao.py`.
- **Nome só no banco**: atributos e classes Python não mudaram — o model aponta a coluna/tabela
  por `db_column`/`db_table` (ex.: `Cliente.ativo` → `eh_ativo`, `Procedimento.buffer_minutos` →
  `intervalo_minutos`, `ItemPacote` → `pacote_item`, `FormularioAnamnese` → `formulario`). SQL
  cru usa o nome do banco; mapa completo no §3.2 da spec.
- Nenhum token de link público fica em texto puro: `link_acesso` guarda só o `sha256` (0049).

### 5.2 Tabelas

| Domínio | Tabela (model) | Para que serve |
|---|---|---|
| Acesso | `usuario` (Usuario) | Equipe. Login por e-mail; `papel` ADMIN/PROFISSIONAL/RECEPCAO (CHECK); `is_staff` = ADMIN; 1:1 opcional com Profissional; `eh_ativo` (desativar, nunca excluir) |
| | `assinatura_push` (AssinaturaPush) | Inscrições Web Push (VAPID) por usuário (`usuario_id`, `chave_p256dh`, `chave_auth`) |
| | `log_auditoria` (LogAuditoria) | Trilha LGPD art. 37: autor (`usuario_autor_id`) + snapshot `usuario_autor_nome` ("Nome <email>"), ação, `tabela` + `registro_pk` (bigint), detalhes JSON (PII mascarada), IP. Append-only no PG (0059): só a purga com 180+ dias, a pseudonimização do esquecimento e o `SET NULL` do autor |
| | `historico_alteracao` (HistoricoAlteracao) | **Trilha de dados** gravada por trigger (0056, só PG) em prontuario, atendimento, compra_pacote, carteira, regra_comissao, preco, promocao, pacote, versao_termo, usuario e configuracao: operação, só as `colunas_alteradas` com antes/depois (`senha_hash` = `"[redigido]"`), autor/IP/origem da sessão (`web`, `cron:<job>`, `celery:<task>`, `migracao`), `transacao_ref`. Append-only; Django admin só leitura (leitura auditada). `cliente` fica fora de propósito (PII; o esquecimento obrigaria a expurgar a trilha) |
| Pessoas | `cliente` (Cliente) | Cliente da clínica. Telefone só dígitos (10–11) é a chave natural; e-mail/CPF opcionais (ausente = `NULL`); consentimentos de comunicação na tabela `consentimento` (histórico, 0050); `qtd_faltas_consecutivas`/`eh_bloqueado_online`; soft delete (`deletado_em`); `cliente_indicador_id` (cashback); `nome` com collation `pt_br` |
| | `profissional` (Profissional) | Quem atende. `antecedencia_minima_horas` (0–720, padrão 2), `antecedencia_maxima_dias` (1–365, padrão 60), campos do Google Calendar (`gcal_token_renovacao`, `gcal_calendario_ref`...). Feed ICS por `link_acesso` (FEED_ICS) |
| Catálogo | `procedimento` (Procedimento) | Serviço: duração, `intervalo_minutos` (folga após, 0–120), categoria, modalidade, retorno obrigatório (`tem_retorno_obrigatorio` + janela em dias + duração do retorno) |
| | `habilitacao` (Habilitacao) | Profissional × procedimento (M2M through) |
| | `preco` (Preco) | Preço versionado por `data_inicio_vigencia`, base (sem profissional) ou por profissional |
| | `promocao` (Promocao) | Desconto 0–100% **XOR** preço fixo, período, procedimento (nulo = geral, só percentual vale) |
| Agenda | `agenda_horario` (AgendaHorario) | Grade semanal: várias janelas por dia, sem sobreposição; `dia_semana` **ISO** (1=seg … 7=dom = `isoweekday()`) |
| | `agenda_excecao` (AgendaExcecao) | Tudo que muda a grade numa data: **FOLGA** (intervalo `data_inicio..data_fim`; sem profissional = clínica inteira: feriado nacional/local, recesso), **HORARIO_DIFERENTE** (substitui a grade num dia) e **BLOQUEIO** (`data_hora_*`, pontual ou recorrente por RRULE no subconjunto DAILY/WEEKLY/MONTHLY + `data_fim_recorrencia`; sem profissional = "Todos"). `origem` MANUAL/FERIADO_NACIONAL/FERIADO_LOCAL/RECESSO/GCAL (+ `origem_ref` = id do evento Google); `eh_ativo=false` = registrada e ignorada (ex.: feriado em que a clínica abre). 0048 unificou as 4 tabelas antigas |
| | `atendimento` (Atendimento) | Agendamento/sessão. FSM de 7 estados, preço gravado (`valor_cobrado`, `valor_original`, `promocao`, `descricao_preco`), retorno (`eh_retorno` + `atendimento_origem_id`), `atendimento_reagendado_id` (o antigo que este substitui) |
| | `notificacao` (Notificacao) | Mensagem por atendimento: tipo (LEMBRETE, LEMBRETE_2H, CONFIRMACAO, CANCELAMENTO, NPS, PESQUISA, APROVACAO, TERMO), canal, status PENDENTE/ENVIADO/FALHOU; o link público (/confirmar/, /nps/, /termo/) vive em `link_acesso` |
| | `lista_espera` (ListaEspera) | Pedido de aviso de vaga (procedimento, data, turno, `profissional_desejado_id` opcional, `email_contato`, `eh_notificado`) |
| Clínico | `prontuario` (Prontuario) | Ficha base 1:1 com a cliente: alergias, contraindicações, histórico, medicamentos, observações + `respostas` (JSONB `{key: valor}` do questionário do prontuário = formulário PRONTUARIO ativo, 0051; atributo Python `respostas_extras`) |
| | `prontuario_versao` (ProntuarioVersao) | **Histórico append-only** da ficha: foto do estado anterior a cada edição, com autor (`usuario_autor_id` + `usuario_autor_nome`, 0046) |
| | `anotacao_sessao` (AnotacaoSessao) | Nota clínica por atendimento; autor PROTECT + snapshot `usuario_autor_nome` |
| | `formulario` (FormularioAnamnese) | Questionário único em `perguntas` (`[{key, tipo, label, obrigatorio, opcoes?, alerta_saude?}]`): ANAMNESE e PESQUISA (respondidas pela cliente) e PRONTUARIO (perguntas extras da ficha, preenchidas pela equipe — no máximo 1 ativo, sempre GLOBAL, nunca obrigatório; 0051). Escopo + `eh_obrigatorio`. Era `formulario_anamnese` até a 0054 |
| | `resposta_formulario` (RespostaAnamnese) | Ficha respondida (`respostas`, `respondido_em`) por cliente/atendimento; link público do convite em `link_acesso` (FORMULARIO, 60 dias). Era `resposta_anamnese` até a 0054 |
| LGPD | `versao_termo` (VersaoTermo) | Termo versionado (LGPD, SAUDE ou PROCEDIMENTO); 1 ativo por escopo; imutável depois do 1º aceite |
| | `aceite_termo` (AceiteTermo) | Prova do aceite: cliente, versão, atendimento, `ip_origem`, user-agent, `conteudo_hash` (SHA-256 do texto, `char(64)`; `NULL` = aceite anterior à 0044), data. Imutável (trigger) |
| | `codigo_otp` (CodigoOtp) | Desafio OTP (`codigo_hash` HMAC-SHA256 com a SECRET_KEY, `char(64)`) por `identificador` (e-mail ou `sms+<dígitos>@shivazen.local`), propósito AGENDAMENTO/LOGIN_CLIENTE/DSAR, TTL, `qtd_tentativas` |
| | `consentimento` (Consentimento) | **Histórico append-only** de concessão/revogação por finalidade × canal (MARKETING/EMAIL, PESQUISA_SATISFACAO/WHATSAPP, LEMBRETE_AGENDAMENTO/WHATSAPP): origem (AGENDAMENTO_ONLINE, DESCADASTRO, PAINEL, ESQUECIMENTO, MIGRACAO, ADMIN), texto exibido, IP, user-agent, autor, `registrado_em`. Estado atual = linha mais recente do par; sem linha = não concedido (0050) |
| | `link_acesso` (LinkAcesso) | Link público com token ("link mágico"): `finalidade` (GERENCIAR_AGENDAMENTO — só os migrados —, CONFIRMAR_PRESENCA, NPS, TERMO, FORMULARIO, DESCADASTRO, FEED_ICS) + exatamente 1 alvo (atendimento, notificação, ficha, cliente ou profissional); só o **SHA-256** do token (`token_hash char(64)`, sem pepper: rotacionar a SECRET_KEY não derruba link enviado), `expira_em`, `usado_em`, `revogado_em`. O token em claro só existe na URL entregue (0049) |
| NPS | `avaliacao_nps` (AvaliacaoNPS) | Nota 0–10 + comentário, `eh_publicacao_autorizada` (opt-in da cliente) e `eh_publicacao_aprovada` (moderação; CHECK: só com o opt-in), `eh_alerta_enviado` |
| Pacotes | `pacote` / `pacote_item` | **Catálogo** (editável a qualquer momento): pacote (`valor_total`, validade em meses) e seus itens (procedimento × `qtd_sessoes`) — só vale p/ vendas futuras. `pacote_item` era `item_pacote` até a 0054 |
| | `compra_pacote` (CompraPacote) | Venda a uma cliente (`CompraPacote.vender`): `pacote_nome` congelado, `valor_pago`, `valor_reembolsado`, status ATIVO/FINALIZADO/CANCELADO/EXPIRADO, `data_expiracao` |
| | `compra_pacote_item` (CompraPacoteItem) | **O que foi vendido** (snapshot imutável, 0052): procedimento, `procedimento_nome`, `qtd_sessoes`, `valor_unitario` (= valor pago ÷ sessões da compra — base da comissão da sessão). Saldo, débito, finalização, e-mail de expiração e DSAR leem daqui |
| | `consumo_sessao` (ConsumoSessao) | 1 sessão consumida por atendimento (OneToOne) de 1 item da compra (`compra_pacote_item`) |
| Financeiro | `carteira` / `movimento_carteira` | Saldo da cliente (`valor_saldo`, CHECK ≥ 0) e ledger append-only (trigger; `valor` > 0, `valor_saldo_resultante` ≥ 0, autor `usuario_autor_id`) — hoje só cashback de indicação e estorno |
| | `regra_comissao` / `movimento_comissao` | Regra (`percentual` 0–100 XOR `valor_fixo`; usada = PROTECT, só desativa) e **livro-razão** de comissão (0053): CREDITO (1 por atendimento, `regra_comissao_id`), PAGAMENTO e ESTORNO (referenciam o crédito, `usuario_autor`). Status PENDENTE/PAGA/ESTORNADA e saldo (pode ficar negativo) são derivados (`creditos_com_status`, `ComissaoService.saldos_por_profissional`) |
| Infra | `configuracao` (Configuracao) | Chave-valor: campos do Branding, `email_admin`, `MAX_FALTAS_BLOQUEIO` (opcional). As perguntas do prontuário saíram daqui na 0051 (formulário PRONTUARIO) |

### 5.3 Invariantes garantidas pelo banco

| Invariante | Objeto | Migration |
|---|---|---|
| Profissional não atende 2 clientes ao mesmo tempo (status PENDENTE/AGENDADO/CONFIRMADO) | `ex_atendimento_sobreposicao` — EXCLUDE gist com `btree_gist`, DEFERRABLE (**só Postgres**) | 0035 / 0055 |
| Telefone/e-mail/CPF únicos entre clientes ativos (sem caixa no e-mail); vazio não existe (ausente = `NULL`) | `uq_cliente_telefone_ativo`, `uq_cliente_email_ativo` (`UPPER(email)`: também é o índice do `email__iexact`), `uq_cliente_cpf_ativo`, `ck_cliente_email_nao_vazio` | 0034 / 0055 |
| Formato de telefone (10–11 dígitos) e CPF (11) | `ck_cliente_telefone_formato`, `ck_cliente_cpf_formato` (**só Postgres**) | 0034 / 0055 |
| 1 termo ativo por escopo; 1 preço por vigência; 1 espera pendente por cliente/procedimento/data | `uq_versao_termo_ativa_por_procedimento`/`_global`, `uq_preco_vigencia`/`uq_preco_base_vigencia`, `uq_lista_espera_pendente` | 0034 / 0055 |
| Promoção: desconto 0–100, preço ≥ 0, desconto XOR preço, geral só percentual | `ck_promocao_*` | 0034 / 0047 / 0055 |
| Carteira nunca negativa; ledger imutável com valor > 0, tipo/origem no domínio e saldo resultante ≥ 0 | `ck_carteira_valor_saldo_nao_negativo`, `ck_movimento_carteira_*`; trigger `tg_movimento_carteira_imutavel` (`fn_bloquear_mutacao`, **só PG**) | 0034 / 0038 / 0054 / 0055 |
| Valor pago de pacote ≥ 0; nota NPS 0–10 (aprovar depoimento exige o opt-in); comissão 0–100%, valor fixo ≥ 0 | `ck_compra_pacote_valor_pago_nao_negativo`, `ck_avaliacao_nps_nota_0_10`, `ck_avaliacao_nps_aprovacao_exige_autorizacao`, `ck_regra_comissao_*` | 0039 / 0044 / 0055 |
| No máximo 1 retorno vivo por atendimento de origem; valores do atendimento ≥ 0 | `uq_atendimento_retorno_por_origem`, `ck_atendimento_valores_nao_negativos` | 0043 / 0055 |
| Notificação e OTP com tipo/canal/status/propósito no domínio; tentativas 0–10; hashes `sha256` hex | `ck_notificacao_{tipo,canal,status,resposta}`, `ck_codigo_otp_{canal,proposito,qtd_tentativas_0_10}`; `ck_codigo_otp_codigo_hash_formato`, `ck_aceite_termo_conteudo_hash_formato` (**só PG**) | 0055 |
| JSON no formato certo (objeto/lista) | `ck_prontuario_respostas_objeto`, `ck_resposta_formulario_respostas_objeto`, `ck_formulario_perguntas_lista` (validados na 0055), `ck_log_auditoria_detalhes_objeto`, `ck_prontuario_versao_dados_objeto` (**só PG**) | 0043 / 0055 |
| Prova de aceite imutável; termo aceito imutável; histórico da ficha imutável | triggers `tg_aceite_termo_imutavel`, `tg_versao_termo_imutavel`, `tg_prontuario_versao_imutavel` (**só PG**; SQLSTATE 23000 → `IntegrityError`) | 0046 / 0054 |
| Ordenação pt-BR de nomes | collation ICU `pt_br` em `cliente/profissional/procedimento.nome`, no estado do Django (`db_collation`; o SQLite dos testes tem um equivalente em `db/collation.py`) | 0038 / 0054 |
| Um cashback por atendimento | `uq_movimento_carteira_cashback_por_atendimento` | 0025/0032 / 0055 |
| Padrão de nomes: PK `pk_<t>`, FK `fk_<t>_<coluna>` com `ON DELETE` igual ao `on_delete` do model, UNIQUE de OneToOne `uq_`, sequência `<t>_id_seq`, nenhum índice `_like`, `COMMENT` em toda tabela | `PadronizarNomes()` + `tests/test_nomes_padrao.py` (**só PG**) | 0055 |
| Grade sem janelas sobrepostas; folgas ativas do mesmo profissional sem sobreposição | `ex_agenda_horario_sobreposicao`, `ex_agenda_excecao_folga_sobreposta` — EXCLUDE gist (**só PG**) | 0048 |
| Exceção coerente com o tipo (FOLGA = datas, HORARIO_DIFERENTE = 1 dia + horas, BLOQUEIO = instantes); feriado/recesso só como FOLGA da clínica; GCAL só BLOQUEIO de profissional | `ck_agenda_excecao_*` (tipo, origem, folga, horario_diferente, bloqueio, recorrencia, feriado_global, gcal) | 0048 |
| RRULE só no subconjunto que o motor expande | `ck_agenda_excecao_regra_recorrencia_suportada` (regex, **só PG**; `clean()` normaliza) | 0048 |
| 1 horário diferente ativo por profissional/dia; 1 feriado por origem/data; importação do Google idempotente | `uq_agenda_excecao_horario_diferente_dia`, `uq_agenda_excecao_feriado`, `uq_agenda_excecao_gcal_evento` | 0048 |
| Link público: alvo coerente com a finalidade (1 FK); 1 link por hash; hash = sha256 hex; depois de criado só `usado_em`/`revogado_em` mudam (uma vez cada) | `ck_link_acesso_finalidade`, `ck_link_acesso_alvo_por_finalidade`, `uq_link_acesso_token_hash`; `ck_link_acesso_token_hash_formato` e trigger `tg_link_acesso_imutavel` (**só PG**) | 0049 |
| Consentimento: só os 3 pares finalidade × canal que existem, origem no domínio; histórico append-only (só a anonimização pode apagar IP/user-agent) | `ck_consentimento_finalidade`, `_canal`, `_finalidade_canal`, `_origem`; trigger `tg_consentimento_imutavel` (**só PG**) | 0050 |
| Questionário: tipo/escopo no domínio e coerentes (escopo PROCEDIMENTO/CATEGORIA/MODALIDADE com o alvo); PRONTUARIO sempre GLOBAL e não obrigatório; no máximo 1 PRONTUARIO ativo | `ck_formulario_tipo`, `ck_formulario_escopo`, `ck_formulario_escopo_coerente`, `ck_formulario_prontuario_global`, `uq_formulario_prontuario_ativo` | 0051 |
| Pacote vendido: 1 item por procedimento na compra, sessões > 0, valor ≥ 0; item imutável; o consumo é de um item **da mesma compra**, do **mesmo procedimento**, nunca de retorno e só com saldo | `uq_compra_pacote_item_compra_procedimento`, `ck_compra_pacote_item_*`; FK composta `fk_consumo_sessao_compra_pacote_item_compra`, triggers `tg_consumo_sessao_validar` e `tg_compra_pacote_item_imutavel` (**só PG**; o Python espelha no `save()`) | 0052 |
| Comissão: 1 CREDITO por atendimento (nem depois de estornado), ≤ 1 PAGAMENTO e ≤ 1 ESTORNO por crédito, valor > 0, origem só fora do CREDITO, regra só no CREDITO; PAGAMENTO/ESTORNO repetem profissional/atendimento/valor do crédito; estornado não se paga; ledger append-only | `uq_movimento_comissao_credito_por_atendimento`, `…_estorno_por_origem`, `…_pagamento_por_origem`, `ck_movimento_comissao_*`; triggers `tg_movimento_comissao_validar` e `tg_movimento_comissao_imutavel` (**só PG**) | 0053 |
| `atualizado_em` sempre atualizado, inclusive em `QuerySet.update()`, procedure e SQL manual (o valor que o ORM manda é respeitado) | `fn_definir_atualizado_em` + `tg_<t>_atualizado_em` nas 22 tabelas com a coluna (**só PG**; o teste de conformidade reprova tabela com a coluna e sem o trigger) | 0056 |
| Nota de sessão é append-only (correção = nova nota) | `tg_anotacao_sessao_imutavel` (**só PG**; o Python recusa save/delete/update) | 0056 |
| Toda escrita nas 11 tabelas sensíveis fica na trilha `historico_alteracao` (autor, IP, origem); a trilha é append-only — só o `SET NULL` do autor e a purga de retenção (flag local `app.expurgo_autorizado` **e** 180+ dias; prontuário 20 anos) passam | `fn_registrar_historico` + `tg_<t>_historico`; `fn_historico_alteracao_imutavel` + `tg_historico_alteracao_imutavel`; `ck_historico_alteracao_*` (**só PG**) | 0056 / 0059 |
| `log_auditoria` append-only: UPDATE só da pseudonimização (`acao` com `[ANONIMIZADO-<id>]`, `detalhes` NULL) e do `SET NULL` do autor; DELETE só com 180+ dias | `fn_log_auditoria_imutavel` + `tg_log_auditoria_imutavel` (**só PG**) | 0059 |

Além disso: FKs `PROTECT` em tudo que é histórico clínico/financeiro (prontuário, anotação,
ficha, compra de pacote, carteira, aceite), índices só onde há query real ou FK (spec §2.11; o
GIN de `prontuario` saiu na 0055 — nenhuma query de contenção), `COMMENT ON` em todas as tabelas
e nas colunas não óbvias. Detalhe por migration:
[`specs/remodelagem-banco-v2.md`](specs/remodelagem-banco-v2.md) (0027–0047) e
[`specs/padrao-sql.md`](specs/padrao-sql.md) (0048+).

### 5.4 Lógica no banco (0056–0060, só Postgres)

O SQLite dos testes não tem nada disto: o Python cobre (auto_now, `save()`/`delete()` que recusam,
fallback das procedures). Texto SQL versionado em `aranha_estetica/migrations/sql/vNNNN_*.py`
(mudar = módulo + migration nova com `CREATE OR REPLACE`). Detalhe e desvios: spec §5 e §5.9.

| Objeto | O que é | Quem usa |
|---|---|---|
| `tg_<t>_atualizado_em` (22) | `fn_definir_atualizado_em`: preenche `atualizado_em` quando o UPDATE não mexeu nela | todo UPDATE |
| `tg_<t>_historico` (11) | `fn_registrar_historico` (AFTER, por linha): grava `historico_alteracao` — só colunas alteradas; UPDATE só de `atualizado_em` não gera linha | prontuario, atendimento, compra_pacote, carteira, regra_comissao, preco, promocao, pacote, versao_termo, usuario (`senha_hash` redigida), configuracao |
| `tg_*_imutavel` / `tg_*_validar` (12) | append-only (`movimento_*`, `anotacao_sessao`, `compra_pacote_item`, `aceite_termo`, `versao_termo` aceita, `prontuario_versao`, `consentimento`, `link_acesso`, `historico_alteracao`) e validação (`consumo_sessao`, `movimento_comissao`) — SQLSTATE 23 → `IntegrityError` | — |
| `fn_fuso_clinica()`, `fn_data_local(ts)`, `fn_sem_acento(txt)` | fuso explícito (`America/Sao_Paulo` = `TIME_ZONE`), data local, `lower(unaccent())` IMMUTABLE | views/funções; `ix_cliente_nome_trgm` (GIN trigram) e a busca de cliente (`utils/busca.filtrar_clientes`: "joao" acha "João") |
| `fn_horarios_livres`, `fn_ocorrencias_recorrencia`, `fn_recorrencia_casa_dia`, `fn_saldo_pacote`, `fn_nps_periodo`, `fn_saldo_comissao`, `fn_tem_consentimento`, `fn_clientes_para_anonimizar` | **espelho de leitura** do Python (SlotService, `AgendaExcecao.ocorrencias`, `saldo_por_procedimento`, `utils/nps`, `ComissaoService`, `services/consentimentos`); `fn_clientes_para_anonimizar` é a regra da retenção (sem espelho: `LgpdService.candidatos_retencao` lê dela), travado por `tests/test_pg_paridade.py` | relatório/integração e as procedures — **o app continua no Python** (slots nunca vêm do SQL) |
| `vw_agenda_dia`, `vw_faturamento_mensal`, `vw_nps_mensal`, `vw_comissao`, `vw_comissao_saldo`, `vw_cliente_resumo`, `vw_consentimento_atual`, `vw_pacote_saldo`, `vw_atendimento_sem_desfecho` | views de relatório (mesmas regras de `services/financeiro`, NPS e comissão) | BI/SQL ad hoc (`railway connect` → `SELECT * FROM vw_faturamento_mensal`); o app não lê views |
| `sp_anonimizar_cliente(cliente, autor, ip)` | direito ao esquecimento (efeito completo testado em `test_pg_paridade`/`test_lgpd_esquecimento`) | `LgpdService.esquecer_cliente` (ação LGPD do admin) |
| `sp_aplicar_retencao_lgpd(...)` | anonimiza os candidatos (COMMIT por cliente), apaga fichas de pedido não realizado e espera vencida | `job_lgpd_purgar_inativos` (`LgpdService.aplicar_retencao`) |
| `sp_purgar_dados_expirados(...)` | OTP, texto de notificação, auditoria, `link_acesso` vencido/revogado, trilha antiga (cada etapa num sub-bloco) | `job_housekeeping` (etapa `banco`) |
| `sp_expirar_pacotes(hoje)` | compras ATIVO vencidas → EXPIRADO | `job_expirar_pacotes` |

**Contexto da sessão (GUCs `app.*`)** lido pela trilha: `app.usuario_id`, `app.ip_origem`,
`app.origem`. Quem define (`utils/banco.definir_contexto_banco`, `set_config(..., false)` = nível de
sessão) e **sempre limpa**: `ContextoBancoMiddleware` (`web` + usuário + IP; o `/healthz/` não toca
o banco), `views/cron.py` (`cron:<job>`), sinais do Celery com worker real (`celery:<task>`),
`migrate_atomico` (`SET LOCAL app.origem = 'migracao'`); conexão nova nasce limpa
(`connection_created`). SQL manual sai com autor `NULL` e origem `''`. **PgBouncer em modo
transação quebraria isso** (o Railway não usa): nesse dia, trocar para `ATOMIC_REQUESTS` +
`SET LOCAL`.

**Fonte única no Postgres** (decisão de 2026-10-27, ARCHITECTURE D23): `utils/banco.chamar('sp_…',
...)` (allowlist com a aridade) é o único caminho; não há fallback Python. Fora do Postgres ele
recusa com `ImproperlyConfigured` (dev: `docker compose up -d db`), e os testes dessas rotinas são
só-Postgres.

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
| `/meus-agendamentos/<id>/reagendar/` (+ `<id>/horarios/`) | Reagendar pelo portal: id + sessão do OTP (só agendamento do telefone da sessão; até 24h antes) |
| `POST /ajax/cancelar-agendamento/` | Cancelar: `{"atendimento_id"}` + sessão do portal, ou `{"token"}` do link legado |
| `/reagendar/<token>/` (+ `horarios/`) | Reagendar pelo link **legado** (tokens antigos migrados na 0049; expiram no início do atendimento). Nenhum link novo é emitido |
| `/confirmar/<token>/` | Confirmar presença / cancelar a partir do lembrete D-1 |
| `/termo/<token>/` | Aceitar termo(s) do atendimento (vale até o fim do atendimento) |
| `/nps/<token>/` | NPS (link de 7 dias) + opt-in de depoimento |
| `/anamnese/<token>/` · `/pesquisa/<token>/` (+ `obrigado/`) | Ficha/pesquisa por link (60 dias; ficha de saúde exige consentimento art. 11). **Mantidas, hoje sem produtor de link** (decisão pendente do dono: remover × gerar o link) |
| `/lgpd/meus-dados/` · `/lgpd/unsubscribe/<token>/` · `/lgpd/aceitar-cookies/` | DSAR com OTP, descadastro, registro do aviso de cookies |
| `/agenda/<slug>/feed.ics?token=` | Feed iCal do profissional (link gerado no painel ou no portal do profissional; gerar = revogar o anterior; a URL só aparece na geração) |

### 6.3 Equipe

| Rota | Função |
|---|---|
| `/admin-login/` · `/admin-logout/` (logout só via POST; GET mostra confirmação) · `/admin-login/recuperar/...` | Login da equipe e reset de senha |
| `/painel/seguranca/2fa/` (+ `verificar/`, `challenge/`) | Cadastro e desafio TOTP |
| `/profissional/` · `/profissional/atendimento/<pk>/{realizado,anotar,aprovar,rejeitar}/` · `POST /profissional/calendario/novo-link/` | Portal do profissional (+ gerar o próprio link do feed ICS) |
| `/painel/`, `/painel/overview/` | Dashboard |
| `/painel/agendamentos/` (+ `novo/`, `bulk/`, `<pk>/aprovar/`, `<pk>/rejeitar/`, `<pk>/termo-link/`, `<pk>/valor/`), `/painel/atualizar-status/` | Agenda em lista, agendamento interno, aprovação, link do termo, valor cobrado, mudança de status (409 `termo_pendente` sem o override) |
| `/painel/calendario/` (+ `eventos/`, `mover/`) | Calendário FullCalendar com arrastar-e-soltar |
| `/painel/clientes/`, `/painel/clientes/<pk>/` | Clientes e ficha do cliente (pacotes, alertas, histórico) |
| `/painel/prontuario/` (+ `<cliente_id>/`, `<cliente_id>/salvar/`), `/painel/anotacao/<id>/salvar/` | Prontuário com histórico de versões e anotações |
| `/painel/profissionais/` (+ `<pk>/ics/novo-token/`, `<prof_id>/excecoes/...`), `/painel/cadastrar-profissional/`, `/painel/editar-profissional/<pk>/` | Profissionais, exceções, gerar link do ICS (a lista mostra só "ativo desde"; a URL aparece uma vez, na geração) |
| `/painel/procedimentos/...`, `/painel/pacotes/...` (+ `vender/`, `compras/<pk>/cancelar/`), `/painel/promocoes/...` (+ `<pk>/disparar/`) | Catálogo, pacotes, promoções |
| `/painel/bloqueios/...`, `/painel/lista-espera/` (+ `<pk>/notificar/`) | Bloqueios de agenda e lista de espera |
| `/painel/financeiro/`, `/painel/comissoes/` (+ `<pk>/pagar/`), `/painel/nps/` (+ `<pk>/publicacao/`), `/painel/exportar-relatorio/` | Relatórios, comissões, NPS e moderação de depoimentos, Excel |
| `/painel/anamneses/...`, `/painel/termos/` (+ `criar/`, `compliance/`) | Formulários e termos |
| `/painel/notificacoes/`, `/painel/auditoria/`, `/painel/usuarios/...`, `/painel/configuracoes/...`, `/painel/branding/`, `/painel/email-preview/[<nome>/]` | Sistema |
| `/painel/integrations/google/{connect,callback,pull}/...` | Google Calendar (UI escondida sem `GOOGLE_OAUTH_*`) |
| `/webpush/{public-key,subscribe,unsubscribe}/` | Web Push da equipe |
| `/django-admin-sv/` | Django admin (só ADMIN com sessão 2FA verificada) |

### 6.4 Webhooks e infraestrutura

| Rota | Função |
|---|---|
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
| **Sentry** | Erros e transações amostradas; `send_default_pii=False`, sem variáveis locais nem corpo do request, `EventScrubber` recursivo (contato e dado de saúde) e filtros em erro, transação e breadcrumb que redigem tokens de link, e-mail/CPF/telefone e o DETAIL do Postgres (`utils/pii.opcoes_sentry`) | `SENTRY_DSN`, `SENTRY_TRACES_SAMPLE_RATE` | Desligado |
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
- **Comunicação:** três opt-ins no wizard — promoções por e-mail (MARKETING/EMAIL), lembrete
  D-1 e aviso de vaga por WhatsApp (LEMBRETE_AGENDAMENTO/WHATSAPP) e pesquisa de satisfação por
  WhatsApp (PESQUISA_SATISFACAO/WHATSAPP) — nunca pré-marcados. Cada concessão/revogação é uma
  linha nova em `consentimento` (append-only) com o texto exibido, IP, user-agent e data; o
  estado vale pela linha mais recente (`services/consentimentos`). Desmarcar (com o estado do
  cadastro exibido) revoga; marcar de novo depois de um descadastro **volta a valer**
  (reconsentimento — o antigo `aceita_comunicacao` vetava para sempre e saiu na 0050).
- **Descadastro** (`/lgpd/unsubscribe/<token>/`): revoga marketing e pesquisa (origem
  DESCADASTRO, com IP/UA de quem confirmou), **mantém o lembrete D-1** (transacional). Cada
  e-mail de marketing leva o seu link (`link_acesso` DESCADASTRO; descartado se o envio falha).
- **Painel** (ficha do cliente, "Comunicação autorizada"): a equipe vê o estado por canal e
  **revoga** a pedido da cliente; registrar uma autorização exige a justificativa (o que foi
  autorizado e como), gravada como `texto` com o autor (origem PAINEL). Django admin: histórico só
  leitura + ação "Revogar consentimentos de comunicação" (origem ADMIN).
- **Depoimento:** só aparece no site com `autoriza_publicacao` (cliente) **e**
  `aprovado_publicacao` (equipe), nota ≥ 9 e comentário.
- **Imutabilidade:** aceite e termo já aceito não mudam (model + trigger 0046); editar o texto
  = publicar nova versão. Aceites anteriores à 0044 ficam com `conteudo_sha256` vazio.

### 9.3 Direitos do titular

| Direito | Como |
|---|---|
| Confirmação, acesso, portabilidade | `/lgpd/meus-dados/`: telefone → OTP DSAR → download JSON (cadastro, consentimentos — estado e histórico —, atendimentos, fichas, prontuário + versões e questionário rotulado, aceites, NPS, lista de espera, pacotes, carteira). Resposta idêntica exista ou não o cadastro; auditado |
| Correção | Pela equipe na ficha do cliente (`/painel/clientes/<pk>/`) |
| Revogação de consentimento | Link de descadastro; desmarcar no wizard; equipe na ficha do cliente (revogar) |
| Anonimização/eliminação | Ação LGPD no Django admin (`LgpdService.esquecer_cliente`) e job semanal (§9.4) |

**Esquecimento** (`esquecer_cliente`): troca o nome por `[ANONIMIZADO-<pk>]` e zera CPF, RG,
e-mail, telefone, endereço e nascimento; revoga os consentimentos vigentes (origem
ESQUECIMENTO — o histórico de datas/finalidades fica como prova de base legal, sem IP e
user-agent); soft delete; apaga OTPs, lista de
espera e texto das notificações; apaga fichas que não são de atendimento REALIZADO; tira o
depoimento do site e apaga o comentário do NPS; **revoga todos os links públicos** do titular
(descadastro, confirmação, NPS, termo, ficha, reagendar legado); pseudonimiza o nome nos textos da
`LogAuditoria` e do histórico do Django admin. Atendimentos, aceites, prontuário e pacotes
ficam ligados a um titular não identificável.

### 9.4 Retenção

| Dado | Prazo | Mecanismo |
|---|---|---|
| Código OTP | 24 h (`RETENCAO_OTP_HORAS`) | `housekeeping` apaga |
| Sessões expiradas | — | `housekeeping` (`clearsessions`) |
| Texto das notificações | 12 meses (`RETENCAO_NOTIFICACAO_DIAS`) | `housekeeping` limpa `mensagem` |
| Log de auditoria | 5 anos (`RETENCAO_LOG_AUDITORIA_DIAS`, mínimo 180 dias — W013) | `housekeeping` apaga (o trigger só aceita apagar linha com 180+ dias) |
| Trilha de dados (`historico_alteracao`) | 5 anos (`RETENCAO_LOG_AUDITORIA_DIAS`); **prontuário: 20 anos** (não purga) | `housekeeping` apaga (flag local `app.expurgo_autorizado` + 180 dias; fora disso o trigger barra DELETE) |
| Link público vencido ou revogado | 90 dias depois de vencer/ser revogado (`RETENCAO_LINK_ACESSO_DIAS`); feed ICS sem validade fica | `housekeeping` apaga |
| Logs do django-axes | 90 dias (`RETENCAO_AXES_LOG_DIAS`) | `housekeeping` apaga |
| Cliente sem atendimento | 5 anos (`LGPD_RETENCAO_CLIENTE_DIAS`) | `lgpd_purgar` anonimiza — **exceto** quem tem prontuário com conteúdo, pacote comprado ou atendimento REALIZADO nos últimos 20 anos |
| Cliente excluído (soft delete) | 30 dias | `lgpd_purgar` anonimiza (mesmas exceções) |
| Ficha de pedido não realizado (CANCELADO, PENDENTE vencido, convite não respondido) | 90 dias | `lgpd_purgar` apaga |
| Lista de espera com data passada | — | `lgpd_purgar` apaga |
| Atendimento realizado, prontuário e versões, aceites | ≥ 20 anos (registro de saúde) | nunca apagados; triggers impedem DELETE de aceite/versão |

**Cifragem em repouso** (0060, `utils/cifragem` + `db/campos.TextoCifrado`/`JsonCifrado`): os 5
campos clínicos e as respostas do prontuário, as versões do prontuário, as anotações de sessão e as
fichas de anamnese (finalidade `saude`) e o refresh token do Google Calendar (finalidade
`segredos`) ficam gravados cifrados (Fernet, chave de `CHAVES_CIFRAGEM` com subchave por
finalidade); a trilha `historico_alteracao` do prontuário guarda o mesmo texto cifrado. A aplicação
lê em claro; vazio continua vazio no banco. Um dump/backup sem a chave não mostra a ficha (e um
incidente com dado ininteligível pesa menos — art. 48 §3). Consequência: o banco não busca nem
filtra pelo conteúdo desses campos. Não protege contra quem tem a chave (ADMIN no painel, acesso ao
Railway).

No Postgres a escrita da retenção é das procedures (`sp_purgar_dados_expirados`,
`sp_aplicar_retencao_lgpd`, `sp_anonimizar_cliente` — §5.4), **fonte única**: não há equivalente
em Python, então no SQLite (dev sem Postgres) esquecimento, retenção e limpeza não rodam
(`docker compose up -d db` — §12).

### 9.5 Incidente (art. 48)

Passo a passo, prazos (3 dias úteis; registro por 5 anos), registro das operações (art. 37),
operadores e o relatório de impacto do dado de saúde: [`LGPD.md`](LGPD.md).

Conter (revogar tokens/sessões, `setup_2fa --force`, rotacionar `DJANGO_SECRET_KEY` — invalida
sessões —, `OTP_HMAC_KEY` e, se a chave de cifragem vazou, `CHAVES_CIFRAGEM` com
`rotacionar_chave_cifragem`), avaliar pela `LogAuditoria`, comunicar a ANPD e os titulares
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
| `pacote_expirar` | 00:30 | `30 0 * * *` | `30 3 * * *` | Compras ATIVO com `data_expiracao` < hoje → EXPIRADO (`sp_expirar_pacotes`) |
| `pacote_expirando` | 07:00 | `0 7 * * *` | `0 10 * * *` | E-mail a quem tem pacote vencendo em 7 ou 1 dia com saldo |
| `lembrete_diario` | 08:00 | `0 8 * * *` | `0 11 * * *` | WhatsApp D-1 (template `confirmacao_d1`) para atendimentos **AGENDADO** de amanhã com LEMBRETE_AGENDAMENTO/WHATSAPP vigente; idempotente |
| `aniversario` | 09:00 | `0 9 * * *` | `0 12 * * *` | E-mail de felicitação (sem desconto) com consentimento de marketing; 29/02 comemora em 28/02 |
| `nps_24h` | 10:00 | `0 10 * * *` | `0 13 * * *` | WhatsApp de NPS para REALIZADO entre 24h e 7 dias, com consentimento; até 3 tentativas |
| `detrator_alerta` | 10:30 | `30 10 * * *` | `30 13 * * *` | E-mail de NPS ≤ 6 para `email_admin` > `ADMIN_EMAIL` > `CLINIC_EMAIL` |
| `limpeza_status` | 23:00 | `0 23 * * *` | `0 2 * * *` | PENDENTE vencido há 24h → CANCELADO (FSM); AGENDADO/CONFIRMADO sem desfecho **só vão para o log** (a equipe decide falta/realizado) |
| `lgpd_purgar` | dom 03:00 | `0 3 * * 0` | `0 6 * * 0` | Anonimização por retenção (confirmada cliente a cliente), fichas de pedidos não realizados, lista de espera vencida (`sp_aplicar_retencao_lgpd`) |
| `housekeeping` | 04:00 | `0 4 * * *` | `0 7 * * *` | Sessões, OTP > 24h, texto de notificação > 12 meses, auditoria e trilha `historico_alteracao` > 5 anos (prontuário fica), link público vencido/revogado > 90 dias (`sp_purgar_dados_expirados`), axes > 90 dias (etapas independentes; falha de uma → 500 no fim) |

O que um job muda nas tabelas com trilha sai em `historico_alteracao` com origem `cron:<job>`.
| `feriados` | dia 1, 04:30 | `30 4 1 * *` | `30 7 1 * *` | `carregar_feriados` (ano atual + seguinte) |

O Brasil está sem horário de verão desde 2019: BRT = UTC−3 o ano todo. `job_promocao_mensal`
não está no cron — é disparado pelo painel (Promoções > Disparar).

---

## 11. Variáveis de ambiente

Lista completa e comentada em [`.env.example`](../.env.example). Em produção:

### 11.1 Obrigatórias / pré-requisito do go-live

| Variável | Propósito |
|---|---|
| `DJANGO_SECRET_KEY` | Chave do Django (sessões, reset; HMAC do OTP se não houver `OTP_HMAC_KEY`). Sem ela o boot falha fora de DEBUG |
| `CHAVES_CIFRAGEM` | Chave(s) Fernet da cifragem em repouso do dado de saúde e do token OAuth (`manage.py gerar_chave_cifragem`). **Sem ela o deploy para** (check `aranha.E001` no pre-deploy). Várias, por vírgula: a 1ª cifra, todas decifram. **Cópia offline com a dona**: sem a chave o prontuário não abre |
| `DJANGO_ENV=prod` | Carrega `settings/prod.py` (a imagem já define; qualquer valor fora de `dev`/`prod` derruba o boot) |
| `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | Domínio próprio (o `RAILWAY_PUBLIC_DOMAIN` e o host do healthcheck entram sozinhos) |
| `SITE_URL` | URL pública https sem barra final — links de e-mail/WhatsApp/termos/JSON-LD (W001) |
| `DATABASE_URL` | Injetada pelo Postgres do Railway |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `ADMIN_NOME` | **Pré-requisito do 1º deploy**: a `0042` desativa as contas demo; o `bootstrap_admin` cria o ADMIN real. Apagar `ADMIN_PASSWORD` depois |
| `CRON_TOKEN` | Autentica o cron HTTP (W005) |
| `ZENVIA_API_TOKEN`, `ZENVIA_FROM` | SMS/OTP. **Sem SMS não há agendamento online** (CTA vira WhatsApp) (W002) |
| `EMAIL_BACKEND` + chave do provedor + `DEFAULT_FROM_EMAIL` | Sem ele nada é entregue (W003). **O Railway Hobby bloqueia SMTP**: use o `django-anymail` (já no requirements), ex.: `EMAIL_BACKEND=anymail.backends.resend.EmailBackend` + `RESEND_API_KEY` (ou Brevo/SendGrid/Mailgun/Postmark — §14 i). `DEFAULT_FROM_EMAIL` = remetente de um **domínio verificado** no provedor; sem a chave ou com o remetente de exemplo, W011 e nada sai. SMTP (`EMAIL_HOST*`) só no plano Pro |
| `WHATSAPP_NUMERO` (ou tela Branding) | Botões de WhatsApp e fallback do agendamento (W004) |
| `CLINIC_EMAIL` (ou tela Branding) | Canal do titular LGPD e fallback do alerta de detrator (W007) |
| `CLINIC_RAZAO_SOCIAL`, `CLINIC_CNPJ`, `ENCARREGADO_NOME`, `ENCARREGADO_EMAIL` (ou tela Branding) | Controlador e encarregado (art. 41) na política de privacidade; vazios somem do texto |

### 11.2 Recomendadas

| Variável | Propósito |
|---|---|
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_TEMPLATE_*` | Lembrete D-1, NPS e aviso de vaga |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | Captcha no pedido de OTP; com SMS real sem captcha poucos IPs esgotam a cota global (W009) |
| `SENTRY_DSN` (+ `SENTRY_TRACES_SAMPLE_RATE`, `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE`) | Monitoramento de erros. Ambiente padrão = `DJANGO_ENV`; release padrão = `RAILWAY_GIT_COMMIT_SHA` |
| `WEBPUSH_VAPID_PUBLIC_KEY`, `WEBPUSH_VAPID_PRIVATE_KEY`, `WEBPUSH_VAPID_CLAIMS_EMAIL` | Push ao profissional |
| `OTP_HMAC_KEY` | Chave própria do HMAC dos códigos OTP (vazia = `DJANGO_SECRET_KEY`): rotacionar uma não mexe na outra |
| `ADMIN_2FA_OBRIGATORIO` | Sem a env: ligado em prod. `false` = válvula de emergência (W008) |
| `PROFISSIONAL_2FA_OBRIGATORIO` | Sem a env: ligado em prod (o PROFISSIONAL lê prontuário e alertas de saúde). `false` = válvula de emergência (W012) |
| `EMBED_FRAME_ANCESTORS` | Origens que podem embutir `/embed/agendar/` (vazio = qualquer `https:`) |
| `LGPD_RETENCAO_CLIENTE_DIAS` | Retenção de cliente inativo (padrão 1825) |
| `REDIS_URL` | Opcional: cache/sessão compartilhados (obrigatório se `WEB_CONCURRENCY` > 1). **Não** liga worker |
| `CRON_MONITOR_URL` | Monitor dos jobs (healthchecks.io: `https://hc-ping.com/<ping key>`): cada job avisa `<url>/<job>` ou `<url>/<job>/fail` |

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
`RETENCAO_LOG_AUDITORIA_DIAS` (também a trilha `historico_alteracao`), `RETENCAO_LINK_ACESSO_DIAS`
(90), `RETENCAO_AXES_LOG_DIAS` · `DJANGO_LOG_LEVEL` ·
`EMAIL_TIMEOUT` (10; vale também para as chamadas HTTP do anymail) · `MAILGUN_SENDER_DOMAIN`
(Mailgun; padrão = domínio do remetente).

Só dev/testes: `DEBUG`, `SMS_DEV_LOG_ONLY` (em prod gera W002), `DB_ENGINE`/`DB_*`,
`TEST_DATABASE_URL`, `PG_EXIGE_ICU`.

**Legada — remover do Railway:** `STATIC_ROOT` (era `/tmp`; `settings/prod.py` ignora e usa
`/app/staticfiles`, preenchido no build). Também não existem mais `ZENVIA_API_KEY` (use
`ZENVIA_API_TOKEN` + `ZENVIA_FROM`) nem `SECRET_KEY` (use `DJANGO_SECRET_KEY`).

---

## 12. Testes e CI

- `python manage.py test aranha_estetica` — suíte Django (SQLite `db_test.sqlite3`, ~1.390
  testes em 64 arquivos — roda **tudo**, inclusive as data migrations). Nos testes: Celery eager
  com propagação, SMS em modo log, avisos `aranha.W00x` silenciados (testados à parte em
  `test_deploy_config.py`), django-vite em `dev_mode` (não precisa do build).
- **Atalho do dia a dia**: `python manage.py test aranha_estetica --exclude-tag migracoes` pula
  `tests/test_migrations_dados.py` (tag `migracoes`: desce e sobe a cadeia de migrations dezenas
  de vezes, ~3/4 do tempo da suíte); `--tag migracoes` roda só ele. Antes do merge, rode tudo (o
  CI roda as duas partes em jobs paralelos).
- **Postgres local** (`docker-compose.yml`, PG 18 com ICU): `docker compose up -d db` e
  `TEST_DATABASE_URL=postgres://shivazen:shivazen@localhost:5432/shivazen PG_EXIGE_ICU=1 python
  manage.py test aranha_estetica`. No SQLite os testes do esquecimento, da retenção, do housekeeping
  e da expiração de pacotes são **pulados** (procedures sem fallback Python — §9.4); o CI roda
  tudo no Postgres.
- `TEST_DATABASE_URL=postgres://... python manage.py test aranha_estetica` — mesma suíte no
  Postgres; `tests/test_pg_ddl.py` confere os objetos só-Postgres (EXCLUDE, CHECK regex,
  triggers, funções, views, procedures, extensões, collation — com `PG_EXIGE_ICU=1` a collation
  tem de ser ICU), `tests/test_nomes_padrao.py` lê o `pg_catalog` e reprova objeto fora do padrão
  SQL, FK sem índice, `ON DELETE` divergente do model, tabela sem `COMMENT`, tabela com
  `atualizado_em` sem `tg_<t>_atualizado_em`, índice `_like` (migration nova que recria FK pelo
  `AlterField` precisa terminar com `PadronizarNomes()`), `tests/test_pg_historico.py` prova a
  trilha (autor/IP do request, contexto limpo entre requests na mesma conexão, cron, Celery,
  redação da senha, append-only) e `tests/test_pg_paridade.py` compara cada `fn_*`/`vw_*` com o
  Python (mudou a regra no Python → atualize a função numa migration nova) e confere o efeito de
  cada `sp_*`.
- Guardas de regressão relevantes: `test_csp.py` (CSP x templates, allowlist do jsdelivr),
  `test_front_pipeline.py` (Vite/cotton/webpush), `test_deploy_config.py` (checks, migrate
  atômico, Dockerfile/railway.json), `test_slots_disponibilidade.py` (caracterização do
  SlotService).

**CI** (`.github/workflows/ci.yml`, push em `main` e `dev` ou manual; sem gatilho de PR — o PR
`dev` → `main` mostra os checks do push do mesmo commit). Commit novo na `dev` cancela a execução
anterior; na `main` toda execução termina. `permissions: contents: read` e `timeout-minutes` em
todos os jobs:

| Job | Passos |
|---|---|
| `testes` | `check`, `makemigrations --check`, suíte Django (SQLite, `--exclude-tag migracoes`), `check --deploy --fail-level ERROR` com settings de prod |
| `migracoes-sqlite` | `tests/test_migrations_dados.py` (`--tag migracoes`) no SQLite, em paralelo |
| `postgres` | Postgres 18: `migrate_atomico` do zero, `bootstrap_admin`, suíte Django no Postgres sem a tag `migracoes` (bloqueante; inclui conformidade, trilha e paridade) |
| `postgres-migracoes` | `tests/test_migrations_dados.py` no Postgres 18 (reverso/ida de cada migration, inclusive triggers/funções/views/procedures), em paralelo |
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
- O deploy de produção sai do branch `main` (configuração do serviço no Railway). O trabalho
  acontece na `dev`; a `main` é protegida no GitHub (os 6 jobs bloqueantes do CI obrigatórios,
  inclusive para admin; sem force-push) e recebe a `dev` por PR ou fast-forward.

---

## 14. Go-live / Operação (runbook)

> **Estado em 2026-10-27:** ainda não há produção. O projeto do Railway que existia (web + Postgres
> na `0026`) era só **ambiente de desenvolvimento**; está em **stand-by** (trial expirado) e não
> precisa de upgrade nem de backup. O primeiro deploy de produção cria um **banco novo**: o
> `migrate_atomico` aplica `0001`→`0060` numa transação e o `seed --force` carrega o catálogo. O
> roteiro de atualização de um banco já existente (`0026` → `0060`) fica no §14 a.1, para o caso
> de esse banco um dia ser reaproveitado.

### a) Pré-deploy

1. **Contas e acessos** — quem entra no GitHub, no Railway ou no DNS passa por cima de todo
   controle da aplicação:
   - 2FA (app autenticador ou passkey, nunca SMS) e o mínimo de membros em GitHub, Railway,
     registrador/DNS, Meta Business, Zenvia, provedor de e-mail, Sentry, cron e monitor. Faça o
     inventário (conta, dono, quem tem acesso) e revise a cada trimestre ([`LGPD.md`](LGPD.md) §6).
   - GitHub: a `main` já está protegida (checks do CI obrigatórios, inclusive para admin; sem
     force-push nem exclusão). Ligue também *Secret scanning* + *Push protection*
     (Settings > Code security).
   - Railway: *Wait for CI* no serviço `web` (deploy só depois do CI verde) e variáveis sensíveis
     como *sealed*. Postgres **sem TCP proxy público**: ligue só durante uma manutenção, desligue
     em seguida e troque a senha se ele ficou aberto.
2. **Chave de cifragem**: `python manage.py gerar_chave_cifragem` → `CHAVES_CIFRAGEM` no Railway e
   **uma cópia offline com a dona** (gerenciador de senhas + papel). Sem a chave o pre-deploy para
   (`aranha.E001`); com a chave perdida, o prontuário não abre.
3. **Variáveis no Railway** (§11): `CHAVES_CIFRAGEM` (passo 2), `CRON_MONITOR_URL` (§14 d),
   `ADMIN_EMAIL`/`ADMIN_PASSWORD`/`ADMIN_NOME` (senha ≥ 10, não comum), `CRON_TOKEN`, `SITE_URL`, `ZENVIA_*`, `EMAIL_BACKEND` + chave do provedor +
   `DEFAULT_FROM_EMAIL` (domínio verificado — §14 i), `WHATSAPP_*`,
   `TURNSTILE_*`, `SENTRY_DSN`, `CLINIC_EMAIL`, `WHATSAPP_NUMERO`; **remova `STATIC_ROOT`**.
   Confira no painel do Railway que não há *start command* manual antigo (`gunicorn
   shivazen.wsgi`) competindo com o `railway.json` (config-as-code: builder DOCKERFILE,
   pre-deploy, healthcheck `/healthz/`).
4. **Backup** configurado antes do primeiro cliente real (§14 h).
5. PR ou fast-forward `dev` → `main` (dispara o deploy; a proteção exige o CI verde no commit).

### a.1) Só para atualizar um banco existente (`0026` → `0060`)

1. **Backup** (é o único rollback): `scripts/backup/backup_postgres.sh` (§14 h). Nunca grave o dump
   em claro dentro do repositório (o `.gitignore`/`.dockerignore` ignoram `*.dump`, mas um
   `git add -f` ou outra extensão passa).
2. **Ensaio**: restaure o backup num Postgres 18 local e rode a atualização:
   ```bash
   createdb shivazen_ensaio
   age -d -i chave-privada.txt -o /tmp/ensaio.dump <backup>.dump.age    # apague depois
   pg_restore --no-owner -d shivazen_ensaio /tmp/ensaio.dump
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
   - `0048` (agenda unificada) — (1) duas janelas do expediente semanal do mesmo
     profissional/dia que se sobrepõem; (2) regra de recorrência de bloqueio fora do subconjunto
     (DAILY/WEEKLY/MONTHLY, INTERVAL, BYDAY — ordinal só em MONTHLY, -1/1..4 — ou 1 BYMONTHDAY;
     COUNT/UNTIL viram data de fim sozinhos). A mensagem lista os ids. Pré-checagem no schema 0026:
     ```sql
     SELECT a.id, b.id, a.profissional_id, a.dia_semana
       FROM disponibilidade_profissional a JOIN disponibilidade_profissional b
         ON a.profissional_id = b.profissional_id AND a.dia_semana = b.dia_semana AND a.id < b.id
        AND a.hora_inicio < b.hora_fim AND b.hora_inicio < a.hora_fim;
     SELECT id, regra_recorrencia FROM bloqueio_agenda WHERE coalesce(regra_recorrencia, '') <> '';
     ```
     A 0048 não aborta (e registra na `LogAuditoria` `migration 0048: agenda unificada`, junto
     com o mapa id antigo → id novo de exceções e feriados; bloqueios mantêm o id) por: horário
     diferente com fim ≤ início (vira FOLGA — era o efeito real), folga com horas (horas
     ignoradas), fim de recorrência sem regra (descartado), COUNT/UNTIL (viram
     `data_fim_recorrencia`).
   - A `0049` (tokens em hash) não aborta: grava `sha256` de cada token em `link_acesso` — os
     links já enviados (lembrete, termo, NPS, ficha, descadastro, feed ICS, reagendar) continuam
     abrindo — e registra `migration 0049: links publicos em hash` com a contagem por finalidade,
     as notificações cujo token nenhuma rota aceitava (não migram) e token repetido entre tabelas
     (fica o 1º). Depois dela a URL do feed ICS não é mais exibida: quem precisar do link gera um
     novo (o antigo continua valendo até lá).
   - A `0050` (consentimentos) não aborta: cada `consent_*` vira linha em `consentimento`
     (concedido com a data/IP do aceite; `False` com data = revogação; sem data = nada) e
     `aceita_comunicacao=False` vira revogação de marketing e pesquisa **por último** (mesmo efeito
     de hoje: veta). Registra `migration 0050: consentimentos com historico` com as contagens.
   - `0051` (questionário do prontuário) — aborta se algum `formulario_anamnese` tem tipo/escopo
     fora do domínio ou escopo sem o alvo (PROCEDIMENTO sem procedimento, CATEGORIA sem categoria,
     MODALIDADE sem modalidade) → corrigir no painel. Pré-checagem no schema 0026:
     ```sql
     SELECT id, tipo, escopo, categoria, modalidade, procedimento_id FROM formulario_anamnese
      WHERE tipo NOT IN ('ANAMNESE', 'PESQUISA')
         OR escopo NOT IN ('GLOBAL', 'CATEGORIA', 'PROCEDIMENTO', 'MODALIDADE')
         OR (escopo = 'PROCEDIMENTO' AND procedimento_id IS NULL)
         OR (escopo = 'CATEGORIA' AND categoria = '')
         OR (escopo = 'MODALIDADE' AND modalidade = '');
     ```
     A configuração `prontuario_perguntas` vira o formulário "Questionário do prontuário" (tipo
     PRONTUARIO; as respostas não mudam). JSON inválido não aborta: fica na Configuração e vai
     para a `LogAuditoria` (`migration 0051: ... JSON invalido`) — recrie as perguntas em
     Painel > Anamneses.
   - A `0052` (pacote congelado) não aborta: cada compra ganha `pacote_nome` e os itens do
     catálogo **atual** do pacote, com o valor de 1 sessão = valor pago ÷ sessões; cada consumo
     aponta para o item do mesmo procedimento. Sessão consumida cujo procedimento saiu do
     catálogo vira **item reconstruído** (sessões = consumos, valor 0) e consumo acima da
     quantidade fica só registrado — `migration 0052: item reconstruido` / `consumo acima da
     quantidade` / `pacote congelado na venda` na `LogAuditoria`. Depois dela o catálogo volta a
     ser editável (só vale para vendas futuras).
   - `0053` (comissão como livro-razão) — aborta se alguma comissão tem valor ≤ 0, status fora de
     PENDENTE/PAGA/ESTORNADA ou se 2+ comissões são do mesmo atendimento (lista os ids) → ajuste
     no Django admin/SQL antes. Pré-checagem no schema 0026:
     ```sql
     SELECT id, atendimento_id, valor, status FROM movimento_comissao
      WHERE valor <= 0 OR status NOT IN ('PENDENTE', 'PAGA', 'ESTORNADA');
     SELECT atendimento_id, array_agg(id ORDER BY id) FROM movimento_comissao
      GROUP BY atendimento_id HAVING count(*) > 1;
     ```
     Cada linha vira CREDITO; PAGA ganha um PAGAMENTO na data `pago_em`; ESTORNADA ganha um
     ESTORNO com **data estimada** (a última alteração do atendimento — o sistema nunca gravou
     quando estornou). Paga e depois estornada deixa o saldo do profissional negativo (a
     descontar do próximo repasse; o painel de comissões mostra). A mesma migration tira o nome da
     indicada das observações de cashback (`Indicacao (cli #id)`), registrado em `migration 0053:
     nome da indicada retirado do ledger da carteira (LGPD)`.
   - A `0054` (nomes de tabelas e colunas) não aborta por dado: normaliza `NULL`↔`''` (texto
     `NOT NULL DEFAULT ''`; e-mail/CPF/telefone/hash vazios viram `NULL`), preenche
     `atualizado_em` nulo com `criado_em`, troca o nome das 3 tabelas renomeadas em
     `log_auditoria.tabela` e registra `migration 0054: nulos e vazios normalizados (padrao SQL)`.
     **Exige ICU** (collation `pt_br` no estado): sem ICU a `0038` só avisa, mas a `0054` aborta
     com mensagem — o Postgres do Railway tem.
   - `0055` (constraints e índices) — a 1ª operação é uma pré-checagem: aborta listando regra e
     ids se alguma linha viola um CHECK/UNIQUE novo ou os 3 CHECK jsonb da 0043 (agora
     validados). O SQL de pré-checagem (nomes de coluna novos, rodar depois da 0054 no ensaio)
     está no topo de `aranha_estetica/migrations/0055_padrao_nomes_constraints_indices.py`;
     corrigir no Django admin/SQL e refazer o ensaio.
   - As `0056`–`0058` (lógica no banco) não mexem em dado: criam a tabela `historico_alteracao`,
     os triggers (`atualizado_em`, imutabilidade, trilha), as extensões `pg_trgm`/`unaccent`, as
     funções, as 9 views e as 4 procedures. Só a **`0057` aborta**, e só se o Postgres não tiver
     `pg_trgm`/`unaccent` (ou o usuário não puder criá-las — no Railway ele é dono do banco e as
     duas são *trusted*). Pré-checagem:
     ```sql
     SELECT name, default_version, installed_version FROM pg_available_extensions
      WHERE name IN ('btree_gist', 'pg_trgm', 'unaccent');      -- 3 linhas
     ```
     Os triggers de trilha nascem depois de todo DML das migrations anteriores: logo depois do
     upgrade `SELECT count(*) FROM historico_alteracao` = 0 (no ensaio também).
   - A `0059` (trilhas append-only) não mexe em dado. A `0060` cifra o dado de saúde existente
     (inclusive na trilha) e **aborta sem `CHAVES_CIFRAGEM`** em prod: defina a chave antes (§14 a, passo 2).
   - A `0034` não aborta: normaliza telefone/CPF/e-mail, tira duplicatas (o cliente com mais
     atendimentos fica com o valor; os outros ficam ativos com o campo nulo) e registra cada caso
     na `LogAuditoria` (`migration 0034: ...`, com `telefone_original`) para mesclagem manual;
     promoção fora do CHECK é **desativada**.
### b) O que o deploy faz

1. Build da imagem (§13) — falha de front, collectstatic ou check derruba o build, nada muda.
2. Pre-deploy: `migrate_atomico` (`0001`→`0060` no banco novo, ou `0027`→`0060` no §14 a.1, numa
   transação; `lock_timeout` 5 s — se o deploy antigo segurar lock, falha e é só refazer) e `bootstrap_admin` (cria/reativa o ADMIN).
   Leia o log do pre-deploy: avisos `aranha.W0xx`, `0042: ATENCAO ...` e
   `bootstrap_admin: ERRO — painel sem administrador ativo` indicam env faltando (os checks
   rodam **antes** das migrations: W006/W010 ali podem ser o banco antigo — confira no passo c).
   Se uma migration falhar, o log termina com `migrate_atomico: FALHOU - transacao desfeita;
   banco continua em aranha_estetica.<ultima aplicada>` (ex.: `0026_otpcode_canal_default_sms`):
   nada foi alterado e o deploy antigo segue no ar — corrija (ensaio do §14 a.1) e refaça.
3. Container novo sobe; só recebe tráfego depois de `/healthz/` responder 200.
4. Até a troca, o código antigo roda contra o schema novo por alguns segundos (erros pontuais
   esperados — por isso o horário sem movimento).

### c) Depois do deploy — verificação

```bash
curl -fsS https://<dominio>/healthz/                  # {"status": "alive"}
curl -fsS https://<dominio>/health/                   # db/cache/front = true
railway ssh   # shell no container:
python manage.py check --database default             # lista W001–W013 pendentes
python manage.py sendtestemail voce@exemplo.com       # e-mail real pelo provedor (§14 i)
python manage.py showmigrations aranha_estetica | tail -3   # [X] 0060
```

Trilha funcionando: edite um prontuário de teste pelo painel e confira (`railway connect` no
Postgres):

```sql
SELECT criado_em, tabela, registro_pk, operacao, colunas_alteradas, usuario_autor_id, ip_origem, origem
  FROM historico_alteracao ORDER BY id DESC LIMIT 5;       -- autor = você, origem = 'web', IP real
SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE 'tg%';   -- 46
```

### d) Jobs periódicos (cron externo)

Crie um agendamento por job da tabela do §10 (`POST`, header `X-Cron-Token`):
- **cron-job.org** (mais simples): um job por URL, método POST, header customizado
  `X-Cron-Token`, fuso `America/Sao_Paulo` (use a coluna BRT), timeout alto e alerta de falha
  por e-mail (a resposta 500 indica job com erro).
- **Railway Cron**: serviço separado (imagem com `curl`) com *Cron Schedule* e o comando
  `curl -fsS -X POST -H "X-Cron-Token: $CRON_TOKEN" https://<dominio>/cron/run/<job>/`; o
  agendamento do Railway é em **UTC** (use a coluna UTC); um serviço por horário.

Teste cada um manualmente uma vez (resposta `{"ok": true, ...}`). Com `CRON_MONITOR_URL`, crie no
monitor um check por job com o **mesmo nome do job** e o horário da tabela: ele alerta quando o job
não roda (cron apagado, token trocado, site fora) ou falha.

### e) Primeiro acesso

1. Entre em `/admin-login/` com `ADMIN_EMAIL`/`ADMIN_PASSWORD`. O painel **obriga o cadastro do
   2FA**: escaneie o QR em `/painel/seguranca/2fa/` e confirme o código. Na mesma tela, gere os
   **códigos de backup** (10, uso único, mostrados uma vez) e guarde-os fora do celular. Cada
   profissional faz o mesmo no primeiro login (o 2FA dele também é obrigatório em prod).
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
banco (W004/W007 via Branding, W006, W010) só rodam com `check --database default` ou no `migrate`
(antes das migrations).

| Check | Significa | Como resolver |
|---|---|---|
| `W001` | `SITE_URL` não é https público | `SITE_URL=https://<dominio>` |
| `W002` | SMS sem provedor (ou `SMS_DEV_LOG_ONLY=true` em prod) — OTP falha | `ZENVIA_API_TOKEN` + `ZENVIA_FROM`; remover `SMS_DEV_LOG_ONLY` |
| `W003` | Backend de e-mail que não entrega (dummy/console/arquivo/locmem, `anymail.backends.test`) | `EMAIL_BACKEND` do anymail + chave do provedor (§14 i); SMTP só no plano Pro |
| `W004` | WhatsApp vazio/inválido — botões e fallback ocultos | Branding ou `WHATSAPP_NUMERO` |
| `W005` | `CRON_TOKEN` ausente — nenhum job roda | Definir e configurar o cron |
| `W006` | Nenhum ADMIN ativo com senha — ninguém entra no painel | `ADMIN_EMAIL`/`ADMIN_PASSWORD` + redeploy |
| `W007` | `CLINIC_EMAIL` vazio — sem canal do titular LGPD | Branding ou env |
| `W008` | `ADMIN_2FA_OBRIGATORIO=false` em prod | Remover a env |
| `W009` | SMS real sem Turnstile — cota global de SMS vulnerável | `TURNSTILE_SITE_KEY` + `TURNSTILE_SECRET_KEY` |
| `W010` | Sem versão ativa do termo LGPD e/ou SAÚDE — sem LGPD o agendamento online recusa confirmar; sem SAÚDE o consentimento art. 11 fica sem prova | Painel > Termos: publicar/reativar a versão (a `0045`/`0047` criam as v1.0 se não houver ativa) |
| `W011` | Backend anymail sem a chave do provedor (ou de provedor não suportado pelos settings), ou `DEFAULT_FROM_EMAIL` de exemplo — nada é entregue | Chave do provedor no Railway; `DEFAULT_FROM_EMAIL` do domínio verificado (§14 i) |
| `W012` | `PROFISSIONAL_2FA_OBRIGATORIO=false` em prod — profissional lê dado de saúde só com a senha | Remover a env; profissional sem celular usa códigos de backup impressos |
| `W013` | `RETENCAO_LOG_AUDITORIA_DIAS` abaixo de 180 — o Postgres recusa a purga da trilha | 180 ou mais (padrão 1825; Marco Civil: 6 meses) |
| `E001` (**erro**) | Prod sem `CHAVES_CIFRAGEM` válida — barra o `migrate_atomico` do pre-deploy (o deploy anterior segue no ar) | `CHAVES_CIFRAGEM=<manage.py gerar_chave_cifragem>` + cópia offline |

Build: `django_vite.W001` (manifest ausente) reprova o `check --tag staticfiles` do Dockerfile.

### g) Rollback

**Rollback = redeploy do anterior + restaurar o backup mais recente.** Os *reverse* das migrations
não recuperam dados (renomeações, dedup da 0034, contas demo desativadas, termo criado, triggers).
Não use `migrate aranha_estetica <anterior>`.

1. Railway → Deployments → *Redeploy* do último deployment bom.
2. No banco atual (se ele ainda abre), liste quem foi anonimizado depois do backup:
   `python manage.py reaplicar_esquecimentos --listar --desde <data do backup> > esquecidos.json`.
3. Teste o backup num banco descartável (`scripts/backup/testar_restauracao.sh`) e restaure:
   decifre com a chave privada num arquivo temporário (`age -d -i chave.txt -o /tmp/r.dump
   <backup>.dump.age`), `pg_restore --clean --if-exists --no-owner --dbname="$DATABASE_URL"
   /tmp/r.dump` e apague o `/tmp/r.dump`.
4. `python manage.py reaplicar_esquecimentos esquecidos.json` — quem pediu o esquecimento depois do
   backup não pode voltar identificado.
5. Confira `/health/` e um login. O que foi gravado entre o backup e o rollback se perde.

### h) Operação contínua

- **SQL manual / BI**: use os nomes do banco (`eh_ativo`, `pacote_item`, `resposta_formulario`...;
  `\d+ <tabela>` mostra o `COMMENT` de tabela e colunas). Os atributos Python seguem os antigos.
- **Migration nova que mexe numa FK** (`AlterField` de tipo/`null`/destino): o Django recria a FK
  com nome de hash e sem `ON DELETE` — termine a migration com `PadronizarNomes()`
  (`aranha_estetica/db/operacoes.py`); o `tests/test_nomes_padrao.py` reprova se esquecer.
- **Tabela nova com `atualizado_em`**: crie o `tg_<t>_atualizado_em` (`EXECUTE FUNCTION
  fn_definir_atualizado_em()`) na mesma migration — o teste de conformidade reprova sem ele. Tabela
  sensível nova entra na trilha com `tg_<t>_historico` (`fn_registrar_historico('atualizado_em',
  '<colunas a redigir>')`).
- **Regra mudou no Python** (slots, saldo de pacote, NPS, comissão, consentimento): o
  `tests/test_pg_paridade.py` reprova no CI Postgres até a função de leitura ser atualizada —
  módulo novo em `migrations/sql/` + migration com `CREATE OR REPLACE`. **Retenção, anonimização,
  limpeza e expiração de pacotes** só existem nas procedures: mudar a regra = migration nova com a
  procedure + ajuste do teste de efeito (`test_lgpd_esquecimento`, `test_pg_paridade`).
- **Quem mudou o quê**: `historico_alteracao` (Django admin, só leitura e auditado, ou SQL) —
  `transacao_ref` agrupa o que saiu do mesmo request; SQL manual sai sem autor (origem `''`). Para
  identificar uma correção manual, rode antes `SELECT set_config('app.origem', 'manual:<seu nome>',
  false);` na sessão do `psql`.
- **BI / relatório**: leia as views `vw_*` (§5.4); se um dia houver acesso de BI direto ao banco,
  crie uma role só-leitura com `GRANT SELECT` nas views. **PgBouncer em modo transação** quebra o
  contexto da trilha (GUC de sessão) — o Railway não usa; se entrar, trocar para
  `ATOMIC_REQUESTS` + `SET LOCAL`.
- **Link do ICS vazado/perdido**: Painel > Profissionais > "Novo link"/"Gerar link" (ou o
  próprio profissional em Minha agenda > "Link do calendário"): mostra a URL uma única vez (o
  banco guarda só o hash); o antigo para de funcionar; auditado.
- **Escalar**: mais de 1 worker gunicorn exige `REDIS_URL` (rate-limit, axes, lock de slot e quota
  de SMS são por processo no LocMem).
- **Backups** (fora do Railway e cifrados): `scripts/backup/backup_postgres.sh` todo dia (serviço
  Cron do Railway com `postgresql-client`, `age` e `rclone`, ou outra máquina) para um bucket com
  versionamento/*object lock*. Lá fica só a chave **pública** do age; a privada fica offline com a
  dona. Retenção sugerida: 30 diários + 12 mensais (declarar em [`LGPD.md`](LGPD.md) §3). **Todo
  mês**, `scripts/backup/testar_restauracao.sh` num banco descartável. Rotacionou a
  `CHAVES_CIFRAGEM`? Guarde a antiga enquanto existir backup feito com ela.
- **Monitoramento**: `SENTRY_DSN` (ambiente e release preenchidos sozinhos; filtros de PII em
  `utils/pii.opcoes_sentry`), `CRON_MONITOR_URL` (§14 d) e um monitor de uptime externo
  (healthchecks.io, UptimeRobot...) em `/healthz/` a cada 5 min.

### i) E-mail em produção (provedor HTTP)

O plano Hobby do Railway bloqueia SMTP de saída: sem um provedor HTTP **nenhum** e-mail sai
(reset de senha, convites, confirmações, termos, contato, marketing). O `django-anymail` já vem
no requirements; `settings/base.py` liga o app e monta `ANYMAIL` quando `EMAIL_BACKEND` é
`anymail.backends.*`, lendo da env só as chaves presentes:

| Provedor | `EMAIL_BACKEND` | Chave |
|---|---|---|
| Resend (sugerido) | `anymail.backends.resend.EmailBackend` | `RESEND_API_KEY` |
| Brevo | `anymail.backends.brevo.EmailBackend` | `BREVO_API_KEY` |
| SendGrid | `anymail.backends.sendgrid.EmailBackend` | `SENDGRID_API_KEY` |
| Mailgun | `anymail.backends.mailgun.EmailBackend` | `MAILGUN_API_KEY` (+ `MAILGUN_SENDER_DOMAIN`) |
| Postmark | `anymail.backends.postmark.EmailBackend` | `POSTMARK_SERVER_TOKEN` |

1. Crie a conta no provedor e **verifique o domínio** da clínica no DNS: SPF terminando em
   `-all`, DKIM e **DMARC** (comece com `p=quarantine; rua=mailto:<e-mail da clínica>` e passe para
   `p=reject` quando os relatórios estiverem limpos — os e-mails levam links de termo, descadastro e
   senha, alvo fácil de phishing). Se o registrador permitir, registro CAA e DNSSEC. Sem domínio
   verificado o provedor recusa o envio.
2. Gere a chave de API (permissão só de envio).
3. No Railway: `EMAIL_BACKEND=anymail.backends.resend.EmailBackend`, `RESEND_API_KEY=re_...` e
   `DEFAULT_FROM_EMAIL=Jaqueline Aranha Estética <contato@<dominio verificado>>`. Redeploy.
4. Confira: `python manage.py check --database default` sem W003/W011 e
   `python manage.py sendtestemail voce@exemplo.com` (erro do provedor aparece no traceback).
5. Em operação, falhas ficam no log como `email_falha_envio` (erro do provedor: o envio conta
   como não feito e o fluxo segue) ou `email_nao_configurado`. Timeout da API = `EMAIL_TIMEOUT`.

---

## 15. Convenções de código

- Regra de negócio em `services/`/`utils/`, não em view; mudança de status só pela FSM.
- Recusa de regra = `excecoes.RegraDeNegocio` com mensagem pronta para a tela (nunca `None`/
  `False`/exceção privada da view); horário de atendimento só por `services/agenda`.
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
| 2026-09-23 | Padrão SQL, fase (a): agenda 4→2 (`agenda_horario` + `agenda_excecao`, migration 0048; [`specs/padrao-sql.md`](specs/padrao-sql.md) §4.a) |
| 2026-10-02 | Padrão SQL, fase (b): tokens de link público em hash (`link_acesso`, migration 0049; portal reagenda/cancela por sessão; ICS exibido só na geração; [`specs/padrao-sql.md`](specs/padrao-sql.md) §4.b) |
| 2026-10-26 | Padrão SQL, fases (c) e (d): consentimentos com histórico append-only (`consentimento`, 0050 — sai `aceita_comunicacao`, reconsentimento vale, painel revoga) e questionário único (perguntas do prontuário = formulário PRONTUARIO, 0051; `alerta_saude` por pergunta) ([`specs/padrao-sql.md`](specs/padrao-sql.md) §4.c/§4.d) |
| 2026-10-26 | Padrão SQL, fases (e) e (f): pacote congelado na venda (`compra_pacote_item`, 0052 — catálogo volta a ser editável) e comissão como livro-razão (CREDITO/PAGAMENTO/ESTORNO, 0053 — saldo negativo visível, regra usada só desativa); nome da indicada fora do ledger da carteira; ticket médio e LTV pela mesma regra (`services/financeiro`) ([`specs/padrao-sql.md`](specs/padrao-sql.md) §4.e/§4.f) |
| 2026-10-26 | Padrão SQL, fase de nomes (N1/N2): 3 tabelas e 64 colunas renomeadas só no banco (0054 — tipos `bigint`/`char(64)`, `NULL`↔`''`, `criado_em`/`atualizado_em` completados, `db_default`, collation no estado, triggers `tg_`/`fn_`) e todas as constraints/índices no padrão (0055 — 15 índices e 39 constraints renomeados, unique de campo no `Meta`, índices das queries reais, CHECKs novos com pré-checagem, PK/FK com `ON DELETE` real, `COMMENT` em todas as tabelas); `tests/test_nomes_padrao.py` ([`specs/padrao-sql.md`](specs/padrao-sql.md) §3) |
| 2026-10-26 | Padrão SQL, fase de lógica (L1–L3): trilha de dados `historico_alteracao` por trigger com autor/IP/origem da sessão (`ContextoBancoMiddleware`, cron, Celery, `migrate_atomico`) e `atualizado_em` por trigger (0056); `pg_trgm`/`unaccent`, busca de cliente sem acento, 8 funções de leitura e 9 views com paridade testada contra o Python, NPS "meio p/ cima" (0057); 4 procedures como fonte única da retenção/LGPD (0058); data migrations em jobs paralelos do CI (tag `migracoes`) ([`specs/padrao-sql.md`](specs/padrao-sql.md) §5) |
| 2026-09-23 | Auditoria pré-produção completa (~450 correções em ~30 commits, `b5fd681..182e2e9`): OTP preso ao telefone em todo agendamento, consentimentos LGPD/art. 11 com prova, 2FA obrigatório do ADMIN, histórico do prontuário, prova de aceite imutável, deploy por Dockerfile + `migrate_atomico`, retenção LGPD efetiva, migrations 0040–0046. Detalhe em [`ARCHITECTURE.md`](ARCHITECTURE.md#6-progresso-checkpoint) |

**Mantenedor:** Rafael Maciel · **Última atualização:** 2026-10-27
