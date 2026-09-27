# Padrão SQL — nomes, tipos, índices, integridade e lógica no banco

> Spec única (sobrescrever a cada revisão). Vale para toda migration nova a partir da `0048`.
> Complementa a [remodelagem v2.1](remodelagem-banco-v2.md), que registra o histórico
> 0027–0047. Guarda automática: [`tests/test_padrao_sql.py`](../../aranha_estetica/tests/test_padrao_sql.py)
> (nomes, domínio dos choices, índice redundante; no Postgres, catálogo real).
> Última varredura: 2026-09-27 (34 tabelas, migrations 0001–0047) → aplicada em `0048`/`0049`.

## 1. Princípios

1. **O banco defende as invariantes.** Tudo que é regra de dado (domínio, unicidade, faixa,
   imutabilidade) tem constraint ou trigger; a aplicação valida antes para dar mensagem boa.
2. **Um nome, um significado.** PT-BR, singular, `snake_case`, sem sigla solta. Quem lê o
   catálogo (`\d+ tabela`) entende sem abrir o código.
3. **Nada de índice "por via das dúvidas".** Índice existe porque uma query o usa; índice
   coberto por outro sai.
4. **Upgrade de produção nunca aborta por dado inesperado** quando dá para seguir com
   segurança (CHECK novo entra `NOT VALID`); aborta, com a lista de ids, só quando seguir
   perderia informação.

## 2. Nomes

### 2.1 Tabelas e colunas

| Item | Regra | Exemplo |
|---|---|---|
| Tabela | `substantivo[_qualificador]`, singular, `db_table` explícito | `compra_pacote`, `movimento_carteira` |
| PK | `id` (`BigAutoField`) | |
| FK | `<entidade>_id` (campo Django `<entidade>`); papel explícito quando há mais de uma | `atendimento_origem_id`, `profissional_desejado_id` |
| Booleano | adjetivo/particípio ou verbo no presente | `ativo`, `notificado`, `eh_retorno`, `exige_retorno`, `aceita_comunicacao` |
| Instante | `<verbo>_em` (`timestamptz`) | `criado_em`, `usado_em`, `expira_em` |
| Data | `data_<qualificador>` ou `<algo>_desde/_ate` | `data_nascimento`, `vigente_desde`, `recorrencia_ate` |
| Dinheiro | `valor[_qualificador]` / `preco_<qualificador>` — `Decimal(10,2)` | `valor_pago`, `preco_total` |
| Percentual | `percentual` / `<algo>_percentual` — `Decimal(5,2)` + CHECK 0–100 | `desconto_percentual` |
| Quantidade / duração | unidade no sufixo | `quantidade_sessoes`, `duracao_minutos`, `validade_meses`, `antecedencia_minima_horas` |
| Segredo / prova | `token_<uso>`, `<algo>_hash`, `conteudo_sha256` | `token_cancelamento`, `codigo_hash` |
| Snapshot de nome | `<papel>_nome` ao lado da FK | `autor_nome`, `usuario_nome` |

**Termos técnicos aceitos em inglês** (nome do protocolo/padrão, não tradução): `slug`, `token`,
`email`, `ip`, `user_agent`, `endpoint`, `p256dh`, `auth`, `url`, `gcal_*`, `ics_token`, `cpf`,
`rg`, `cep`.

### 2.2 Objetos do banco

| Objeto | Formato | Limite |
|---|---|---|
| Índice | `idx_<tabela>_<colunas ou propósito>` | **30** chars (Django `models.E034`) |
| UNIQUE (constraint ou índice parcial) | `uniq_<tabela>_<descrição>` | 63 (Postgres) |
| CHECK | `chk_<tabela>_<descrição>` | 63 |
| EXCLUDE | `excl_<tabela>_<descrição>` | 63 |
| Trigger | `trg_<tabela>_<efeito>` | 63 |
| Função (plpgsql) | `fn_<tabela>_<efeito>` | 63 |

Quando `idx_<tabela>_...` não cabe em 30, o índice usa a **sigla registrada** da tabela
(só estas; a lista vive em `SIGLAS_INDICE` do teste):

| Tabela | Sigla |
|---|---|
| `formulario_anamnese` | `form_anamnese` |
| `resposta_anamnese` | `resp_anamnese` |
| `movimento_carteira` | `mov_carteira` |
| `movimento_comissao` | `mov_comissao` |

`unique_together`/`index_together` não são usados: toda unicidade é `UniqueConstraint`
nomeada. FKs e PKs ficam com o nome que o Django gera.

## 3. Tipos e nulidade

