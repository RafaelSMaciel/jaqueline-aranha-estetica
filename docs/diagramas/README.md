# Diagramas UML + Requisitos — Shiva Zen

Fonte dos diagramas (Mermaid) e do documento de requisitos.

## Arquivos

- `*.mmd` — fonte dos 8 diagramas UML (casos de uso, classes, estados, sequência, componentes).
- `*.png` — diagramas renderizados (commitados p/ visualização sem re-render).
- `../_dados_requisitos.json` — dados extraídos do código (entidades, FSM, RN/RF/RNF, casos de uso, arquitetura). Fonte das tabelas do DOCX.
- `../gerar_requisitos.py` — gera `../Requisitos-ShivaZen.docx` + `.pdf` (python-docx + docx2pdf).
- `puppeteer.json` — **não versionado** (path absoluto do Chrome, específico da máquina).

## Regenerar

Pré-requisitos: `@mermaid-js/mermaid-cli` (global), Chrome instalado, `python-docx` + `docx2pdf`, MS Word (p/ o PDF).

```bash
# 1. puppeteer.json apontando p/ o Chrome local (barras normais):
#    { "executablePath": "C:/Program Files/Google/Chrome/Application/chrome.exe",
#      "args": ["--no-sandbox", "--disable-setuid-sandbox"] }

# 2. renderizar os diagramas
cd docs/diagramas
for f in *.mmd; do npx @mermaid-js/mermaid-cli -i "$f" -o "${f%.mmd}.png" -p puppeteer.json -b white -s 2; done

# 3. gerar DOCX + PDF
cd ../.. && python docs/gerar_requisitos.py
```

Para atualizar os **dados** (após mudança de model/view/regra), re-extrair do código e
sobrescrever `_dados_requisitos.json`, depois rodar o passo 3. Bump da versão em `gerar_requisitos.py` (`VERSAO`).

> Regra do projeto: a cada mudança de fluxo/model/view, regerar DOCX+PDF e versionar.
> Cuidado com `;` em mensagens de `sequenceDiagram` (separador de statement no Mermaid) e
> parênteses em alias de `participant` (quebram o parser).
