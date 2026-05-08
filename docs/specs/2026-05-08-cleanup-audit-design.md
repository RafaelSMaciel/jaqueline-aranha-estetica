# Cleanup Audit — Design

**Data:** 2026-05-08
**Branch:** dev
**Tipo:** Audit-only (sem deletes automáticos)

## Objetivo

Identificar arquivos, código, templates, assets, dependências e configurações **não utilizados ou sem sentido** no projeto `shivazen-app`. Produzir relatório consolidado em `docs/CLEANUP_2026-05-08.md` com classificação de risco por item, para decisão manual posterior.

## Escopo

Cobertura em 5 áreas:

### 1. Filesystem (artefatos órfãos)

Alvos:
- SQLites soltos na raiz: `default_1.sqlite3`, `default_2.sqlite3`, `default_3.sqlite3`, `default_4.sqlite3` (4× 1.16MB, criados em 2026-05-08 00:22, não rastreados, não cobertos pelo `.gitignore` atual).
- Backup antigo: `db_dev.sqlite3.bak-1777499921` (já gitignored, ainda em disco).
- Docs duplicados: `docs/erd.md` vs `docs/PROJECT.md` (consolidação prévia em S326 pode ter deixado órfão).
- Scripts one-shot: `scripts/smoke_test.sh`, `scripts/gerar_docx_tecnica.py` (40KB) — verificar refs em CI/Procfile/docs.
- Caches: `.ruff_cache/` (gitignored, ok manter).
- Outros: arquivos `.bak`, `.old`, `.tmp`, `.orig`, `.swp` em qualquer subpasta.

### 2. Código Python (dead code)

Ferramenta: `vulture --min-confidence 80` em:
- `aranha_estetica/`
- `clinica/`

Trio manual obrigatório para descartar falsos positivos comuns em Django:
- Signals (`@receiver`, `connect()`)
- `AppConfig.ready()`
- Autodiscover (`admin.autodiscover`, Celery `autodiscover_tasks`)
- Management commands (`BaseCommand` subclasses)
- Celery tasks (`@shared_task`, `@app.task`)
- DRF serializer methods (`get_<field>`, `validate_<field>`)
- Django views referenciadas só em `urls.py` (vulture costuma flaggar)
- Hooks de migração (`RunPython` callbacks)

### 3. Templates e Static

Templates:
- Listar todo `*.html` em `**/templates/`.
- Para cada um, grep contra:
  - `render(*, "<nome>"`
  - `TemplateView.as_view(template_name="<nome>"`
  - `{% include "<nome>" %}`
  - `{% extends "<nome>" %}`
  - `template_name = "<nome>"`
- Templates sem nenhum hit → candidato.

Static:
- Listar `static/**/*.{css,js,png,jpg,svg,webp,ico,woff,woff2}`.
- Grep contra:
  - `{% static "<path>" %}`
  - CSS `url(<path>)`
  - JS `import` paths
  - `<link>`/`<script>` direto em templates
- Sem hit → candidato.

### 4. Dependências

Ferramenta: `deptry .` contra `requirements.txt`.

Trio manual para descartar implicit deps Django comuns:
- `gunicorn` (Procfile/Dockerfile)
- `whitenoise` (settings MIDDLEWARE)
- `psycopg2` / `psycopg2-binary` (DATABASES backend)
- `dj-database-url` (settings)
- Pacotes que aparecem só em `INSTALLED_APPS` sem import direto

### 5. Configuração

Inspeção manual:
- `INSTALLED_APPS`: cada app — verificar se tem refs (models, views, urls incluídas).
- `MIDDLEWARE`: cada classe — verificar se módulo existe e é ativo.
- URLs comentadas em `urls.py`.
- Settings legacy/duplicados em `clinica/settings/` (dev/prod/base).
- Variáveis de ambiente em `.env.example` sem uso no código.

## Método de execução

1. Instalar tools dev (não persistir em `requirements.txt`):
   ```bash
   pip install vulture deptry
   ```
2. Rodar tools, capturar output.
3. Para cada candidato, validar com `grep -r` cruzado.
4. Classificar risco:
   - **low** — zero refs em todo o repo, arquivo isolado, sem dynamic loading possível. Delete seguro.
   - **med** — refs ausentes mas tipo de objeto suporta dynamic ref (ex: signals, template tags, management cmd). Verificar manualmente antes.
   - **high** — refs ausentes mas é parte de feature potencialmente em uso (ex: view em URL comentada, app em INSTALLED_APPS). Investigar fluxo antes de remover.
5. Consolidar em relatório único.

## Deliverable

Arquivo: `docs/CLEANUP_2026-05-08.md`

Estrutura:

```markdown
# Cleanup Audit — 2026-05-08

## Resumo executivo
- N candidatos identificados
- X low risk, Y med, Z high
- Espaço potencial liberado: ~MB

## 1. Filesystem
| Caminho | Razão | Risco | Comando sugerido |
|---|---|---|---|
| default_1.sqlite3 | criado por bug em settings DATABASES, não rastreado | low | `rm default_*.sqlite3` |
| ... | ... | ... | ... |

## 2. Código Python
(mesma tabela)

## 3. Templates e Static
(mesma tabela)

## 4. Dependências
(mesma tabela)

## 5. Configuração
(mesma tabela)

## Anexos
- Output bruto vulture
- Output bruto deptry
```

## Não-objetivos

Não tocar / não incluir no scan:
- `.env`, `.env.example`, `.env.*` (config sensível)
- `migrations/` (mesmo "vazias" — Django precisa do histórico)
- `tmp_req/create_docx.js` e demais arquivos de `tmp_req/` (uso ativo do usuário, conforme memory `feedback_sempre_atualizar_docs`)
- `db_dev.sqlite3` (DB dev ativo)
- `.git/`, `.claude/`
- Arquivos do Railway/Docker (`railway.json`, `Procfile`, `Dockerfile`, `.dockerignore`) sem instrução explícita

Não executar deletes automáticos. Relatório é leitura, não ação.

## Critérios de sucesso

- Relatório cobre as 5 áreas.
- Cada candidato tem caminho, razão, risco, comando sugerido.
- Zero falso positivo de risco "low" (validar manualmente cada um).
- Tools dev (`vulture`, `deptry`) **não** aparecem em `requirements.txt` após scan.

## Riscos conhecidos

- **Vulture FP em Django:** mitigado pela lista de padrões do trio manual.
- **Deptry FP em implicit deps:** mitigado pela lista de exclusões conhecidas.
- **Dynamic imports / `getattr`:** vulture pode perder, grep manual cobre na maioria dos casos. Itens de risco "high" sinalizam casos onde validação automática é insuficiente.
- **Templates incluídos via variável Python:** `render(request, template_var)` onde `template_var` é construída em runtime. Grep não pega. Risco aceito; flagged como "med" se template tem nome padrão suspeito.
