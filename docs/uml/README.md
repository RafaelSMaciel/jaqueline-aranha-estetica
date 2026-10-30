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
| `lucid/` | PNGs das versões do Lucidchart (MER, DER, classes, casos de uso, atividades) | exportado do Lucid |

## Versões no Lucidchart (apresentação)

Os cinco tipos que a banca cobra também estão no Lucidchart, na pasta **Shiva Zen — TCC** da conta
do autor (editáveis, com o mesmo conteúdo do código): MER conceitual em notação de Chen (novo, só no
Lucid), DER completo + 5 por área, classes do domínio (5 páginas), casos de uso (7 páginas) e
atividades (3 páginas, a do pedido online com raias). Os PNGs exportados ficam em `lucid/`, com os
mesmos nomes de `png/`; o `gerar_documentacao.py` usa a versão do Lucid quando ela existe.

| Documento no Lucid | Link (só o dono abre; compartilhe pelo Lucid) |
|---|---|
| MER conceitual | https://lucid.app/lucidchart/4beee2f2-12c4-4227-9c9d-6a08d4f04ed2/edit |
| DER completo (37 tabelas) | https://lucid.app/lucidchart/42915621-0aff-4481-b05b-3083e8be351f/edit |
| DER — agenda e atendimento | https://lucid.app/lucidchart/c421de51-3d04-411c-a32f-ef937653306e/edit |
| DER — clientes, saúde e formulários | https://lucid.app/lucidchart/4947d573-3e00-4dcc-be90-dac91a8a353f/edit |
| DER — pacotes, comissão e carteira | https://lucid.app/lucidchart/5e53a2f6-db41-4eaf-9e67-9b3d7054a34d/edit |
| DER — comunicação, links e verificação | https://lucid.app/lucidchart/0cac6427-4627-4e39-aeef-46b7ec7e06a2/edit |
| DER — LGPD, termos, auditoria e acesso | https://lucid.app/lucidchart/42e845fc-8842-488c-8443-53eb43898b12/edit |
| Diagrama de classes (domínio) | https://lucid.app/lucidchart/9abe5bfd-eb41-413e-aedf-b36edbb8c6b1/edit |
| Casos de uso | https://lucid.app/lucidchart/086ea942-cdda-461c-98e2-7068ccfe84f8/edit |
| Diagramas de atividade | https://lucid.app/lucidchart/0bc05f01-fc82-4329-8a6b-49e4e1a369a5/edit |

O Lucid é cópia de apresentação: a fonte oficial continua sendo o PlantUML gerado do código. Mudou
model, caso de uso ou fluxo? Atualize o PlantUML (`gerar_uml.py`) e refaça a página no Lucid.

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
