# Cleanup Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce `docs/CLEANUP_2026-05-08.md` — audit-only report identifying unused/orphan items across filesystem, Python code, templates, static assets, dependencies, and config in `shivazen-app`. Zero deletes executed.

**Architecture:** Hybrid scan — `vulture` for Python dead code + `deptry` for unused requirements + manual `grep` for filesystem, templates, static, and config. Each scan section is appended to a single consolidated markdown report. Each task commits its section so the report grows incrementally and recoverably.

**Tech Stack:** Python 3.14 (system), `vulture` (pip), `deptry` (pip), `git`, `grep`/ripgrep, bash on Windows.

**Spec:** `docs/specs/2026-05-08-cleanup-audit-design.md`

**Repo layout (relevant):**
- Apps: `aranha_estetica/` (single Django app, modular: `models/`, `views/`, `services/`, `forms/`, `api/`, `domain/`, `tasks.py`, `signals.py`, `middleware.py`, `decorators*.py`, `validators.py`, `sitemaps.py`, `context_processors.py`, `exceptions.py`, `constants.py`, `management/`, `templates/`, `static/`, `tests/`)
- Project: `clinica/` (settings split: `base.py`/`dev.py`/`prod.py`; `celery.py`; `urls.py`; `wsgi.py`/`asgi.py`)
- URLs: `clinica/urls.py`, `aranha_estetica/urls.py`, `aranha_estetica/api/urls.py`
- Static: `aranha_estetica/static/{assets,css,js,vendor}`
- Templates: `aranha_estetica/templates/{agenda,email,estrutura,painel,partials,profissional,publico,servicos,usuario}`
- Root files of interest: `manage.py`, `requirements.txt`, `Procfile`, `Dockerfile`, `railway.json`, `.dockerignore`, `db_dev.sqlite3`, `db_dev.sqlite3.bak-1777499921`, `default_1.sqlite3`..`default_4.sqlite3`
- Scripts: `scripts/gerar_docx_tecnica.py`, `scripts/smoke_test.sh`
- Docs: `docs/PROJECT.md`, `docs/erd.md`, `docs/specs/`
- `tmp_req/` — keep entirely (active user workflow per memory)

**Convention:**
- Report file: `docs/CLEANUP_2026-05-08.md` (created in Task 0, appended by each section task)
- Risk levels: `low` (zero refs anywhere, isolated, safe delete) / `med` (no refs but supports dynamic loading — verify manually) / `high` (no refs but part of feature potentially in use — investigate flow)
- Each item table row: `| caminho | razão | risco | comando sugerido |`

---

## Task 0: Setup tooling and report skeleton

**Files:**
- Create: `docs/CLEANUP_2026-05-08.md`

- [ ] **Step 1: Install dev tools without persisting in `requirements.txt`**

Run:
```bash
pip install vulture deptry
```
Expected: Both install successfully. Confirm with:
```bash
vulture --version && deptry --version
```

- [ ] **Step 2: Verify tools do not appear in `requirements.txt`**

Run:
```bash
grep -E "^(vulture|deptry)" requirements.txt
```
Expected: zero output (no match). If present, abort and remove before proceeding.

- [ ] **Step 3: Create the report skeleton**

Write `docs/CLEANUP_2026-05-08.md` with:

```markdown
# Cleanup Audit — 2026-05-08

**Branch:** dev
**Spec:** `docs/specs/2026-05-08-cleanup-audit-design.md`
**Tipo:** Audit-only — nenhum delete executado.

## Resumo executivo

- Itens totais: _(preencher na consolidação final)_
- Distribuição de risco: low _N_ / med _N_ / high _N_
- Espaço potencial liberado: _~MB_

## 1. Filesystem

_(seção a ser preenchida pela Task 1)_

## 2. Código Python (dead code)

_(seção a ser preenchida pela Task 2)_

## 3. Templates

_(seção a ser preenchida pela Task 3)_

## 4. Static assets

_(seção a ser preenchida pela Task 4)_

## 5. Dependências

_(seção a ser preenchida pela Task 5)_

## 6. Configuração

_(seção a ser preenchida pela Task 6)_

## Anexos

_(outputs brutos das tools — preenchido pela Task 7)_
```

