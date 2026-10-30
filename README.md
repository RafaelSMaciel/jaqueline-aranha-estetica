# Jaqueline Aranha Estética — Shiva Zen

Site e sistema de gestão da clínica de estética da biomédica Jaqueline Aranha
(São José do Rio Preto/SP). Um único app Django serve o **site público** (vitrine,
agendamento online, área "Meus agendamentos", LGPD) e o **painel da equipe**
(agenda, clientes, ficha/prontuário, pacotes, promoções, comissões, NPS, termos).

> Nomes técnicos: o app Django é `aranha_estetica` e o projeto é `clinica`. "Shiva Zen"
> é o nome do repositório/TCC; a marca exibida vem da tela **Branding** do painel
> (ou da env `CLINIC_NAME`).

Documentação completa: **[docs/PROJECT.md](docs/PROJECT.md)** (fonte viva: arquitetura,
modelos, rotas, env vars, jobs, deploy e runbook de go-live).

---

## O que o sistema faz

**Site público (sem login)**
- Agendamento online em 3 passos (procedimento → data/horário → dados). **Todo**
  agendamento exige o código OTP enviado por SMS ao celular informado; o telefone é a
  identidade da cliente (sem senha). Sem provedor de SMS, os botões "Agendar" levam ao
  WhatsApp da clínica.
- Aceite obrigatório da Política de Privacidade (prova gravada: IP, navegador e SHA-256
  do texto), consentimento específico para a ficha de saúde (LGPD art. 11) e aceite dos
  termos do procedimento no próprio wizard.
- Preço gravado = preço com a promoção vigente **na data do atendimento**.
- "Meus agendamentos" com login por celular ou e-mail (o código vai sempre ao celular do
  cadastro; abre também no fim do agendamento, sem novo SMS): consultar, reagendar (até 24h antes)
  e cancelar (com menos de 24h de um horário aprovado, conta como falta), mudar as preferências de
  comunicação, revogar o consentimento de saúde, pedir correção ou exclusão e baixar os próprios
  dados (sem a ficha de saúde).
- Links por token: confirmar presença (lembrete D-1 por WhatsApp ou e-mail), NPS, termo,
  ficha de anamnese, descadastro. Reagendar e cancelar só pelo "Meus agendamentos".
- Lista de espera sem cadastro (com turno; aviso de vaga sem tirar da fila), contato com
  captcha e limites, promoções, depoimentos (só com autorização da cliente e aprovação da
  clínica), páginas institucionais, PWA e widget `/embed/agendar/`.
- LGPD: Política de Privacidade = termo aceito no agendamento (`aranha_estetica/conteudo/lgpd.py`,
  **revisão jurídica pendente antes do go-live**), "Meus dados" (exportação com OTP),
  descadastro de marketing em um clique, pseudonimização e expurgo do registro de saúde aos 20 anos.

**Painel da equipe** (`/admin-login/`)
- ADMIN: painel completo com **2FA obrigatório** (TOTP). PROFISSIONAL: portal
  `/profissional/` com a própria agenda, aprovação dos pedidos, "Realizado", anotações,
  acesso à ficha só de quem ela atende, "Minhas comissões" e o link do feed ICS da agenda.
- Agenda (lista + calendário arrastar-e-soltar), agendamento interno pela recepção,
  aprovação de pedidos, valor cobrado, link do termo por atendimento.
- Clientes com alertas de saúde visíveis, ficha/prontuário com **histórico de versões**,
  pacotes (venda, saldo, validade, prorrogação, cancelamento com reembolso), promoções e
  disparo por e-mail, financeiro por competência mensal, comissões (regras, repasse em lote),
  NPS e moderação de depoimentos, termos versionados (imutáveis após o 1º aceite), auditoria
  (LGPD art. 37), Branding, usuários.
- Jobs periódicos pelo serviço Cron do Railway (`rodar_jobs_devidos`) ou cron HTTP (fila de
  e-mail, lembrete D-1, sem desfecho, NPS, pacotes, aniversário, limpeza, retenção LGPD,
  feriados).

