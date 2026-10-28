# Desenvolvimento local

Guia do dia a dia: ambiente igual ao CI, testes rápidos, ganchos e o que o CI confere.
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

`manage.py test` sempre usa `clinica/settings/test.py` (única detecção de "estou em teste", em
`settings/__init__.py`; o pytest aponta para o mesmo módulo no `pyproject.toml`): senha com hash
MD5 (rápido; produção não muda), SMS só em log, avisos de produção silenciados, django-vite em
`dev_mode` (não precisa do build).

| Comando | Quando |
|---|---|
| `python manage.py test aranha_estetica --exclude-tag migracoes` | Dia a dia — ~1.460 testes em ~40 s |
| `python manage.py test aranha_estetica.tests.test_x --keepdb` | Um módulo: o banco de teste (`db_test.sqlite3`) fica pronto entre execuções (~15 s → ~2 s) |
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
  TEST_DATABASE_URL=postgres://shivazen:shivazen@localhost:5432/shivazen PG_EXIGE_ICU=1 \
    python manage.py test aranha_estetica --exclude-tag migracoes
  ```

- **Navegador** (`tests/test_wizard_browser.py`, Playwright + Chromium; regras que só existem
  no `wizard.js`). Pulado sem Playwright/Chromium/bundle; no CI reprova (`EXIGE_NAVEGADOR=1`).

  ```bash
  python -m playwright install chromium        # uma vez
  npm run build
  TESTE_VITE_BUILD=1 python manage.py test aranha_estetica.tests.test_wizard_browser
  ```

- **Cobertura** (catraca do CI: `fail_under` em `pyproject.toml` só sobe):

  ```bash
  coverage run manage.py test aranha_estetica --exclude-tag migracoes && coverage report
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
(`.pre-commit-config.yaml`, ferramentas do venv — sem download):

- `ruff check` — erros reais (F, E4/E7/E9) + fuso (DTZ). Lista fixa em `[tool.ruff.lint]`.
- `lint-imports` — camadas `views > services > models` (`[tool.importlinter]`).
- `makemigrations --check` — quando muda `models/` ou `migrations/`.

`pre-commit run --all-files` roda tudo de uma vez. Antes do push vale também `mypy` (services).

## 5. O que o CI confere

`.github/workflows/ci.yml` (push em `main`/`dev`). Tabela de jobs: [PROJECT.md §12](PROJECT.md#12-testes-e-ci).
Endurecimento: token só leitura (`permissions: contents: read`), `timeout-minutes` em todo job,
actions fixadas por SHA e imagens (`postgres`, `python`, `node`) por digest — o Dependabot
atualiza o pin —, `pip-audit` bloqueante, cache de camadas do `docker build`. O
`tests/test_deploy_config.CiFixadoTests` reprova pin solto.