- [ ] **Step 4: Commit**

```bash
git add docs/CLEANUP_2026-05-08.md
git commit -m "chore(cleanup): inicia relatorio de audit cleanup com skeleton"
```

---

## Task 1: Filesystem scan

**Files:**
- Modify: `docs/CLEANUP_2026-05-08.md` (substituir placeholder da seção `## 1. Filesystem`)

- [ ] **Step 1: Listar SQLites soltos na raiz**

Run:
```bash
ls -la *.sqlite3 *.sqlite3.bak-* 2>/dev/null
```
Expected: deve listar `db_dev.sqlite3` (ativo, manter), `db_dev.sqlite3.bak-1777499921` (backup), `default_1.sqlite3`..`default_4.sqlite3` (suspeitos).

- [ ] **Step 2: Confirmar que `default_*.sqlite3` não está rastreado**

Run:
```bash
git ls-files --error-unmatch default_1.sqlite3 default_2.sqlite3 default_3.sqlite3 default_4.sqlite3 2>&1 | head -5
```
Expected: cada arquivo retorna erro `did not match any file(s) known to git` — confirma que estão untracked.

- [ ] **Step 3: Confirmar `.gitignore` atual NÃO cobre `default_*.sqlite3`**

Run:
```bash
git check-ignore -v default_1.sqlite3 || echo "NÃO IGNORADO"
```
Expected: imprime `NÃO IGNORADO`. Se imprimir regra do gitignore, então já está coberto e o item vira "não candidato".

- [ ] **Step 4: Procurar arquivos `.bak`/`.old`/`.tmp`/`.orig`/`.swp`/`.swo` em todo repo (excluindo node_modules, .git, .ruff_cache, __pycache__)**

Run:
```bash
find . -type f \( -name "*.bak" -o -name "*.bak-*" -o -name "*.old" -o -name "*.tmp" -o -name "*.orig" -o -name "*.swp" -o -name "*.swo" \) \
  -not -path "*/node_modules/*" \
  -not -path "*/.git/*" \
  -not -path "*/.ruff_cache/*" \
  -not -path "*/__pycache__/*"
```
Expected: lista todos os candidatos. Capturar output para a tabela.

- [ ] **Step 5: Verificar se `docs/erd.md` está referenciado em algum lugar do repo**

Run:
```bash
grep -rn "erd\.md\|docs/erd" --include="*.md" --include="*.py" --include="*.html" --include="*.json" --include="*.yml" --include="*.yaml" .
```
Expected: capturar refs. Se zero refs além do próprio arquivo, candidato (med — pode ser usado por humanos).

- [ ] **Step 6: Verificar se `scripts/gerar_docx_tecnica.py` é referenciado**

Run:
```bash
grep -rn "gerar_docx_tecnica" --include="*.md" --include="*.py" --include="*.json" --include="*.yml" --include="*.yaml" --include="*.sh" --include="Procfile" --include="Dockerfile" --include="*.ini" --include="*.cfg" --include="*.toml" .
```
Expected: capturar refs. Se zero (fora do próprio arquivo), candidato.

- [ ] **Step 7: Verificar se `scripts/smoke_test.sh` é referenciado**

Run:
```bash
grep -rn "smoke_test" --include="*.md" --include="*.py" --include="*.json" --include="*.yml" --include="*.yaml" --include="*.sh" --include="Procfile" --include="Dockerfile" --include="*.ini" --include="*.cfg" --include="*.toml" .
```
Expected: capturar refs. Avaliar — pode ser script humano puro de smoke test.

- [ ] **Step 8: Calcular tamanho dos candidatos low-risk para o resumo**

Run:
```bash
du -ch default_*.sqlite3 db_dev.sqlite3.bak-* 2>/dev/null | tail -1
```
Anotar valor para o "Espaço potencial liberado".

- [ ] **Step 9: Substituir o placeholder `## 1. Filesystem` no relatório**

Edit `docs/CLEANUP_2026-05-08.md` — substituir o bloco `_(seção a ser preenchida pela Task 1)_` por uma seção no formato:

```markdown
## 1. Filesystem

| Caminho | Razão | Risco | Comando sugerido |
|---|---|---|---|
| `default_1.sqlite3` | Untracked, criado por bug em DATABASES (4 cópias idênticas em 2026-05-08 00:22), não coberto por `.gitignore` | low | `rm default_*.sqlite3` |
| `default_2.sqlite3` | idem `default_1.sqlite3` | low | (incluído no comando acima) |
| `default_3.sqlite3` | idem | low | (idem) |
| `default_4.sqlite3` | idem | low | (idem) |
| `db_dev.sqlite3.bak-1777499921` | Backup antigo do DB de dev, já gitignored, ainda em disco | low | `rm db_dev.sqlite3.bak-1777499921` |
| _(demais arquivos `.bak`/`.old`/`.tmp` encontrados na Step 4)_ | _(razão por item)_ | low | `rm <caminho>` |
| `docs/erd.md` | _(preencher conforme refs encontradas na Step 5; se zero refs externas, conteúdo já consolidado em `PROJECT.md`)_ | _(low se zero refs, med caso contrário)_ | `git rm docs/erd.md` |
| `scripts/gerar_docx_tecnica.py` | _(preencher conforme Step 6)_ | _(low/med)_ | `git rm scripts/gerar_docx_tecnica.py` |
| `scripts/smoke_test.sh` | _(preencher conforme Step 7)_ | _(med — script de validação humana)_ | _(decidir após review)_ |

**Recomendação adicional:** acrescentar regra `default_*.sqlite3` ao `.gitignore` para evitar reincidência:

```diff
+ default_*.sqlite3
```
```

Substitua os trechos `_(...)_` pelos valores reais coletados nas steps anteriores. Não deixe placeholders.

- [ ] **Step 10: Commit**

```bash
git add docs/CLEANUP_2026-05-08.md
git commit -m "chore(cleanup): adiciona secao filesystem ao audit"
```

---

## Task 2: Python dead code (vulture)

**Files:**
- Create: `tmp_audit/vulture.txt` (output bruto)
- Modify: `docs/CLEANUP_2026-05-08.md` (substituir placeholder da seção `## 2. Código Python (dead code)`)

- [ ] **Step 1: Criar diretório temporário para outputs brutos**

Run:
```bash
mkdir -p tmp_audit
```
Expected: diretório criado. Não vai pro git (adicionar entrada local ou simplesmente não fazer `git add` em `tmp_audit/`).

- [ ] **Step 2: Rodar vulture com confidence mínima 80**

Run:
```bash
vulture aranha_estetica clinica --min-confidence 80 --exclude "*/migrations/*,*/__pycache__/*,*/tests/*" > tmp_audit/vulture.txt 2>&1 || true
```
Expected: arquivo `tmp_audit/vulture.txt` populado. Vulture sai com código != 0 quando encontra itens — `|| true` garante prossegue.

- [ ] **Step 3: Inspecionar output**

Run:
```bash
wc -l tmp_audit/vulture.txt && head -50 tmp_audit/vulture.txt
```
Expected: contagem total e primeiras 50 linhas para classificação.

- [ ] **Step 4: Filtrar falsos positivos comuns Django (lista de padrões a IGNORAR)**

Para cada linha do output, classificar como FP (falso positivo) se o nome corresponde a:
- Métodos `get_<field>` ou `validate_<field>` em arquivos sob `aranha_estetica/api/` (DRF serializers)
- Funções decoradas com `@receiver` em `aranha_estetica/signals.py`
- Subclasses de `BaseCommand` em `aranha_estetica/management/commands/` (método `handle`, `add_arguments`)
- Funções decoradas com `@shared_task` ou `@app.task` em `aranha_estetica/tasks.py`
- Métodos `ready()` em `apps.py`
- Callbacks `RunPython` em `migrations/` (já excluídas, mas reconfirmar)
- Views referenciadas em `aranha_estetica/urls.py` ou `aranha_estetica/api/urls.py`
- Métodos especiais Django (`__str__`, `Meta`, `clean`, `save`, `delete`, `get_absolute_url`)

Para cada linha NÃO-FP, validar com grep cruzado:

```bash
grep -rn "<nome_do_simbolo>" --include="*.py" --include="*.html" aranha_estetica clinica
```

Se grep retorna SOMENTE a definição (1 hit no arquivo do vulture) → candidato real.