---

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.12, Django 5.2 (server-render, sem SPA) |
| Banco | PostgreSQL 18 em produção/CI (EXCLUDE, CHECK, triggers, collation ICU); SQLite em dev/testes rápidos |
| Front-end | Vite 6 + Tailwind CSS v4 + Alpine.js (build **CSP**, `@alpinejs/csp`) + componentes django-cotton. **Sem HTMX, sem Bootstrap, sem jQuery** |
| Jobs / fila | Sem Celery: jobs com horário em `jobs.JOB_AGENDA`, rodados pelo serviço Cron do Railway (`manage.py rodar_jobs_devidos`), pelo cron HTTP `/cron/run/<job>/` ou por `manage.py rodar_job`; fila de e-mail no banco |
| Cache/sessão | LocMem + sessão no banco (1 worker gunicorn); Redis opcional via `REDIS_URL` |
| Segurança | CSP com nonce, django-axes, django-ratelimit, django-otp (TOTP), Cloudflare Turnstile |
| Integrações | Zenvia (SMS/OTP), WhatsApp Business (Meta), e-mail (backend Django), Web Push (VAPID), Sentry; a agenda da profissional sai pelo feed ICS (sem Google Calendar) |
| API | Rotas JSON, webhooks, cron e feed ICS descritos em OpenAPI 3.1 (`aranha_estetica/api/openapi.yaml`) com Swagger UI em `/painel/api/` (só a equipe) |
| Deploy | Railway: Dockerfile multi-stage (Node 22 → Python 3.12), gunicorn 1 worker × 4 threads, WhiteNoise |
| CI | GitHub Actions (`.github/workflows/ci.yml`): SQLite, Postgres 18, cobertura combinada, build do front, build e smoke da imagem, pip-audit, npm audit e gitleaks |

---

## Rodando localmente

Pré-requisitos: Python 3.12 (o do CI e da imagem; `uv venv --python 3.12` baixa se faltar),
Node ≥ 20 (o repositório usa 22 — `.nvmrc`). Postgres é opcional em dev. Guia do dia a dia
(venv igual ao lock, testes rápidos, ganchos, regra de fuso): [docs/DESENVOLVIMENTO.md](docs/DESENVOLVIMENTO.md).

```bash
# 1. Python
python -m venv .venv                 # Python 3.12 (Windows: py -3.12 -m venv .venv)
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt -c requirements.lock
pre-commit install                   # ruff + camadas + migrations antes de cada commit

# 2. Ambiente
cp .env.example .env                 # DEBUG=True basta p/ dev; o resto tem default

# 3. Banco (sem DATABASE_URL = SQLite em db_dev.sqlite3)
python manage.py migrate
python manage.py seed --demo         # catálogo real, profissional, agenda, termo LGPD,
                                     # anamnese padrão, feriados + clientes/atendimentos fictícios

# 4. Usuário ADMIN (o seed não cria usuários)
ADMIN_EMAIL=voce@exemplo.com ADMIN_PASSWORD='uma-senha-forte-10+' python manage.py bootstrap_admin
#   (ou: python manage.py createsuperuser)

# 5. Front-end (terminal 1) — em DEBUG o django-vite aponta p/ o dev server
npm ci
npm run dev                          # Vite em http://localhost:5173 (HMR)

# 6. Django (terminal 2)
python manage.py runserver           # http://127.0.0.1:8000
```

- `npm run build` gera `aranha_estetica/static/dist/` + `manifest.json` (usado quando
  `DEBUG=False`, no CI e na imagem Docker).
- Em dev (`DEBUG=True`) o 2FA só é obrigatório se `ADMIN_2FA_OBRIGATORIO=true`, e o SMS
  entra sozinho em modo log (`utils/sms.sms_modo_dev`): nada é enviado, o envio vira o
  evento `sms_dev_log` no logger (a prévia da mensagem vai nos campos *extra*). Fora de
  DEBUG, SMS só com `ZENVIA_API_TOKEN` + `ZENVIA_FROM`.
- Postgres local: defina `DATABASE_URL=postgres://...` (ou `DB_ENGINE`/`DB_NAME`/...).
- `python manage.py seed` sem `--demo` cria só os dados reais; em produção o comando exige
  `--force` e recusa `--demo`.

### Testes

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
ruff check .
python manage.py test aranha_estetica --exclude-tag migracoes   # SQLite, ~40 s (--keepdb: reaproveita o banco)
python manage.py test aranha_estetica                 # tudo, inclusive as data migrations (lento)

# Mesma suíte no Postgres (como a CI): EXCLUDE, CHECK, triggers e collation só existem lá
docker compose up -d db
TEST_DATABASE_URL=postgres://shivazen:shivazen@localhost:5432/shivazen PG_EXIGE_ICU=1 \
  python manage.py test aranha_estetica
