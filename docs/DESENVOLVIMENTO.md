# Desenvolvimento local

Guia do dia a dia: ambiente igual ao CI, testes rápidos, ganchos, o que o CI confere e as fotos do
site.
Visão do sistema e runbook de produção: [PROJECT.md](PROJECT.md).

## 1. Ambiente igual ao CI e à imagem

O CI e a imagem de produção rodam **Python 3.12** com as versões do `requirements.lock`
(inclusive transitivas). Python ou pacote diferente no seu computador esconde bug que só o CI
vê — o system check `aranha.W014` avisa no `runserver`/`check` (só com `DEBUG=True`).

```bash
# com uv (baixa o Python 3.12 se faltar)
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt -c requirements.lock
#   Linux/macOS: --python .venv/bin/python

# ou com o Python 3.12 instalado
py -3.12 -m venv .venv               # Linux/macOS: python3.12 -m venv .venv
.venv\Scripts\activate               # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt -c requirements.lock

npm ci                               # Node 22 (.nvmrc)
pre-commit install                   # ganchos do §4 (uma vez por clone)
```

Mudou `requirements.txt`? Regere o lock (cabeçalho do `requirements.lock`) e reinstale com
`-c requirements.lock`. O Dependabot (`.github/dependabot.yml`) abre PRs semanais na `dev`.