- [ ] **Step 5: Classificar candidatos por risco**

Para cada candidato confirmado:
- `low` — função/classe/variável em módulo isolado, sem imports externos, não exposta em `__init__.py`, não em `urls.py`/`signals.py`/`apps.py`
- `med` — referenciada em algum lugar dinâmico potencial (template, string em settings, `getattr`)
- `high` — parte de feature visível (view, model, form) mas atualmente sem rota — pode estar em desenvolvimento

- [ ] **Step 6: Substituir o placeholder `## 2. Código Python (dead code)` no relatório**

Edit `docs/CLEANUP_2026-05-08.md` — formato:

```markdown
## 2. Código Python (dead code)

> Tool: `vulture --min-confidence 80`. Padrões Django (signals, DRF, management commands, Celery tasks) descartados manualmente como falsos positivos.

| Caminho:linha | Símbolo | Razão | Risco | Comando sugerido |
|---|---|---|---|---|
| `aranha_estetica/<arquivo>.py:NN` | `<nome>` | Vulture flag + zero refs em grep cruzado | low | Remover linhas NN-MM |
| ... | ... | ... | ... | ... |

**Falsos positivos descartados:** _(N itens)_ — padrões: signals, DRF serializer methods, management commands, Celery tasks, `apps.ready()`.
```

Preencher cada linha com dados reais. Se a tabela ficar vazia (zero candidatos reais), escrever:

```markdown
Nenhum candidato real após filtragem de falsos positivos. Todos os _N_ itens flaggados pelo vulture correspondem a padrões dinâmicos do Django.
```

- [ ] **Step 7: Salvar `tmp_audit/vulture.txt` como anexo no relatório**

Apêndice será consolidado na Task 7. Por ora, manter `tmp_audit/vulture.txt` no disco — não commit.

- [ ] **Step 8: Commit**

```bash
git add docs/CLEANUP_2026-05-08.md
git commit -m "chore(cleanup): adiciona secao Python dead code ao audit"
```

---

## Task 3: Templates orphan scan

**Files:**
- Create: `tmp_audit/templates_scan.txt`
- Modify: `docs/CLEANUP_2026-05-08.md` (seção `## 3. Templates`)

- [ ] **Step 1: Listar todos os templates**

Run:
```bash
find aranha_estetica/templates -name "*.html" -type f | sort > tmp_audit/templates_all.txt
wc -l tmp_audit/templates_all.txt
```
Expected: lista total.

- [ ] **Step 2: Para cada template, gerar dois nomes de busca — caminho relativo e nome simples**

Exemplo: `aranha_estetica/templates/painel/agendamentos.html` → buscar tanto `painel/agendamentos.html` quanto, em casos ambíguos, apenas `agendamentos.html`.

Rodar script de validação (loop bash):

```bash
> tmp_audit/templates_scan.txt
while read tpl; do
  rel="${tpl#aranha_estetica/templates/}"
  count=$(grep -rln --include="*.py" --include="*.html" -F "$rel" aranha_estetica clinica | grep -v "^${tpl}$" | wc -l)
  echo "$count	$tpl" >> tmp_audit/templates_scan.txt
done < tmp_audit/templates_all.txt
sort -n tmp_audit/templates_scan.txt | head -30
```
Expected: linhas começando com `0\t` são candidatos a órfão.

- [ ] **Step 3: Inspecionar candidatos com 0 refs**

Run:
```bash
awk -F'\t' '$1 == 0 {print $2}' tmp_audit/templates_scan.txt
```
Expected: lista de candidatos. Para cada um, fazer um segundo grep mais permissivo (sem path, só nome do arquivo):

```bash
for tpl in $(awk -F'\t' '$1 == 0 {print $2}' tmp_audit/templates_scan.txt); do
  base=$(basename "$tpl")
  hits=$(grep -rln --include="*.py" --include="*.html" -F "$base" aranha_estetica clinica | grep -v "^${tpl}$" | wc -l)
  echo "$hits	$tpl"
done
```
Templates com `0` em ambos os greps → candidatos confirmados.

- [ ] **Step 4: Para cada candidato confirmado, verificar se é referenciado dinamicamente**

Heurística: grep por `template_name` setado por variável ou `render(request, f"...")`:

