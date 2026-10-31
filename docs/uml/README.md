# Diagramas UML — Jaqueline Aranha Estética

Uma notação (PlantUML) e uma pasta. As imagens prontas estão em `png/` (para o Word e para
apresentação) e `svg/` (vetorial, para ampliar sem perder nitidez). Todas também entram no
documento [`../tcc/JaquelineAranhaEstetica-Documentacao.pdf`](../tcc/JaquelineAranhaEstetica-Documentacao.pdf).

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

Os cinco tipos que a banca cobra (e os de sequência, estados, componentes e implantação) também estão no Lucidchart, na pasta **Jaqueline Aranha Estética — TCC** da conta
do autor (editáveis, com o mesmo conteúdo do código): MER conceitual em notação de Chen (novo, só no
Lucid), DER completo + 5 por área, classes do domínio (5 páginas), casos de uso (7 páginas) e
atividades (3 páginas, a do pedido online com raias), 9 diagramas de sequência (um documento cada),
estados (4 páginas), componentes (2) e implantação + CI (2). Fora da pasta (a API perdeu a permissão de
mover; arraste à mão no Lucid): sequências 01–06, estados, componentes e implantação. Os títulos dos documentos ainda começam com "Shiva Zen —": a API do Lucid não deixa renomear documento (só a pasta), então troque à mão para "Jaqueline Aranha Estética —". DERs com os metadados em inglês (0079), por último em
cada tabela. Os PNGs exportados ficam em `lucid/`, com os
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
| Sequência 01 agendamento online (wizard + OTP) | https://lucid.app/lucidchart/75d6a0e9-a36e-40f5-91c1-cd4b60de8840/edit |
| Sequência 02 aprovação de pedido pendente | https://lucid.app/lucidchart/3c4b4efc-0d92-42c4-a91a-ab1be135492a/edit |
| Sequência 03 marcar REALIZADO e efeitos | https://lucid.app/lucidchart/10f4860a-cf45-4b00-b982-e414117e20cd/edit |
| Sequência 04 cancelamento pela cliente | https://lucid.app/lucidchart/fd820495-842d-4b73-8a62-85e321dbe662/edit |
| Sequência 05 lembrete D-1 e confirmação | https://lucid.app/lucidchart/86c912d8-9308-499a-a6f7-0855ad798eb7/edit |
| Sequência 06 NPS e pesquisa pós-atendimento | https://lucid.app/lucidchart/43346644-be62-45e4-b7be-a1d4b5a28574/edit |
| Sequência 07 login da equipe com 2FA | https://lucid.app/lucidchart/0c747222-7e7e-46a9-87ee-67b4f9adba79/edit |
| Sequência 08 fila de e-mail transacional | https://lucid.app/lucidchart/8d624d58-edab-47e5-a35c-bb41446e7d01/edit |
| Sequência 09 LGPD: pseudonimização e retenção | https://lucid.app/lucidchart/5ce819b2-dfa2-4e0f-986b-8a910413d530/edit |
| Diagramas de estados (4 páginas) | https://lucid.app/lucidchart/53cad129-f936-4b63-9069-2ab897f2f4e2/edit |
| Diagramas de componentes (2 páginas) | https://lucid.app/lucidchart/cfe98291-26b3-47b5-9c45-8f9965b33298/edit |
| Implantação e CI (2 páginas) | https://lucid.app/lucidchart/8f3c4247-731b-43ca-934d-de943735f37d/edit |

O Lucid é cópia de apresentação: a fonte oficial continua sendo o PlantUML gerado do código. Mudou
model, caso de uso ou fluxo? Atualize o PlantUML (`gerar_uml.py`) e refaça a página no Lucid.

## Regerar

```bash
python docs/uml/gerar_uml.py          # ER + classes a partir do código, depois render (SVG + PNG)
python docs/tcc/gerar_documentacao.py     # DOCX + PDF com todos os diagramas (Word no Windows)
```

O `gerar_uml.py` roda com o Python do projeto (`.venv`, lê os models) e precisa do PlantUML
(env `PLANTUML`: executável nativo ou `.jar`) e do Graphviz (env `GRAPHVIZ_DOT`, se não estiver
no PATH). Fontes geradas não se editam à mão: tabela ou serviço novo sem lugar em `PARTES` /
`GRUPOS_SERVICOS` faz o script parar com a lista do que falta. O estilo comum (cores da marca,
fundo branco para impressão) está em `fontes/estilo.iuml`.