```

São ~2.020 testes (fora a tag `migracoes`) em 125 arquivos (`aranha_estetica/tests/`); o runner
é só o `manage.py test`. A CI também roda `migrate_atomico` do zero no Postgres, `check --deploy`
com settings de produção e de staging, cobertura combinada (SQLite + Postgres) com catraca, o
wizard no Chromium (Playwright), o build do Vite, o `docker build` com o smoke do pre-deploy e do
start, `pip-audit`, `npm audit` e gitleaks — detalhes em [docs/DESENVOLVIMENTO.md](docs/DESENVOLVIMENTO.md).

---

## Estrutura

```
aranha_estetica/                 app Django
├── models/                      21 módulos, 37 tabelas de domínio (ver docs/PROJECT.md §5)
├── views/                       uma pasta por área (publico, conta, painel, profissional, infra) +
│                                comum.py; as áreas não se importam (import-linter)
├── urls/                        as rotas de cada área, no namespace `aranha`
├── services/                    regras de negócio (agenda, atendimento, disponibilidade, OTP, LGPD,
│                                termos, comissão, financeiro, pacotes, cashback, retorno, lista de
│                                espera, lembretes, NPS, marketing, alertas, prontuário, push...)
├── utils/                       branding, e-mail, SMS, WhatsApp, telefone, preços, saúde, segurança,
│                                PII, respostas (contrato JSON)...
├── management/commands/         seed, bootstrap_admin, migrate_atomico, setup_2fa, carregar_feriados,
│                                rodar_job, rodar_jobs_devidos, rotação da chave de cifragem...
├── migrations/                  0001–0078 (+ migrations/sql/: texto das rotinas do Postgres)
├── templates/                   estrutura/ (site), painel/ (equipe), profissional/ (casca própria),
│                                agendamento/, publico/, conta/, cotton/ (componentes), email/, pwa/,
│                                erros 400/403/404/429/500
├── conteudo/                    textos que também são regra: política de privacidade (lgpd.py) e FAQ
├── static/src/                  fonte do Vite (tokens.css, app.css; JS por tela: app/wizard/painel-busca/
│                                painel-status/painel-calendario/webpush)
├── signals.py · jobs.py · jobs_manutencao.py · middleware.py · checks.py
└── tests/                       por área: conta/, infra/, jobs/, painel/, publico/, servicos/, utils/ + raiz
clinica/                         settings (base/dev/prod/staging/test), urls, wsgi, gunicorn_logger,
                                 logging_filtros
docs/                            documentação (abaixo)
Dockerfile · railway.json · railway.cron.json · .github/workflows/ci.yml · package.json · vite.config.js
requirements.txt · requirements-dev.txt · requirements.lock · .env.example
```

---

## Documentação

| Documento | Conteúdo |
|---|---|
| [docs/PROJECT.md](docs/PROJECT.md) | Fonte viva: stack, arquitetura, modelos/tabelas, rotas, env vars, comandos, jobs, testes, deploy e **runbook de go-live** |
| [docs/DESENVOLVIMENTO.md](docs/DESENVOLVIMENTO.md) | Dia a dia: ambiente igual ao lock, testes rápidos (`--keepdb`, paralelo, Postgres, navegador), ganchos, regra de fuso, o que o CI confere |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Decisões (ADR-lite) e checkpoint de progresso/pendências |
| [docs/AUTENTICACAO-E-ACESSO.md](docs/AUTENTICACAO-E-ACESSO.md) | Login da equipe, 2FA, OTP da cliente, links por token, sessões |
| [docs/REGRAS-DE-NEGOCIO.md](docs/REGRAS-DE-NEGOCIO.md) | Regras funcionais com os valores reais do código |
| [docs/CUSTOS-OPERACIONAIS.md](docs/CUSTOS-OPERACIONAIS.md) | Custo mensal de operação: preço de cada provedor, cenários por volume, travas de custo |
| [docs/DESIGN-E-MARKETING.md](docs/DESIGN-E-MARKETING.md) | Identidade visual (psicologia das cores, tipografia), tom de voz, acessibilidade e recursos de marketing |
| [aranha_estetica/api/openapi.yaml](aranha_estetica/api/openapi.yaml) | Contrato HTTP (OpenAPI 3.1) das rotas de máquina: wizard, portal, painel, webhooks, cron, ICS, saúde. Navegável no Swagger UI em `/painel/api/` |
| [docs/LGPD.md](docs/LGPD.md) | Registro das operações (art. 37), operadores, relatório de impacto do dado de saúde, plano de incidente |
| [docs/specs/](docs/specs/) | Specs por assunto: banco (remodelagem 0027–0047 e padrão SQL 0048+), front, registry de regras |
| [docs/uml/](docs/uml/README.md) + [docs/gerar_documentacao.py](docs/gerar_documentacao.py) | UML completa (PlantUML: casos de uso, classes geradas do código, sequência, estados, atividades, componentes, implantação, ER) e a documentação técnica do TCC (`ShivaZen-Documentacao.docx/.pdf`) |

---

## Licença

Sem arquivo de licença no repositório (`package.json`: `UNLICENSED`). Código de uso da
clínica e do TCC do autor.

Desenvolvido por Rafael Maciel.