```bash
grep -rn "template_name\s*=\|render(request,\s*f\"" --include="*.py" aranha_estetica
```
Se houver render dinâmico próximo ao nome do template, classificar risco como `med`.

- [ ] **Step 5: Substituir o placeholder `## 3. Templates` no relatório**

Edit `docs/CLEANUP_2026-05-08.md`:

```markdown
## 3. Templates

> Método: grep cruzado de cada caminho relativo em `*.py` e `*.html`. Confirmação por busca de basename. Risco `med` quando há `render(request, var)` dinâmico no app.

| Caminho | Razão | Risco | Comando sugerido |
|---|---|---|---|
| `aranha_estetica/templates/<path>.html` | Zero refs em `render`/`include`/`extends`/`template_name` | low | `git rm aranha_estetica/templates/<path>.html` |
| ... | ... | ... | ... |
```

Se zero candidatos: escrever `Nenhum template órfão encontrado.`

- [ ] **Step 6: Commit**

```bash
git add docs/CLEANUP_2026-05-08.md
git commit -m "chore(cleanup): adiciona secao templates ao audit"
```

---

## Task 4: Static assets orphan scan

**Files:**
- Create: `tmp_audit/static_scan.txt`
- Modify: `docs/CLEANUP_2026-05-08.md` (seção `## 4. Static assets`)

- [ ] **Step 1: Listar todos os arquivos estáticos**

Run:
```bash
find aranha_estetica/static -type f \( \
  -name "*.css" -o -name "*.js" -o -name "*.png" -o -name "*.jpg" \
  -o -name "*.jpeg" -o -name "*.svg" -o -name "*.webp" -o -name "*.ico" \
  -o -name "*.woff" -o -name "*.woff2" -o -name "*.ttf" -o -name "*.gif" \
\) | sort > tmp_audit/static_all.txt
wc -l tmp_audit/static_all.txt
```

- [ ] **Step 2: Para cada static, buscar referência por basename e por caminho relativo**

Run:
```bash
> tmp_audit/static_scan.txt
while read asset; do
  rel="${asset#aranha_estetica/static/}"
  base=$(basename "$asset")
  hits_rel=$(grep -rln --include="*.html" --include="*.css" --include="*.js" --include="*.py" -F "$rel" aranha_estetica clinica | grep -v "^${asset}$" | wc -l)
  hits_base=$(grep -rln --include="*.html" --include="*.css" --include="*.js" --include="*.py" -F "$base" aranha_estetica clinica | grep -v "^${asset}$" | wc -l)
  total=$((hits_rel + hits_base))
  echo "$total	$asset" >> tmp_audit/static_scan.txt
done < tmp_audit/static_all.txt
sort -n tmp_audit/static_scan.txt | head -30
```

- [ ] **Step 3: Filtrar candidatos**

Run:
```bash
awk -F'\t' '$1 == 0 {print $2}' tmp_audit/static_scan.txt
```
Expected: lista de candidatos a órfão.

- [ ] **Step 4: Cuidados especiais**

Itens a sempre marcar como `med` (não `low`) mesmo com zero hits:
- `vendor/` — bibliotecas terceiras (Bootstrap, FontAwesome) usadas via `<link>` que pode estar em template via include parcial
- `.woff2` / `.woff` — referenciados dentro de CSS via `url()`. Se o CSS pai tem refs, fonte é usada implicitamente.
- Imagens em `assets/` que podem ser referenciadas por OG tags, manifest.json, sitemap, etc.

Antes de marcar `low`, validar manualmente:

```bash
grep -rn "<basename_sem_extensao>" --include="*.html" --include="*.css" --include="*.js" aranha_estetica
```

- [ ] **Step 5: Substituir o placeholder `## 4. Static assets` no relatório**

Edit `docs/CLEANUP_2026-05-08.md`:

```markdown
## 4. Static assets

> Método: grep duplo (caminho relativo + basename) em `*.html`/`*.css`/`*.js`/`*.py`. `vendor/` e fontes recebem risco `med` por padrão.

| Caminho | Razão | Risco | Comando sugerido |
|---|---|---|---|
| `aranha_estetica/static/<path>` | Zero refs por basename e caminho | low | `git rm aranha_estetica/static/<path>` |
| ... | ... | ... | ... |
```

