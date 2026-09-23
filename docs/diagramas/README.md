# Diagramas UML + Requisitos — Shiva Zen

Fonte dos diagramas (Mermaid) e do documento de requisitos do TCC.

## Arquivos

- `*.mmd` — fonte dos 8 diagramas UML: casos de uso público (01) e interno (02), classes do
  núcleo de agendamento (03), financeiro/pacotes (04) e prontuário/LGPD (05), estados do
  atendimento (06), sequência do agendamento com OTP (07) e componentes/implantação (08).
- `*.png` — diagramas renderizados (versionados, para ver sem re-renderizar).
- `../_dados_requisitos.json` — dados extraídos do código (entidades com campos/relações da
  introspecção dos models, FSM, RN/RF/RNF, atores, casos de uso, arquitetura). Fonte das tabelas
  do DOCX.
- `../gerar_requisitos.py` — gera `../Requisitos-ShivaZen.docx` + `.pdf` (python-docx + docx2pdf).
  `VERSAO`, `DATA` e `HISTORICO` ficam no topo do script.
- `puppeteer.json` — **não versionado** (`.gitignore`): caminho do Chrome local.

## Regenerar

Pré-requisitos: Node (usa `npx @mermaid-js/mermaid-cli`), Chrome instalado, `python-docx` +
`docx2pdf` e MS Word (o `docx2pdf` usa o Word para o PDF; sem Word, gere só o DOCX e exporte o
PDF pelo Word/LibreOffice).

```bash
# 1. puppeteer.json apontando p/ o Chrome local (barras normais):
#    { "executablePath": "C:/Program Files/Google/Chrome/Application/chrome.exe",
#      "args": ["--no-sandbox", "--disable-setuid-sandbox"] }

# 2. renderizar os diagramas
cd docs/diagramas
for f in *.mmd; do npx -y @mermaid-js/mermaid-cli -i "$f" -o "${f%.mmd}.png" -p puppeteer.json -b white -s 2; done

# 3. gerar DOCX + PDF
cd ../.. && python docs/gerar_requisitos.py
```

Mudou model/view/regra? Atualize `_dados_requisitos.json` (campos e relações podem ser
re-extraídos dos models por introspecção; descrições, regras e casos de uso são redigidos a
partir do código), acrescente uma linha em `HISTORICO`, suba `VERSAO` e rode os passos 2–3.

> Regra do projeto: a cada mudança de fluxo/model/view, regerar DOCX+PDF e versionar junto.
> Cuidados com o Mermaid: `;` em mensagem de `sequenceDiagram` separa instruções; parênteses em
> alias de `participant` quebram o parser; em `classDiagram` qualquer linha com `(...)` vira
> método — anotações como `«imutável por trigger»` não podem ter parênteses.