- **Texto nunca nulo** (`default=''`), exceto: colunas sob UNIQUE parcial (e-mail, CPF,
  telefone, tokens, `codigo_indicacao`, `slug`) e choices em que "sem valor" é um estado
  (`notificacao.resposta`, `lista_espera.turno_desejado`). Ver dívida em §8.
- **Status/tipo/escopo** = `CharField` + `choices` + **CHECK de domínio**
  (`<campo>__in=[...]`, lista literal igual aos choices). O teste reprova choice sem CHECK
  ou CHECK fora de sincronia.
- **Referência genérica a registro** (`log_auditoria.registro_id`) é `bigint`, como as PKs.
- **JSON** é `JSONField` (jsonb) com CHECK `jsonb_typeof` quando o formato é fixo.
- **IP** é `GenericIPAddressField`; valor inválido vira NULL, nunca inventado.
- **Telefone** só dígitos, nacional (10–11), CHECK regex no Postgres.

## 4. Índices

- Criar para: filtro/ordenação de tela ou job que roda com frequência; FK usada em join ou
  em `ON DELETE`; lookup `iexact` (índice de expressão `UPPER(col)`).
- **FK coberta não tem índice próprio.** Se um índice composto ou UNIQUE começa pela FK, o
  índice simples dela é redundante: `db_index=False` na FK (o teste reprova).
- **Mesmo conjunto de colunas = um índice só.** A B-tree serve `ORDER BY ... DESC` pela
  varredura reversa; `-campo` num índice não justifica duplicar um UNIQUE.
- Índice parcial quando a query sempre filtra o mesmo predicado (ex.: `deletado_em IS NULL`).
- GIN/GiST e `btree_gist` só com `RunPython` + checagem de vendor (SQLite não tem).

## 5. Integridade referencial

| Tipo de filho | `on_delete` |
|---|---|
| Histórico clínico, financeiro ou legal (atendimento, prontuário, aceite, movimentos, consumo de pacote) | `PROTECT` / `RESTRICT` |
| Parte da definição do pai (item do pacote → pacote, preço → procedimento, grade → profissional) | `CASCADE` |
| Composição que não pode sumir em silêncio (item do pacote → procedimento) | `PROTECT` |
| Vínculo opcional (autor, promoção aplicada, indicação) | `SET_NULL` + snapshot do nome quando for autoria |

O `on_delete` do Django 5.2 vale na aplicação; as FKs no Postgres são `NO ACTION DEFERRABLE`.

## 6. Lógica no banco (triggers e funções)

- **Só para invariantes que a aplicação sozinha não garante**: imutabilidade de ledger e de
  prova (hoje `fn_/trg_movimento_carteira_imutavel`, `fn_/trg_aceite_termo_imutavel`,
  `fn_/trg_versao_termo_imutavel`, `fn_/trg_prontuario_versao_imutavel`).
- **Regra de negócio fica na aplicação** (services/FSM): stored procedure de negócio não é
  usada. Motivo: testável no SQLite, versionada com o código, sem duas fontes de verdade.
- Trigger de `atualizado_em` e ledger de comissão imutável: **não adotados** (ver remodelagem §7).
- Toda função/trigger/constraint só-Postgres entra em `tests/test_pg_ddl.py`
  (`CONSTRAINTS_SO_PG`, `FUNCOES_SO_PG`, `TRIGGERS_SO_PG`).

## 7. Catálogo e migrations

- **Toda tabela tem `db_table_comment`** (vira `COMMENT ON TABLE` no Postgres; o SQLite
  ignora e o aviso `models.W046` é silenciado só quando o banco é SQLite).
- Rename de índice: `RenameIndex`. Rename de constraint: `RenomearConstraint`
  (`migrations/_operacoes.py`) — no Postgres só troca o nome no catálogo, sem revalidar.
- CHECK novo em tabela com dado de produção: `AdicionarCheckNaoValidado` (mesmo módulo) —
  entra `NOT VALID`, valida num savepoint e, se houver linha fora do domínio, fica
  `NOT VALID` com aviso em stderr. Depois de corrigir os dados:
  `ALTER TABLE <t> VALIDATE CONSTRAINT <nome>;`
- Coluna citada por CHECK/índice renomeada: remove o objeto, renomeia, recria.
- `AlterField` em `cliente/profissional/procedimento.nome` reseta a collation ICU (0038):
  reaplicar via `RunSQL`.
- Upgrade pelo `migrate_atomico` (transação única, `lock_timeout 5s`).

## 8. Varredura de 2026-09-27

### Aplicado na 0048 (nomes)

