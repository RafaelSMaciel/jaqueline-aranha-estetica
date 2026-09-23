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
  cadastro): consultar, reagendar (até 24h antes) e cancelar.
- Links por token: confirmar presença (lembrete D-1 do WhatsApp), reagendar, NPS, termo,
  ficha de anamnese, descadastro.
- Lista de espera, promoções, depoimentos (só com autorização da cliente e aprovação da
  clínica), páginas institucionais, PWA e widget `/embed/agendar/`.
- LGPD: "Meus dados" (exportação com OTP), descadastro de marketing em um clique.

**Painel da equipe** (`/admin-login/`)
- ADMIN: painel completo com **2FA obrigatório** (TOTP). PROFISSIONAL: portal
  `/profissional/` com a própria agenda, aprovação dos pedidos, "Realizado", anotações e
  acesso à ficha só de quem ela atende.
- Agenda (lista + calendário arrastar-e-soltar), agendamento interno pela recepção,
  aprovação de pedidos, valor cobrado, link do termo por atendimento.
- Clientes com alertas de saúde visíveis, ficha/prontuário com **histórico de versões**,
  pacotes (venda, saldo, validade, cancelamento com reembolso), promoções e disparo por
  e-mail, comissões, NPS e moderação de depoimentos, termos versionados (imutáveis após o
  1º aceite), auditoria (LGPD art. 37), Branding, usuários.
- Jobs periódicos por cron HTTP (lembrete D-1, NPS, pacotes, aniversário, limpeza,
  retenção LGPD, feriados).

---

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.12, Django 5.2 (server-render, sem SPA) |
| Banco | PostgreSQL 18 em produção/CI (EXCLUDE, CHECK, triggers, collation ICU); SQLite em dev/testes rápidos |
| Front-end | Vite 6 + Tailwind CSS v4 + Alpine.js (build **CSP**, `@alpinejs/csp`) + componentes django-cotton. **Sem HTMX, sem Bootstrap, sem jQuery** |
| Assíncrono | Celery 5.4 em modo *eager* (sem worker por padrão); jobs periódicos via cron HTTP `/cron/run/<job>/` |
| Cache/sessão | LocMem + sessão no banco (1 worker gunicorn); Redis opcional via `REDIS_URL` |
| Segurança | CSP com nonce, django-axes, django-ratelimit, django-otp (TOTP), Cloudflare Turnstile |
| API | Django REST Framework (somente leitura, só ADMIN) + drf-spectacular (Swagger) |
| Integrações | Zenvia (SMS/OTP), WhatsApp Business (Meta), e-mail (backend Django), Web Push (VAPID), Google Calendar (opcional), Sentry |
| Deploy | Railway: Dockerfile multi-stage (Node 22 → Python 3.12), gunicorn 1 worker × 4 threads, WhiteNoise |
| CI | GitHub Actions (`.github/workflows/ci.yml`): SQLite, Postgres 18, build do front, build da imagem, pip-audit |

---

## Rodando localmente

Pré-requisitos: Python 3.12, Node ≥ 20 (o repositório usa 22 — `.nvmrc`). Postgres é
opcional em dev.

```bash
# 1. Python
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt -c requirements.lock

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
python manage.py test aranha_estetica                 # SQLite (db_test.sqlite3), rápido
pytest aranha_estetica/tests/test_2fa.py              # único arquivo só-pytest (o CI roda à parte)

# Mesma suíte no Postgres (como a CI): EXCLUDE, CHECK, triggers e collation só existem lá
TEST_DATABASE_URL=postgres://postgres:postgres@localhost:5432/ci PG_EXIGE_ICU=1 \
  python manage.py test aranha_estetica
```

São ~1.060 testes em 52 arquivos (`aranha_estetica/tests/`). A CI também roda
`migrate_atomico` do zero no Postgres, `check --deploy` com settings de produção, o build
do Vite e o `docker build`.

---

## Estrutura

```
aranha_estetica/                 app Django
├── models/                      15 módulos, 34 tabelas de domínio (ver docs/PROJECT.md §5)
├── views/                       site público, booking, portal do profissional, painel, cron, health
├── services/                    regras de negócio (agendamento, disponibilidade, OTP, LGPD, termos,
│                                comissão, cashback, retorno, lista de espera, alertas, gcal, push)
├── domain/                      EventBus + eventos (comissão, cashback, retorno reagem a eles)
├── utils/                       branding, e-mail, SMS, WhatsApp, preços, saúde, segurança, PII...
├── api/                         DRF v1 (somente leitura, ADMIN) + Swagger
├── management/commands/         seed, bootstrap_admin, migrate_atomico, setup_2fa, carregar_feriados
├── migrations/                  0001–0046
├── templates/                   estrutura/ (site), painel/ (equipe), profissional/, agenda/, publico/,
│                                cotton/ (componentes), email/, pwa/, usuario/, erros 400/403/404/429/500
├── static/src/                  fonte do Vite (tokens.css, app.css, app.js — Alpine CSP)
├── static/js/                   wizard.js, admin-search.js, webpush.js (servidos direto)
├── signals.py · tasks.py · tasks_manutencao.py · middleware.py · checks.py
└── tests/
clinica/                         settings (base/dev/prod), urls, celery, wsgi, gunicorn_logger
docs/                            documentação (abaixo)
Dockerfile · railway.json · .github/workflows/ci.yml · package.json · vite.config.js
requirements.txt · requirements-dev.txt · requirements.lock · .env.example
```

---

## Documentação

| Documento | Conteúdo |
|---|---|
| [docs/PROJECT.md](docs/PROJECT.md) | Fonte viva: stack, arquitetura, modelos/tabelas, rotas, env vars, comandos, jobs, testes, deploy e **runbook de go-live** |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Decisões (ADR-lite) e checkpoint de progresso/pendências |
| [docs/AUTENTICACAO-E-ACESSO.md](docs/AUTENTICACAO-E-ACESSO.md) | Login da equipe, 2FA, OTP da cliente, links por token, sessões |
| [docs/REGRAS-DE-NEGOCIO.md](docs/REGRAS-DE-NEGOCIO.md) | Regras funcionais com os valores reais do código |
| [docs/specs/](docs/specs/) | Specs por assunto: banco (migrations 0027–0046), front, registry de regras |
| [docs/diagramas/](docs/diagramas/) + [docs/gerar_requisitos.py](docs/gerar_requisitos.py) | UML (Mermaid) e documento de requisitos do TCC (`Requisitos-ShivaZen.docx/.pdf`) |

---

## Licença

Sem arquivo de licença no repositório (`package.json`: `UNLICENSED`). Código de uso da
clínica e do TCC do autor.

Desenvolvido por Rafael Maciel.
