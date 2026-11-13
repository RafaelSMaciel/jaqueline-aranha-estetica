# Diagramas

Imagens exportadas do Lucidchart, prontas para o documento e para o artigo do TCC. Os quatro
diagramas principais (MER, DER, classes e casos de uso) existem em duas formas:

- **`completos/`** — o diagrama inteiro, numa imagem grande (para anexo ou consulta com zoom);
- **`recortes/<tipo>/`** — o mesmo diagrama separado por assunto, em tamanho legível numa página
  (para colocar no texto do artigo).

`outros/` guarda os diagramas de apoio: sequência, estados, atividades, componentes e implantação.

## Onde editar

| Diagrama | Documento no Lucidchart |
|---|---|
| MER | [Jaqueline Aranha Estética — MER](https://lucid.app/lucidchart/17b6a9b7-83b0-45a3-ba78-b4d1d3297de4/edit) |
| DER | [Jaqueline Aranha Estética — DER](https://lucid.app/lucidchart/c92ac255-ad3f-4c19-9cf4-d76c3aabdc4c/edit) |
| Classes | [Jaqueline Aranha Estética — Diagrama de classes](https://lucid.app/lucidchart/c7d76c51-99e3-47e3-87d7-04b2e9c96f3c/edit) |
| Casos de uso | [Jaqueline Aranha Estética — Casos de uso](https://lucid.app/lucidchart/65a20df7-d308-4e63-82c6-0804f0e56104/edit) |

**Diagramas completos (layout revisado em 13/11/2026).** As quatro imagens de `completos/` saem de
um documento só, com uma página por diagrama (classes, DER, casos de uso e MER):
[TCC Jaqueline Aranha — Diagramas completos (layout revisado)](https://lucid.app/lucidchart/58665c42-2b2e-4a43-a21e-0f6b051e06ce/edit).
O conteúdo é o mesmo da página 1 dos documentos acima (46 classes e 64 relacionamentos, 37 tabelas e
63 chaves estrangeiras, 90 casos de uso e 8 atores, 25 entidades e 35 relacionamentos); mudou a
disposição: blocos organizados em camadas, linhas em traçado ortogonal, cores por assunto e legenda.
No diagrama de classes, as oito ligações `created_by` com `Usuario` aparecem como atributo
(`- created_by: Usuario [0..1]`), notação equivalente à da associação dirigida, e as duas
autoassociações de `Atendimento` ganharam o nome do papel (`atendimento_reagendado` e
`atendimento_origem`). No de casos de uso, as três fronteiras do painel trazem o nome da parte
(agenda, cadastros e prontuário, financeiro e administração).
`completos/TCC_CONTEEX_2026_Diagramas_Completos.pdf` junta os quatro num arquivo só.

Em cada documento da tabela a página 1 é o diagrama completo no layout antigo e as seguintes são os recortes, na mesma ordem
dos arquivos daqui. O conteúdo vem do código: [`../geradores/diagramas_lucid.py`](../geradores/diagramas_lucid.py)
lê os models e escreve os scripts que montam cada página no Lucid; depois as páginas são exportadas
em PNG para estas pastas. Mudou um model? Rode o gerador, refaça as páginas afetadas e exporte de novo.

## Assuntos dos recortes

MER, DER e classes usam os mesmos dez assuntos, com as mesmas tabelas em cada um:

| Arquivo | Assunto | Tabelas |
|---|---|---|
| `01-atendimento-e-lista-de-espera` | Atendimento e lista de espera | atendimento, lista_espera |
| `02-profissionais-e-catalogo` | Profissionais e catálogo | profissional, procedimento, habilitacao, preco, promocao |
| `03-expediente-e-folgas` | Expediente e folgas | agenda_horario, agenda_excecao |
| `04-clientes-e-prontuario` | Clientes e prontuário | cliente, prontuario, prontuario_versao, anotacao_sessao |
| `05-fichas-de-anamnese` | Fichas de anamnese | formulario, resposta_formulario |
| `06-pacotes` | Pacotes | pacote, pacote_item, compra_pacote, compra_pacote_item, consumo_sessao |
| `07-comissao-e-carteira` | Comissão e carteira | regra_comissao, movimento_comissao, carteira, movimento_carteira |
| `08-comunicacao-e-links` | Comunicação e links | notificacao, avaliacao_nps, link_acesso, assinatura_push |
| `09-lgpd-e-termos` | LGPD e termos | consentimento, versao_termo, aceite_termo |
| `10-acesso-e-auditoria` | Acesso e auditoria | usuario, log_auditoria, historico_alteracao, codigo_otp, configuracao, execucao_job |

Casos de uso: `01-site-publico`, `02-area-da-cliente`, `03-painel-agenda`,
`04-painel-cadastros-e-prontuario`, `05-painel-financeiro-e-administracao`,
`06-portal-da-profissional` e `07-rotinas-automaticas`.

## Como os diagramas se correspondem

Os nomes são os mesmos nos três modelos de dados, para um poder ser conferido com o outro:

| MER (conceitual) | DER (físico) | Classes (UML) |
|---|---|---|
| Entidade (retângulo), ex.: **Cliente** | Tabela `cliente` | Classe `Cliente` |
| Atributo (elipse), ex.: `data_nascimento` | Coluna `data_nascimento` (tipo do PostgreSQL) | Atributo `- data_nascimento: Date [0..1]` |
| Atributo-chave sublinhado (`id`) | `PK` | `- id: Integer {id}` |
| Relacionamento (losango) com (mín., máx.) | `FK` + pé de galinha | Associação com papel (nome da FK sem `_id`) e multiplicidades |
| Relacionamento com atributos (ex.: **aceita**) | Tabela associativa (`aceite_termo`) | Classe associativa |

## Notação

**MER (Chen, com cardinalidade de Heuser).** Retângulo = entidade; losango = relacionamento; elipse
= atributo; atributo sublinhado = identificador. Junto de cada entidade vai o par (mínimo, máximo):
`(0,N)` = nenhum ou vários, `(1,1)` = exatamente um. Nas páginas por assunto, entidades de outros
assuntos aparecem em cinza tracejado, sem atributos.

**DER.** Tabelas com coluna de chave (`PK`, `FK`, `UK`), nome e tipo; ligações em pé de galinha
(barra = um, círculo = opcional, pé = muitos). Tabelas de outros assuntos aparecem com cabeçalho
cinza, só com a chave.

**Classes (UML 2).**

| Elemento | Significado |
|---|---|
| `- nome: Tipo` | atributo privado (coluna da tabela) |
| `[0..1]` | atributo opcional (aceita vazio) |
| `{id}`, `{unique}`, `{cifrado}` | chave, valor único, guardado criptografado |
| `+ /nome: Tipo {readOnly}` | propriedade derivada, calculada a partir de outros atributos |
| `+ nome(param: Tipo): Retorno` | operação pública; sublinhada = operação da classe (estática) |
| losango cheio | composição: a parte é apagada junto com o todo |
| seta aberta com rótulo | associação dirigida; o rótulo é o papel e os números são as multiplicidades (`1`, `0..1`, `0..*`) |
| seta tracejada | dependência (ex.: a classe usa uma enumeração) |
| `«enumeration»` | lista fixa de valores (situação, tipo, canal) |
| classe cinza tracejada | classe de outro assunto, mostrada só pelo nome |

**Casos de uso (UML 2).** Boneco = ator (à esquerda quem usa o sistema, à direita os serviços
externos); elipse = caso de uso; retângulo = fronteira do sistema. `«include»` (seta tracejada do
caso base para o incluído) = passo sempre executado. `«extend»` (seta tracejada do caso que estende
para o caso base) = comportamento opcional, com a condição entre colchetes; o caso base lista, no
compartimento **Extension Points**, os pontos onde as extensões entram.