| Antes | Depois |
|---|---|
| 21 índices com sigla solta (`idx_atend_*`, `idx_notif_*`, `idx_pront_versao`, `idx_anot_atn_criado`, `idx_otp_*`, `idx_auditoria_*`, `idx_anamnese_*`, `idx_movcart_cart`, `idx_movcred_*` (nome da tabela antiga), `idx_pacote_cli_status` (em `compra_pacote`), `idx_comissao_*`, `idx_pushsub_*`…) | `idx_<tabela ou sigla>_…` |
| `chk_atendimento_status_v2`, `chk_retorno_*`, `uniq_retorno_por_origem` | `chk_atendimento_status`, `chk_atendimento_retorno_*`, `uniq_atendimento_retorno_por_origem` |
| `uniq_termo_ativo_*`, `uniq_aceite_cliente_versao` | `uniq_versao_termo_ativo_*`, `uniq_aceite_termo_cliente_versao` |
| `chk_pacote_cliente_status` (em `compra_pacote`) | `chk_compra_pacote_status` |
| `uniq_espera_ativa`, `uq_feriado_data_escopo`, `chk_otp_tentativas_teto` | `uniq_lista_espera_ativa`, `uniq_feriado_data_escopo`, `chk_codigo_otp_tentativas_teto` |
| `uniq_cashback_indicacao_por_atendimento`, `uniq_comissao_ativa_por_atendimento` | `uniq_movimento_carteira_…`, `uniq_movimento_comissao_…` |
| `chk_regra_comissao_percentual_xor_fixo` (a coluna virou `valor` na 0031) | `chk_regra_comissao_percentual_xor_valor` |
| `profissional.min_notice_horas` / `max_advance_dias` | `antecedencia_minima_horas` / `antecedencia_maxima_dias` (+ CHECKs) |
| `assinatura_push.user_id` | `usuario_id` |

### Aplicado na 0049 (integridade e catálogo)

- `COMMENT ON TABLE` em 34/34 tabelas (antes 13, fora do estado do Django).
- CHECK de domínio em 12 choices sem CHECK: `notificacao.tipo/canal/status/resposta`,
  `feriado.escopo`, `codigo_otp.canal/proposito`, `formulario_anamnese.tipo/escopo`,
  `movimento_carteira.tipo/origem`, `movimento_comissao.status`.
- 21 índices redundantes a menos: `idx_preco_lookup` (= `uniq_preco_vigencia`),
  `idx_sessao_pct_cli`, `idx_espera_cliente`, `idx_anamnese_resp_atend` (= índice da FK) e o
  índice simples de 17 FKs cobertas por composto/UNIQUE (a de `assinatura_push` já na 0048).
- `unique_together` → `uniq_habilitacao_profissional_procedimento`,
  `uniq_item_pacote_pacote_procedimento`.
- `item_pacote.procedimento` e `consumo_sessao.compra_pacote`: `CASCADE` → `PROTECT`.
- `log_auditoria.registro_id`: `integer` → `bigint`.
- Postgres: funções de trigger `fn_*`; `chk_formulario_schema_lista` →
  `chk_formulario_anamnese_schema_lista`.
- `CheckConstraint(check=)` → `condition=` nos models (o `check=` sai no Django 6.0).

### Dívida registrada (não aplicada)

| Item | Por que ficou | Quando |
|---|---|---|
| Texto nulo em ~25 colunas livres (`profissional.especialidade`, `cliente.rg/profissao/cep/endereco`, `procedimento.descricao/descricao_longa/imagem_destaque`, `preco.descricao`, `promocao.descricao/imagem_url`, `atendimento.descricao_preco`, `notificacao.mensagem`, 5 textos do `prontuario`, `avaliacao_nps.comentario`, `pacote.descricao`, `log_auditoria.tabela`, `feriado.observacao`, `configuracao.valor/descricao`, `codigo_otp.telefone`, `bloqueio_agenda/excecao_disponibilidade.motivo`) | Há código que grava `None` (ex.: esquecimento LGPD) e filtros `__isnull`; precisa de revisão campo a campo | PR própria |
| `cliente.consent_*` (9 colunas) → `consentimento_*` | Nome em inglês espalhado por 28 arquivos, formulários e templates | PR própria |
| `disponibilidade_profissional`, `bloqueio_agenda`, `excecao_disponibilidade` (nomes de constraint/índice, FK coberta) | A Fase 7 da remodelagem troca as 3 tabelas por `agenda_horario` + `agenda_excecao` | Fase 7 |
| Sufixo `_json` em `schema_json`/`respostas_json` | Cosmético; citado por CHECK jsonb e pelo front | Oportunista |
| `cliente/atendimento.atualizado_em` nulos | Linhas anteriores ao campo | Com a dívida de texto |