Se zero candidatos: escrever `Nenhum asset estático órfão encontrado.`

- [ ] **Step 6: Commit**

```bash
git add docs/CLEANUP_2026-05-08.md
git commit -m "chore(cleanup): adiciona secao static assets ao audit"
```

---

## Task 5: Dependências (deptry)

**Files:**
- Create: `tmp_audit/deptry.txt`
- Modify: `docs/CLEANUP_2026-05-08.md` (seção `## 5. Dependências`)

- [ ] **Step 1: Rodar deptry**

Run:
```bash
deptry . --known-first-party aranha_estetica --known-first-party clinica > tmp_audit/deptry.txt 2>&1 || true
cat tmp_audit/deptry.txt
```
Expected: deptry imprime 4 categorias — `DEP001` (missing), `DEP002` (unused), `DEP003` (transitive), `DEP004` (misplaced dev). Foco principal em `DEP002`.

- [ ] **Step 2: Para cada item `DEP002` (unused), descartar implicit deps Django conhecidos**

Lista de implicit deps a NUNCA marcar como candidato:
- `gunicorn` — referenciado em `Procfile`/`Dockerfile`
- `whitenoise` — referenciado em `MIDDLEWARE` em `clinica/settings/`
- `psycopg2`, `psycopg2-binary` — backend de DB em produção
- `dj-database-url` — settings prod
- `django-environ` — settings reading
- `celery`, `redis`, `kombu` — Celery worker stack (usado via app)
- `django-redis` — cache backend
- `daphne`, `channels` — ASGI/websockets se usados
- `pillow` — `ImageField` em models

Para cada item NÃO na lista acima, validar manualmente:

```bash
grep -rn "import <pacote>\|from <pacote>" --include="*.py" .
```
Se zero hits → candidato real.

- [ ] **Step 3: Validar `DEP001` (missing)**

Cada item DEP001 indica um import sem entrada no `requirements.txt`. Não é candidato a remoção, mas vai pro relatório como nota informativa.

- [ ] **Step 4: Substituir o placeholder `## 5. Dependências` no relatório**

Edit `docs/CLEANUP_2026-05-08.md`:

```markdown
## 5. Dependências

> Tool: `deptry`. Implicit deps Django (whitenoise, gunicorn, psycopg2, dj-database-url, django-environ, celery, redis, kombu, django-redis, daphne, channels, pillow) descartados como falsos positivos.

### Não usados (DEP002)

| Pacote | Razão | Risco | Comando sugerido |
|---|---|---|---|
| `<pacote>` | deptry DEP002 + zero imports em `*.py` | low | Remover linha de `requirements.txt`; `pip uninstall <pacote>` |
| ... | ... | ... | ... |

### Faltando declaração (DEP001) — nota informativa

| Import | Origem | Ação |
|---|---|---|
| `<modulo>` | usado em `<arquivo>.py` mas ausente de `requirements.txt` | Adicionar a `requirements.txt` |

### Falsos positivos descartados

_(lista das implicit deps Django acima, presentes no output do deptry)_
```

Se zero unused: `Nenhum pacote não-usado encontrado após filtragem.`

- [ ] **Step 5: Commit**

```bash
git add docs/CLEANUP_2026-05-08.md
git commit -m "chore(cleanup): adiciona secao dependencias ao audit"
```

---

## Task 6: Configuração (settings, INSTALLED_APPS, MIDDLEWARE, URLs)

**Files:**
- Modify: `docs/CLEANUP_2026-05-08.md` (seção `## 6. Configuração`)

- [ ] **Step 1: Listar `INSTALLED_APPS` em `clinica/settings/base.py`**

Run:
```bash
grep -nA 50 "INSTALLED_APPS" clinica/settings/base.py | head -80
```
Expected: lista completa.

Para cada app NÃO-Django-padrão e NÃO `aranha_estetica`, validar:
- Existe entrada `import <app>` em algum lugar?
- Tem URL include em `clinica/urls.py` ou `aranha_estetica/urls.py`?
- Foi instalado mas nunca configurado?

```bash
grep -rn "<app_name>" --include="*.py" .
```