Resto do setup (`.env`, `migrate`, `seed --demo`, `bootstrap_admin`, Vite + runserver): [README](../README.md#rodando-localmente).

## 2. Testes

O runner é um só: `manage.py test` (pytest, pytest-django e conftest saíram do projeto — teste em
função solta, estilo pytest, nunca seria coletado; `test_deploy_config.RunnerUnicoTests` reprova a
volta). Ele sempre usa `clinica/settings/test.py` (única detecção de "estou em teste", em
`settings/__init__.py`): senha com hash MD5 (rápido; produção não muda), SMS só em log, avisos de
produção silenciados, django-vite em `dev_mode` (não precisa do build). Testes novos de uma área
vão na pasta dela (`tests/conta/`, `infra/`, `jobs/`, `painel/`, `publico/`, `servicos/`, `utils/`).

| Comando | Quando |
|---|---|
| `python manage.py test aranha_estetica --exclude-tag migracoes` | Dia a dia — ~2.020 testes em ~40 s |
| `python manage.py test aranha_estetica.tests.test_x --keepdb` | Um módulo: o banco de teste (`.cache/db_test.sqlite3`) fica pronto entre execuções (~15 s → ~2 s) |
| `python manage.py test aranha_estetica --tag migracoes` | Data migrations (descem e sobem a cadeia; lento) — antes do merge |
| `python manage.py test aranha_estetica` | Tudo |

- **`--keepdb`** reaproveita o banco; migration nova é aplicada nele na próxima execução. Se o
  banco de teste ficar estranho (migration editada), rode uma vez sem `--keepdb`.
- **`--parallel`**: no Linux funciona direto. No Windows/macOS o Django só clona o banco para os
  workers a partir do SQLite em memória: `TESTE_SQLITE_MEMORIA=1 python manage.py test ... --parallel auto`
  (sem `--keepdb`). `tblib` (no requirements-dev) mostra o traceback dos workers.
- **Postgres** (como o CI; procedures, triggers, EXCLUDE e collation só existem lá — no SQLite
  esses testes são pulados):

  ```bash
  docker compose up -d db
  TEST_DATABASE_URL=postgres://aranha_estetica:aranha_estetica@localhost:5432/aranha_estetica PG_EXIGE_ICU=1 \
    python manage.py test aranha_estetica --exclude-tag migracoes
  ```

  O volume é o `pgdata_aranha_estetica` (usuário, senha e banco `aranha_estetica`). Um volume
  `pgdata` antigo, da época do nome Shiva Zen, pode ser apagado com `docker volume ls` +
  `docker volume rm <nome>`.

- **Navegador** (Playwright + Chromium): `tests/test_wizard_browser.py` (regras que só existem no
  `wizard.js`), `tests/painel/test_status_navegador.py` (seletor de status, lote e FullCalendar do
  bundle) e `tests/test_crawl_visual.py` — a varredura visual: toda rota pública, da equipe e da
  profissional em 1366px (claro) e 390px (escuro), reprovando erro HTTP/JS/console/CSP, rolagem
  lateral no celular, template vazando e violação serious/critical do axe-core (que vem do
  `npm ci`, devDependency). Pulados sem Playwright/Chromium/bundle (a varredura também sem o
  axe-core); no CI reprovam (`EXIGE_NAVEGADOR=1`).

  ```bash
  python -m playwright install chromium        # uma vez
  npm ci && npm run build
  TESTE_VITE_BUILD=1 python manage.py test aranha_estetica.tests.test_wizard_browser
  TESTE_VITE_BUILD=1 python manage.py test aranha_estetica.tests.painel.test_status_navegador
  TESTE_VITE_BUILD=1 python manage.py test aranha_estetica.tests.test_crawl_visual
  # uma tela só (trecho do nome da rota em tests/test_rotas.ROTAS):
  TESTE_VITE_BUILD=1 VARREDURA_ROTAS=painel_clientes python manage.py test aranha_estetica.tests.test_crawl_visual
  ```

  Achado de terceiro que não dá para corrigir aqui entra na lista `PERMITIDOS` do teste, com o
  motivo — nunca desligando a regra do axe.

- **Cobertura** (catraca do CI: `fail_under` em `pyproject.toml` só sobe). O coverage roda em modo
  paralelo (um arquivo `.cache/coverage.*` por execução): junte antes do relatório. No CI o job
  `cobertura` combina a rodada do SQLite com a do Postgres (o código só-Postgres conta) e aplica a
  catraca no total; localmente, rodar também no Postgres e combinar dá o mesmo número.

  ```bash
  coverage run manage.py test aranha_estetica --exclude-tag migracoes
  coverage combine && coverage report
  ```

## 3. Fuso horário (regra do ruff)

O servidor roda em UTC; a clínica, em `America/Sao_Paulo`. `date.today()` e `datetime.now()`
erram o dia entre 21h e 24h. O ruff (`DTZ`) barra no CI e no gancho:

| Em vez de | Use |
|---|---|
| `date.today()`, `timezone.now().date()` | `utils.datas.hoje()` (ou `timezone.localdate()`) |
| `dt.strftime(...)` de datetime do banco | `utils.datas.fmt_local(dt)` |
| `datetime.strptime(x, '%Y-%m-%d').date()` (formulário/query) | `utils.datas.ler_data(x)` / `ler_hora(x)` |
| `datetime(2026, 1, 1, 9)` em código | `timezone.make_aware(datetime.combine(dia, hora))` |
| `.filter(campo__date=dia)` | `.filter(**utils.datas.dias_locais('campo', dia))` (intervalo: usa o índice) |

Nos testes, datetime naive antes do `make_aware` é permitido (`per-file-ignores`).

## 4. Ganchos e checks locais

`pre-commit install` liga, em todo commit, os mesmos checks rápidos do CI
(`.pre-commit-config.yaml`; ferramentas do venv, sem download — exceto o gitleaks, binário que o
pre-commit baixa uma vez, na versão fixa do CI):

- `gitleaks` — segredo commitado por engano (`.env`, dump, chave de API) no que está sendo
  commitado; os dados sintéticos conhecidos (chaves falsas da suíte e do CI) estão na allowlist
  explicada em `.gitleaks.toml`.
- `ruff check` — erros reais (F, E4/E7/E9) + fuso (DTZ) + `TID251` (abaixo). Lista fixa em
  `[tool.ruff.lint]`.
- `lint-imports` — camadas `views > services > models`, `utils` sem `services` e as áreas de views
  independentes (`[tool.importlinter]`; helper de view comum vai em `views/comum.py`).
- `makemigrations --check` — quando muda `models/` ou `migrations/`.

`pre-commit run --all-files` roda tudo de uma vez. Antes do push vale também `mypy` (services).

**Configuração por ambiente (regra `TID251` do ruff):** `os.environ`/`os.getenv` só em
`clinica/settings` — lá a variável é lida, convertida e validada (valor inválido derruba o boot com
o nome dela) — e nos pontos de entrada listados em `[tool.ruff.lint.per-file-ignores]` (manage.py,
comandos de gerenciamento, opções do Sentry, `services/lgpd`, docs, testes). O resto do código lê
`settings.X` na hora da chamada (`override_settings` funciona nos testes). Variável nova: setting em
`clinica/settings/base.py` + linha no `.env.example` (`tests/infra/test_deploy.EnvExampleTests`
reprova se faltar ou sobrar).

## 5. O que o CI confere

`.github/workflows/ci.yml` (push em `main`/`dev`). Tabela de jobs: [PROJECT.md §12](PROJECT.md#12-testes-e-ci).
Endurecimento: token só leitura (`permissions: contents: read`), `timeout-minutes` em todo job,
actions fixadas por SHA e imagens (`postgres`, `python`, `node`) por digest — o Dependabot
atualiza o pin —, cache de camadas do `docker build` e o smoke da imagem com o pre-deploy e o start
lidos do `railway.json`. O job `auditoria-deps` é bloqueante: `pip-audit` no `requirements.lock`,
`npm audit --package-lock-only --audit-level=high` (vulnerabilidade alta do front derruba) e
gitleaks no histórico do push. O `tests/test_deploy_config.CiFixadoTests` reprova pin solto.

Antes de mexer no `package.json`, rode `npm audit --package-lock-only --audit-level=high`
localmente (o mesmo do CI). Lib de terceiro do navegador entra aqui, como o FullCalendar e o
Chart.js (versão exata), e é importada num módulo do Vite — nunca por `<script src>` de CDN
(`test_csp` reprova).

## 6. Fotos do site

As fotos do site ficam em `aranha_estetica/static/assets/clinica/*.webp` (originais). O que o
navegador baixa são as variantes de `static/assets/clinica/variantes/` — WebP e JPEG de reserva em
480, 768 e 1024 px (sem ampliar: a maior é a largura original) + `manifest.json` com largura e altura
—, servidas pela tag `{% foto 'nome' alt='...' sizes='...' %}` (`templatetags/site_tags`;
`prioridade=True` só na foto do topo). As variantes são **versionadas no git**: a imagem de produção
não gera nada.

```bash
python scripts/imagens.py              # gera/atualiza as variantes e o manifest (foto nova ou trocada)
python scripts/imagens.py --verificar  # só confere: sai 1 se faltar variante ou o manifest estiver velho
```

Requer Pillow, que vem do `requirements-dev.txt` (a imagem de produção não tem). Com a mesma versão do
Pillow a saída é determinística — rodar de novo sem foto nova não muda nenhum arquivo. A suíte faz a
mesma conferência do `--verificar` (`test_site_tags.FotoResponsivaTests`): foto nova sem rodar o
script reprova no CI. Moldura sem foto real usa a arte da marca (`partials/_arte_marca.html`) —
nunca foto inventada ou de outro site.
