# Diagramas UML — Shiva Zen

Uma notação (PlantUML) e uma pasta. As imagens prontas estão em `png/` (para o Word e para
apresentação) e `svg/` (vetorial, para ampliar sem perder nitidez). Todas também entram no
documento [`../ShivaZen-Documentacao.pdf`](../ShivaZen-Documentacao.pdf).

| Pasta | Conteúdo | Origem |
|---|---|---|
| `01-casos-de-uso/` | visão geral + site público, área da cliente, painel (agenda e gestão), portal da profissional, rotinas automáticas | escrito à mão |
| `02-classes/` | classes do domínio (5 partes) e camada de serviços (3 grupos) | **gerado** dos models e do código |
| `03-sequencia/` | agendamento online, aprovação, realizado, cancelamento, lembrete, NPS, login 2FA, fila de e-mail, LGPD | escrito à mão |
| `04-estados/` | atendimento (FSM), compra de pacote, notificação, comissão | escrito à mão |
| `05-atividades/` | validação de horário, do pedido ao realizado, cálculo de comissão | escrito à mão |
| `06-componentes/` | camadas do monólito e integrações externas | escrito à mão |
| `07-implantacao/` | Railway (produção e testes) e pipeline de CI | escrito à mão |
| `08-dados/` | modelo físico (visão geral + 5 partes) | **gerado** dos models |

## Regerar

```bash
python docs/uml/gerar_uml.py          # ER + classes a partir do código, depois render (SVG + PNG)
python docs/gerar_documentacao.py     # DOCX + PDF com todos os diagramas (Word no Windows)
```

O `gerar_uml.py` roda com o Python do projeto (`.venv`, lê os models) e precisa do PlantUML
(env `PLANTUML`: executável nativo ou `.jar`) e do Graphviz (env `GRAPHVIZ_DOT`, se não estiver
no PATH). Fontes geradas não se editam à mão: tabela ou serviço novo sem lugar em `PARTES` /
`GRUPOS_SERVICOS` faz o script parar com a lista do que falta. O estilo comum (cores da marca,
fundo branco para impressão) está em `fontes/estilo.iuml`.