- [ ] **Step 2: Listar `MIDDLEWARE`**

Run:
```bash
grep -nA 30 "MIDDLEWARE" clinica/settings/base.py | head -60
```
Para cada middleware customizado (path próprio, não Django padrão), confirmar que módulo existe:

```bash
python -c "from <middleware_path> import *" 2>&1 | head -3
```
Erro de import → middleware quebrado / removido.

- [ ] **Step 3: Procurar URLs comentadas**

Run:
```bash
grep -rn "^\s*#.*\(path\|url\|re_path\)(" --include="*.py" aranha_estetica clinica
```
Linhas comentadas em arquivos `urls.py` são candidatas a remoção (ou descomentar se for feature pendente).

- [ ] **Step 4: Comparar `dev.py` e `prod.py` para configs duplicadas/legacy**

Run:
```bash
diff -u clinica/settings/dev.py clinica/settings/prod.py | head -100
```
Inspecionar — settings comentadas, valores hardcoded duplicados, configs órfãs apontando para módulos removidos.

- [ ] **Step 5: Validar variáveis de `.env.example` contra uso real no código**

Run:
```bash
grep -E "^[A-Z_]+=" .env.example | cut -d= -f1 > tmp_audit/env_vars.txt
while read var; do
  hits=$(grep -rln "$var" --include="*.py" clinica aranha_estetica | wc -l)
  echo "$hits	$var"
done < tmp_audit/env_vars.txt | sort -n | head -20
```
Vars com `0` hits são candidatas a remoção do `.env.example`.

- [ ] **Step 6: Substituir o placeholder `## 6. Configuração` no relatório**

Edit `docs/CLEANUP_2026-05-08.md`:

```markdown
## 6. Configuração

### INSTALLED_APPS

| App | Razão | Risco | Comando sugerido |
|---|---|---|---|
| `<app>` | Sem refs externas, sem URLs incluídas | high | Remover de `INSTALLED_APPS` em `clinica/settings/base.py` |

### MIDDLEWARE

| Classe | Razão | Risco | Comando sugerido |
|---|---|---|---|
| `<path.Class>` | Módulo não importável | high | Remover de `MIDDLEWARE` em `clinica/settings/base.py` |

### URLs comentadas

| Arquivo:linha | Trecho | Risco | Comando sugerido |
|---|---|---|---|
| `<arquivo>:NN` | `# path("...", view)` | low | Remover linha NN |

### Variáveis `.env.example` sem uso

| Variável | Risco | Comando sugerido |
|---|---|---|
| `<VAR>` | low | Remover linha de `.env.example` |

### Settings duplicadas / legacy

_(observações livres do diff dev/prod, se aplicável)_
```

Se zero achados: `Configuração consistente — nada a remover.`

- [ ] **Step 7: Commit**

```bash
git add docs/CLEANUP_2026-05-08.md
git commit -m "chore(cleanup): adiciona secao configuracao ao audit"
```

---

## Task 7: Consolidação final — resumo executivo + anexos

**Files:**
- Modify: `docs/CLEANUP_2026-05-08.md` (atualizar seção `## Resumo executivo` e adicionar `## Anexos`)

- [ ] **Step 1: Contar itens por seção e por risco**

Run:
```bash
grep -E "^\| \`" docs/CLEANUP_2026-05-08.md | awk -F'|' '{print $4}' | sort | uniq -c
```
Expected: contagem por nível de risco.

- [ ] **Step 2: Calcular espaço total liberável (apenas low risk)**

Somar tamanhos dos arquivos low risk de Filesystem (já calculado na Task 1) + tamanhos de templates/static órfãos low risk.

```bash
du -ch <lista_de_caminhos_low_risk> 2>/dev/null | tail -1
```

- [ ] **Step 3: Atualizar a seção `## Resumo executivo`**

Edit `docs/CLEANUP_2026-05-08.md` substituindo o bloco de resumo por valores reais:

```markdown
## Resumo executivo

- **Itens totais:** N
- **Distribuição de risco:** low _N1_ / med _N2_ / high _N3_
- **Espaço potencial liberado (apenas low risk):** ~X MB

**Recomendação de execução:**

1. Aplicar todos os low risk em um único commit (`chore: cleanup low-risk dead weight`).
2. Revisar med risk caso a caso, validando dynamic loading antes de remover.
3. Tratar high risk como tickets separados — exigem investigação de fluxo / decisão de produto.

**Itens fora do scope** _(não tocar):_
- `tmp_req/` — workflow ativo do usuário
- `migrations/` — histórico Django obrigatório
- `db_dev.sqlite3` (ativo), `.env*`, `.git/`, `.claude/`
- `Procfile`, `Dockerfile`, `railway.json`, `.dockerignore` — sem instrução explícita
```

- [ ] **Step 4: Adicionar anexos com outputs brutos**

Anexar conteúdo de `tmp_audit/vulture.txt` e `tmp_audit/deptry.txt` ao final do relatório:

Edit `docs/CLEANUP_2026-05-08.md`, substituir o bloco `_(outputs brutos das tools — preenchido pela Task 7)_` por:

````markdown
## Anexos

### Vulture (output bruto)

```
<conteúdo de tmp_audit/vulture.txt>
```

### Deptry (output bruto)

```
<conteúdo de tmp_audit/deptry.txt>
```

### Templates scan (totais por arquivo)

```
<primeiras 30 linhas de tmp_audit/templates_scan.txt>
```

### Static scan (totais por arquivo)

```
<primeiras 30 linhas de tmp_audit/static_scan.txt>
```
````

- [ ] **Step 5: Final pass — verificar zero placeholders**

Run:
```bash
grep -nE "TODO|TBD|_\(.*preencher.*\)_|_\(.*seção a ser preenchida.*\)_|_\(.*pacote.*\)_|_\(.*arquivo.*\)_" docs/CLEANUP_2026-05-08.md
```
Expected: zero matches. Se houver, voltar e preencher cada um.

- [ ] **Step 6: Limpar `tmp_audit/`**

Run:
```bash
rm -rf tmp_audit
```
Expected: diretório removido. Conteúdo já está nos anexos do relatório.

- [ ] **Step 7: Confirmar que nem `vulture` nem `deptry` foram persistidas em `requirements.txt`**

Run:
```bash
grep -E "^(vulture|deptry)" requirements.txt && echo "ERRO: ferramentas vazaram para requirements.txt" || echo "OK"
```
Expected: `OK`.

- [ ] **Step 8: Commit final**

```bash
git add docs/CLEANUP_2026-05-08.md
git commit -m "chore(cleanup): finaliza audit com resumo executivo e anexos"
```

- [ ] **Step 9: Sumário ao usuário**

Reportar:
- Caminho do relatório: `docs/CLEANUP_2026-05-08.md`
- Total de itens e distribuição de risco
- Espaço liberável estimado
- Próxima ação sugerida: revisar relatório e decidir lote a executar

---

## Recap & Self-Review

**Spec coverage:**

- Spec § 1 Filesystem → Task 1 ✅
- Spec § 2 Código Python → Task 2 ✅
- Spec § 3 Templates e Static → Tasks 3 + 4 ✅
- Spec § 4 Dependências → Task 5 ✅
- Spec § 5 Configuração → Task 6 ✅
- Spec deliverable (`docs/CLEANUP_2026-05-08.md` com tabelas + risco + comando) → Tasks 0–7 ✅
- Spec não-objetivos (não tocar `.env*`, migrations, `tmp_req/`, db ativo) → Task 7 Step 3 (lista explícita) ✅
- Spec critérios de sucesso (vulture/deptry não em `requirements.txt`) → Task 0 Step 2 + Task 7 Step 7 ✅

**Placeholder scan:** templates de tabela usam `_(...)_` como instruções de preenchimento, com instrução explícita "Substitua os trechos `_(...)_` pelos valores reais coletados". Task 7 Step 5 verifica que não sobraram. OK.

**Type consistency:** sem types — projeto é um relatório markdown. N/A.

**Notas operacionais:**
- Plano salvo em `docs/specs/` (não em `docs/superpowers/plans/`) porque `.gitignore` exclui `docs/superpowers/`. Preserva commitabilidade.
- `tmp_audit/` é local apenas, removido na Task 7 — não vai para o git.
- Modo caveman do usuário não afeta conteúdo do plano (skill regra: "Code/commits/PRs: write normal").
