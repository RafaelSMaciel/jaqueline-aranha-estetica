# Padrão SQL e esquema-alvo do banco — Shiva Zen

> **Spec única** (sobrescrever a cada revisão — política do dono: 1 MD por assunto, sem versões
> acumuladas). **Status: fases (a)–(d) IMPLEMENTADAS (migrations `0048_agenda_unificada`,
> `0049_link_acesso`, `0050_consentimento` e `0051_formulario_prontuario`, notas de implementação
> no fim do §4.a–§4.d); (e)–(f), N1–N2 e L1–L3 seguem em DESIGN.** Base verificada:
> `front-fundacao` = `main` @ `1462eff`, migrations até `0047`, produção na `0026`, Django 5.2.1, PostgreSQL 18 (Railway, com
> `btree_gist`, `pg_trgm`, `unaccent`, ICU). Tudo aqui entra como migrations **`0048`+** — as
> `0027`–`0047` não são editadas. Histórico das 0027–0047: [`remodelagem-banco-v2.md`](remodelagem-banco-v2.md)
> (a "Fase 7 — agenda 3→2" de lá é **substituída** pela reestruturação (a) deste documento).

## Sumário

0. [Resumo executivo](#0-resumo-executivo)
1. [Regras do padrão](#1-regras-do-padrão)
2. [Esquema-alvo (DDL)](#2-esquema-alvo-ddl) — 36 tabelas + revisão de índices
3. [Mapa de renomeação](#3-mapa-de-renomeação) — tabelas, colunas, `Model.atributo → db_column`, constraints/índices, objetos automáticos, mecânica no Django
4. [Reestruturações (a)–(f)](#4-reestruturações-af)
5. [Lógica no banco](#5-lógica-no-banco) — triggers, views, funções, procedures, middleware, paridade, SQLite × Postgres
6. [Plano de migrations](#6-plano-de-migrations-0048)
7. [Riscos, decisões em aberto e o que não fazer](#7-riscos-decisões-em-aberto-e-o-que-não-fazer)

---

## 0. Resumo executivo

| Item | Hoje (0047) | Alvo |
|---|---|---|
| Tabelas de domínio | 34 | **36** (−4 da agenda; +`agenda_horario`, `agenda_excecao`, `link_acesso`, `consentimento`, `compra_pacote_item`, `historico_alteracao`) |
| Tabelas renomeadas | — | 3 (`item_pacote→pacote_item`, `formulario_anamnese→formulario`, `resposta_anamnese→resposta_formulario`) |
| Colunas | 303 nas 34 tabelas (inventário do estado Django) | **64 renomeadas** em 25 tabelas, **19 removidas** (tokens em texto puro, `consent_*`, `aceita_comunicacao`, status da comissão), `criado_em`/`atualizado_em` completados |
| Objetos de banco | 98 com nome explícito (40 índices + 51 constraints do `Meta` + 7 só-PG) e 170 com nome automático (34 PK, 51 FK, 46 índices de FK, 15 UNIQUE de campo, 2 `unique_together`, 13 `_like`, 5 `db_index`, 4 CHECK de `Positive*Field`) + 34 sequências | **todos** no padrão: 36 `pk_`, 65 `fk_` (com `ON DELETE` real), 34 `uq_`, 91 `ck_`, 68 `ix_`, 3 `ex_` |
| Índices | — | **82 deixam de existir** (13 `_like`, 38 automáticos de FK cobertos por outro índice ou trocados por parcial, 25 do `Meta`, 5 `db_index`, 1 GIN sem uso), 23 renomeados, **45 criados** (30 em tabelas existentes — compostos/parciais/trigram — e 15 nas novas) |
| Tokens em texto puro | 5 colunas (+1 legado) | 0 — `link_acesso.token_hash` (sha256) |
| Lógica no banco | 4 triggers de imutabilidade, 1 EXCLUDE | **45 triggers** (11 funções de trigger), **9 views**, **11 funções** (3 utilitárias + 8 de leitura com paridade), **4 procedures** (fonte única), 3 EXCLUDE |

As 5 decisões/riscos que precisam de revisão estão no [§7.1](#71-as-5-decisões-que-precisam-da-revisão-do-dono).

---

## 1. Regras do padrão

### 1.1 Princípios

1. **Nome só no banco.** O Python continua com os mesmos atributos (`Cliente.ativo`,
   `Atendimento.reagendado_de`...). O padrão é aplicado por `Meta.db_table`, `Field(db_column=...)`
   e nomes explícitos em `Meta.indexes`/`Meta.constraints`. O que o Django nomeia sozinho (PK, FK,
   UNIQUE de `OneToOneField`, sequência) é renomeado por migration só-Postgres com introspecção
   do catálogo (§3.6).
2. **ASCII snake_case PT-BR**, sem acento, `[a-z0-9_]`, singular, **≤ 63 caracteres** (limite
   `NAMEDATALEN-1`; o PG trunca calado — hoje já existe
   `lista_espera_profissional_desejad_c9c2ce7d_fk_profissio`, truncado).
3. **Prefixo diz a classe do valor, sufixo diz o papel físico.** Prefixos: `eh_`, `tem_`, `data_`,
   `hora_`, `valor_`, `qtd_`, `percentual_`, `url_`. Sufixos: `_id` (FK), `_em` (instante),
   `_hash`, `_ref` (id externo), unidade (`_minutos`, `_horas`, `_dias`, `_meses`).
4. **Todo objeto explícito.** Nenhum índice/UNIQUE vem de `unique=True`/`db_index=True`/
   `unique_together` em campo (exceto a PK e o UNIQUE implícito de `OneToOneField`): tudo em
   `Meta`, com nome do padrão. Motivo: o Django cria `_like` (`varchar_pattern_ops`) inútil para
   todo `CharField` único/indexado e nomeia com hash.
5. **Conformidade testada.** `tests/test_nomes_padrao.py` (CI Postgres) lê `pg_catalog` e reprova
   qualquer objeto fora do padrão, FK sem índice, FK cuja ação `ON DELETE` diverge do `on_delete`
   do model, tabela sem `COMMENT`, tabela com `atualizado_em` sem trigger e índice `_like` (§3.6.4).

### 1.2 Tabelas e colunas

| Elemento | Regra | Exemplos |
|---|---|---|
| Tabela (conceito autônomo) | substantivo singular na ordem natural `substantivo[_complemento]`; sem prefixo de app | `atendimento`, `cliente`, `aceite_termo`, `regra_comissao`, `movimento_carteira`, `versao_termo` |
| Tabela (parte de um agregado) | prefixo do agregado: `<pai>_<parte>` (agrupa no `\dt pai*`) | `agenda_horario`, `agenda_excecao`, `pacote_item`, `compra_pacote_item`, `prontuario_versao` |
| Associativa N:N | substantivo semântico quando existe; senão `<tabela_a>_<tabela_b>` na ordem de dependência | `habilitacao` (profissional × procedimento) |
| PK | `id bigint GENERATED BY DEFAULT AS IDENTITY` (`BigAutoField`) | — |
| FK | `<tabela_referenciada>_id` | `cliente_id`, `procedimento_id` |
| FK com papel / 2+ FKs p/ a mesma tabela | `<tabela_referenciada>_<papel>_id` (papel = o que a linha referenciada é para esta) | `atendimento_origem_id`, `atendimento_reagendado_id`, `cliente_indicador_id`, `profissional_desejado_id`, `movimento_comissao_origem_id` |
| FK de autoria (quem registrou) | sempre `usuario_autor_id`; nunca `user_id`/`usuario_id` p/ autoria | `anotacao_sessao.usuario_autor_id` |
| Snapshot de atributo de FK | `<fk sem _id>_<atributo>` — congela o valor no momento do registro | `usuario_autor_nome`, `pacote_nome`, `procedimento_nome` |
| `_id` | **reservado a FK real**. Id de sistema externo: `_ref`. Chave polimórfica: `registro_pk` | `gcal_calendario_ref`, `agenda_excecao.origem_ref`, `log_auditoria.registro_pk` |
| Data (`date`) | `data_<qualificador>` | `data_nascimento`, `data_expiracao`, `data_inicio_vigencia`, `data_fim_recorrencia` |
| Instante (`timestamptz`) | `<particípio>_em`, **masculino** (uniforme) | `criado_em`, `atualizado_em`, `deletado_em`, `enviado_em`, `respondido_em`, `expira_em`, `usado_em`, `revogado_em`, `registrado_em`, `pago_em` |
| Exceção aprovada | `data_hora_inicio` / `data_hora_fim` (intervalo de atendimento e de bloqueio) | `atendimento`, `agenda_excecao` |
| Hora do dia (`time`, hora local) | `hora_<qualificador>` | `hora_inicio`, `hora_fim` |
| Booleano | `eh_<adjetivo/particípio>` ou `tem_<substantivo>`; `NOT NULL` com default; forma masculina em qualquer tabela | `eh_ativo`, `eh_retorno`, `eh_publicacao_aprovada`, `tem_retorno_obrigatorio` |
| Dinheiro | `valor_<qualificador>` `numeric(10,2)` (agregado de view: `numeric(12,2)`). `valor` puro só quando a linha **é** o valor (`preco`, `movimento_*`) | `valor_cobrado`, `valor_saldo`, `valor_total`, `valor_fixo` |
| Contagem | `qtd_<coisa>` (`smallint`/`integer`) | `qtd_sessoes`, `qtd_tentativas`, `qtd_faltas_consecutivas` |
| Percentual | `percentual_<qualificador>` `numeric(5,2)` + CHECK 0–100; `percentual` puro na `regra_comissao` (é o assunto da linha) | `percentual_desconto` |
| Medida com unidade | `<grandeza>_<unidade>` | `duracao_minutos`, `intervalo_minutos`, `antecedencia_minima_horas`, `validade_meses` |
| URL | `url_<qualificador>` | `url_foto`, `url_imagem_destaque`, `url_videoconferencia` |
| Status / tipo / canal (enum) | `varchar(n)` + `ck_<tabela>_<coluna>` com `IN (...)`; valores MAIÚSCULOS ASCII. **Sem ENUM nativo** (trocar valor de ENUM exige `ALTER TYPE` e acopla o tipo ao Django; CHECK troca em migration comum). Enum opcional: `NULL` = não se aplica | `status`, `tipo`, `canal`, `origem` |
| JSON | `jsonb` + `ck_<tabela>_<coluna>_objeto` / `_lista` (`jsonb_typeof`); **sem** sufixo `_json` | `respostas`, `perguntas`, `detalhes`, `dados` |
| Hash de segredo | `<x>_hash` `char(64)` (sha256/HMAC hex) + `ck_<tabela>_<x>_hash_formato` (`^[0-9a-f]{64}$`). Exceção: `usuario.senha_hash varchar(255)` (formato `pbkdf2_sha256$…` do Django) | `token_hash`, `codigo_hash`, `conteudo_hash` |
| IP de origem | `ip_origem inet` (inválido vira `NULL`, nunca inventado) | — |
| Texto | `NOT NULL DEFAULT ''`. Exceções: (i) colunas sob UNIQUE parcial (`email`, `cpf`, `telefone`) — ausente = `NULL` + CHECK `<> ''`; (ii) dado pessoal opcional do titular (`rg`, `cep`, `endereco`, `profissao`) — `NULL` = não informado/apagado pela anonimização | — |
| Termo técnico de protocolo | mantém o nome do protocolo | `user_agent`, `endpoint`, `chave_p256dh`, `slug` |
| Auditoria de linha | `criado_em NOT NULL DEFAULT now()` em **toda** tabela. `atualizado_em NOT NULL DEFAULT now()` + `tg_<t>_atualizado_em` em toda tabela com UPDATE de negócio. Tabela append-only/imutável não tem `atualizado_em`. Instante de negócio diferente da inserção ganha coluna própria (`registrado_em`, `respondido_em`) | exceções efêmeras: `codigo_otp`, `link_acesso` (têm `usado_em`/`revogado_em`) |
| Soft delete | só `cliente.deletado_em` | — |
| Defaults | todo default literal ou `now()` vira também `db_default` (Django ≥ 5.0) — procedures/triggers inserem por SQL | — |

### 1.3 Objetos

| Objeto | Padrão | Exemplo |
|---|---|---|
| PK | `pk_<tabela>` | `pk_atendimento` |
| FK | `fk_<tabela>_<coluna sem _id>`; composta: `fk_<tabela>_<1ª coluna sem _id>_<qualificador>` | `fk_atendimento_atendimento_origem`, `fk_consumo_sessao_compra_pacote_item_compra` |
| Ação da FK | espelha o `on_delete`: `CASCADE→ON DELETE CASCADE`, `SET_NULL→ON DELETE SET NULL`, `PROTECT/RESTRICT→NO ACTION`; sempre `DEFERRABLE INITIALLY DEFERRED` (como o Django cria) | — |
| UNIQUE (constraint ou índice único parcial/expressão) | `uq_<tabela>_<colunas ou regra>` | `uq_cliente_telefone_ativo`, `uq_movimento_comissao_credito_por_atendimento` |
| CHECK | `ck_<tabela>_<regra>` | `ck_atendimento_fim_apos_inicio`, `ck_promocao_percentual_desconto_0_100` |
| Índice | `ix_<tabela>_<colunas>`; parcial `…_parcial`; GIN trigram `…_trgm`; de expressão: nome da expressão | `ix_atendimento_profissional_data_hora_inicio`, `ix_cliente_deletado_em_parcial`, `ix_cliente_nome_trgm` |
| EXCLUDE | `ex_<tabela>_<regra>` | `ex_atendimento_sobreposicao` |
| Trigger | `tg_<tabela>_<ação>` — ações padronizadas: `atualizado_em`, `imutavel`, `historico`, `validar` | `tg_prontuario_historico` |
| Função | `fn_<nome>`; função de trigger genérica `fn_<ação>`, específica `fn_<tabela>_<ação>` | `fn_definir_atualizado_em`, `fn_aceite_termo_imutavel` |
| Procedure | `sp_<verbo>_<objeto>` | `sp_anonimizar_cliente` |
| View | `vw_<assunto>` | `vw_agenda_dia` |
| Sequência | `<tabela>_id_seq` (padrão do PG; renomeada junto com a tabela) | `carteira_id_seq` (hoje `credito_cliente_id_seq`) |
| Collation | `pt_br` (ICU determinística, 0038) | — |
| Configuração de sessão (GUC) | `app.<nome>` | `app.usuario_id`, `app.ip_origem`, `app.origem` |

### 1.4 Nomes longos (> 63)

Aplicar em ordem até caber: (1) tirar `_id`; (2) se a FK repete o nome da tabela dona, usar só o
papel (`movimento_comissao.movimento_comissao_origem_id` → `…_origem`); (3) trocar a lista de
colunas pelo **nome da regra** (`uq_movimento_comissao_estorno_por_origem`); (4) abreviações
fixas: `atendimento→atend`, `profissional→prof`, `procedimento→proc`, `movimento→mov`. No
esquema-alvo nenhum nome precisou do passo (4): o maior tem 57 caracteres
(`ck_movimento_carteira_valor_saldo_resultante_nao_negativo`; conferido por script sobre os 353
identificadores deste documento). O teste de conformidade reprova nome > 63.

### 1.5 Fora do padrão (terceiros) — listadas, **não renomeadas**

`auth_group`, `auth_group_permissions`, `auth_permission`, `django_admin_log`,
`django_content_type`, `django_migrations`, `django_session`, `axes_accessattempt`,
`axes_accessattemptexpiration`, `axes_accessfailurelog`, `axes_accesslog`,
`otp_static_staticdevice`, `otp_static_statictoken`, `otp_totp_totpdevice`.

Por quê: os nomes/colunas pertencem às migrations dos pacotes (`django.contrib.*`, `django-axes`,
`django-otp`); renomear exigiria forkar os models (não há `db_table` configurável) e quebraria as
próximas migrations deles; o próprio Django referencia `django_migrations`/`django_content_type`/
`django_session` por nome. Elas referenciam `usuario(id)` (FK de `django_admin_log.user_id`,
`otp_*.user_id`, `axes`), o que não muda. O teste de conformidade as ignora por allowlist; a
retenção delas continua no Python (`clearsessions`, `AxesProxyHandler.reset_logs`).

---

## 2. Esquema-alvo (DDL)

Leitura:

- É o **DDL canônico do estado final** (para revisão e para o teste de conformidade), não o script
  de migração — a implementação é `models` + migrations do §6.
- `-- [PG]` = objeto só-Postgres (criado por `RunPython` com checagem de vendor, padrão já usado
  nas 0034–0047). O resto existe também no SQLite dos testes (vem do estado do Django).
- `DEFAULT` = `db_default` no model (além do `default=` Python). FKs: `DEFERRABLE INITIALLY
  DEFERRED` com a ação do §1.3. `COMMENT ON` é implementado por `Meta.db_table_comment` e
  `Field(db_comment=...)` (Django ≥ 4.2; o SQLite ignora) — o comentário vive no estado e não se
  perde num `AlterField` futuro (os 13 `COMMENT ON TABLE` da 0038 são substituídos).
- Volume de referência (1 clínica, ≤ 3 profissionais, ~20 atendimentos/dia): `atendimento` ~6 mil
  linhas/ano (120 mil em 20 anos), `notificacao` ~20 mil/ano, `log_auditoria` ~50 mil/ano. Índice
  só onde há query real **ou** FK (checagem de `ON DELETE`); nada de índice em booleano.

### 2.0 Pré-requisitos

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;   -- 0035 (EXCLUDE com =)
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- 0057 (busca de nome)
CREATE EXTENSION IF NOT EXISTS unaccent;     -- 0057 (busca sem acento)
CREATE COLLATION IF NOT EXISTS pt_br (provider = icu, locale = 'pt-BR');  -- 0038, determinística
-- fn_fuso_clinica(), fn_data_local(), fn_sem_acento(): §5.1
```

### 2.1 Acesso e auditoria

```sql
CREATE TABLE usuario (
    id               bigint       GENERATED BY DEFAULT AS IDENTITY,
    papel            varchar(20)  NOT NULL DEFAULT 'PROFISSIONAL',
    profissional_id  bigint       NULL,
    nome             varchar(100) NOT NULL,
    email            varchar(100) NOT NULL,
    senha_hash       varchar(255) NOT NULL,
    eh_ativo         boolean      NOT NULL DEFAULT true,
    criado_em        timestamptz  NOT NULL DEFAULT now(),
    atualizado_em    timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT pk_usuario PRIMARY KEY (id),
    CONSTRAINT uq_usuario_email UNIQUE (email),
    CONSTRAINT uq_usuario_profissional UNIQUE (profissional_id),
    CONSTRAINT fk_usuario_profissional FOREIGN KEY (profissional_id) REFERENCES profissional (id)
        ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_usuario_papel CHECK (papel IN ('ADMIN', 'PROFISSIONAL', 'RECEPCAO'))
);
COMMENT ON TABLE usuario IS 'Equipe (login por e-mail). papel ADMIN/PROFISSIONAL/RECEPCAO; is_staff = ADMIN. Nunca excluir: desativar (eh_ativo=false).';
COMMENT ON COLUMN usuario.senha_hash IS 'Hash do Django (pbkdf2_sha256$...). Excecao a regra char(64) de *_hash.';
COMMENT ON COLUMN usuario.profissional_id IS 'Profissional que este login representa (1:1 opcional).';

CREATE TABLE assinatura_push (
    id               bigint       GENERATED BY DEFAULT AS IDENTITY,
    usuario_id       bigint       NOT NULL,
    endpoint         varchar(600) NOT NULL,
    chave_p256dh     varchar(140) NOT NULL,
    chave_auth       varchar(80)  NOT NULL,
    user_agent       varchar(300) NOT NULL DEFAULT '',
    eh_ativo         boolean      NOT NULL DEFAULT true,
    ultima_falha_em  timestamptz  NULL,
    criado_em        timestamptz  NOT NULL DEFAULT now(),
    atualizado_em    timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT pk_assinatura_push PRIMARY KEY (id),
    CONSTRAINT uq_assinatura_push_endpoint UNIQUE (endpoint),
    CONSTRAINT fk_assinatura_push_usuario FOREIGN KEY (usuario_id) REFERENCES usuario (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX ix_assinatura_push_usuario ON assinatura_push (usuario_id);
COMMENT ON TABLE assinatura_push IS 'Inscricao Web Push (VAPID) de um usuario da equipe.';
COMMENT ON COLUMN assinatura_push.chave_p256dh IS 'Chave publica do navegador (PushManager.subscribe).';
COMMENT ON COLUMN assinatura_push.chave_auth IS 'Segredo de autenticacao do navegador (PushManager.subscribe).';

CREATE TABLE log_auditoria (
    id                  bigint       GENERATED BY DEFAULT AS IDENTITY,
    usuario_autor_id    bigint       NULL,
    usuario_autor_nome  varchar(255) NOT NULL DEFAULT '',
    acao                text         NOT NULL,
    tabela              varchar(100) NOT NULL DEFAULT '',
    registro_pk         bigint       NULL,
    detalhes            jsonb        NULL,
    ip_origem           inet         NULL,
    criado_em           timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT pk_log_auditoria PRIMARY KEY (id),
    CONSTRAINT fk_log_auditoria_usuario_autor FOREIGN KEY (usuario_autor_id) REFERENCES usuario (id)
        ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_log_auditoria_detalhes_objeto                                   -- [PG]
        CHECK (detalhes IS NULL OR jsonb_typeof(detalhes) = 'object')
);
CREATE INDEX ix_log_auditoria_criado_em ON log_auditoria (criado_em DESC);
CREATE INDEX ix_log_auditoria_tabela_registro ON log_auditoria (tabela, registro_pk);
COMMENT ON TABLE log_auditoria IS 'Trilha de ACOES da aplicacao (LGPD art. 37): autor + snapshot "Nome <email>", acao, alvo, IP. Retencao 5 anos. A trilha de DADOS (antes/depois) fica em historico_alteracao.';
COMMENT ON COLUMN log_auditoria.registro_pk IS 'PK do registro afetado na tabela indicada em "tabela" (referencia polimorfica, sem FK).';
COMMENT ON COLUMN log_auditoria.usuario_autor_nome IS 'Snapshot "Nome <email>" do autor no momento da acao.';

CREATE TABLE historico_alteracao (
    id                 bigint       GENERATED BY DEFAULT AS IDENTITY,
    tabela             varchar(63)  NOT NULL,
    registro_pk        bigint       NOT NULL,
    operacao           varchar(6)   NOT NULL,
    colunas_alteradas  jsonb        NOT NULL DEFAULT '[]',
    dados_antes        jsonb        NULL,
    dados_depois       jsonb        NULL,
    usuario_autor_id   bigint       NULL,
    ip_origem          inet         NULL,
    origem             varchar(60)  NOT NULL DEFAULT '',
    transacao_ref      bigint       NOT NULL,
    criado_em          timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT pk_historico_alteracao PRIMARY KEY (id),
    CONSTRAINT fk_historico_alteracao_usuario_autor FOREIGN KEY (usuario_autor_id) REFERENCES usuario (id)
        ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_historico_alteracao_operacao CHECK (operacao IN ('INSERT', 'UPDATE', 'DELETE')),
    CONSTRAINT ck_historico_alteracao_dados_coerentes CHECK (
        (operacao = 'INSERT' AND dados_antes IS NULL AND dados_depois IS NOT NULL)
     OR (operacao = 'UPDATE' AND dados_antes IS NOT NULL AND dados_depois IS NOT NULL)
     OR (operacao = 'DELETE' AND dados_antes IS NOT NULL AND dados_depois IS NULL)),
    CONSTRAINT ck_historico_alteracao_colunas_alteradas_lista                       -- [PG]
        CHECK (jsonb_typeof(colunas_alteradas) = 'array')
);
CREATE INDEX ix_historico_alteracao_tabela_registro ON historico_alteracao (tabela, registro_pk, criado_em DESC);
CREATE INDEX ix_historico_alteracao_criado_em ON historico_alteracao (criado_em);
CREATE INDEX ix_historico_alteracao_usuario_autor_parcial ON historico_alteracao (usuario_autor_id, criado_em DESC)
    WHERE usuario_autor_id IS NOT NULL;
COMMENT ON TABLE historico_alteracao IS 'Trilha de dados gravada por trigger (fn_registrar_historico) nas tabelas sensiveis: antes/depois so das colunas alteradas. Append-only (tg_historico_alteracao_imutavel). Retencao: 20 anos p/ prontuario, 5 anos p/ o resto.';
COMMENT ON COLUMN historico_alteracao.origem IS 'current_setting(app.origem): web, cron:<job>, celery:<task>, migracao; vazio = SQL manual.';
COMMENT ON COLUMN historico_alteracao.transacao_ref IS 'pg_current_xact_id() — agrupa as linhas de uma mesma transacao/request.';
```

### 2.2 Pessoas

```sql
CREATE TABLE cliente (
    id                       bigint       GENERATED BY DEFAULT AS IDENTITY,
    nome                     varchar(150) COLLATE pt_br NOT NULL,
    data_nascimento          date         NULL,
    cpf                      varchar(11)  NULL,
    rg                       varchar(20)  NULL,
    profissao                varchar(100) NULL,
    email                    varchar(254) NULL,
    telefone                 varchar(20)  NULL,
    cep                      varchar(10)  NULL,
    endereco                 varchar(255) NULL,
    eh_ativo                 boolean      NOT NULL DEFAULT true,
    qtd_faltas_consecutivas  smallint     NOT NULL DEFAULT 0,
    eh_bloqueado_online      boolean      NOT NULL DEFAULT false,
    url_foto                 varchar(600) NOT NULL DEFAULT '',
    cliente_indicador_id     bigint       NULL,
    codigo_indicacao         varchar(12)  NULL,
    criado_em                timestamptz  NOT NULL DEFAULT now(),
    atualizado_em            timestamptz  NOT NULL DEFAULT now(),
    deletado_em              timestamptz  NULL,
    CONSTRAINT pk_cliente PRIMARY KEY (id),
    CONSTRAINT uq_cliente_codigo_indicacao UNIQUE (codigo_indicacao),
    CONSTRAINT fk_cliente_cliente_indicador FOREIGN KEY (cliente_indicador_id) REFERENCES cliente (id)
        ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_cliente_nao_indica_a_si CHECK (cliente_indicador_id IS NULL OR cliente_indicador_id <> id),
    CONSTRAINT ck_cliente_qtd_faltas_nao_negativa CHECK (qtd_faltas_consecutivas >= 0),
    CONSTRAINT ck_cliente_email_nao_vazio CHECK (email IS NULL OR email <> ''),
    CONSTRAINT ck_cliente_telefone_formato CHECK (telefone IS NULL OR telefone ~ '^[0-9]{10,11}$'),  -- [PG]
    CONSTRAINT ck_cliente_cpf_formato CHECK (cpf IS NULL OR cpf ~ '^[0-9]{11}$')                     -- [PG]
);
CREATE UNIQUE INDEX uq_cliente_telefone_ativo ON cliente (telefone) WHERE deletado_em IS NULL;
CREATE UNIQUE INDEX uq_cliente_cpf_ativo ON cliente (cpf) WHERE deletado_em IS NULL;
CREATE UNIQUE INDEX uq_cliente_email_ativo ON cliente (upper(email::text)) WHERE deletado_em IS NULL;
CREATE INDEX ix_cliente_nome ON cliente (nome);
CREATE INDEX ix_cliente_nome_trgm ON cliente USING gin (fn_sem_acento(nome) gin_trgm_ops);        -- [PG]
CREATE INDEX ix_cliente_cliente_indicador_parcial ON cliente (cliente_indicador_id) WHERE cliente_indicador_id IS NOT NULL;
CREATE INDEX ix_cliente_deletado_em_parcial ON cliente (deletado_em) WHERE deletado_em IS NOT NULL;
COMMENT ON TABLE cliente IS 'Cliente da clinica. Telefone (so digitos, 10-11) e a chave natural do agendamento publico (uq_cliente_telefone_ativo). Soft delete em deletado_em; anonimizacao LGPD troca nome por [ANONIMIZADO-<id>] (sp_anonimizar_cliente). Consentimentos em consentimento.';
COMMENT ON COLUMN cliente.nome IS 'COLLATE pt_br (ICU) — ordenacao correta de acentos.';
COMMENT ON COLUMN cliente.qtd_faltas_consecutivas IS 'Zera ao REALIZADO; ao atingir MAX_FALTAS_BLOQUEIO liga eh_bloqueado_online.';
COMMENT ON COLUMN cliente.cliente_indicador_id IS 'Quem indicou este cliente (cashback de indicacao em movimento_carteira).';
COMMENT ON COLUMN cliente.codigo_indicacao IS 'Codigo curto compartilhavel (hex maiusculo) gerado no save.';

CREATE TABLE profissional (
    id                         bigint       GENERATED BY DEFAULT AS IDENTITY,
    nome                       varchar(100) COLLATE pt_br NOT NULL,
    slug                       varchar(140) NULL,
    especialidade              text         NOT NULL DEFAULT '',
    url_foto                   varchar(600) NOT NULL DEFAULT '',
    biografia                  text         NOT NULL DEFAULT '',
    eh_ativo                   boolean      NOT NULL DEFAULT true,
    antecedencia_minima_horas  smallint     NOT NULL DEFAULT 2,
    antecedencia_maxima_dias   smallint     NOT NULL DEFAULT 60,
    gcal_token_renovacao       text         NOT NULL DEFAULT '',
    gcal_calendario_ref        varchar(200) NOT NULL DEFAULT 'primary',
    gcal_token_sincronizacao   text         NOT NULL DEFAULT '',
    gcal_sincronizado_em       timestamptz  NULL,
    criado_em                  timestamptz  NOT NULL DEFAULT now(),
    atualizado_em              timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT pk_profissional PRIMARY KEY (id),
    CONSTRAINT uq_profissional_slug UNIQUE (slug),
    CONSTRAINT ck_profissional_antecedencia_minima CHECK (antecedencia_minima_horas BETWEEN 0 AND 720),
    CONSTRAINT ck_profissional_antecedencia_maxima CHECK (antecedencia_maxima_dias BETWEEN 1 AND 365)
);
COMMENT ON TABLE profissional IS 'Quem atende. Grade semanal em agenda_horario, folgas/bloqueios em agenda_excecao. Feed ICS por link_acesso (finalidade FEED_ICS).';
COMMENT ON COLUMN profissional.antecedencia_minima_horas IS 'Horas minimas entre agora e o inicio de um horario oferecido online.';
COMMENT ON COLUMN profissional.antecedencia_maxima_dias IS 'Dias maximos a frente oferecidos online.';
COMMENT ON COLUMN profissional.gcal_token_renovacao IS 'Refresh token OAuth do Google Calendar — segredo reversivel (nao da p/ hashear); ver §7 (cifrar).';
COMMENT ON COLUMN profissional.gcal_calendario_ref IS 'Id do calendario no Google (externo).';
```

### 2.3 Catálogo

```sql
CREATE TABLE procedimento (
    id                       bigint       GENERATED BY DEFAULT AS IDENTITY,
    nome                     varchar(100) COLLATE pt_br NOT NULL,
    slug                     varchar(140) NULL,
    descricao                text         NOT NULL DEFAULT '',
    descricao_longa          text         NOT NULL DEFAULT '',
    duracao_minutos          smallint     NOT NULL,
    intervalo_minutos        smallint     NOT NULL DEFAULT 0,
    categoria                varchar(20)  NOT NULL DEFAULT 'OUTRO',
    modalidade               varchar(12)  NOT NULL DEFAULT 'PRESENCIAL',
    url_videoconferencia     varchar(500) NOT NULL DEFAULT '',
    url_imagem_destaque      varchar(500) NOT NULL DEFAULT '',
    eh_ativo                 boolean      NOT NULL DEFAULT true,
    tem_retorno_obrigatorio  boolean      NOT NULL DEFAULT false,
    retorno_minimo_dias      smallint     NULL,
    retorno_maximo_dias      smallint     NULL,
    duracao_retorno_minutos  smallint     NOT NULL DEFAULT 30,
    criado_em                timestamptz  NOT NULL DEFAULT now(),
    atualizado_em            timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT pk_procedimento PRIMARY KEY (id),
    CONSTRAINT uq_procedimento_slug UNIQUE (slug),
    CONSTRAINT ck_procedimento_duracao_positiva CHECK (duracao_minutos > 0),
    CONSTRAINT ck_procedimento_intervalo_0_120 CHECK (intervalo_minutos BETWEEN 0 AND 120),
    CONSTRAINT ck_procedimento_categoria CHECK (categoria IN ('FACIAL', 'CORPORAL', 'CAPILAR', 'OUTRO')),
    CONSTRAINT ck_procedimento_modalidade CHECK (modalidade IN ('PRESENCIAL', 'ONLINE', 'HIBRIDO')),
    CONSTRAINT ck_procedimento_retorno_coerente CHECK (NOT tem_retorno_obrigatorio
        OR (retorno_minimo_dias IS NOT NULL AND retorno_maximo_dias IS NOT NULL)),
    CONSTRAINT ck_procedimento_retorno_minimo_dias_nao_negativo CHECK (retorno_minimo_dias >= 0),
    CONSTRAINT ck_procedimento_retorno_maximo_dias_nao_negativo CHECK (retorno_maximo_dias >= 0),
    CONSTRAINT ck_procedimento_duracao_retorno_nao_negativa CHECK (duracao_retorno_minutos >= 0)
);
COMMENT ON TABLE procedimento IS 'Catalogo de servicos. tem_retorno_obrigatorio dispara o retorno gratuito (RetornoService).';
COMMENT ON COLUMN procedimento.intervalo_minutos IS 'Folga apos o procedimento (limpeza/preparo) que tambem ocupa a agenda (0-120).';
COMMENT ON COLUMN procedimento.duracao_retorno_minutos IS '0 = usa a duracao padrao do retorno (30 min).';

CREATE TABLE habilitacao (
    id               bigint      GENERATED BY DEFAULT AS IDENTITY,
    profissional_id  bigint      NOT NULL,
    procedimento_id  bigint      NOT NULL,
    criado_em        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_habilitacao PRIMARY KEY (id),
    CONSTRAINT uq_habilitacao_profissional_procedimento UNIQUE (profissional_id, procedimento_id),
    CONSTRAINT fk_habilitacao_profissional FOREIGN KEY (profissional_id) REFERENCES profissional (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_habilitacao_procedimento FOREIGN KEY (procedimento_id) REFERENCES procedimento (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX ix_habilitacao_procedimento ON habilitacao (procedimento_id);
COMMENT ON TABLE habilitacao IS 'Profissional x procedimento que ele realiza. Sem nenhuma linha p/ um procedimento, qualquer profissional ativo atende (services/disponibilidade.profissionais_para).';

CREATE TABLE preco (
    id                    bigint        GENERATED BY DEFAULT AS IDENTITY,
    procedimento_id       bigint        NOT NULL,
    profissional_id       bigint        NULL,
    valor                 numeric(10,2) NOT NULL,
    descricao             text          NOT NULL DEFAULT '',
    data_inicio_vigencia  date          NOT NULL,
    criado_em             timestamptz   NOT NULL DEFAULT now(),
    atualizado_em         timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT pk_preco PRIMARY KEY (id),
    CONSTRAINT uq_preco_vigencia UNIQUE (procedimento_id, profissional_id, data_inicio_vigencia),
    CONSTRAINT fk_preco_procedimento FOREIGN KEY (procedimento_id) REFERENCES procedimento (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_preco_profissional FOREIGN KEY (profissional_id) REFERENCES profissional (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_preco_valor_nao_negativo CHECK (valor >= 0)
);
CREATE UNIQUE INDEX uq_preco_base_vigencia ON preco (procedimento_id, data_inicio_vigencia) WHERE profissional_id IS NULL;
CREATE INDEX ix_preco_profissional_parcial ON preco (profissional_id) WHERE profissional_id IS NOT NULL;
COMMENT ON TABLE preco IS 'Preco versionado por data_inicio_vigencia: base (profissional NULL) ou por profissional. Vale a vigencia mais recente <= data do atendimento (utils/precos).';

CREATE TABLE promocao (
    id                   bigint        GENERATED BY DEFAULT AS IDENTITY,
    procedimento_id      bigint        NULL,
    nome                 varchar(150)  NOT NULL,
    descricao            text          NOT NULL DEFAULT '',
    percentual_desconto  numeric(5,2)  NOT NULL DEFAULT 0,
    valor_promocional    numeric(10,2) NULL,
    data_inicio          date          NOT NULL,
    data_fim             date          NOT NULL,
    eh_ativo             boolean       NOT NULL DEFAULT true,
    url_imagem           text          NOT NULL DEFAULT '',
    criado_em            timestamptz   NOT NULL DEFAULT now(),
    atualizado_em        timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT pk_promocao PRIMARY KEY (id),
    CONSTRAINT fk_promocao_procedimento FOREIGN KEY (procedimento_id) REFERENCES procedimento (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_promocao_periodo CHECK (data_fim >= data_inicio),
    CONSTRAINT ck_promocao_percentual_desconto_0_100 CHECK (percentual_desconto BETWEEN 0 AND 100),
    CONSTRAINT ck_promocao_valor_promocional_nao_negativo CHECK (valor_promocional IS NULL OR valor_promocional >= 0),
    CONSTRAINT ck_promocao_desconto_xor_valor CHECK (NOT (percentual_desconto > 0 AND valor_promocional IS NOT NULL)),
    CONSTRAINT ck_promocao_geral_so_percentual CHECK (procedimento_id IS NOT NULL OR valor_promocional IS NULL)
);
CREATE INDEX ix_promocao_procedimento_parcial ON promocao (procedimento_id) WHERE procedimento_id IS NOT NULL;
COMMENT ON TABLE promocao IS 'Desconto percentual XOR preco fixo num periodo. procedimento NULL = promocao geral (so percentual).';
COMMENT ON COLUMN promocao.url_imagem IS 'text (legado sem limite de tamanho): excecao ao varchar das url_*.';
```

### 2.4 Agenda

```sql
CREATE TABLE agenda_horario (
    id               bigint      GENERATED BY DEFAULT AS IDENTITY,
    profissional_id  bigint      NOT NULL,
    dia_semana       smallint    NOT NULL,
    hora_inicio      time        NOT NULL,
    hora_fim         time        NOT NULL,
    criado_em        timestamptz NOT NULL DEFAULT now(),
    atualizado_em    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_agenda_horario PRIMARY KEY (id),
    CONSTRAINT fk_agenda_horario_profissional FOREIGN KEY (profissional_id) REFERENCES profissional (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_agenda_horario_dia_semana CHECK (dia_semana BETWEEN 1 AND 7),
    CONSTRAINT ck_agenda_horario_fim_apos_inicio CHECK (hora_fim > hora_inicio),
    CONSTRAINT ex_agenda_horario_sobreposicao EXCLUDE USING gist (                    -- [PG]
        profissional_id WITH =,
        dia_semana WITH =,
        tsrange(DATE '2000-01-01' + hora_inicio, DATE '2000-01-01' + hora_fim, '[)') WITH &&)
);
-- sem índice extra: o gist do EXCLUDE (btree_gist) atende profissional_id = X (FK) e o lookup do dia
COMMENT ON TABLE agenda_horario IS 'Grade semanal de expediente do profissional (varias janelas por dia, sem sobreposicao).';
COMMENT ON COLUMN agenda_horario.dia_semana IS 'ISO 8601: 1=segunda ... 7=domingo (= extract(isodow) e date.isoweekday()).';

CREATE TABLE agenda_excecao (
    id                    bigint       GENERATED BY DEFAULT AS IDENTITY,
    profissional_id       bigint       NULL,
    tipo                  varchar(20)  NOT NULL,
    origem                varchar(20)  NOT NULL DEFAULT 'MANUAL',
    origem_ref            varchar(255) NULL,
    data_inicio           date         NULL,
    data_fim              date         NULL,
    hora_inicio           time         NULL,
    hora_fim              time         NULL,
    data_hora_inicio      timestamptz  NULL,
    data_hora_fim         timestamptz  NULL,
    regra_recorrencia     varchar(255) NULL,
    data_fim_recorrencia  date         NULL,
    motivo                text         NOT NULL DEFAULT '',
    observacao            text         NOT NULL DEFAULT '',
    eh_ativo              boolean      NOT NULL DEFAULT true,
    usuario_autor_id      bigint       NULL,
    criado_em             timestamptz  NOT NULL DEFAULT now(),
    atualizado_em         timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT pk_agenda_excecao PRIMARY KEY (id),
    CONSTRAINT fk_agenda_excecao_profissional FOREIGN KEY (profissional_id) REFERENCES profissional (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_agenda_excecao_usuario_autor FOREIGN KEY (usuario_autor_id) REFERENCES usuario (id)
        ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_agenda_excecao_tipo CHECK (tipo IN ('FOLGA', 'HORARIO_DIFERENTE', 'BLOQUEIO')),
    CONSTRAINT ck_agenda_excecao_origem CHECK (origem IN ('MANUAL', 'FERIADO_NACIONAL', 'FERIADO_LOCAL', 'RECESSO', 'GCAL')),
    CONSTRAINT ck_agenda_excecao_folga CHECK (tipo <> 'FOLGA' OR (
        data_inicio IS NOT NULL AND data_fim IS NOT NULL AND data_fim >= data_inicio
        AND hora_inicio IS NULL AND hora_fim IS NULL
        AND data_hora_inicio IS NULL AND data_hora_fim IS NULL
        AND regra_recorrencia IS NULL AND data_fim_recorrencia IS NULL)),
    CONSTRAINT ck_agenda_excecao_horario_diferente CHECK (tipo <> 'HORARIO_DIFERENTE' OR (
        profissional_id IS NOT NULL
        AND data_inicio IS NOT NULL AND data_fim IS NOT NULL AND data_fim = data_inicio
        AND hora_inicio IS NOT NULL AND hora_fim IS NOT NULL AND hora_fim > hora_inicio
        AND data_hora_inicio IS NULL AND data_hora_fim IS NULL
        AND regra_recorrencia IS NULL AND data_fim_recorrencia IS NULL)),
    CONSTRAINT ck_agenda_excecao_bloqueio CHECK (tipo <> 'BLOQUEIO' OR (
        data_hora_inicio IS NOT NULL AND data_hora_fim IS NOT NULL AND data_hora_fim > data_hora_inicio
        AND data_inicio IS NULL AND data_fim IS NULL AND hora_inicio IS NULL AND hora_fim IS NULL)),
    CONSTRAINT ck_agenda_excecao_recorrencia CHECK (regra_recorrencia IS NOT NULL OR data_fim_recorrencia IS NULL),
    CONSTRAINT ck_agenda_excecao_feriado_global CHECK (origem NOT IN ('FERIADO_NACIONAL', 'FERIADO_LOCAL', 'RECESSO')
        OR (profissional_id IS NULL AND tipo = 'FOLGA')),
    CONSTRAINT ck_agenda_excecao_gcal CHECK (origem <> 'GCAL' OR (tipo = 'BLOQUEIO' AND profissional_id IS NOT NULL)),
    CONSTRAINT ck_agenda_excecao_regra_recorrencia_suportada CHECK (                  -- [PG]
        regra_recorrencia IS NULL OR regra_recorrencia ~ (
          '^(FREQ=(DAILY|WEEKLY)(;INTERVAL=[1-9][0-9]?)?(;BYDAY=(MO|TU|WE|TH|FR|SA|SU)(,(MO|TU|WE|TH|FR|SA|SU))*)?'
          '|FREQ=MONTHLY(;INTERVAL=[1-9][0-9]?)?(;BYDAY=(-1|[1-4])?(MO|TU|WE|TH|FR|SA|SU)(,(-1|[1-4])?(MO|TU|WE|TH|FR|SA|SU))*'
          '|;BYMONTHDAY=([1-9]|[12][0-9]|3[01]))?)$')),
    CONSTRAINT ex_agenda_excecao_folga_sobreposta EXCLUDE USING gist (               -- [PG]
        profissional_id WITH =,
        daterange(data_inicio, data_fim, '[]') WITH &&)
        WHERE (tipo = 'FOLGA' AND eh_ativo AND profissional_id IS NOT NULL)
);
CREATE UNIQUE INDEX uq_agenda_excecao_horario_diferente_dia ON agenda_excecao (profissional_id, data_inicio)
    WHERE tipo = 'HORARIO_DIFERENTE' AND eh_ativo;
CREATE UNIQUE INDEX uq_agenda_excecao_feriado ON agenda_excecao (origem, data_inicio)
    WHERE origem IN ('FERIADO_NACIONAL', 'FERIADO_LOCAL');
CREATE UNIQUE INDEX uq_agenda_excecao_gcal_evento ON agenda_excecao (profissional_id, origem_ref)
    WHERE origem = 'GCAL' AND origem_ref IS NOT NULL;
CREATE INDEX ix_agenda_excecao_profissional_data_fim ON agenda_excecao (profissional_id, data_fim);
CREATE INDEX ix_agenda_excecao_profissional_data_hora_fim ON agenda_excecao (profissional_id, data_hora_fim);
CREATE INDEX ix_agenda_excecao_usuario_autor_parcial ON agenda_excecao (usuario_autor_id) WHERE usuario_autor_id IS NOT NULL;
COMMENT ON TABLE agenda_excecao IS 'Excecoes da grade semanal: FOLGA (dia(s) inteiro(s)), HORARIO_DIFERENTE (substitui a grade num dia) e BLOQUEIO (intervalo exato, pontual ou recorrente). profissional_id NULL = clinica inteira (feriado, recesso, bloqueio global). eh_ativo=false = registrada mas ignorada (ex.: feriado nacional em que a clinica abre).';
COMMENT ON COLUMN agenda_excecao.origem IS 'MANUAL (painel) | FERIADO_NACIONAL/FERIADO_LOCAL (carregar_feriados) | RECESSO (clinica) | GCAL (importado do Google Calendar).';
COMMENT ON COLUMN agenda_excecao.origem_ref IS 'Id do evento no sistema de origem (GCAL): torna a importacao idempotente.';
COMMENT ON COLUMN agenda_excecao.data_inicio IS 'FOLGA/HORARIO_DIFERENTE: primeiro dia (data local). NULL em BLOQUEIO.';
COMMENT ON COLUMN agenda_excecao.data_fim IS 'FOLGA: ultimo dia (inclusivo). HORARIO_DIFERENTE: = data_inicio.';
COMMENT ON COLUMN agenda_excecao.regra_recorrencia IS 'RRULE (RFC 5545) no subconjunto suportado (fn_ocorrencias_recorrencia): FREQ DAILY/WEEKLY/MONTHLY, INTERVAL, BYDAY (ordinal so em MONTHLY) ou BYMONTHDAY. DTSTART = data_hora_inicio (hora local). Sem COUNT/UNTIL: use data_fim_recorrencia.';
COMMENT ON COLUMN agenda_excecao.data_fim_recorrencia IS 'Ultimo dia (inclusivo) em que uma ocorrencia pode comecar.';

CREATE TABLE atendimento (
    id                         bigint        GENERATED BY DEFAULT AS IDENTITY,
    cliente_id                 bigint        NOT NULL,
    profissional_id            bigint        NOT NULL,
    procedimento_id            bigint        NOT NULL,
    promocao_id                bigint        NULL,
    atendimento_reagendado_id  bigint        NULL,
    atendimento_origem_id      bigint        NULL,
    eh_retorno                 boolean       NOT NULL DEFAULT false,
    data_hora_inicio           timestamptz   NOT NULL,
    data_hora_fim              timestamptz   NOT NULL,
    valor_cobrado              numeric(10,2) NULL,
    valor_original             numeric(10,2) NULL,
    descricao_preco            text          NOT NULL DEFAULT '',
    status                     varchar(20)   NOT NULL DEFAULT 'PENDENTE',
    criado_em                  timestamptz   NOT NULL DEFAULT now(),
    atualizado_em              timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT pk_atendimento PRIMARY KEY (id),
    CONSTRAINT fk_atendimento_cliente FOREIGN KEY (cliente_id) REFERENCES cliente (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_atendimento_profissional FOREIGN KEY (profissional_id) REFERENCES profissional (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_atendimento_procedimento FOREIGN KEY (procedimento_id) REFERENCES procedimento (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_atendimento_promocao FOREIGN KEY (promocao_id) REFERENCES promocao (id)
        ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_atendimento_atendimento_reagendado FOREIGN KEY (atendimento_reagendado_id) REFERENCES atendimento (id)
        ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_atendimento_atendimento_origem FOREIGN KEY (atendimento_origem_id) REFERENCES atendimento (id)
        ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_atendimento_status CHECK (status IN
        ('PENDENTE', 'AGENDADO', 'CONFIRMADO', 'REALIZADO', 'CANCELADO', 'FALTOU', 'REAGENDADO')),
    CONSTRAINT ck_atendimento_fim_apos_inicio CHECK (data_hora_fim > data_hora_inicio),
    CONSTRAINT ck_atendimento_retorno_tem_origem CHECK (NOT eh_retorno OR atendimento_origem_id IS NOT NULL),
    CONSTRAINT ck_atendimento_retorno_valor_zero CHECK (NOT eh_retorno OR valor_cobrado IS NULL OR valor_cobrado = 0),
    CONSTRAINT ck_atendimento_valores_nao_negativos CHECK (
        (valor_cobrado IS NULL OR valor_cobrado >= 0) AND (valor_original IS NULL OR valor_original >= 0)),
    CONSTRAINT ex_atendimento_sobreposicao EXCLUDE USING gist (                          -- [PG]
        profissional_id WITH =,
        tstzrange(data_hora_inicio, data_hora_fim) WITH &&)
        WHERE (status IN ('PENDENTE', 'AGENDADO', 'CONFIRMADO'))
        DEFERRABLE INITIALLY IMMEDIATE
);
CREATE UNIQUE INDEX uq_atendimento_retorno_por_origem ON atendimento (atendimento_origem_id)
    WHERE eh_retorno AND status IN ('PENDENTE', 'AGENDADO', 'CONFIRMADO', 'REALIZADO');
CREATE INDEX ix_atendimento_profissional_data_hora_inicio ON atendimento (profissional_id, data_hora_inicio);
CREATE INDEX ix_atendimento_cliente_data_hora_inicio ON atendimento (cliente_id, data_hora_inicio);
CREATE INDEX ix_atendimento_data_hora_inicio ON atendimento (data_hora_inicio);
CREATE INDEX ix_atendimento_data_hora_inicio_ativo_parcial ON atendimento (data_hora_inicio)
    WHERE status IN ('PENDENTE', 'AGENDADO', 'CONFIRMADO');
CREATE INDEX ix_atendimento_procedimento ON atendimento (procedimento_id);
CREATE INDEX ix_atendimento_promocao_parcial ON atendimento (promocao_id) WHERE promocao_id IS NOT NULL;
CREATE INDEX ix_atendimento_atendimento_reagendado_parcial ON atendimento (atendimento_reagendado_id)
    WHERE atendimento_reagendado_id IS NOT NULL;
CREATE INDEX ix_atendimento_atendimento_origem_parcial ON atendimento (atendimento_origem_id)
    WHERE atendimento_origem_id IS NOT NULL;
COMMENT ON TABLE atendimento IS 'Agendamento/sessao. FSM (models.Atendimento.TRANSICOES): PENDENTE->AGENDADO->CONFIRMADO->REALIZADO|CANCELADO|FALTOU|REAGENDADO. ex_atendimento_sobreposicao impede 2 ativos sobrepostos do mesmo profissional.';
COMMENT ON COLUMN atendimento.atendimento_reagendado_id IS 'Atendimento anterior (status REAGENDADO) que este substitui.';
COMMENT ON COLUMN atendimento.atendimento_origem_id IS 'Sessao principal quando este e o retorno gratuito (eh_retorno).';
COMMENT ON COLUMN atendimento.valor_cobrado IS 'Preco gravado no agendamento (promocao da data); sessao de pacote leva o preco cheio e nao soma no faturamento (D20).';
COMMENT ON COLUMN atendimento.valor_original IS 'Preco cheio quando houve promocao.';
```

### 2.5 Comunicação

```sql
CREATE TABLE notificacao (
    id              bigint      GENERATED BY DEFAULT AS IDENTITY,
    atendimento_id  bigint      NOT NULL,
    tipo            varchar(30) NOT NULL DEFAULT 'LEMBRETE',
    canal           varchar(20) NOT NULL DEFAULT 'WHATSAPP',
    status          varchar(20) NOT NULL DEFAULT 'PENDENTE',
    resposta        varchar(20) NULL,
    mensagem        text        NOT NULL DEFAULT '',
    enviado_em      timestamptz NULL,
    respondido_em   timestamptz NULL,
    criado_em       timestamptz NOT NULL DEFAULT now(),
    atualizado_em   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_notificacao PRIMARY KEY (id),
    CONSTRAINT fk_notificacao_atendimento FOREIGN KEY (atendimento_id) REFERENCES atendimento (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_notificacao_tipo CHECK (tipo IN
        ('LEMBRETE', 'LEMBRETE_2H', 'CONFIRMACAO', 'CANCELAMENTO', 'NPS', 'PESQUISA', 'APROVACAO', 'TERMO')),
    CONSTRAINT ck_notificacao_canal CHECK (canal IN ('WHATSAPP', 'SMS', 'EMAIL')),
    CONSTRAINT ck_notificacao_status CHECK (status IN ('PENDENTE', 'ENVIADO', 'FALHOU')),
    CONSTRAINT ck_notificacao_resposta CHECK (resposta IS NULL OR resposta IN ('CONFIRMOU', 'CANCELOU'))
);
CREATE INDEX ix_notificacao_atendimento_tipo ON notificacao (atendimento_id, tipo);
CREATE INDEX ix_notificacao_criado_em ON notificacao (criado_em DESC);
COMMENT ON TABLE notificacao IS 'Mensagem ligada a um atendimento (lembrete, NPS, termo...). O link publico da mensagem vive em link_acesso (so o hash). mensagem e limpa apos 12 meses.';

CREATE TABLE lista_espera (
    id                        bigint       GENERATED BY DEFAULT AS IDENTITY,
    cliente_id                bigint       NOT NULL,
    procedimento_id           bigint       NOT NULL,
    profissional_desejado_id  bigint       NULL,
    data_desejada             date         NOT NULL,
    turno_desejado            varchar(20)  NULL,
    eh_notificado             boolean      NOT NULL DEFAULT false,
    email_contato             varchar(254) NULL,
    criado_em                 timestamptz  NOT NULL DEFAULT now(),
    atualizado_em             timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT pk_lista_espera PRIMARY KEY (id),
    CONSTRAINT fk_lista_espera_cliente FOREIGN KEY (cliente_id) REFERENCES cliente (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_lista_espera_procedimento FOREIGN KEY (procedimento_id) REFERENCES procedimento (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_lista_espera_profissional_desejado FOREIGN KEY (profissional_desejado_id) REFERENCES profissional (id)
        ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_lista_espera_turno CHECK (turno_desejado IS NULL OR turno_desejado IN ('MANHA', 'TARDE', 'NOITE')),
    CONSTRAINT ck_lista_espera_email_contato_nao_vazio CHECK (email_contato IS NULL OR email_contato <> '')
);
CREATE UNIQUE INDEX uq_lista_espera_pendente ON lista_espera (cliente_id, procedimento_id, data_desejada)
    WHERE NOT eh_notificado;
CREATE INDEX ix_lista_espera_cliente ON lista_espera (cliente_id);
CREATE INDEX ix_lista_espera_procedimento_data_desejada ON lista_espera (procedimento_id, data_desejada);
CREATE INDEX ix_lista_espera_data_desejada ON lista_espera (data_desejada);
CREATE INDEX ix_lista_espera_profissional_desejado_parcial ON lista_espera (profissional_desejado_id)
    WHERE profissional_desejado_id IS NOT NULL;
COMMENT ON TABLE lista_espera IS 'Pedido de aviso de vaga. Apagado quando data_desejada passa (sp_aplicar_retencao_lgpd).';
COMMENT ON COLUMN lista_espera.turno_desejado IS 'NULL = qualquer turno.';
COMMENT ON COLUMN lista_espera.email_contato IS 'E-mail digitado no formulario publico quando o cliente ja existe (nao reescreve o cadastro).';

CREATE TABLE avaliacao_nps (
    id                        bigint      GENERATED BY DEFAULT AS IDENTITY,
    atendimento_id            bigint      NOT NULL,
    nota                      smallint    NOT NULL,
    comentario                text        NOT NULL DEFAULT '',
    eh_publicacao_autorizada  boolean     NOT NULL DEFAULT false,
    eh_publicacao_aprovada    boolean     NOT NULL DEFAULT false,
    eh_alerta_enviado         boolean     NOT NULL DEFAULT false,
    criado_em                 timestamptz NOT NULL DEFAULT now(),
    atualizado_em             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_avaliacao_nps PRIMARY KEY (id),
    CONSTRAINT uq_avaliacao_nps_atendimento UNIQUE (atendimento_id),
    CONSTRAINT fk_avaliacao_nps_atendimento FOREIGN KEY (atendimento_id) REFERENCES atendimento (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_avaliacao_nps_nota_0_10 CHECK (nota BETWEEN 0 AND 10),
    CONSTRAINT ck_avaliacao_nps_aprovacao_exige_autorizacao CHECK (NOT eh_publicacao_aprovada OR eh_publicacao_autorizada)
);
CREATE INDEX ix_avaliacao_nps_criado_em ON avaliacao_nps (criado_em);
CREATE INDEX ix_avaliacao_nps_alerta_pendente_parcial ON avaliacao_nps (criado_em)
    WHERE nota <= 6 AND NOT eh_alerta_enviado;
CREATE INDEX ix_avaliacao_nps_depoimento_parcial ON avaliacao_nps (criado_em DESC)
    WHERE nota >= 9 AND eh_publicacao_autorizada AND eh_publicacao_aprovada;
COMMENT ON TABLE avaliacao_nps IS 'Nota 0-10 do atendimento. Depoimento publico exige opt-in do cliente (eh_publicacao_autorizada) E moderacao (eh_publicacao_aprovada).';
COMMENT ON COLUMN avaliacao_nps.eh_alerta_enviado IS 'Detrator (nota <= 6) ja avisado por e-mail a clinica.';
```

### 2.6 Clínico

```sql
CREATE TABLE prontuario (
    id                  bigint      GENERATED BY DEFAULT AS IDENTITY,
    cliente_id          bigint      NOT NULL,
    alergias            text        NOT NULL DEFAULT '',
    contraindicacoes    text        NOT NULL DEFAULT '',
    historico_saude     text        NOT NULL DEFAULT '',
    medicamentos_uso    text        NOT NULL DEFAULT '',
    observacoes_gerais  text        NOT NULL DEFAULT '',
    respostas           jsonb       NOT NULL DEFAULT '{}',
    criado_em           timestamptz NOT NULL DEFAULT now(),
    atualizado_em       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_prontuario PRIMARY KEY (id),
    CONSTRAINT uq_prontuario_cliente UNIQUE (cliente_id),
    CONSTRAINT fk_prontuario_cliente FOREIGN KEY (cliente_id) REFERENCES cliente (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_prontuario_respostas_objeto CHECK (jsonb_typeof(respostas) = 'object')   -- [PG]
);
COMMENT ON TABLE prontuario IS 'Ficha clinica base 1:1 com o cliente (retencao 20 anos). Cada edicao grava antes a foto anterior em prontuario_versao; historico_alteracao registra qualquer escrita (inclusive fora da app).';
COMMENT ON COLUMN prontuario.respostas IS 'Respostas do questionario do prontuario {key: valor}; keys = formulario.perguntas do formulario tipo PRONTUARIO ativo.';

CREATE TABLE prontuario_versao (
    id                  bigint       GENERATED BY DEFAULT AS IDENTITY,
    prontuario_id       bigint       NOT NULL,
    usuario_autor_id    bigint       NULL,
    usuario_autor_nome  varchar(100) NOT NULL DEFAULT '',
    dados               jsonb        NOT NULL DEFAULT '{}',
    criado_em           timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT pk_prontuario_versao PRIMARY KEY (id),
    CONSTRAINT fk_prontuario_versao_prontuario FOREIGN KEY (prontuario_id) REFERENCES prontuario (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_prontuario_versao_usuario_autor FOREIGN KEY (usuario_autor_id) REFERENCES usuario (id)
        ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_prontuario_versao_dados_objeto CHECK (jsonb_typeof(dados) = 'object')    -- [PG]
);
CREATE INDEX ix_prontuario_versao_prontuario_criado_em ON prontuario_versao (prontuario_id, criado_em DESC);
COMMENT ON TABLE prontuario_versao IS 'Historico append-only do prontuario (CFM 1.638/2002): foto do estado ANTERIOR a cada edicao. Imutavel (tg_prontuario_versao_imutavel; so anular usuario_autor_id e aceito).';
COMMENT ON COLUMN prontuario_versao.dados IS 'Chaves = atributos Python (CAMPOS_CLINICOS + respostas_extras). E registro historico: nunca renomear as chaves.';

CREATE TABLE anotacao_sessao (
    id                  bigint       GENERATED BY DEFAULT AS IDENTITY,
    atendimento_id      bigint       NOT NULL,
    usuario_autor_id    bigint       NULL,
    usuario_autor_nome  varchar(100) NOT NULL DEFAULT '',
    texto               text         NOT NULL,
    criado_em           timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT pk_anotacao_sessao PRIMARY KEY (id),
    CONSTRAINT fk_anotacao_sessao_atendimento FOREIGN KEY (atendimento_id) REFERENCES atendimento (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_anotacao_sessao_usuario_autor FOREIGN KEY (usuario_autor_id) REFERENCES usuario (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX ix_anotacao_sessao_atendimento_criado_em ON anotacao_sessao (atendimento_id, criado_em DESC);
COMMENT ON TABLE anotacao_sessao IS 'Nota clinica de um atendimento, append-only (correcao = nova anotacao; tg_anotacao_sessao_imutavel).';

CREATE TABLE formulario (
    id               bigint       GENERATED BY DEFAULT AS IDENTITY,
    nome             varchar(120) NOT NULL,
    tipo             varchar(12)  NOT NULL DEFAULT 'ANAMNESE',
    escopo           varchar(20)  NOT NULL DEFAULT 'GLOBAL',
    categoria        varchar(20)  NOT NULL DEFAULT '',
    modalidade       varchar(12)  NOT NULL DEFAULT '',
    procedimento_id  bigint       NULL,
    perguntas        jsonb        NOT NULL DEFAULT '[]',
    eh_ativo         boolean      NOT NULL DEFAULT true,
    eh_obrigatorio   boolean      NOT NULL DEFAULT false,
    criado_em        timestamptz  NOT NULL DEFAULT now(),
    atualizado_em    timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT pk_formulario PRIMARY KEY (id),
    CONSTRAINT fk_formulario_procedimento FOREIGN KEY (procedimento_id) REFERENCES procedimento (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_formulario_tipo CHECK (tipo IN ('ANAMNESE', 'PESQUISA', 'PRONTUARIO')),
    CONSTRAINT ck_formulario_escopo CHECK (escopo IN ('GLOBAL', 'CATEGORIA', 'PROCEDIMENTO', 'MODALIDADE')),
    CONSTRAINT ck_formulario_escopo_coerente CHECK (
        (escopo <> 'PROCEDIMENTO' OR procedimento_id IS NOT NULL)
        AND (escopo <> 'CATEGORIA' OR categoria <> '')
        AND (escopo <> 'MODALIDADE' OR modalidade <> '')),
    CONSTRAINT ck_formulario_prontuario_global CHECK (tipo <> 'PRONTUARIO'
        OR (escopo = 'GLOBAL' AND NOT eh_obrigatorio AND procedimento_id IS NULL)),
    CONSTRAINT ck_formulario_perguntas_lista CHECK (jsonb_typeof(perguntas) = 'array')     -- [PG]
);
CREATE UNIQUE INDEX uq_formulario_prontuario_ativo ON formulario (tipo) WHERE tipo = 'PRONTUARIO' AND eh_ativo;
CREATE INDEX ix_formulario_procedimento_parcial ON formulario (procedimento_id) WHERE procedimento_id IS NOT NULL;
COMMENT ON TABLE formulario IS 'Questionario unico: ANAMNESE (pre-atendimento, respondida pela cliente), PESQUISA (pos-atendimento) e PRONTUARIO (perguntas extras da ficha, preenchidas pela equipe; no maximo 1 ativo).';
COMMENT ON COLUMN formulario.perguntas IS 'Lista [{key, tipo (bool|text|longtext|select|scale|checkboxes|number|date|email), label, obrigatorio, opcoes?, alerta_saude?}] — validada por services/anamnese.validar_respostas.';

CREATE TABLE resposta_formulario (
    id              bigint      GENERATED BY DEFAULT AS IDENTITY,
    formulario_id   bigint      NOT NULL,
    cliente_id      bigint      NOT NULL,
    atendimento_id  bigint      NULL,
    respostas       jsonb       NOT NULL DEFAULT '{}',
    respondido_em   timestamptz NULL,
    criado_em       timestamptz NOT NULL DEFAULT now(),
    atualizado_em   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_resposta_formulario PRIMARY KEY (id),
    CONSTRAINT fk_resposta_formulario_formulario FOREIGN KEY (formulario_id) REFERENCES formulario (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_resposta_formulario_cliente FOREIGN KEY (cliente_id) REFERENCES cliente (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_resposta_formulario_atendimento FOREIGN KEY (atendimento_id) REFERENCES atendimento (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_resposta_formulario_respostas_objeto CHECK (jsonb_typeof(respostas) = 'object')  -- [PG]
);
CREATE INDEX ix_resposta_formulario_cliente_criado_em ON resposta_formulario (cliente_id, criado_em DESC);
CREATE INDEX ix_resposta_formulario_atendimento ON resposta_formulario (atendimento_id);
CREATE INDEX ix_resposta_formulario_formulario ON resposta_formulario (formulario_id);
COMMENT ON TABLE resposta_formulario IS 'Ficha ANAMNESE/PESQUISA respondida pela cliente (dado de saude quando ANAMNESE). respondido_em NULL = convite nao respondido. O link publico vive em link_acesso (FORMULARIO).';
```

### 2.7 LGPD, consentimento e acesso público

```sql
CREATE TABLE versao_termo (
    id                    bigint      GENERATED BY DEFAULT AS IDENTITY,
    tipo                  varchar(20) NOT NULL,
    procedimento_id       bigint      NULL,
    titulo                text        NOT NULL,
    conteudo              text        NOT NULL,
    versao                varchar(20) NOT NULL,
    data_inicio_vigencia  date        NOT NULL,
    eh_ativo              boolean     NOT NULL DEFAULT true,
    criado_em             timestamptz NOT NULL DEFAULT now(),
    atualizado_em         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_versao_termo PRIMARY KEY (id),
    CONSTRAINT fk_versao_termo_procedimento FOREIGN KEY (procedimento_id) REFERENCES procedimento (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_versao_termo_tipo CHECK (tipo IN ('LGPD', 'SAUDE', 'PROCEDIMENTO'))
);
CREATE UNIQUE INDEX uq_versao_termo_ativa_por_procedimento ON versao_termo (tipo, procedimento_id) WHERE eh_ativo;
CREATE UNIQUE INDEX uq_versao_termo_ativa_global ON versao_termo (tipo) WHERE eh_ativo AND procedimento_id IS NULL;
CREATE INDEX ix_versao_termo_procedimento_parcial ON versao_termo (procedimento_id) WHERE procedimento_id IS NOT NULL;
COMMENT ON TABLE versao_termo IS 'Texto versionado de termo (LGPD, SAUDE art. 11, PROCEDIMENTO). Imutavel depois do 1o aceite (tg_versao_termo_imutavel); 1 ativo por escopo.';

CREATE TABLE aceite_termo (
    id               bigint       GENERATED BY DEFAULT AS IDENTITY,
    cliente_id       bigint       NOT NULL,
    versao_termo_id  bigint       NOT NULL,
    atendimento_id   bigint       NULL,
    ip_origem        inet         NULL,
    user_agent       varchar(500) NOT NULL DEFAULT '',
    conteudo_hash    char(64)     NULL,
    criado_em        timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT pk_aceite_termo PRIMARY KEY (id),
    CONSTRAINT uq_aceite_termo_cliente_versao UNIQUE (cliente_id, versao_termo_id),
    CONSTRAINT fk_aceite_termo_cliente FOREIGN KEY (cliente_id) REFERENCES cliente (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_aceite_termo_versao_termo FOREIGN KEY (versao_termo_id) REFERENCES versao_termo (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_aceite_termo_atendimento FOREIGN KEY (atendimento_id) REFERENCES atendimento (id)
        ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_aceite_termo_conteudo_hash_formato                                    -- [PG]
        CHECK (conteudo_hash IS NULL OR conteudo_hash ~ '^[0-9a-f]{64}$')
);
CREATE INDEX ix_aceite_termo_versao_termo ON aceite_termo (versao_termo_id);
CREATE INDEX ix_aceite_termo_atendimento_parcial ON aceite_termo (atendimento_id) WHERE atendimento_id IS NOT NULL;
COMMENT ON TABLE aceite_termo IS 'Prova do aceite (LGPD art. 8): IP, user-agent e SHA-256 do texto lido. Imutavel (tg_aceite_termo_imutavel); retencao 20 anos.';
COMMENT ON COLUMN aceite_termo.conteudo_hash IS 'SHA-256 do versao_termo.conteudo aceito; NULL = aceite anterior a 0044 (sem prova do texto).';

CREATE TABLE consentimento (
    id                bigint       GENERATED BY DEFAULT AS IDENTITY,
    cliente_id        bigint       NOT NULL,
    finalidade        varchar(30)  NOT NULL,
    canal             varchar(20)  NOT NULL,
    eh_concedido      boolean      NOT NULL,
    origem            varchar(30)  NOT NULL,
    texto             text         NOT NULL DEFAULT '',
    ip_origem         inet         NULL,
    user_agent        varchar(500) NOT NULL DEFAULT '',
    usuario_autor_id  bigint       NULL,
    registrado_em     timestamptz  NOT NULL DEFAULT now(),
    criado_em         timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT pk_consentimento PRIMARY KEY (id),
    CONSTRAINT fk_consentimento_cliente FOREIGN KEY (cliente_id) REFERENCES cliente (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_consentimento_usuario_autor FOREIGN KEY (usuario_autor_id) REFERENCES usuario (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_consentimento_finalidade CHECK (finalidade IN
        ('MARKETING', 'PESQUISA_SATISFACAO', 'LEMBRETE_AGENDAMENTO')),
    CONSTRAINT ck_consentimento_canal CHECK (canal IN ('EMAIL', 'WHATSAPP', 'SMS')),
    CONSTRAINT ck_consentimento_finalidade_canal CHECK (
        (finalidade = 'MARKETING' AND canal = 'EMAIL')
     OR (finalidade = 'PESQUISA_SATISFACAO' AND canal = 'WHATSAPP')
     OR (finalidade = 'LEMBRETE_AGENDAMENTO' AND canal = 'WHATSAPP')),
    CONSTRAINT ck_consentimento_origem CHECK (origem IN
        ('AGENDAMENTO_ONLINE', 'DESCADASTRO', 'PAINEL', 'ESQUECIMENTO', 'MIGRACAO', 'ADMIN'))
);
CREATE INDEX ix_consentimento_cliente_finalidade_canal_registrado_em
    ON consentimento (cliente_id, finalidade, canal, registrado_em DESC, id DESC);
CREATE INDEX ix_consentimento_usuario_autor_parcial ON consentimento (usuario_autor_id) WHERE usuario_autor_id IS NOT NULL;
COMMENT ON TABLE consentimento IS 'Historico append-only de concessao/revogacao por finalidade x canal (LGPD art. 8). Estado atual = linha mais recente (vw_consentimento_atual / fn_tem_consentimento). Sem linha = nao concedido.';
COMMENT ON COLUMN consentimento.texto IS 'Texto exibido ao titular no momento (prova do que foi consentido).';
COMMENT ON COLUMN consentimento.registrado_em IS 'Quando o titular concedeu/revogou (na migracao = data original de consent_*_em).';

CREATE TABLE link_acesso (
    id                      bigint      GENERATED BY DEFAULT AS IDENTITY,
    finalidade              varchar(30) NOT NULL,
    atendimento_id          bigint      NULL,
    notificacao_id          bigint      NULL,
    resposta_formulario_id  bigint      NULL,
    cliente_id              bigint      NULL,
    profissional_id         bigint      NULL,
    token_hash              char(64)    NOT NULL,
    expira_em               timestamptz NULL,
    usado_em                timestamptz NULL,
    revogado_em             timestamptz NULL,
    criado_em               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_link_acesso PRIMARY KEY (id),
    CONSTRAINT uq_link_acesso_token_hash UNIQUE (token_hash),
    CONSTRAINT fk_link_acesso_atendimento FOREIGN KEY (atendimento_id) REFERENCES atendimento (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_link_acesso_notificacao FOREIGN KEY (notificacao_id) REFERENCES notificacao (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_link_acesso_resposta_formulario FOREIGN KEY (resposta_formulario_id) REFERENCES resposta_formulario (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_link_acesso_cliente FOREIGN KEY (cliente_id) REFERENCES cliente (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_link_acesso_profissional FOREIGN KEY (profissional_id) REFERENCES profissional (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_link_acesso_finalidade CHECK (finalidade IN
        ('GERENCIAR_AGENDAMENTO', 'CONFIRMAR_PRESENCA', 'NPS', 'TERMO', 'FORMULARIO', 'DESCADASTRO', 'FEED_ICS')),
    CONSTRAINT ck_link_acesso_alvo_por_finalidade CHECK (
        (finalidade = 'GERENCIAR_AGENDAMENTO' AND atendimento_id IS NOT NULL AND notificacao_id IS NULL
            AND resposta_formulario_id IS NULL AND cliente_id IS NULL AND profissional_id IS NULL)
     OR (finalidade IN ('CONFIRMAR_PRESENCA', 'NPS', 'TERMO') AND notificacao_id IS NOT NULL AND atendimento_id IS NULL
            AND resposta_formulario_id IS NULL AND cliente_id IS NULL AND profissional_id IS NULL)
     OR (finalidade = 'FORMULARIO' AND resposta_formulario_id IS NOT NULL AND atendimento_id IS NULL
            AND notificacao_id IS NULL AND cliente_id IS NULL AND profissional_id IS NULL)
     OR (finalidade = 'DESCADASTRO' AND cliente_id IS NOT NULL AND atendimento_id IS NULL
            AND notificacao_id IS NULL AND resposta_formulario_id IS NULL AND profissional_id IS NULL)
     OR (finalidade = 'FEED_ICS' AND profissional_id IS NOT NULL AND atendimento_id IS NULL
            AND notificacao_id IS NULL AND resposta_formulario_id IS NULL AND cliente_id IS NULL)),
    CONSTRAINT ck_link_acesso_token_hash_formato CHECK (token_hash ~ '^[0-9a-f]{64}$')    -- [PG]
);
CREATE INDEX ix_link_acesso_atendimento_parcial ON link_acesso (atendimento_id) WHERE atendimento_id IS NOT NULL;
CREATE INDEX ix_link_acesso_notificacao_parcial ON link_acesso (notificacao_id) WHERE notificacao_id IS NOT NULL;
CREATE INDEX ix_link_acesso_resposta_formulario_parcial ON link_acesso (resposta_formulario_id) WHERE resposta_formulario_id IS NOT NULL;
CREATE INDEX ix_link_acesso_cliente_parcial ON link_acesso (cliente_id) WHERE cliente_id IS NOT NULL;
CREATE INDEX ix_link_acesso_profissional_parcial ON link_acesso (profissional_id) WHERE profissional_id IS NOT NULL;
CREATE INDEX ix_link_acesso_expira_em_parcial ON link_acesso (expira_em) WHERE expira_em IS NOT NULL;
COMMENT ON TABLE link_acesso IS 'Link publico com token (link magico). So o SHA-256 do token e gravado: o token em claro existe apenas na URL entregue. Alvo tipado por finalidade (exatamente 1 FK preenchida).';
COMMENT ON COLUMN link_acesso.token_hash IS 'sha256(token) hex. Sem pepper/HMAC de proposito: rotacionar a SECRET_KEY (incidente, PROJECT §9.5) nao pode invalidar links ja enviados.';
COMMENT ON COLUMN link_acesso.usado_em IS 'Primeiro uso efetivo (acao concluida). Links de uso multiplo (ICS, descadastro, gerenciar) continuam validos.';
COMMENT ON COLUMN link_acesso.revogado_em IS 'Revogado (rotacao do ICS, anonimizacao). Link revogado = 404.';

CREATE TABLE codigo_otp (
    id              bigint       GENERATED BY DEFAULT AS IDENTITY,
    identificador   varchar(254) NOT NULL,
    telefone        varchar(20)  NULL,
    canal           varchar(10)  NOT NULL DEFAULT 'SMS',
    codigo_hash     char(64)     NOT NULL,
    proposito       varchar(20)  NOT NULL DEFAULT 'AGENDAMENTO',
    expira_em       timestamptz  NOT NULL,
    usado_em        timestamptz  NULL,
    qtd_tentativas  smallint     NOT NULL DEFAULT 0,
    ip_origem       inet         NULL,
    criado_em       timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT pk_codigo_otp PRIMARY KEY (id),
    CONSTRAINT ck_codigo_otp_canal CHECK (canal IN ('EMAIL', 'SMS')),
    CONSTRAINT ck_codigo_otp_proposito CHECK (proposito IN ('AGENDAMENTO', 'LOGIN_CLIENTE', 'DSAR')),
    CONSTRAINT ck_codigo_otp_qtd_tentativas_0_10 CHECK (qtd_tentativas BETWEEN 0 AND 10),
    CONSTRAINT ck_codigo_otp_codigo_hash_formato CHECK (codigo_hash ~ '^[0-9a-f]{64}$')    -- [PG]
);
CREATE INDEX ix_codigo_otp_identificador_proposito_criado_em ON codigo_otp (identificador, proposito, criado_em DESC);
COMMENT ON TABLE codigo_otp IS 'Desafio OTP (HMAC-SHA256 com a SECRET_KEY), TTL e lockout. Efemero: apagado apos 24h (sp_purgar_dados_expirados).';
COMMENT ON COLUMN codigo_otp.identificador IS 'Chave do desafio: e-mail, ou "sms+<digitos>@shivazen.local" nos fluxos so-telefone.';
```

### 2.8 Pacotes

```sql
CREATE TABLE pacote (
    id              bigint        GENERATED BY DEFAULT AS IDENTITY,
    nome            varchar(150)  NOT NULL,
    descricao       text          NOT NULL DEFAULT '',
    valor_total     numeric(10,2) NOT NULL,
    eh_ativo        boolean       NOT NULL DEFAULT true,
    validade_meses  smallint      NOT NULL DEFAULT 12,
    criado_em       timestamptz   NOT NULL DEFAULT now(),
    atualizado_em   timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT pk_pacote PRIMARY KEY (id),
    CONSTRAINT ck_pacote_valor_total_nao_negativo CHECK (valor_total >= 0),
    CONSTRAINT ck_pacote_validade_meses_positiva CHECK (validade_meses > 0)
);
COMMENT ON TABLE pacote IS 'Catalogo de pacotes, editavel a qualquer momento: a venda congela nome/itens em compra_pacote/compra_pacote_item.';

CREATE TABLE pacote_item (
    id               bigint      GENERATED BY DEFAULT AS IDENTITY,
    pacote_id        bigint      NOT NULL,
    procedimento_id  bigint      NOT NULL,
    qtd_sessoes      smallint    NOT NULL DEFAULT 1,
    criado_em        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_pacote_item PRIMARY KEY (id),
    CONSTRAINT uq_pacote_item_pacote_procedimento UNIQUE (pacote_id, procedimento_id),
    CONSTRAINT fk_pacote_item_pacote FOREIGN KEY (pacote_id) REFERENCES pacote (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_pacote_item_procedimento FOREIGN KEY (procedimento_id) REFERENCES procedimento (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_pacote_item_qtd_sessoes_positiva CHECK (qtd_sessoes > 0)
);
CREATE INDEX ix_pacote_item_procedimento ON pacote_item (procedimento_id);
COMMENT ON TABLE pacote_item IS 'Itens do pacote no catalogo (procedimento x sessoes).';

CREATE TABLE compra_pacote (
    id                 bigint        GENERATED BY DEFAULT AS IDENTITY,
    cliente_id         bigint        NOT NULL,
    pacote_id          bigint        NOT NULL,
    pacote_nome        varchar(150)  NOT NULL,
    valor_pago         numeric(10,2) NOT NULL,
    valor_reembolsado  numeric(10,2) NOT NULL DEFAULT 0,
    status             varchar(20)   NOT NULL DEFAULT 'ATIVO',
    data_expiracao     date          NULL,
    criado_em          timestamptz   NOT NULL DEFAULT now(),
    atualizado_em      timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT pk_compra_pacote PRIMARY KEY (id),
    CONSTRAINT fk_compra_pacote_cliente FOREIGN KEY (cliente_id) REFERENCES cliente (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_compra_pacote_pacote FOREIGN KEY (pacote_id) REFERENCES pacote (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_compra_pacote_status CHECK (status IN ('ATIVO', 'FINALIZADO', 'CANCELADO', 'EXPIRADO')),
    CONSTRAINT ck_compra_pacote_valor_pago_nao_negativo CHECK (valor_pago >= 0),
    CONSTRAINT ck_compra_pacote_valor_reembolsado CHECK (valor_reembolsado >= 0 AND valor_reembolsado <= valor_pago)
);
CREATE INDEX ix_compra_pacote_cliente_status ON compra_pacote (cliente_id, status);
CREATE INDEX ix_compra_pacote_pacote ON compra_pacote (pacote_id);
CREATE INDEX ix_compra_pacote_criado_em ON compra_pacote (criado_em);
CREATE INDEX ix_compra_pacote_data_expiracao_ativo_parcial ON compra_pacote (data_expiracao) WHERE status = 'ATIVO';
COMMENT ON TABLE compra_pacote IS 'Venda de pacote (registro fiscal, PROTECT). Receita = valor_pago - valor_reembolsado na data da venda (D20).';
COMMENT ON COLUMN compra_pacote.pacote_nome IS 'Nome do pacote no momento da venda (o catalogo pode mudar depois).';
COMMENT ON COLUMN compra_pacote.data_expiracao IS 'Ultimo dia valido (data local); sessao feita ate esse dia debita mesmo com o job ja tendo marcado EXPIRADO.';

CREATE TABLE compra_pacote_item (
    id                 bigint        GENERATED BY DEFAULT AS IDENTITY,
    compra_pacote_id   bigint        NOT NULL,
    procedimento_id    bigint        NOT NULL,
    procedimento_nome  varchar(100)  NOT NULL,
    qtd_sessoes        smallint      NOT NULL,
    valor_unitario     numeric(10,2) NOT NULL,
    criado_em          timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT pk_compra_pacote_item PRIMARY KEY (id),
    CONSTRAINT uq_compra_pacote_item_compra_procedimento UNIQUE (compra_pacote_id, procedimento_id),
    CONSTRAINT uq_compra_pacote_item_id_compra UNIQUE (id, compra_pacote_id),
    CONSTRAINT fk_compra_pacote_item_compra_pacote FOREIGN KEY (compra_pacote_id) REFERENCES compra_pacote (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_compra_pacote_item_procedimento FOREIGN KEY (procedimento_id) REFERENCES procedimento (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_compra_pacote_item_qtd_sessoes_positiva CHECK (qtd_sessoes > 0),
    CONSTRAINT ck_compra_pacote_item_valor_unitario_nao_negativo CHECK (valor_unitario >= 0)
);
CREATE INDEX ix_compra_pacote_item_procedimento ON compra_pacote_item (procedimento_id);
COMMENT ON TABLE compra_pacote_item IS 'Itens congelados na venda (snapshot). Saldo = qtd_sessoes - consumos do item (fn_saldo_pacote). Imutavel (tg_compra_pacote_item_imutavel).';
COMMENT ON COLUMN compra_pacote_item.valor_unitario IS 'Rateio do valor pago por sessao: round(valor_pago / total de sessoes da compra, 2) — base da comissao da sessao de pacote (D20).';

CREATE TABLE consumo_sessao (
    id                     bigint      GENERATED BY DEFAULT AS IDENTITY,
    compra_pacote_id       bigint      NOT NULL,
    compra_pacote_item_id  bigint      NOT NULL,
    atendimento_id         bigint      NOT NULL,
    criado_em              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_consumo_sessao PRIMARY KEY (id),
    CONSTRAINT uq_consumo_sessao_atendimento UNIQUE (atendimento_id),
    CONSTRAINT fk_consumo_sessao_compra_pacote FOREIGN KEY (compra_pacote_id) REFERENCES compra_pacote (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_consumo_sessao_compra_pacote_item FOREIGN KEY (compra_pacote_item_id) REFERENCES compra_pacote_item (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_consumo_sessao_atendimento FOREIGN KEY (atendimento_id) REFERENCES atendimento (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_consumo_sessao_compra_pacote_item_compra                              -- [PG]
        FOREIGN KEY (compra_pacote_item_id, compra_pacote_id) REFERENCES compra_pacote_item (id, compra_pacote_id)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX ix_consumo_sessao_compra_pacote ON consumo_sessao (compra_pacote_id);
CREATE INDEX ix_consumo_sessao_compra_pacote_item ON consumo_sessao (compra_pacote_item_id);
COMMENT ON TABLE consumo_sessao IS '1 sessao de pacote consumida por atendimento REALIZADO (nunca retorno). tg_consumo_sessao_validar confere item da compra, procedimento, retorno e saldo.';
```

### 2.9 Financeiro

```sql
CREATE TABLE carteira (
    id             bigint        GENERATED BY DEFAULT AS IDENTITY,
    cliente_id     bigint        NOT NULL,
    valor_saldo    numeric(10,2) NOT NULL DEFAULT 0,
    criado_em      timestamptz   NOT NULL DEFAULT now(),
    atualizado_em  timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT pk_carteira PRIMARY KEY (id),
    CONSTRAINT uq_carteira_cliente UNIQUE (cliente_id),
    CONSTRAINT fk_carteira_cliente FOREIGN KEY (cliente_id) REFERENCES cliente (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_carteira_valor_saldo_nao_negativo CHECK (valor_saldo >= 0)
);
COMMENT ON TABLE carteira IS 'Saldo de credito do cliente (cashback, vale). Toda mutacao: select_for_update + movimento_carteira na mesma transacao.';

CREATE TABLE movimento_carteira (
    id                      bigint        GENERATED BY DEFAULT AS IDENTITY,
    carteira_id             bigint        NOT NULL,
    tipo                    varchar(10)   NOT NULL,
    origem                  varchar(30)   NOT NULL DEFAULT 'OUTRO',
    valor                   numeric(10,2) NOT NULL,
    valor_saldo_resultante  numeric(10,2) NOT NULL,
    atendimento_id          bigint        NULL,
    observacao              text          NOT NULL DEFAULT '',
    usuario_autor_id        bigint        NULL,
    criado_em               timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT pk_movimento_carteira PRIMARY KEY (id),
    CONSTRAINT fk_movimento_carteira_carteira FOREIGN KEY (carteira_id) REFERENCES carteira (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_movimento_carteira_atendimento FOREIGN KEY (atendimento_id) REFERENCES atendimento (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_movimento_carteira_usuario_autor FOREIGN KEY (usuario_autor_id) REFERENCES usuario (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_movimento_carteira_tipo CHECK (tipo IN ('CREDITO', 'DEBITO')),
    CONSTRAINT ck_movimento_carteira_origem CHECK (origem IN ('VALE_PRESENTE', 'CANCELAMENTO', 'AJUSTE_MANUAL',
        'USO_AGENDAMENTO', 'CASHBACK_INDICACAO', 'CASHBACK_ESTORNO', 'OUTRO')),
    CONSTRAINT ck_movimento_carteira_valor_positivo CHECK (valor > 0),
    CONSTRAINT ck_movimento_carteira_valor_saldo_resultante_nao_negativo CHECK (valor_saldo_resultante >= 0)
);
CREATE UNIQUE INDEX uq_movimento_carteira_cashback_por_atendimento ON movimento_carteira (carteira_id, atendimento_id)
    WHERE origem = 'CASHBACK_INDICACAO';
CREATE INDEX ix_movimento_carteira_carteira_criado_em ON movimento_carteira (carteira_id, criado_em DESC);
CREATE INDEX ix_movimento_carteira_atendimento_parcial ON movimento_carteira (atendimento_id) WHERE atendimento_id IS NOT NULL;
COMMENT ON TABLE movimento_carteira IS 'Ledger append-only da carteira (tg_movimento_carteira_imutavel). Estorno = novo movimento.';

CREATE TABLE regra_comissao (
    id               bigint        GENERATED BY DEFAULT AS IDENTITY,
    profissional_id  bigint        NULL,
    procedimento_id  bigint        NULL,
    percentual       numeric(5,2)  NULL,
    valor_fixo       numeric(10,2) NULL,
    eh_ativo         boolean       NOT NULL DEFAULT true,
    criado_em        timestamptz   NOT NULL DEFAULT now(),
    atualizado_em    timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT pk_regra_comissao PRIMARY KEY (id),
    CONSTRAINT fk_regra_comissao_profissional FOREIGN KEY (profissional_id) REFERENCES profissional (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_regra_comissao_procedimento FOREIGN KEY (procedimento_id) REFERENCES procedimento (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_regra_comissao_percentual_xor_valor_fixo CHECK (
        (percentual IS NOT NULL AND valor_fixo IS NULL) OR (percentual IS NULL AND valor_fixo IS NOT NULL)),
    CONSTRAINT ck_regra_comissao_percentual_0_100 CHECK (percentual IS NULL OR percentual BETWEEN 0 AND 100),
    CONSTRAINT ck_regra_comissao_valor_fixo_nao_negativo CHECK (valor_fixo IS NULL OR valor_fixo >= 0)
);
CREATE INDEX ix_regra_comissao_profissional_procedimento ON regra_comissao (profissional_id, procedimento_id);
CREATE INDEX ix_regra_comissao_procedimento_parcial ON regra_comissao (procedimento_id) WHERE procedimento_id IS NOT NULL;
COMMENT ON TABLE regra_comissao IS 'Regra percentual XOR valor fixo por (profissional, procedimento); NULL = qualquer. Mais especifica vence; empate = atualizada por ultimo. Regra ja usada nao se exclui (FK de movimento_comissao): desative.';

CREATE TABLE movimento_comissao (
    id                            bigint        GENERATED BY DEFAULT AS IDENTITY,
    profissional_id               bigint        NOT NULL,
    atendimento_id                bigint        NOT NULL,
    tipo                          varchar(10)   NOT NULL,
    valor                         numeric(10,2) NOT NULL,
    regra_comissao_id             bigint        NULL,
    movimento_comissao_origem_id  bigint        NULL,
    observacao                    text          NOT NULL DEFAULT '',
    usuario_autor_id              bigint        NULL,
    criado_em                     timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT pk_movimento_comissao PRIMARY KEY (id),
    CONSTRAINT fk_movimento_comissao_profissional FOREIGN KEY (profissional_id) REFERENCES profissional (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_movimento_comissao_atendimento FOREIGN KEY (atendimento_id) REFERENCES atendimento (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_movimento_comissao_regra_comissao FOREIGN KEY (regra_comissao_id) REFERENCES regra_comissao (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_movimento_comissao_movimento_comissao_origem FOREIGN KEY (movimento_comissao_origem_id)
        REFERENCES movimento_comissao (id) ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_movimento_comissao_usuario_autor FOREIGN KEY (usuario_autor_id) REFERENCES usuario (id)
        ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_movimento_comissao_tipo CHECK (tipo IN ('CREDITO', 'ESTORNO', 'PAGAMENTO')),
    CONSTRAINT ck_movimento_comissao_valor_positivo CHECK (valor > 0),
    CONSTRAINT ck_movimento_comissao_origem_coerente CHECK (
        (tipo = 'CREDITO' AND movimento_comissao_origem_id IS NULL)
     OR (tipo <> 'CREDITO' AND movimento_comissao_origem_id IS NOT NULL)),
    CONSTRAINT ck_movimento_comissao_regra_so_credito CHECK (tipo = 'CREDITO' OR regra_comissao_id IS NULL)
);
CREATE UNIQUE INDEX uq_movimento_comissao_credito_por_atendimento ON movimento_comissao (atendimento_id)
    WHERE tipo = 'CREDITO';
CREATE UNIQUE INDEX uq_movimento_comissao_estorno_por_origem ON movimento_comissao (movimento_comissao_origem_id)
    WHERE tipo = 'ESTORNO';
CREATE UNIQUE INDEX uq_movimento_comissao_pagamento_por_origem ON movimento_comissao (movimento_comissao_origem_id)
    WHERE tipo = 'PAGAMENTO';
CREATE INDEX ix_movimento_comissao_profissional_criado_em ON movimento_comissao (profissional_id, criado_em DESC);
CREATE INDEX ix_movimento_comissao_atendimento ON movimento_comissao (atendimento_id);
CREATE INDEX ix_movimento_comissao_criado_em ON movimento_comissao (criado_em DESC);
CREATE INDEX ix_movimento_comissao_regra_comissao_parcial ON movimento_comissao (regra_comissao_id)
    WHERE regra_comissao_id IS NOT NULL;
CREATE INDEX ix_movimento_comissao_origem_parcial ON movimento_comissao (movimento_comissao_origem_id, tipo)
    WHERE movimento_comissao_origem_id IS NOT NULL;
COMMENT ON TABLE movimento_comissao IS 'Ledger append-only de comissao: CREDITO (atendimento realizado), ESTORNO (cancelamento) e PAGAMENTO (baixa), estes dois referenciando o CREDITO. Status e saldo sao derivados (vw_comissao, vw_comissao_saldo). Imutavel (tg_movimento_comissao_imutavel).';
COMMENT ON COLUMN movimento_comissao.movimento_comissao_origem_id IS 'CREDITO que o ESTORNO/PAGAMENTO liquida (NULL no proprio CREDITO).';
COMMENT ON COLUMN movimento_comissao.valor IS 'Sempre positivo; o sinal vem do tipo (CREDITO +, ESTORNO/PAGAMENTO -).';
```

### 2.10 Infra

```sql
CREATE TABLE configuracao (
    id             bigint       GENERATED BY DEFAULT AS IDENTITY,
    chave          varchar(100) NOT NULL,
    valor          text         NOT NULL DEFAULT '',
    descricao      text         NOT NULL DEFAULT '',
    criado_em      timestamptz  NOT NULL DEFAULT now(),
    atualizado_em  timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT pk_configuracao PRIMARY KEY (id),
    CONSTRAINT uq_configuracao_chave UNIQUE (chave)
);
COMMENT ON TABLE configuracao IS 'Chave-valor editavel no painel: Branding, email_admin, MAX_FALTAS_BLOQUEIO. (prontuario_perguntas saiu p/ formulario tipo PRONTUARIO.)';
```

### 2.11 Revisão de índices (a partir das queries reais)

Regras aplicadas: (1) toda FK tem índice, exceto se coberta pelo prefixo de outro índice/UNIQUE;
(2) exceção registrada: FK de autoria (`usuario_autor_id` em `prontuario_versao`,
`anotacao_sessao`, `movimento_carteira`, `movimento_comissao`, `log_auditoria`) — `usuario` nunca
é excluído (desativar) e nenhuma tela filtra por autor; (3) parcial quando a coluna é quase sempre
`NULL` ou a query só olha um subconjunto; (4) nada em booleano/enum de baixa cardinalidade; (5)
tabela de dezenas de linhas (catálogo) só tem PK/UNIQUE/FK.

| Tabela | Índice-alvo | Query atendida (arquivo → função) | Decisão |
|---|---|---|---|
| atendimento | `ix_atendimento_profissional_data_hora_inicio` | `services/disponibilidade.SlotService.slots_livres` (prof + dia, status ativo); conflito em `views/booking_public.confirmar_agendamento`, `booking_reagendar.reagendar_agendamento`, `admin_calendar.admin_calendar_mover`; `views/profissional.agenda` (dia/semana); `agenda_publica.ics_feed_profissional`; `views/prontuario._vinculo`. Cobre a FK `profissional_id` | renomeia `idx_atend_prof_data` |
| | `ix_atendimento_cliente_data_hora_inicio` | `booking_reagendar.meus_agendamentos` (cliente IN … ORDER BY início), `admin_management._contexto_cliente`, `prontuario.prontuario_detalhe`, `lgpd.candidatos_purga`/export, `fidelidade_service.liberar_cashback_indicacao`. Cobre a FK `cliente_id` | **substitui** `idx_atendimento_cli_status` (cliente tem dezenas de linhas; a 2ª coluna útil é a data) |
| | `ix_atendimento_data_hora_inicio` | `admin_calendar` (intervalo sem profissional), `dashboard.painel_overview` (mês), `admin_financeiro._calcular_metricas`, `relatorios.exportar_relatorio_excel`, `api.AtendimentoViewSet` (ORDER BY −início) | renomeia `idx_atendimento_data` |
| | `ix_atendimento_data_hora_inicio_ativo_parcial` | `dashboard.painel_overview` "próximos"/"pendentes" (`início >= now() AND status IN ativos ORDER BY início LIMIT`), `profissional.agenda` pendentes, `tasks.job_limpeza_status_atendimentos` (PENDENTE vencido) | **novo** — "agenda em aberto" |
| | `uq_atendimento_retorno_por_origem` + `ix_atendimento_atendimento_origem_parcial` | `services/retorno_service.sugerir_retorno` (`atendimento_origem=X, eh_retorno`), FK SET NULL | parcial (coluna quase sempre NULL) |
| | `ix_atendimento_procedimento`, `…_promocao_parcial`, `…_atendimento_reagendado_parcial` | FKs; `admin_financeiro` top procedimentos | parciais onde a coluna é quase sempre NULL |
| | EXCLUDE `ex_atendimento_sobreposicao` (gist) | garante a invariante; o ORM usa `<`/`>` nas colunas (não `&&`), por isso o btree acima continua necessário | renomeia `excl_atendimento_sobreposicao` |
| | ~~`idx_atendimento_status`~~, ~~`eh_retorno` db_index~~, ~~FK automáticas de `cliente_id`/`profissional_id`~~ | status (7 valores) e booleano não são seletivos; FKs cobertas | **removidos** |
| | (código) | `data_hora_inicio__date=` (`dashboard.painel_overview`, `profissional.agenda`, `tasks.job_enviar_lembrete_dia_seguinte`) vira `(col AT TIME ZONE …)::date` e **não usa índice**: trocar por intervalo `[início do dia local, +1 dia)` (helper em `utils/datas`) | recomendação (sem índice de expressão) |
| cliente | `uq_cliente_telefone_ativo` | todo lookup por telefone (`booking_otp`, `booking_public`, `booking_reagendar.meus_agendamentos`, `public.lista_espera`, `lgpd`, `admin_agendamento`) — sempre pelo manager padrão, que já filtra `deletado_em IS NULL` | predicado simplificado; ~~`idx_cliente_telefone`~~ **removido** (redundante) |
| | `uq_cliente_email_ativo` em `upper(email::text)` | `email__iexact` gera `UPPER("email"::text) = UPPER(%s)` (`booking_public`, `booking_otp`, `admin_agendamento`) + unicidade | **funde** `uniq_cliente_email_ativo` (LOWER) e `idx_cliente_email_upper` |
| | `ix_cliente_nome_trgm` (GIN sobre `fn_sem_acento(nome)`) | `utils/busca.q_busca_cliente` (painel de clientes e busca do dashboard), `api.ClienteViewSet ?q=`, `admin_management` (busca por nome) — hoje `icontains` = seq scan | **novo** + lookup no PG (§5.5) |
| | `ix_cliente_nome` | listas ordenadas por nome (`pacotes.admin_pacotes`, painel), collation `pt_br` | renomeia `idx_cliente_nome` |
| | `ix_cliente_deletado_em_parcial` | `lgpd.candidatos_purga` / `fn_clientes_para_anonimizar` (`deletado_em < limite`) | **substitui** o `db_index` cheio (IS NULL na maioria não é seletivo) |
| | `ix_cliente_cliente_indicador_parcial` | FK SET NULL; `Cliente.indicacoes` | parcial |
| | ~~`_like` de `codigo_indicacao`, `token_descadastro`~~ | nenhuma query `LIKE 'x%'` | **removidos** |
| notificacao | `ix_notificacao_atendimento_tipo` | `tasks.job_enviar_lembrete_dia_seguinte` (já enviou?), `tasks.candidatos_nps` (Exists + Count), `services/termos.obter_link_termo`. Cobre a FK | renomeia `idx_notif_atend_tipo` |
| | `ix_notificacao_criado_em` | housekeeping (`criado_em < limite`), `notificacoes.painel_notificacoes` | renomeia `idx_notificacao_criado` |
| | ~~`idx_notificacao_tipo_status`~~, ~~`idx_notif_nps_lookup`~~, ~~`token` UNIQUE + `_like`~~ | nenhuma query filtra tipo+canal+status sem o atendimento; token vai p/ `link_acesso` | **removidos** |
| lista_espera | `uq_lista_espera_pendente`, `ix_lista_espera_procedimento_data_desejada` | `services/lista_espera_service.candidatos` (procedimento + data + não notificado, FIFO). Cobre a FK procedimento | `idx_espera_proc_data_notif` sem o booleano |
| | `ix_lista_espera_cliente`, `ix_lista_espera_data_desejada` | FK/`sp_anonimizar_cliente`; purga `data_desejada < hoje` | renomeia `idx_espera_cliente`; data **nova** |
| avaliacao_nps | `ix_avaliacao_nps_criado_em` | `relatorios.painel_nps`, `dashboard` (NPS 30d), `fn_nps_periodo` | **novo** |
| | `ix_avaliacao_nps_alerta_pendente_parcial` | `tasks.job_alerta_detrator_nps` (`nota <= 6 AND NOT alerta`) — quase sempre vazio | **novo** |
| | `ix_avaliacao_nps_depoimento_parcial` | `views/public.depoimentos` (página pública) | **novo** |
| prontuario | ~~`gin_prontuario_extras`~~ | nenhuma query de contenção (`@>`) no código; só `~Q(respostas_extras={})` | **removido** (recriar quando houver busca por resposta) |
| resposta_formulario | `ix_resposta_formulario_cliente_criado_em` | `utils/fichas.fichas_do_cliente`, `utils/saude.alertas_saude`, `services/alertas` (cliente IN …) | renomeia `idx_anamnese_resp_cli` |
| | `ix_resposta_formulario_atendimento`, `…_formulario` | FKs; `lgpd.fichas_sem_atendimento_para_purga` | ~~`idx_anamnese_resp_data`~~ **removido** (ninguém filtra `respondida_em` sem cliente) |
| catálogo (formulario, procedimento, profissional, promocao, pacote) | só UNIQUE e FKs | dezenas de linhas | **removidos** `idx_anamnese_ativo_escopo`, `idx_anamnese_ativo_tipo`, `formulario_anamnese.tipo` db_index, `idx_procedimento_ativo`, `idx_procedimento_categoria`, `procedimento.modalidade` db_index, `idx_profissional_ativo`, `idx_promocao_vigente` |
| preco | `uq_preco_vigencia` (prefixo procedimento) | `utils/precos.preco_para`, `preco_base_map` | ~~`idx_preco_lookup`~~ **removido** (mesmas colunas do UNIQUE) |
| aceite_termo | `uq_aceite_termo_cliente_versao`, `ix_aceite_termo_versao_termo` | `AceiteTermo.registrar`, `services/termos.termos_pendentes`, `VersaoTermo._validar_imutavel` e `fn_versao_termo_imutavel` (EXISTS por versão) | ~~`idx_aceite_cli_data`~~ **removido** (< 10 aceites por cliente; UNIQUE cobre) |
| codigo_otp | `ix_codigo_otp_identificador_proposito_criado_em` | `CodigoOtp.verificar` / `pode_reenviar` / `gerar` (identificador + propósito, mais recente) | **funde** `idx_otp_email_data` e `idx_otp_prop_data` (propósito sozinho não é seletivo; linhas vivem 24 h) |
| compra_pacote | `ix_compra_pacote_data_expiracao_ativo_parcial` | `sp_expirar_pacotes`, `tasks.job_verificar_pacotes_expirando` | **novo** |
| | `ix_compra_pacote_criado_em` | receita de pacotes (`dashboard`, `admin_financeiro`, `vw_faturamento_mensal`) | **novo** |
| | `ix_compra_pacote_cliente_status` | `signals.processar_mudanca_status` (débito de sessão), ficha do cliente | renomeia `idx_pacote_cli_status` |
| consumo_sessao | `ix_consumo_sessao_compra_pacote_item` | saldo por item (`fn_saldo_pacote`, `tg_consumo_sessao_validar`) | **novo** |
| movimento_carteira | `ix_movimento_carteira_carteira_criado_em`, `…_atendimento_parcial` | extrato; `fidelidade_service.estornar_cashback_de_atendimento` | ~~`idx_movcred_origem_tipo`~~ **removido** (sem query) |
| movimento_comissao | `ix_movimento_comissao_profissional_criado_em`, `…_criado_em`, `…_origem_parcial` | `relatorios.painel_comissoes` (período + profissional), `vw_comissao` (junta estorno/pagamento pela origem), `fn_saldo_comissao` | substituem `idx_comissao_prof_status` / `idx_comissao_criado` |
| log_auditoria | `ix_log_auditoria_tabela_registro` | `sp_anonimizar_cliente` / `LgpdService` (pseudonimização por tabela + ids), `admin.admin_auditoria` (filtro por tabela) | **substitui** `idx_auditoria_tabela` |
| | ~~`idx_auditoria_user_data`~~ | nenhuma tela filtra por usuário | **removido** |
| agenda_excecao | `ix_agenda_excecao_profissional_data_fim`, `…_profissional_data_hora_fim` | `SlotService` (folga/horário diferente/bloqueio do dia, `profissional_id = X OR IS NULL` — btree indexa NULL), `booking_api.api_dias_disponiveis` (mês), `admin_calendar` | **novos** (cobrem a FK) |

Total: **82 índices deixam de existir** (13 `_like`, 38 automáticos de FK, 25 do `Meta`, 5
`db_index`, 1 GIN), **23 renomeados** (15 do `Meta` + 8 automáticos de FK que viram `ix_` explícito),
**45 criados** (30 em tabelas existentes, 15 nas novas). Contagem conferida contra o inventário do
estado Django (`apps.get_models()`, 34 models) e o DDL acima.

---

## 3. Mapa de renomeação

### 3.1 Tabelas

| Antes | Depois | Como (migration) |
|---|---|---|
| `item_pacote` (ItemPacote) | `pacote_item` | `AlterModelTable` + `ALTER SEQUENCE item_pacote_id_seq RENAME TO pacote_item_id_seq` (0054) |
| `formulario_anamnese` (FormularioAnamnese) | `formulario` | idem (0054) — a tabela passa a guardar ANAMNESE, PESQUISA e PRONTUARIO |
| `resposta_anamnese` (RespostaAnamnese) | `resposta_formulario` | idem (0054) |
| `disponibilidade_profissional` | → `agenda_horario` | dados copiados, `DeleteModel` (0048) |
| `excecao_disponibilidade`, `bloqueio_agenda`, `feriado` | → `agenda_excecao` | dados copiados, `DeleteModel` (0048) |
| — | `agenda_horario`, `agenda_excecao`, `link_acesso`, `consentimento`, `compra_pacote_item`, `historico_alteracao` | `CreateModel` já com nomes finais (0048–0053, 0056) |

Os models Python **não** mudam de nome (`ItemPacote`, `FormularioAnamnese`, `RespostaAnamnese`
continuam), só `db_table`. Sequências com nome legado da 0032 (a PK continua a mesma, só o nome da
sequência): `otp_code_id_seq→codigo_otp_id_seq`, `credito_cliente_id_seq→carteira_id_seq`,
`movimento_credito_id_seq→movimento_carteira_id_seq`, `pacote_cliente_id_seq→compra_pacote_id_seq`,
`sessao_pacote_id_seq→consumo_sessao_id_seq`, `web_push_subscription_id_seq→assinatura_push_id_seq`,
`configuracao_sistema_id_seq→configuracao_id_seq`, `profissional_procedimento_id_seq→habilitacao_id_seq`
— renomeadas por introspecção (`pg_get_serial_sequence`) no §3.6.

### 3.2 Colunas — `Model.atributo → db_column` (64 renomeações em 25 tabelas)

O atributo Python é o mesmo; muda só a coluna (`db_column`). FK: o atributo é o nome do campo
(`indicado_por`), o `attname` segue `indicado_por_id` no Python.

| # | Tabela | `Model.atributo` | Coluna antes | Coluna depois | Obs. |
|---|---|---|---|---|---|
| 1 | usuario | `Usuario.ativo` | ativo | eh_ativo | |
| 2 | assinatura_push | `AssinaturaPush.user` | user_id | usuario_id | FK |
| 3 | | `AssinaturaPush.p256dh` | p256dh | chave_p256dh | |
| 4 | | `AssinaturaPush.auth` | auth | chave_auth | |
| 5 | | `AssinaturaPush.ativo` | ativo | eh_ativo | |
| 6 | log_auditoria | `LogAuditoria.usuario` | usuario_id | usuario_autor_id | FK |
| 7 | | `LogAuditoria.usuario_nome` | usuario_nome | usuario_autor_nome | |
| 8 | | `LogAuditoria.registro_id` | registro_id | registro_pk | `integer→bigint`; `IntegerField→BigIntegerField` |
| 9 | cliente | `Cliente.ativo` | ativo | eh_ativo | |
| 10 | | `Cliente.faltas_consecutivas` | faltas_consecutivas | qtd_faltas_consecutivas | |
| 11 | | `Cliente.bloqueado_online` | bloqueado_online | eh_bloqueado_online | |
| 12 | | `Cliente.foto_url` | foto_url | url_foto | |
| 13 | | `Cliente.indicado_por` | indicado_por_id | cliente_indicador_id | FK |
| 14 | profissional | `Profissional.foto_url` | foto_url | url_foto | |
| 15 | | `Profissional.bio` | bio | biografia | |
| 16 | | `Profissional.ativo` | ativo | eh_ativo | |
| 17 | | `Profissional.min_notice_horas` | min_notice_horas | antecedencia_minima_horas | |
| 18 | | `Profissional.max_advance_dias` | max_advance_dias | antecedencia_maxima_dias | |
| 19 | | `Profissional.gcal_refresh_token` | gcal_refresh_token | gcal_token_renovacao | |
| 20 | | `Profissional.gcal_calendar_id` | gcal_calendar_id | gcal_calendario_ref | não é FK → `_ref` |
| 21 | | `Profissional.gcal_sync_token` | gcal_sync_token | gcal_token_sincronizacao | |
| 22 | | `Profissional.gcal_ultimo_sync_em` | gcal_ultimo_sync_em | gcal_sincronizado_em | |
| 23 | procedimento | `Procedimento.buffer_minutos` | buffer_minutos | intervalo_minutos | |
| 24 | | `Procedimento.link_videoconferencia` | link_videoconferencia | url_videoconferencia | |
| 25 | | `Procedimento.imagem_destaque` | imagem_destaque | url_imagem_destaque | NULL→'' |
| 26 | | `Procedimento.ativo` | ativo | eh_ativo | |
| 27 | | `Procedimento.exige_retorno` | exige_retorno | tem_retorno_obrigatorio | |
| 28 | preco | `Preco.vigente_desde` | vigente_desde | data_inicio_vigencia | |
| 29 | promocao | `Promocao.desconto_percentual` | desconto_percentual | percentual_desconto | |
| 30 | | `Promocao.preco_promocional` | preco_promocional | valor_promocional | |
| 31 | | `Promocao.ativa` | ativa | eh_ativo | |
| 32 | | `Promocao.imagem_url` | imagem_url | url_imagem | NULL→'' |
| 33 | atendimento | `Atendimento.reagendado_de` | reagendado_de_id | atendimento_reagendado_id | FK (aponta p/ o antigo, `booking_reagendar.py:243`) |
| 34 | lista_espera | `ListaEspera.notificado` | notificado | eh_notificado | |
| 35 | avaliacao_nps | `AvaliacaoNPS.autoriza_publicacao` | autoriza_publicacao | eh_publicacao_autorizada | |
| 36 | | `AvaliacaoNPS.aprovado_publicacao` | aprovado_publicacao | eh_publicacao_aprovada | |
| 37 | | `AvaliacaoNPS.alerta_enviado` | alerta_enviado | eh_alerta_enviado | |
| 38 | prontuario | `Prontuario.respostas_extras` | respostas_extras | respostas | chaves JSON inalteradas |
| 39 | prontuario_versao | `ProntuarioVersao.autor` | autor_id | usuario_autor_id | FK |
| 40 | | `ProntuarioVersao.autor_nome` | autor_nome | usuario_autor_nome | |
| 41 | anotacao_sessao | `AnotacaoSessao.autor` | autor_id | usuario_autor_id | FK |
| 42 | | `AnotacaoSessao.autor_nome` | autor_nome | usuario_autor_nome | |
| 43 | formulario | `FormularioAnamnese.schema_json` | schema_json | perguntas | |
| 44 | | `FormularioAnamnese.ativo` | ativo | eh_ativo | |
| 45 | | `FormularioAnamnese.obrigatorio` | obrigatorio | eh_obrigatorio | |
| 46 | resposta_formulario | `RespostaAnamnese.respostas_json` | respostas_json | respostas | |
| 47 | | `RespostaAnamnese.respondida_em` | respondida_em | respondido_em | |
| 48 | versao_termo | `VersaoTermo.vigente_desde` | vigente_desde | data_inicio_vigencia | |
| 49 | | `VersaoTermo.ativa` | ativa | eh_ativo | |
| 50 | aceite_termo | `AceiteTermo.ip` | ip | ip_origem | |
| 51 | | `AceiteTermo.conteudo_sha256` | conteudo_sha256 | conteudo_hash | `varchar(64)→char(64) NULL`; `''→NULL` |
| 52 | codigo_otp | `CodigoOtp.email` | email | identificador | guarda também o pseudo-e-mail `sms+…` |
| 53 | | `CodigoOtp.tentativas` | tentativas | qtd_tentativas | `PositiveSmallIntegerField→SmallIntegerField` + CHECK |
| 54 | pacote | `Pacote.preco_total` | preco_total | valor_total | |
| 55 | | `Pacote.ativo` | ativo | eh_ativo | |
| 56 | pacote_item | `ItemPacote.quantidade_sessoes` | quantidade_sessoes | qtd_sessoes | |
| 57 | carteira | `Carteira.saldo` | saldo | valor_saldo | |
| 58 | movimento_carteira | `MovimentoCarteira.saldo_resultante` | saldo_resultante | valor_saldo_resultante | |
| 59 | | `MovimentoCarteira.observacoes` | observacoes | observacao | |
| 60 | | `MovimentoCarteira.usuario` | usuario_id | usuario_autor_id | FK |
| 61 | regra_comissao | `RegraComissao.valor` | valor | valor_fixo | |
| 62 | | `RegraComissao.ativo` | ativo | eh_ativo | |
| 63 | movimento_comissao | `MovimentoComissao.regra` | regra_id | regra_comissao_id | FK; `SET_NULL→PROTECT` (ledger imutável) |
| 64 | | `MovimentoComissao.observacoes` | observacoes | observacao | |

**Colunas removidas (19):** `atendimento.token_cancelamento`, `notificacao.token`,
`resposta_anamnese.token`, `cliente.token_descadastro`, `profissional.ics_token`,
`lista_espera.token_reserva`, `lista_espera.expira_em` (legado sem uso — ARCHITECTURE §6.2) → (b);
`cliente.aceita_comunicacao` + os 9 `cliente.consent_{email_marketing,whatsapp_nps,whatsapp_confirmacao}[_em|_ip]` → (c);
`movimento_comissao.status`, `movimento_comissao.pago_em` → (f).

**Colunas novas em tabelas existentes:** `compra_pacote.pacote_nome` (e), `consumo_sessao.compra_pacote_item_id`
(e), `movimento_comissao.tipo` / `movimento_comissao_origem_id` / `usuario_autor_id` (f); auditoria:
`criado_em` em usuario, profissional, procedimento, habilitacao, preco, promocao, prontuario,
versao_termo, pacote, pacote_item, configuracao; `atualizado_em` em usuario, assinatura_push,
profissional, procedimento, preco, promocao, notificacao, lista_espera, avaliacao_nps,
resposta_formulario, versao_termo, pacote, compra_pacote, configuracao (linhas antigas recebem o
instante da migration — o `COMMENT` da coluna diz isso).

**Nulidade (regra de texto):** `NULL→''` + `NOT NULL` em profissional.especialidade,
procedimento.descricao/descricao_longa/url_imagem_destaque, promocao.descricao/url_imagem,
preco.descricao, atendimento.descricao_preco, notificacao.mensagem, avaliacao_nps.comentario, os 5
textos do prontuario, configuracao.valor/descricao, log_auditoria.tabela; `''→NULL` em
cliente.email, lista_espera.email_contato, aceite_termo.conteudo_hash; `atualizado_em NULL→NOT NULL`
(backfill = `criado_em`) em atendimento, cliente, formulario.

### 3.3 Constraints e índices nomeados (antes → depois)

`RENAME` = só nome (`ALTER … RENAME`); `RECRIA` = definição muda; `DROP` = some; `→ tabela` = a
regra passa a outra tabela.

| Tabela | Antes | Depois | Ação |
|---|---|---|---|
| usuario | `chk_usuario_papel` | `ck_usuario_papel` | RENAME |
| profissional | `idx_profissional_ativo` | — | DROP |
| | `chk_profissional_min_notice_range` / `chk_profissional_max_advance_range` | `ck_profissional_antecedencia_minima` / `ck_profissional_antecedencia_maxima` | RENAME |
| disponibilidade_profissional | `chk_disponibilidade_dia_semana`, `chk_disponibilidade_hora_fim_apos_inicio`, `uniq_disponibilidade_prof_dia_hora` | `ck_agenda_horario_dia_semana`, `ck_agenda_horario_fim_apos_inicio`, `ex_agenda_horario_sobreposicao` | → agenda_horario (UNIQUE vira EXCLUDE) |
| excecao_disponibilidade | `idx_excecao_prof_data`, `uniq_excecao_prof_data_tipo`, `chk_excecao_horario_completo` | `ix_agenda_excecao_profissional_data_fim`, `ex_agenda_excecao_folga_sobreposta` + `uq_agenda_excecao_horario_diferente_dia`, `ck_agenda_excecao_horario_diferente` | → agenda_excecao |
| bloqueio_agenda | `chk_bloqueio_fim_maior_inicio` | `ck_agenda_excecao_bloqueio` | → agenda_excecao |
| feriado | `uq_feriado_data_escopo` | `uq_agenda_excecao_feriado` | → agenda_excecao |
| cliente | `idx_cliente_telefone` | — | DROP (coberto por `uq_cliente_telefone_ativo`) |
| | `idx_cliente_nome` | `ix_cliente_nome` | RENAME |
| | `idx_cliente_email_upper` + `uniq_cliente_email_ativo` (LOWER) | `uq_cliente_email_ativo` (UPPER, `WHERE deletado_em IS NULL`) | RECRIA (funde 2 em 1) |
| | `uniq_cliente_telefone_ativo` / `uniq_cliente_cpf_ativo` | `uq_cliente_telefone_ativo` / `uq_cliente_cpf_ativo` | RECRIA (predicado só `deletado_em IS NULL`; vazio vira NULL) |
| | `chk_cliente_nao_indica_si_mesmo` | `ck_cliente_nao_indica_a_si` | RENAME |
| | `chk_cliente_telefone_digits` / `chk_cliente_cpf_digits` [PG] | `ck_cliente_telefone_formato` / `ck_cliente_cpf_formato` | RECRIA (sem a exceção `= ''`) |
| procedimento | `idx_procedimento_ativo`, `idx_procedimento_categoria` | — | DROP |
| | `chk_procedimento_duracao_positiva` | `ck_procedimento_duracao_positiva` | RENAME |
| | `chk_procedimento_buffer_range` | `ck_procedimento_intervalo_0_120` | RENAME |
| | `chk_procedimento_categoria` / `chk_procedimento_modalidade` | `ck_procedimento_categoria` / `ck_procedimento_modalidade` | RENAME |
| | `chk_procedimento_prazo_retorno_coerente` | `ck_procedimento_retorno_coerente` | RENAME |
| preco | `idx_preco_lookup` | — | DROP (= colunas do UNIQUE) |
| | `chk_preco_valor_positivo` | `ck_preco_valor_nao_negativo` | RENAME |
| | `uniq_preco_vigencia` / `uniq_preco_base_vigencia` | `uq_preco_vigencia` / `uq_preco_base_vigencia` | RENAME |
| promocao | `idx_promocao_vigente` | — | DROP |
| | `chk_promocao_data_fim_valida` | `ck_promocao_periodo` | RENAME |
| | `chk_promocao_desconto_0_100` | `ck_promocao_percentual_desconto_0_100` | RENAME |
| | `chk_promocao_preco_positivo` | `ck_promocao_valor_promocional_nao_negativo` | RENAME |
| | `chk_promocao_desconto_xor_preco` | `ck_promocao_desconto_xor_valor` | RENAME |
| | `chk_promocao_geral_so_percentual` | `ck_promocao_geral_so_percentual` | RENAME |
| atendimento | `idx_atendimento_status`, `idx_atendimento_cli_status` | — (→ `ix_atendimento_cliente_data_hora_inicio`) | DROP |
| | `idx_atendimento_data` / `idx_atend_prof_data` | `ix_atendimento_data_hora_inicio` / `ix_atendimento_profissional_data_hora_inicio` | RENAME |
| | `chk_atendimento_status_v2` | `ck_atendimento_status` | RENAME |
| | `chk_atendimento_fim_apos_inicio` | `ck_atendimento_fim_apos_inicio` | RENAME |
| | `chk_retorno_tem_origem` / `chk_retorno_valor_zero` | `ck_atendimento_retorno_tem_origem` / `ck_atendimento_retorno_valor_zero` | RENAME |
| | `uniq_retorno_por_origem` | `uq_atendimento_retorno_por_origem` | RENAME (índice parcial) |
| | `excl_atendimento_sobreposicao` [PG] | `ex_atendimento_sobreposicao` | RENAME |
| notificacao | `idx_notificacao_tipo_status`, `idx_notif_nps_lookup` | — | DROP |
| | `idx_notificacao_criado` / `idx_notif_atend_tipo` | `ix_notificacao_criado_em` / `ix_notificacao_atendimento_tipo` | RENAME |
| lista_espera | `chk_lista_espera_turno` | `ck_lista_espera_turno` | RENAME |
| | `uniq_espera_ativa` | `uq_lista_espera_pendente` | RENAME (predicado segue a coluna renomeada) |
| | `idx_espera_cliente` | `ix_lista_espera_cliente` | RENAME |
| | `idx_espera_proc_data_notif` | — (→ `ix_lista_espera_procedimento_data_desejada`) | DROP |
| avaliacao_nps | `chk_avaliacao_nps_nota` | `ck_avaliacao_nps_nota_0_10` | RENAME |
| prontuario | `gin_prontuario_extras` [PG] | — | DROP |
| | `chk_prontuario_extras_objeto` [PG, NOT VALID] | `ck_prontuario_respostas_objeto` | RENAME + `VALIDATE` |
| prontuario_versao | `idx_pront_versao` | `ix_prontuario_versao_prontuario_criado_em` | RENAME |
| anotacao_sessao | `idx_anot_atn_criado` | `ix_anotacao_sessao_atendimento_criado_em` | RENAME |
| formulario | `idx_anamnese_ativo_escopo`, `idx_anamnese_ativo_tipo` | — | DROP |
| | `chk_formulario_schema_lista` [PG, NOT VALID] | `ck_formulario_perguntas_lista` | RENAME + `VALIDATE` |
| resposta_formulario | `idx_anamnese_resp_cli` / `idx_anamnese_resp_atend` | `ix_resposta_formulario_cliente_criado_em` / `ix_resposta_formulario_atendimento` | RENAME |
| | `idx_anamnese_resp_data` | — | DROP |
| | `chk_resposta_anamnese_objeto` [PG, NOT VALID] | `ck_resposta_formulario_respostas_objeto` | RENAME + `VALIDATE` |
| versao_termo | `chk_versao_termo_tipo` | `ck_versao_termo_tipo` | RENAME |
| | `uniq_termo_ativo_por_escopo` / `uniq_termo_ativo_global` | `uq_versao_termo_ativa_por_procedimento` / `uq_versao_termo_ativa_global` | RENAME |
| aceite_termo | `uniq_aceite_cliente_versao` | `uq_aceite_termo_cliente_versao` | RENAME |
| | `idx_aceite_cli_data` | — | DROP |
| codigo_otp | `idx_otp_email_data`, `idx_otp_prop_data` | — (→ `ix_codigo_otp_identificador_proposito_criado_em`) | DROP |
| | `chk_otp_tentativas_teto` | `ck_codigo_otp_qtd_tentativas_0_10` | RECRIA (`BETWEEN 0 AND 10`, absorve o CHECK do `PositiveSmallIntegerField`) |
| assinatura_push | `idx_pushsub_user_ativo` | — (→ `ix_assinatura_push_usuario`) | DROP |
| pacote | `chk_pacote_preco_positivo` / `chk_pacote_validade_positiva` | `ck_pacote_valor_total_nao_negativo` / `ck_pacote_validade_meses_positiva` | RENAME |
| pacote_item | `chk_item_pacote_qtd_positiva` | `ck_pacote_item_qtd_sessoes_positiva` | RENAME |
| compra_pacote | `idx_pacote_cli_status` | `ix_compra_pacote_cliente_status` | RENAME |
| | `chk_pacote_cliente_status` | `ck_compra_pacote_status` | RENAME |
| | `chk_compra_pacote_valor_pago` / `chk_compra_pacote_valor_reembolsado` | `ck_compra_pacote_valor_pago_nao_negativo` / `ck_compra_pacote_valor_reembolsado` | RENAME |
| consumo_sessao | `idx_sessao_pct_cli` | `ix_consumo_sessao_compra_pacote` | RENAME |
| carteira | `chk_carteira_saldo_nao_negativo` | `ck_carteira_valor_saldo_nao_negativo` | RENAME |
| movimento_carteira | `idx_movcart_cart` | `ix_movimento_carteira_carteira_criado_em` | RENAME |
| | `idx_movcred_origem_tipo` | — | DROP |
| | `uniq_cashback_indicacao_por_atendimento` | `uq_movimento_carteira_cashback_por_atendimento` | RENAME |
| regra_comissao | `chk_regra_comissao_percentual_xor_fixo` / `chk_regra_comissao_percentual_0_100` | `ck_regra_comissao_percentual_xor_valor_fixo` / `ck_regra_comissao_percentual_0_100` | RENAME |
| | `idx_regra_comissao_lookup` | — (→ `ix_regra_comissao_profissional_procedimento`, sem o booleano) | DROP |
| movimento_comissao | `uniq_comissao_ativa_por_atendimento` | `uq_movimento_comissao_credito_por_atendimento` (+ `…_estorno_por_origem`, `…_pagamento_por_origem`) | RECRIA (ledger, §4.f) |
| | `idx_comissao_prof_status` | — (→ `ix_movimento_comissao_profissional_criado_em`) | DROP |
| | `idx_comissao_criado` | `ix_movimento_comissao_criado_em` | RENAME |

CHECKs **novos** em tabelas existentes (todos com pré-checagem na migration: se houver linha
violando, a migration aborta listando os ids, padrão da 0035/0043): `ck_atendimento_valores_nao_negativos`,
`ck_cliente_qtd_faltas_nao_negativa`, `ck_cliente_email_nao_vazio`, `ck_notificacao_{tipo,canal,status,resposta}`
(hoje os choices só existem no Python), `ck_lista_espera_email_contato_nao_vazio`,
`ck_avaliacao_nps_aprovacao_exige_autorizacao`, `ck_formulario_{tipo,escopo,escopo_coerente,prontuario_global}`,
`ck_aceite_termo_conteudo_hash_formato`, `ck_codigo_otp_{canal,proposito,codigo_hash_formato}`,
`ck_procedimento_{retorno_minimo_dias,retorno_maximo_dias}_nao_negativo`/`duracao_retorno_nao_negativa`
(substituem os CHECK automáticos `*_check` dos `PositiveSmallIntegerField`),
`ck_movimento_carteira_{tipo,origem,valor_positivo,valor_saldo_resultante_nao_negativo}`,
`ck_regra_comissao_valor_fixo_nao_negativo`, `ck_log_auditoria_detalhes_objeto`,
`ck_prontuario_versao_dados_objeto`.

### 3.4 Triggers e funções legados

| Antes (0038/0046) | Depois | Observação |
|---|---|---|
| `bloquear_mutacao_ledger()` | `fn_bloquear_mutacao()` | genérica; passa a levantar SQLSTATE `23000` (Django → `IntegrityError`, capturável) em vez de `P0001` (`InternalError`) |
| `trg_movimento_carteira_imutavel` | `tg_movimento_carteira_imutavel` | |
| `aceite_termo_imutavel()` / `trg_aceite_termo_imutavel` | `fn_aceite_termo_imutavel()` / `tg_aceite_termo_imutavel` | **corpo reescrito**: cita `ip`/`conteudo_sha256` (renomeadas) |
| `versao_termo_imutavel()` / `trg_versao_termo_imutavel` | `fn_versao_termo_imutavel()` / `tg_versao_termo_imutavel` | colunas citadas não mudam |
| `prontuario_versao_imutavel()` / `trg_prontuario_versao_imutavel` | `fn_prontuario_versao_imutavel()` / `tg_prontuario_versao_imutavel` | **corpo reescrito**: cita `autor_id`/`autor_nome` |

Corpo de PL/pgSQL é texto: renomear coluna **não** atualiza a função (quebraria no 1º UPDATE).
Por isso a 0054 derruba os 3 triggers da 0046 **antes** dos renames e os recria no fim com os
nomes novos (§6). CHECK, EXCLUDE, índice, view e função SQL com corpo `BEGIN ATOMIC` guardam a
árvore já resolvida (por `attnum`) e seguem o rename sozinhos.

### 3.5 Objetos de nome automático → padrão

| Objeto | Nome hoje (padrão Django/PG; tabelas da 0032 com prefixo da tabela antiga) | Alvo |
|---|---|---|
| PK | `<tabela>_pkey` (ex.: `credito_cliente_pkey` em `carteira`) | `pk_<tabela>` |
| FK | `<t>_<col>_<hash8>_fk_<tabela_ref>_id`, truncado se > 63 (ex.: `lista_espera_profissional_desejad_c9c2ce7d_fk_profissio`, `pacote_cliente_cliente_id_…_fk_cliente_id`) | `fk_<tabela>_<coluna sem _id>`, recriada com a ação `ON DELETE` do §1.3 |
| UNIQUE de `OneToOneField` | `<t>_<col>_key` ou `<t>_<col>_<hash>_uniq` | `uq_<tabela>_<coluna sem _id>` (continua de campo: `OneToOneField` é sempre `unique`) |
| UNIQUE de campo (`unique=True`) e `unique_together` | `<t>_<col>_key`, `<t>_<cols>_<hash>_uniq` | vira `UniqueConstraint` no `Meta` com nome do padrão (§3.6.2) |
| `_like` (`varchar_pattern_ops`) | `<t>_<col>_<hash>_like` (13) | **DROP** |
| Índice de FK / `db_index` | `<t>_<col>_<hash8>` (46 + 5) | `ix_` explícito no `Meta` ou DROP (§2.11) |
| CHECK de `Positive*Field` | `<t>_<col>_check` (4) | `ck_…_nao_negativo` no `Meta` |
| Sequência | `<tabela_antiga>_id_seq` | `<tabela>_id_seq` |

### 3.6 Mecânica no Django (como aplicar sem o Django se perder)

#### 3.6.1 O Django reencontra objetos por introspecção — confirmado no código instalado

`django/db/backends/base/schema.py::_constraint_names` (Django 5.2.1, linha 1958) chama
`connection.introspection.get_constraints(cursor, db_table)` (PG: lê `pg_constraint` + `pg_index`,
`postgresql/introspection.py::get_constraints`) e filtra **por colunas e tipo**, nunca por nome. É
usado para: soltar FK (`remove_field`, l. 821; `_alter_field`, l. 958 — `foreign_key=True`),
soltar UNIQUE de campo (l. 981 — `unique=True, primary_key=False`, excluindo nomes do `Meta`),
soltar índice de `db_index` (l. 1039 — `type_=Index.suffix`, excluindo nomes do `Meta`), CHECK de
campo (l. 1058), `unique_together` (`_delete_composed_index`, l. 652) e PK (`_delete_primary_key`,
l. 2011). Sequência: `_get_sequence_name` (postgresql/schema.py l. 138) também é por introspecção.
**Logo, renomear PK, FK, UNIQUE de campo, CHECK de campo e sequência não atrapalha nenhuma
migration futura.**

Exceções (onde o Django usa **nome calculado**) e como o padrão as neutraliza:

1. **`_like`** — `postgresql/schema.py` l. 160 e l. 300 calculam
   `_create_index_name(tabela, [col], "_like")` e mandam `DROP INDEX IF EXISTS` (l. 25): se o
   nome real for outro (tabelas renomeadas na 0032), o índice fica **órfão calado**. Solução: não
   existe mais `unique=True`/`db_index=True` em `CharField` (tudo no `Meta`) e a 0055 apaga todo
   índice `varchar_pattern_ops`/`text_pattern_ops` por introspecção.
2. **`Meta.indexes`/`Meta.constraints`** — `RemoveIndex`/`RemoveConstraint`/`RenameIndex` usam o
   nome do estado. Solução: o nome do banco é **igual** ao do estado (as renomeações do §3.3 são
   feitas com `RenameIndex` ou com a operação `RenomearConstraint` abaixo, que muda estado e banco
   juntos).
3. **`unique_together` → `UniqueConstraint`** — `_delete_composed_index` exclui nomes do `Meta` e
   exige exatamente 1 constraint: com o UNIQUE já renomeado para o nome do `Meta`, um
   `AlterUniqueTogether(set())` comum falharia ("Found wrong number (0)"). Solução:
   `SeparateDatabaseAndState` (estado: `AlterUniqueTogether(set())` + `AddConstraint`; banco: só o
   `RENAME`).
4. **FK recriada pelo Django** — um `AlterField` futuro que mexa na FK (tipo, `null`, destino) solta
   a FK por introspecção e **recria com nome de hash e sem `ON DELETE`**. Não quebra nada, mas sai
   do padrão: o teste de conformidade (§3.6.4) reprova e o dev acrescenta `PadronizarNomes()` à
   migration (é idempotente). Quando o projeto for p/ Django 6.x, avaliar `on_delete=models.DB_CASCADE`
   / `DB_SET_NULL` (ação no estado) no lugar do `RunSQL` — conferir a release note na atualização.

#### 3.6.2 Receitas (operações de migration)

- **Coluna** (64): editar o model com `db_column=…`, rodar `makemigrations` (gera `AlterField`) e
  embrulhar em `SeparateDatabaseAndState(state_operations=[AlterField(...)],
  database_operations=[RunSQL('ALTER TABLE t RENAME COLUMN a TO b', 'ALTER TABLE t RENAME COLUMN b TO a')])`.
  Por quê: o `AlterField` de uma FK com coluna nova **solta e recria a FK** (revalida a tabela e
  volta com nome de hash); o `RENAME COLUMN` é só catálogo. Funciona no SQLite (≥ 3.25, que
  também reescreve índices/CHECK/FK que citam a coluna).
- **Tabela** (3): `AlterModelTable` normal (é `ALTER TABLE … RENAME TO`) + rename da sequência.
- **Índice do `Meta`** (15): `migrations.RenameIndex(model_name, new_name=…, old_name=…)` (Django ≥ 4.1).
  **Nome > 30 caracteres** (quase todo `ix_…` do padrão): o check `models.E034` limita
  `Index.name` a 30 por causa do Oracle e reprova o `manage.py check`. Usar
  `aranha_estetica.db.indices.Indice` — o mesmo `models.Index` com `max_name_length = 63` (limite
  real do PG; o teste de conformidade confere). Criado na 0048; o N2 troca os `models.Index` do
  `Meta` por `Indice` ao renomear.
- **Constraint do `Meta`**: operação própria `RenomearConstraint(model_name, antigo, novo)` em
  `aranha_estetica/db/operacoes.py` (módulo congelado — mudança = nova classe): `state_forwards`
  troca o nome no estado; no PG faz `ALTER TABLE … RENAME CONSTRAINT` ou, se o objeto for índice
  (UNIQUE parcial/de expressão vira `CREATE UNIQUE INDEX`, não constraint), `ALTER INDEX … RENAME`
  (decide por `pg_constraint`/`pg_class`); no SQLite faz `remove_constraint` + `add_constraint`
  (o SQLite não renomeia constraint; a tabela é refeita, volume de teste).
- **Campo único/indexado → `Meta`**: `SeparateDatabaseAndState` com estado
  `AlterField(unique=False/db_index=False)` + `AddConstraint`/`AddIndex`, e banco = rename do
  objeto existente (achado por introspecção de colunas) ou `CREATE` se não existir. Vale também
  para `Usuario.email` (`USERNAME_FIELD`): o check `auth.E003` aceita um `UniqueConstraint` total
  sobre o campo (`django/contrib/auth/checks.py` l. 74, `_meta.total_unique_constraints`), então
  `uq_usuario_email` no `Meta` não quebra o `check`.
- **Objetos automáticos**: `PadronizarNomes(tabelas=None)` (`RunPython` só-PG, idempotente,
  reversível por no-op) — para cada tabela do app: PK → `pk_<t>`; FK de 1 coluna → recria como
  `fk_<t>_<col sem _id>` com a ação do `on_delete` do model (`NOT VALID` + `VALIDATE`, tabelas
  pequenas); UNIQUE de 1 coluna fora do `Meta` → `uq_<t>_<col sem _id>`; sequência de `id` →
  `<t>_id_seq`; índices `*_pattern_ops` → `DROP`. Vai ao fim de cada migration que cria tabela
  (0048–0053, 0056) e na 0055. **Implementada na 0048** (`aranha_estetica/db/operacoes.py`). Achado
  da implementação: o `CreateModel` deixa FK, índices e UNIQUE parcial em
  `schema_editor.deferred_sql` (o Django só os cria **no fim da migration**) — a operação executa
  esse SQL pendente antes de introspectar; sem isso a FK da tabela recém-criada nascia depois, com
  nome de hash e sem `ON DELETE`. Consequência a lembrar nas fases seguintes: dentro da própria
  migration que cria a tabela, os índices/UNIQUE parciais do `Meta` só existem no PG depois do
  `PadronizarNomes` (a cópia de dados anterior a ele não é barrada por eles — a pré-checagem é
  quem garante).

#### 3.6.3 Collation no estado (recomendado, junto da 0054)

Hoje a collation `pt_br` está **fora do estado** (0038): um `AlterField` futuro em `nome` a
reseta. Proposta: `CharField(db_collation='pt_br')` em `Cliente/Profissional/Procedimento.nome` +
`connection_created` registrando no SQLite uma collation `pt_br` (`connection.create_collation`
com chave `unicodedata` sem acento/caixa) em `AppConfig.ready()`. O `AlterField` resultante no PG é
no-op (mesma collation). Pré-requisito: produção com ICU (o dono confirmou; sem ICU a 0038 teria
pulado e esta migration falharia — o ensaio do dump mostra).

#### 3.6.4 Teste de conformidade (`tests/test_nomes_padrao.py`, só PG)

Consulta o catálogo e falha com a lista de ofensores se: tabela do app sem `COMMENT`; objeto fora
dos regex `^pk_<t>$`, `^fk_<t>_`, `^uq_<t>_`, `^ck_<t>_`, `^ix_<t>_`, `^ex_<t>_`, `^tg_<t>_`,
funções/procedures do schema `public` fora de `^(fn|sp)_` (ignorando as de extensão via
`pg_depend.deptype = 'e'`), views fora de `^vw_`; nome > 63; FK sem índice cujo prefixo seja a
coluna (exceto a allowlist de autoria); `confdeltype` divergente do `on_delete` do model; tabela com
`atualizado_em` sem `tg_<t>_atualizado_em`; qualquer índice com opclass `*_pattern_ops`. Tabelas de
terceiros (§1.5) ficam fora por allowlist.

---

## 4. Reestruturações (a)–(f)

Todas: migration **reversível executável** (o `tests/test_migrations_dados.py` volta até a
`0028` e sobe de novo — cada `RunPython` tem reverso, cada `RunSQL` tem `reverse_sql`); DML
seguido de DDL chama `SET CONSTRAINTS ALL IMMEDIATE` no PG (padrão 0034–0047); se o dado legado
impede a regra nova, a migration **aborta com a lista de ids** (padrão 0035/0043) e o runbook traz
a pré-checagem SQL. Rollback de produção continua sendo restaurar o `pg_dump`.

### 4.a Agenda 4→2 (`agenda_horario` + `agenda_excecao`) — migration 0048

**Modelo** (DDL no §2.4). `models/agenda.py` novo com `AgendaHorario` e `AgendaExcecao`
(constantes `TIPO_FOLGA/HORARIO_DIFERENTE/BLOQUEIO`, `ORIGEM_*`; manager com `ativas()`,
`do_profissional_ou_global(prof)`, `cobrindo(data)`, `folga_global_em(data)`; método
`ocorrencias(inicio, fim)` = o atual `BloqueioAgenda.expandir_ocorrencias`, agora também p/ BLOQUEIO
pontual). `clean()` espelha os CHECKs e chama `normalizar_regra_recorrencia()` (parse com
`dateutil`, reescreve na forma canônica `FREQ;INTERVAL;BYDAY|BYMONTHDAY` e recusa o que estiver
fora do subconjunto — o mesmo que `ck_agenda_excecao_regra_recorrencia_suportada` exige no PG e que
`fn_ocorrencias_recorrencia` sabe expandir).

Decisões de modelagem:
- `dia_semana` passa a **ISO** (1=seg…7=dom): `date.isoweekday()` no Python, `extract(isodow)` no
  SQL. Hoje é 1=Dom…7=Sáb e o código converte (`isoweekday() % 7 + 1` em `disponibilidade.py` e
  `booking_api.py`).
- Várias janelas por dia continuam permitidas; sobreposição passa a ser proibida
  (`ex_agenda_horario_sobreposicao`; hoje só havia UNIQUE em `(prof, dia, hora_inicio)`).
- FOLGA vira **intervalo de datas** (`data_inicio..data_fim`, ex.: férias numa linha só);
  feriado = FOLGA com `profissional_id NULL` e `origem FERIADO_*`/`RECESSO`; feriado nacional em que
  a clínica abre = `eh_ativo=false` (hoje `bloqueia_agendamento=false`; sem essa linha o
  `carregar_feriados` recriaria o feriado todo mês).
- BLOQUEIO guarda instantes (`data_hora_*`), como hoje; recorrência só em BLOQUEIO.
- Importação do Google: `origem='GCAL'` + `origem_ref` = id do evento → upsert idempotente
  (`uq_agenda_excecao_gcal_evento`); hoje o `get_or_create` por `(prof, início, fim)` duplicava
  evento remarcado.

**Migração de dados** (`RunPython`, portável):

| Origem | Destino |
|---|---|
| `disponibilidade_profissional (prof, dia 1=Dom…7=Sáb, hi, hf)` | `agenda_horario (prof, dia_semana = ((dia + 5) % 7) + 1, hi, hf)` |
| `excecao_disponibilidade` FOLGA | `agenda_excecao` FOLGA, `data_inicio = data_fim = data`, `motivo = coalesce(motivo,'')`, origem MANUAL |
| `excecao_disponibilidade` HORARIO_DIFERENTE | HORARIO_DIFERENTE, `data_inicio = data_fim = data`, `hora_*` |
| `feriado (data, nome, escopo, bloqueia_agendamento, observacao, criado_em)` | FOLGA, `profissional NULL`, `data_inicio = data_fim = data`, `motivo = nome`, `observacao = coalesce(observacao,'')`, `origem = {NACIONAL→FERIADO_NACIONAL, LOCAL→FERIADO_LOCAL, CLINICA→RECESSO}`, `eh_ativo = bloqueia_agendamento`, `criado_em` preservado |
| `bloqueio_agenda` | BLOQUEIO **com o mesmo `id`**, `data_hora_*`, `motivo`, `regra_recorrencia = normalizada ou NULL` (`''→NULL`), `data_fim_recorrencia = recorrencia_ate`, `origem = GCAL se motivo LIKE 'gcal:%' senão MANUAL` |

Ordem: bloqueios primeiro com `id` explícito → `setval(pg_get_serial_sequence('agenda_excecao','id'),
max(id))` no PG → exceções e feriados com ids novos. Um único `LogAuditoria`
(`acao='migration 0048: agenda unificada'`) guarda em `detalhes` o mapa
`{excecao_disponibilidade: {id_antigo: id_novo}, feriado: {…}}` — os logs antigos com
`tabela='excecao_disponibilidade'|'feriado'` continuam rastreáveis sem reescrever a trilha;
`bloqueio_agenda` mantém os ids.

Aborta (lista os ids) se: (1) duas janelas do mesmo profissional/dia se sobrepõem; (2) regra de
recorrência que o `normalizar_regra_recorrencia` não consegue representar no subconjunto.
Pré-checagem para o runbook (schema 0026, onde as tabelas já têm estes nomes):

```sql
SELECT a.id, b.id, a.profissional_id, a.dia_semana
  FROM disponibilidade_profissional a JOIN disponibilidade_profissional b
    ON a.profissional_id = b.profissional_id AND a.dia_semana = b.dia_semana AND a.id < b.id
   AND a.hora_inicio < b.hora_fim AND b.hora_inicio < a.hora_fim;
SELECT id, regra_recorrencia FROM bloqueio_agenda WHERE coalesce(regra_recorrencia, '') <> '';
```

**Reversibilidade:** o reverso recria as 4 tabelas (reverso do `DeleteModel`) e copia de volta:
grade (dia ISO → `(dia % 7) + 1`), FOLGA de profissional expandida em uma linha por data, FOLGA
global → `feriado` (origem → escopo; `MANUAL` → `CLINICA`), BLOQUEIO → `bloqueio_agenda` (mesmo id).
Perde só o que o modelo antigo não representa (exceção inativa de profissional, `origem_ref`) —
fica registrado em `LogAuditoria`.

**`SlotService` (services/disponibilidade.py) — o que muda:** de 5 consultas para 3, mesma regra.

```python
excecoes = list(
    AgendaExcecao.objects.ativas().do_profissional_ou_global(profissional).filter(
        Q(tipo__in=(FOLGA, HORARIO_DIFERENTE), data_inicio__lte=dia, data_fim__gte=dia)
        | Q(tipo=BLOQUEIO, data_hora_inicio__lt=dia_fim)
          & (Q(regra_recorrencia__isnull=False) | Q(data_hora_fim__gt=dia_inicio))
    )
)
if any(e.tipo == FOLGA for e in excecoes):          # folga do profissional OU feriado/recesso
    return []
hd = next((e for e in excecoes if e.tipo == HORARIO_DIFERENTE), None)  # só existe c/ profissional
janelas = ([(hd.hora_inicio, hd.hora_fim)] if hd else list(
    AgendaHorario.objects.filter(profissional=profissional, dia_semana=dia.isoweekday())
    .values_list('hora_inicio', 'hora_fim')))
bloqueios = [iv for e in excecoes if e.tipo == BLOQUEIO for iv in e.ocorrencias(dia_inicio, dia_fim)]
# atendimentos ocupados, laço de slots, min_notice/max_advance: inalterados
```

Os testes de caracterização (`tests/test_slots_disponibilidade.py`) ficam como estão (só as
fábricas passam a criar `AgendaHorario`/`AgendaExcecao`); `fn_horarios_livres` (§5.5) é o espelho
SQL, com teste de paridade.

**Impacto no código (planejado):** `models/profissionais.py` (sai `DisponibilidadeProfissional`,
`BloqueioAgenda`, `ExcecaoDisponibilidade`), `models/sistema.py` (sai `Feriado`), `models/__init__.py`;
`services/disponibilidade.py` (acima); `views/booking_api.api_dias_disponiveis` (grade ISO + folgas
por intervalo + feriado global); `views/booking_public.confirmar_agendamento` (l. 397) e
`views/booking_reagendar.reagendar_agendamento` (l. 183): `Feriado.objects.filter(...)` →
`AgendaExcecao.objects.folga_global_em(data)`; `views/admin_calendar` (eventos de bloqueio/folga e
`admin_calendar_mover`, que passa a expandir recorrência — hoje não expande, comentado como
"débito menor"); `views/admin_excecoes` (CRUD; FOLGA com intervalo); `views/admin_management`
(`admin_bloqueios`/`admin_criar_bloqueio`/`admin_excluir_bloqueio` → BLOQUEIO); `views/admin_professional`
(`_dias_do_profissional`, `_salvar_janelas`, `DIA_NUMERO` em ISO); `views/dashboard.painel_overview`
(capacidade semanal); `services/gcal.pull_eventos_externos` (upsert por `origem_ref`);
`management/commands/carregar_feriados.py` (`get_or_create(origem=FERIADO_NACIONAL, data_inicio=…)`);
`management/commands/seed.py`; `admin.py` (inline de grade, admin de exceções); templates
`painel/bloqueios.html`, `painel/excecoes*.html`, `painel/cadastro_profissional.html`/`editar_profissional.html`;
fábricas e testes que criam os models antigos.

**Implementação (0048) — o que ficou diferente do desenho acima, e por quê:**

- **Nome só no banco nos models novos também:** `AgendaExcecao.ativo` → coluna `eh_ativo`
  (como `Cliente.ativo`/`Profissional.ativo` serão na 0054); `usuario_autor`, `related_name`
  `agenda_horarios`/`agenda_excecoes`; `criado_em` com `default=timezone.now` + `db_default=Now()`
  (não `auto_now_add`, que sobrescreveria o `criado_em` preservado do feriado na cópia). FKs sem
  índice automático (`db_index=False`): cobertas pelo gist do EXCLUDE (`agenda_horario`) e pelos
  `ix_` do `Meta` (`agenda_excecao`), como no §2.4. `db_table_comment`/`db_comment` já na 0048.
- **`models.E034`:** os `ix_agenda_excecao_*` passam de 30 caracteres → `Indice` (§3.6.2).
- **`PadronizarNomes`** implementada na 0048 e executa o `deferred_sql` pendente (§3.6.2).
- **Pré-checagem** num `RunPython` próprio antes da cópia (aborta só nos 2 casos do spec, com os
  ids). **Saneamentos sem abortar** (comportamento preservado; tudo no `detalhes` do
  `LogAuditoria` da migration): `HORARIO_DIFERENTE` legado com fim ≤ início (anterior à validação da
  tela; zerava o dia) vira `FOLGA` — ou é descartado se já há FOLGA no dia; FOLGA com horas (o CHECK
  antigo aceitava, o motor ignorava) perde as horas; `recorrencia_ate` sem regra é descartado;
  escopo de feriado desconhecido vira `RECESSO`; `motivo 'gcal:%'` só vira `origem=GCAL` com
  profissional (senão violaria `ck_agenda_excecao_gcal`).
- **RRULE** (`aranha_estetica/utils/recorrencia.py`, `normalizar_regra_recorrencia(regra, dtstart)`):
  aceita e canoniza `RRULE:`/minúsculas/`;` final/`WKST=MO`/`INTERVAL=1`/BYDAY repetido ou fora de
  ordem; ordinal em BYDAY de DAILY/WEEKLY é descartado (o dateutil já o ignorava); **COUNT/UNTIL
  viram `data_fim_recorrencia`** (data local da última ocorrência — exato, pois a regra gera no
  máximo 1 ocorrência por dia; combinado por `min` com o `recorrencia_ate`). A regex do CHECK fica
  congelada na migration e um teste confere que é igual a `REGEX_SUBCONJUNTO`. O motor
  (`AgendaExcecao.ocorrencias`) é o `expandir_ocorrencias` de antes (DTSTART local, fim inclusivo),
  agora também para BLOQUEIO pontual e para FOLGA (dias inteiros locais; HORARIO_DIFERENTE → `[]`).
- **`LogAuditoria` da migration** só em banco que opera (há cliente) ou com exceção/bloqueio/aviso
  (padrão 0045/0047): instalação nova não ganha log só pelos feriados semeados da 0012.
- **QuerySet:** `ativas()`, `do_profissional_ou_global(prof|pk)`, `cobrindo(dia)`,
  `folga_global_em(dia)` (devolve QuerySet: `…folga_global_em(d).exists()`) e
  `bloqueios_no_intervalo(inicio, fim)`. `clean()` espelha os CHECKs e os 2 EXCLUDE (mensagem
  legível no painel/admin; o banco continua sendo a garantia).
- **Consumidores:** `api_dias_disponiveis` também tira o dia de FOLGA global manual ("clínica
  fechada") e ignora a inativa; o calendário mostra feriado/recesso (antes não mostrava), expande
  bloqueio recorrente (ids `bloq-<pk>-<n>`) e, com filtro de profissional, inclui os globais; o
  `admin_calendar_mover` expande recorrência (§7.3 item 4), mas a checagem de folga continua só a
  do profissional (feriado não trava o arrastar, como antes); painel de exceções ganhou "Até"
  (FOLGA em intervalo; reenviar o mesmo período atualiza; sobreposição é recusada); importação do
  Google faz upsert por `origem_ref` e **adota** a linha importada antes da 0048 (mesmo
  início/fim, `origem_ref` nulo) em vez de duplicar; `motivo` mantém o prefixo `gcal:`.
- **SQLite dos testes:** `fields.W163`/`models.W046` (COMMENT só existe no PG) silenciados em
  `settings/dev.py` (modo teste).
- **Testes:** `tests/test_migrations_dados.py` (dados antigos → novos, reverso, aborto nos 2 casos
  e caracterização "mesmos slots antes/depois" contra uma cópia congelada do motor 0047),
  `tests/test_agenda_unificada.py`, `tests/test_pg_ddl.py` (EXCLUDE/CHECK/nomes/`ON DELETE`). O
  ensaio 0026 → 0048 em PG com dados sujos deu os mesmos slots (736 combinações
  profissional × dia × procedimento) pelo motor 0047 e pelo `SlotService` novo.

### 4.b Tokens em hash (`link_acesso`) — migration 0049

**Inventário (grep):** tokens em texto puro hoje: `atendimento.token_cancelamento` (UNIQUE +
`_like`), `notificacao.token` (UNIQUE + `_like`), `resposta_anamnese.token` (UNIQUE + `_like`),
`cliente.token_descadastro` (`db_index` + `_like`), `profissional.ics_token` (UNIQUE + `_like`) e o
legado sem uso `lista_espera.token_reserva`. Fora do escopo por não serem "links": `codigo_otp`
(já HMAC), `usuario.senha_hash` (Django), `gcal_refresh_token`/`gcal_sync_token` (segredos que
precisam ser lidos — ver §7), `assinatura_push.auth/p256dh` (chaves do navegador, precisam ser lidas).

**Modelo** (DDL no §2.7) + `services/links.py`:

```python
def sha256_hex(token: str) -> str: return hashlib.sha256(token.encode('utf-8')).hexdigest()

def emitir(finalidade, alvo, *, expira_em=None) -> str:
    """Cria o link e devolve o token EM CLARO (única vez em que ele existe)."""
    token = secrets.token_urlsafe(32)
    LinkAcesso.objects.create(finalidade=finalidade, token_hash=sha256_hex(token),
                              expira_em=expira_em, **{CAMPO_ALVO[finalidade]: alvo})
    return token

def resolver(finalidades, token, *, select_related=()) -> 'LinkAcesso | None':
    if not token or len(token) > 128:
        return None
    link = (LinkAcesso.objects.select_related(*select_related)
            .filter(finalidade__in=finalidades, token_hash=sha256_hex(token), revogado_em__isnull=True)
            .first())
    if link is None or (link.expira_em and link.expira_em <= timezone.now()):
        return None
    return link

def marcar_usado(link): LinkAcesso.objects.filter(pk=link.pk, usado_em__isnull=True).update(usado_em=timezone.now())
def revogar(finalidade, alvo): LinkAcesso.objects.filter(finalidade=finalidade, **{CAMPO_ALVO[finalidade]: alvo},
                                                        revogado_em__isnull=True).update(revogado_em=timezone.now())
```

A busca é por igualdade do hash (índice único), então não há comparação de token em Python (o
`hmac.compare_digest` do ICS some). sha256 **sem** chave: o token tem 256 bits (não há força bruta)
e a SECRET_KEY pode ser rotacionada num incidente sem derrubar links enviados.

| Finalidade | Alvo | Rota (view) | Emitido em | `expira_em` | Uso |
|---|---|---|---|---|---|
| `GERENCIAR_AGENDAMENTO` | atendimento | `/reagendar/<token>/` (`booking_reagendar.reagendar_agendamento`, `reagendar_horarios`), `POST /api/cancelar` (`booking_api.cancelar_agendamento`) | **só legado** (migrado); novos não são emitidos (ver UX) | `data_hora_inicio` | múltiplo |
| `CONFIRMAR_PRESENCA` | notificacao LEMBRETE/LEMBRETE_2H/CONFIRMACAO | `/confirmar/<token>/` (`notificacoes.confirmar_presenca`) | `utils/whatsapp.enviar_confirmacao_d1` | início do atendimento | resposta única (`notificacao.resposta`) |
| `NPS` | notificacao NPS | `/nps/<token>/` (`admin_management.nps_web`) | `tasks.job_pesquisa_satisfacao_24h` | `criado_em + 7 dias` (`NPS_TOKEN_EXPIRY`) | 1 avaliação |
| `TERMO` | notificacao TERMO (e legado LEMBRETE+EMAIL) | `/termo/<token>/` (`admin_management.termo_assinatura`) | `services/termos.obter_link_termo` | fim do atendimento | até aceitar |
| `FORMULARIO` | resposta_formulario | `/anamnese/<token>/`, `/pesquisa/<token>/` (`anamnese_publica._get_resposta_or_404`) | convite (hoje sem produtor — D18) | `criado_em + 60 dias` | até responder |
| `DESCADASTRO` | cliente | `/lgpd/unsubscribe/<token>/` (`lgpd.unsubscribe`) + header `List-Unsubscribe` | cada e-mail de marketing (`tasks.job_aniversario_clientes`, `job_promocao_mensal`, `admin_promotions`) | NULL | múltiplo |
| `FEED_ICS` | profissional | `/agenda/<slug>/feed.ics?token=` (`agenda_publica.ics_feed_profissional`) | painel "Gerar link do calendário" | NULL | múltiplo |

**Migração que mantém válidos os links já enviados** (`RunPython`, `hashlib` — portável):

```python
def migrar_tokens(apps, schema_editor):
    L = apps.get_model('aranha_estetica', 'LinkAcesso')
    Atendimento = apps.get_model('aranha_estetica', 'Atendimento')
    Notificacao = apps.get_model('aranha_estetica', 'Notificacao')
    h = lambda t: hashlib.sha256(t.encode('utf-8')).hexdigest()
    novos = []
    for a in Atendimento.objects.exclude(token_cancelamento__isnull=True).exclude(token_cancelamento='') \
            .values('id', 'token_cancelamento', 'data_hora_inicio'):
        novos.append(L(finalidade='GERENCIAR_AGENDAMENTO', atendimento_id=a['id'],
                       token_hash=h(a['token_cancelamento']), expira_em=a['data_hora_inicio']))
    for n in Notificacao.objects.exclude(token__isnull=True).exclude(token='').select_related('atendimento'):
        fin = ('NPS' if n.tipo == 'NPS'
               else 'TERMO' if n.tipo == 'TERMO' or (n.tipo == 'LEMBRETE' and n.canal == 'EMAIL')
               else 'CONFIRMAR_PRESENCA' if n.tipo in ('LEMBRETE', 'LEMBRETE_2H', 'CONFIRMACAO')
               else None)                           # nenhuma rota aceita os demais: não migra (conta no log)
        if fin:
            expira = {'NPS': n.criado_em + timedelta(days=7), 'TERMO': n.atendimento.data_hora_fim,
                      'CONFIRMAR_PRESENCA': n.atendimento.data_hora_inicio}[fin]
            novos.append(L(finalidade=fin, notificacao_id=n.pk, token_hash=h(n.token),
                           expira_em=expira, usado_em=n.respondido_em))
    # resposta_anamnese.token -> FORMULARIO (criado_em + 60 dias, usado_em = respondida_em)
    # cliente.token_descadastro -> DESCADASTRO; profissional.ics_token -> FEED_ICS (sem expiração)
    L.objects.bulk_create(novos, batch_size=500)
```

Depois: `RemoveField` das 7 colunas (o `DROP COLUMN` leva junto os UNIQUE e os `_like`, inclusive
os de nome legado). `criado_em` dos links migrados = instante da migration (a validade já foi
calculada a partir das datas originais).

**Reversibilidade:** o reverso recria as colunas e grava **tokens novos** (hash não volta a
texto) — links antigos deixam de funcionar num rollback de dev; em produção o rollback é o dump.

**UX / impacto no código:**
- **Feed ICS** (`templates/painel/profissionais.html` l. 84–88 mostra hoje a URL com o token): o
  token só existe na geração. "Gerar link do calendário" (`admin_professional.profissional_rotacionar_ics`)
  passa a **renderizar** (não redirecionar — nada de token em URL de redirect/log) uma página com a
  URL completa, botão copiar e o aviso "guarde agora: por segurança o link não é exibido de novo";
  gerar outro revoga o anterior (mesma semântica da rotação atual). A lista mostra "Link ativo desde
  dd/mm/aaaa" (links migrados: "ativo, criado antes da atualização") ou "Sem link". O profissional
  pode gerar o próprio link no portal.
- **Meus agendamentos** (`templates/agenda/meus_agendamentos.html` l. 544–561 e `agenda/reagendar.html`
  l. 170 leem `ag.token_cancelamento`): a página já é autenticada por OTP (sessão com o telefone,
  `booking_otp.SESSAO_MEUS_AGENDAMENTOS`). Reagendar/cancelar passam a usar **o id + a sessão**
  (`/meus-agendamentos/<id>/reagendar/`, `POST /api/cancelar {atendimento_id}` com checagem
  `cliente__telefone = sessão`, anti-IDOR) — nenhum token novo é emitido. `/reagendar/<token>/` e o
  cancelamento por token ficam só para os links migrados, até expirarem.
- **Termo** (`services/termos.obter_link_termo` "reusa o link já emitido"): não dá para reexibir
  um link antigo. Proposta (D1 no §7): cada clique em "Gerar link do termo" emite um novo; os
  anteriores continuam válidos até o fim do atendimento (a validade natural limita a exposição).
- **Descadastro:** cada e-mail de marketing emite o seu token (`utils/email.enviar_aniversario_email`
  / `enviar_promocao_email` recebem `unsub_token=links.emitir('DESCADASTRO', cliente)`);
  `LgpdService.cliente_por_token_descadastro` → `links.resolver(['DESCADASTRO'], token)`;
  anonimização revoga todos os links do titular.
- **Confirmação / NPS / termo / ficha:** `enviar_confirmacao_d1` (`utils/whatsapp.py` l. 163–187)
  cria a Notificacao e depois `links.emitir('CONFIRMAR_PRESENCA', notif, expira_em=…)` — o retry por
  colisão de UNIQUE some; `enviar_nps_whatsapp(atendimento, link_nps, token_notif)` (l. 212/235)
  passa a receber a `notificacao` (hoje procura pelo token); `confirmar_presenca`, `nps_web`,
  `termo_assinatura`, `_get_resposta_or_404` trocam `get_object_or_404(..., token=token)` por
  `links.resolver(...)` + 404; `RespostaAnamnese.get_link_publico()` vira
  `links.emitir('FORMULARIO', resposta, …)` no momento do convite.
- Saem: `Atendimento.save()` (gera token), `Cliente.save()` (gera token), `Profissional.save()`
  (gera ics), `_gen_resposta_token`; `admin.py` (campos `token_*` em readonly/fieldsets); `urls.py`
  (rotas novas do portal). `utils/pii.redigir_tokens_url` continua valendo (URLs em log).

**Implementação (0049) — o que ficou diferente do desenho acima, e por quê:**

- **Ordem da migration:** `CreateModel` → `AlterField resposta_anamnese.token` para NULL → `RunPython`
  (hash + `LogAuditoria`, termina com `SET CONSTRAINTS ALL IMMEDIATE`) → PG: CHECK de formato,
  `fn_link_acesso_imutavel` + `tg_link_acesso_imutavel` → `PadronizarNomes(['link_acesso'])` →
  `RemoveField` ×7. O `AlterField` existe só para o **reverso**: recriar uma coluna NOT NULL
  UNIQUE com default callable daria o mesmo token a todas as linhas; nula, o reverso grava um
  token por linha e o `AlterField` reverso volta ao NOT NULL.
- **Notificação → finalidade = a rota que aceitava o token antes** (nada passa a abrir onde dava
  404): `NPS` (qualquer canal — `/nps/` filtrava só o tipo); `TERMO` ou `LEMBRETE`+`EMAIL` →
  TERMO; `LEMBRETE`/`LEMBRETE_2H`/`CONFIRMACAO` **só com canal WHATSAPP** → CONFIRMAR_PRESENCA
  (o `/confirmar/` exigia WhatsApp; o código do desenho mapeava qualquer canal). O resto não migra
  (ids em `notificacao_sem_rota_nao_migrada` no log).
- **Descadastro:** só cliente não excluído (o resolver antigo usava o manager de ativos); o
  `LgpdService.link_descadastro` também recusa link de cliente com `deletado_em`.
- **`usado_em` do NPS** = `respondido_em` ou, sem ele, `avaliacao_nps.criado_em` do atendimento (a
  avaliação nunca preenchia `respondido_em`).
- **Token repetido entre tabelas** (dado sujo: o UNIQUE era por tabela): fica o primeiro na ordem
  atendimento → notificação → ficha → cliente → profissional; os outros em
  `token_repetido_descartado`. `LogAuditoria` `migration 0049: links publicos em hash
  (link_acesso)` com a contagem por finalidade — só quando migrou algo ou há aviso.
- **`resolver(..., aceitar_expirado=False)`:** com `True` devolve o link vencido (nunca o
  revogado) para a view mostrar a página amigável em vez de 404 — NPS 410 "link expirado"; termo
  410 pela regra `aceita_assinatura` sobre o fim **atual** do atendimento; confirmação só leitura;
  reagendar/cancelar legado com a mensagem de horário. Nenhuma ação é executada com link vencido.
  Motivo: `expira_em` é fixado na emissão (e o trigger proíbe mudar), mas o atendimento pode ser
  movido no calendário — "link do termo vale até o fim do atendimento" continua valendo para o
  horário novo, como antes. FORMULARIO segue estrito (404, como o convite de 60 dias antigo).
- **Serviço (`services/links.py`) — extras além do desenho:** `gerar_token`, `descartar(token)`
  (e-mail de marketing que não saiu: o link, que nunca deixou o servidor, é apagado — via
  `utils.email.enviar_marketing_com_descadastro`), `vigente()`, `revogar_do_cliente()`
  (anonimização; espelho do passo 3 do `sp_anonimizar_cliente`), `gerar_link_feed_ics()` (revoga +
  emite numa transação), `emitir_link_formulario()` (substitui `RespostaAnamnese.get_link_publico`)
  e `aplicada_em_0049()` (o painel diz "criado antes da atualização" para o link migrado).
- **`HashSha256Field`** (`aranha_estetica/db/campos.py`, `char(64)`, sem `_like`). Achado no ensaio
  PG: o `bulk_create` do Django 5.2 no Postgres usa `UNNEST` e tira o tamanho do tipo no cast do
  array (`char(64)[]` → `char[]` = `char(1)[]`) — **truncava o hash para 1 caractere**. O campo
  define `get_placeholder`, o que faz o Django voltar ao `INSERT … VALUES` (teste em `test_pg_ddl`).
- **Termo (D1):** `obter_link_termo` reaproveita a Notificação TERMO do atendimento (1 por
  atendimento) e emite um link novo a cada clique, com `expira_em` = fim do atendimento; a
  auditoria sempre registra "Gerou link do termo" (`notificacao_nova` nos detalhes).
- **ICS:** a geração renderiza `painel/ics_link.html` (URL completa + copiar + aviso "guarde
  agora"); a lista mostra "Link ativo desde dd/mm/aaaa", "Link ativo (criado antes da
  atualização)" ou "Sem link" + "Gerar link". O profissional gera o próprio no portal
  (`POST /profissional/calendario/novo-link/`, botão "Link do calendário" em Minha agenda). O 1º
  acesso bem-sucedido ao feed preenche `usado_em`. `Profissional.save()` não gera mais token:
  profissional novo começa "Sem link".
- **Portal:** `/meus-agendamentos/<id>/reagendar/` e `/meus-agendamentos/<id>/horarios/`;
  `POST /ajax/cancelar-agendamento/` aceita `{"atendimento_id"}` (sessão do OTP, só cliente ativo
  com o telefone da sessão) ou `{"token"}` (link legado). A tela de reagendar recebe
  `url_horarios` (portal ou legado).
- **Cascata conta no `delete()`:** `LgpdService.purgar_fichas_sem_atendimento` passou a contar só
  as fichas (o total do `delete()` incluiria os `link_acesso` apagados em cascata).
- **Admin:** `LinkAcessoAdmin` somente leitura (sem o hash) com a ação "Revogar"; o
  `ProfissionalAdmin` deixou de expor `ics_token` editável. `_gen_resposta_token` fica no model
  (a 0023 referencia).
- **Fora desta fase:** purga de links vencidos/revogados (fica no `sp_purgar_dados_expirados`, L3 —
  até lá as linhas ficam); regerar os UML/Requisitos (`docs/gerar_requisitos.py`; os diagramas
  ainda mostram as tabelas de agenda anteriores à 0048 e os campos `token_*`).
- **Testes:** `tests/test_links_acesso.py` (serviço: hash, finalidade errada, adulterado,
  revogado, vencido, uso; CHECK de alvo; matriz rota × finalidade = 404),
  `tests/test_migrations_dados.py` (`LinksEmHash0049Tests`: hash/validade/uso/não migrados/log/
  colunas e as rotas públicas abrindo com os tokens antigos; `LinksEmHashReversa0049Tests`),
  `tests/test_pg_ddl.py` (nomes, `char(64)`, `ON DELETE CASCADE`, CHECK de formato, trigger,
  `bulk_create`), regressões dos fluxos (portal, confirmação, termo, NPS, ficha, descadastro, ICS,
  anonimização). Ensaio 0026 → 0049 (`migrate_atomico`, 23 migrations numa transação) em PG com
  banco de produção simulado no código 0026 (dados sujos que 0034/0035/0043/0048 tratam + tokens
  de todos os tipos: 49 tokens, 43 migrados, 6 sem rota — 5 notificações e o cliente excluído):
  todos resolvem pelo token em claro e as rotas respondem como antes (200/302/400/410; 404 onde já
  era 404); do zero ok; reverso 0049 → 0048 → 0049 ok.

### 4.c Consentimentos com histórico (`consentimento`) — migration 0050

**Modelo** (DDL no §2.7). Finalidade × canal (os 3 pares que existem hoje, fechados por
`ck_consentimento_finalidade_canal` — um par novo = migration que amplia o CHECK):

| Coluna antiga em `cliente` | `finalidade` | `canal` | Onde é exigido hoje |
|---|---|---|---|
| `consent_email_marketing` (+`_em`, `_ip`) | MARKETING | EMAIL | `tasks.job_aniversario_clientes`, `tasks.job_promocao_mensal`, `views/admin_promotions` |
| `consent_whatsapp_nps` (+`_em`, `_ip`) | PESQUISA_SATISFACAO | WHATSAPP | `tasks.candidatos_nps` |
| `consent_whatsapp_confirmacao` (+`_em`, `_ip`) | LEMBRETE_AGENDAMENTO | WHATSAPP | `tasks.job_enviar_lembrete_dia_seguinte`, `services/lista_espera_service` (aviso de vaga) |

`services/consentimentos.py`:

```python
PARES = {'consent_email_marketing': ('MARKETING', 'EMAIL'),
         'consent_whatsapp_nps': ('PESQUISA_SATISFACAO', 'WHATSAPP'),
         'consent_whatsapp_confirmacao': ('LEMBRETE_AGENDAMENTO', 'WHATSAPP')}

def estado_atual(cliente) -> dict[tuple[str, str], bool]          # 1 query
def vigente(cliente, finalidade, canal) -> bool
def registrar(cliente, finalidade, canal, concedido, *, origem, texto='', request=None, usuario=None):
    """Append-only; no-op se o estado vigente já é `concedido` (não polui o histórico)."""
def anotar_vigente(qs, finalidade, canal, *, caminho='cliente', nome='tem_consentimento'):
    ultimo = (Consentimento.objects.filter(cliente=OuterRef(caminho), finalidade=finalidade, canal=canal)
              .order_by('-registrado_em', '-id').values('eh_concedido')[:1])
    return qs.annotate(**{nome: Coalesce(Subquery(ultimo), Value(False))})
```

`anotar_vigente` é portável (SQLite e PG) e no PG vira um top-1 por índice
(`ix_consentimento_cliente_finalidade_canal_registrado_em`). Ex.: `candidatos_nps` troca
`cliente__consent_whatsapp_nps=True, cliente__aceita_comunicacao=True` por
`anotar_vigente(qs, 'PESQUISA_SATISFACAO', 'WHATSAPP').filter(tem_consentimento=True)`.

**`aceita_comunicacao` (default `True`) — avaliação LGPD:** um "aceite" que nasce marcado para
todo cadastro não é consentimento válido (LGPD art. 8 §4 e art. 9 §1: consentimento é manifestação
livre, informada e específica; autorização genérica é nula). No código ele **nunca concede nada**
sozinho — só aparece em `AND` com o consentimento granular (aniversário, promoção, NPS) e como
opt-out geral do descadastro (`LgpdService.unsubscribe_por_token`). Ou seja, na prática é uma
**revogação global**. Na tabela nova ele deixa de existir: o `False` vira linhas de revogação de
MARKETING/EMAIL e PESQUISA_SATISFACAO/WHATSAPP; o `True` não vira nada. Efeito colateral (bom,
mas é mudança de comportamento — D2 no §7): hoje quem se descadastra e depois marca de novo o opt-in
no wizard **continua** sem receber (o `aceita_comunicacao=False` segue vetando); com histórico, a
linha mais recente vale, então o reconsentimento funciona.

**Migração de dados** (`RunPython`, portável), por cliente e por par:
- `consent_x = True` → linha `eh_concedido=true`, `registrado_em = consent_x_em` (ou
  `cliente.criado_em` se nulo), `ip_origem = consent_x_ip`, `origem='MIGRACAO'`,
  `texto='(migrado de cliente.consent_x)'`;
- `consent_x = False` **com** `consent_x_em` preenchido (foi revogado — o wizard grava data/IP da
  revogação) → linha `eh_concedido=false` com a data/IP da revogação;
- `consent_x = False` sem data → nenhuma linha (nunca concedeu = sem linha);
- `aceita_comunicacao = False` → revogação de MARKETING/EMAIL e PESQUISA_SATISFACAO/WHATSAPP com
  `registrado_em = greatest(cliente.atualizado_em, datas acima) + 1 µs` (fica por último: preserva
  o efeito atual, que é vetar), `origem='MIGRACAO'`, `texto='opt-out geral (aceita_comunicacao=false)'`.

Resultado conferível: para todo cliente e par, `vw_consentimento_atual.eh_concedido` =
`consent_x AND (aceita_comunicacao OR par = LEMBRETE_AGENDAMENTO)` antes da migração (teste de
migração com dados em `test_migrations_dados.py`). Depois: `RemoveField` das 10 colunas.

**Reversibilidade:** o reverso recria as colunas a partir do estado vigente (`consent_x` =
vigente, `_em/_ip` = da linha vigente) e `aceita_comunicacao = not (revogação MIGRACAO/DESCADASTRO
mais recente que qualquer concessão)`.

**Anonimização:** o histórico fica (prova de que o tratamento teve base legal); `sp_anonimizar_cliente`
insere revogações `origem='ESQUECIMENTO'` do que estiver concedido e **minimiza** `ip_origem`/
`user_agent` das linhas do titular (única mutação que `tg_consentimento_imutavel` aceita — D3 no §7).

**Impacto no código:** `models/clientes.py` (saem 10 campos), `models/consentimentos.py` (novo); `views/booking_public.confirmar_agendamento`
(l. 461–497: `defaults` com `consent_*` e o laço de concede/revoga → `consentimentos.registrar(...,
origem='AGENDAMENTO_ONLINE', texto=<rótulo do checkbox>, request=request)`; `CONSENTS_COMUNICACAO`
vira `PARES`); `views/booking_otp` (l. 102–104, pré-preenchimento → `estado_atual`); `tasks.py`
(lembrete l. 69–73, `candidatos_nps` l. 130–137, aniversário l. 380, promoção l. 440–445);
`services/lista_espera_service` (l. 134); `services/lgpd` (`CAMPOS_ANONIMIZADOS`,
`unsubscribe_por_token` → revogações `origem='DESCADASTRO'`, export DSAR → histórico completo);
`views/admin_promotions` (l. 142–143); `views/lgpd.unsubscribe`; `admin.py` (l. 190–249:
`_CONSENTS`, fieldsets, `list_filter`); `forms/cliente.py` (l. 31) e
`templates/painel/cliente_detalhe.html` (l. 181: "Aceita comunicação" vira o estado por canal com
ação "Revogar", `origem='PAINEL'`, autor = usuário); `domain/handlers.py` (docstring).

**Implementação (0050) — o que ficou diferente do desenho acima, e por quê:**

- **Nome só no banco** (precedente da 0048): atributo `Consentimento.concedido` → coluna
  `eh_concedido`; `usuario_autor` (FK `PROTECT` = `NO ACTION` do DDL: o trigger não deixa a
  autoria mudar, então `SET NULL` falharia). FK do cliente sem índice automático (coberta pelo
  `ix_consentimento_cliente_finalidade_canal_registrado_em`, `(cliente_id, finalidade, canal,
  registrado_em DESC, id DESC)`).
- **Ordem da migration:** `CreateModel` → `RunPython` (histórico + `LogAuditoria` `migration 0050:
  consentimentos com historico` com as contagens; `SET CONSTRAINTS ALL IMMEDIATE`) → PG:
  `fn_consentimento_imutavel` + `tg_consentimento_imutavel` (corpo do §5.2; SQLSTATE 23000 →
  `IntegrityError`) → `PadronizarNomes(['consentimento'])` → `RemoveField` ×10 (o §6 não listava o
  `PadronizarNomes`, mas toda migration que cria tabela termina com ele).
- **Regras de dado** (além do desenho): `consent_x=False` **com** `_em` vira revogação na data/IP
  gravados — o descadastro e o admin antigos não atualizavam o `_em`, então essa data pode ser a
  da concessão (o estado vigente é o mesmo). `aceita_comunicacao=False` grava as 2 revogações
  **sempre** (mesmo sem concessão anterior: documenta o opt-out e permite o reverso), inclusive
  para excluído/anonimizado (todo cliente migra), em `greatest(atualizado_em, criado_em,
  consent_*_em) + 1 µs` (`criado_em` entra porque `atualizado_em` é anulável).
- **Reverso:** `consent_x/_em/_ip` = linha vigente; `aceita_comunicacao=False` quando a revogação
  de "opt-out geral" mais recente de MARKETING/PESQUISA — `DESCADASTRO`, `ESQUECIMENTO` ou
  `MIGRACAO` com o texto `opt-out geral (aceita_comunicacao=false)` (a revogação MIGRACAO de um
  `consent_x=False` do wizard **não** é opt-out geral) — é posterior a qualquer concessão desses
  pares. Ida-e-volta preserva o estado, não as linhas (o reverso vira concessão + opt-out em
  `consent_x=False` com data; subir de novo grava a revogação).
- **Serviço** (`services/consentimentos.py`), além de `estado_atual/vigente/registrar/
  anotar_vigente`: `com_consentimento(qs, …)` (anota + filtra; usado pelos jobs/painel),
  `estado_wizard` (pré-preenchimento do OTP com as chaves dos checkboxes), `registrar_do_wizard`
  (marcado concede; desmarcado só revoga com `consents_sincronizados` e cadastro existente),
  `revogar`/`revogar_todos`, `historico` (DSAR), `resumo_painel`, `esquecer` (revoga
  `ESQUECIMENTO` + `minimizar_dados_de_origem()` = o passo 2 do `sp_anonimizar_cliente`).
  `registrar` trava a linha do cliente (`select_for_update`) e é no-op quando o vigente já é o
  pedido (revogar o que nunca foi concedido também não grava). Prova: `texto` = rótulo do
  checkbox, que o template lê de `TEXTOS_WIZARD` (o que se grava é o que foi exibido), IP
  (`client_ip`, inválido → NULL) e user-agent.
- **Nomes dos checkboxes do wizard mantidos** (`consent_email_marketing`, `consent_whatsapp_nps`,
  `consent_whatsapp_confirmacao`): são o protocolo POST/JSON do wizard (a chave do `PARES`, como no
  desenho) — as únicas referências aos nomes antigos fora das migrations; nenhum campo de model.
- **Python cobre o trigger no SQLite:** `save()` de linha existente, `delete()` e
  `QuerySet.delete()` levantam `ValidationError`; a única mutação é
  `QuerySet.minimizar_dados_de_origem()` (IP → NULL, UA → '').
- **Painel** (ficha do cliente, card "Comunicação autorizada"): estado por canal com data e
  origem; "Revogar" (1 clique, `origem='PAINEL'`, autor); "Registrar autorização" exige
  justificativa ≥ 10 caracteres (vira o `texto`, com o autor) — decisão D3. `LogAuditoria`
  "Revogou/Registrou consentimento" com o id. **Django admin:** `ConsentimentoInline` e
  `ConsentimentoAdmin` só leitura + ação "Revogar consentimentos de comunicação" (`origem='ADMIN'`);
  o `ClienteAdminForm` (que barrava conceder) saiu com os campos.
- **Descadastro** grava IP/UA de quem confirmou (o `unsubscribe` passa o `request`); DSAR exporta
  `consentimentos: {vigentes, historico}` (sai `preferencias.aceita_comunicacao`).
- **Fora desta fase:** `vw_consentimento_atual`/`fn_tem_consentimento` (L2, com paridade) e o
  `sp_anonimizar_cliente` (L3) — o Python de hoje já faz o mesmo que o passo 2.
- **Testes:** `tests/test_consentimentos.py` (serviço: append-only, no-op, último manda, empate por
  id, linha antiga inserida depois não vence, anotação em Atendimento/Cliente, imutável no
  Python, esquecimento sem IP/UA, DSAR; regressão: D-1, NPS, aniversário, promoção — job e painel —
  e aviso de vaga por WhatsApp só com o par vigente, descadastro não corta o D-1,
  reconsentimento volta a receber), `test_migrations_dados.py` (`Consentimentos0050Tests`:
  invariante `consent_x AND (aceita_comunicacao OR LEMBRETE)` por cliente/par, revogado, sem
  data, opt-out sem opt-in, excluído, log e colunas; `ConsentimentosReversa0050Tests`),
  `test_pg_ddl.py` (nomes, `NO ACTION`, `inet`, índice DESC, trigger aceita só a minimização,
  CHECK de par), painel/admin/wizard/OTP/descadastro nos testes existentes.

**Ensaio PG (0026 → 0051, `migrate_atomico`, 25 migrations numa transação):** banco 0026 com
clientes de todos os casos (tudo concedido com IP v4/v6, NPS revogado no wizard, descadastrado com
opt-ins ainda marcados, opt-out sem opt-in, concedido sem data, excluído) → estado vigente igual
ao esperado em todos os pares, IPs preservados, reconsentimento vale, trigger barra UPDATE/DELETE
e aceita a minimização; do zero ok; reverso 0051 → 0049 → 0051 ok (estado igual).

### 4.d Questionários unificados (`formulario` tipo PRONTUARIO) — migration 0051

**Hoje há dois sistemas:** (1) perguntas do prontuário em `configuracao.prontuario_perguntas`
(JSON `[{chave, texto, tipo: TEXTO|BOOLEAN}]`, lido por `utils/saude.perguntas_prontuario`,
editado em `views/admin_config`) com respostas em `prontuario.respostas_extras`; (2)
`formulario_anamnese` (`schema_json [{key, tipo, label, obrigatorio, opcoes}]`, 9 tipos, validado
por `services/anamnese.validar_respostas`) com respostas em `resposta_anamnese`.

**Alvo — uma definição, um formato, um validador, duas "caixas" de resposta:**

| Tipo | Quem responde | Onde fica a resposta | Por quê |
|---|---|---|---|
| ANAMNESE | cliente (wizard ou link) | `resposta_formulario` (1 por atendimento/convite) | é uma declaração datada da cliente, imutável depois de respondida |
| PESQUISA | cliente (pós-atendimento) | `resposta_formulario` | idem |
| PRONTUARIO | equipe | `prontuario.respostas` (1 por cliente, editável) | é parte da ficha viva: edição com trava otimista (`atualizado_em`), foto anterior em `prontuario_versao` e trilha em `historico_alteracao`. Mandar para `resposta_formulario` partiria a ficha em duas tabelas e duplicaria o versionamento |

- No máximo **1 PRONTUARIO ativo** (`uq_formulario_prontuario_ativo`), sempre GLOBAL, nunca
  obrigatório (`ck_formulario_prontuario_global`). Editar perguntas = editar esse formulário no
  mesmo CRUD das anamneses (`views/admin_anamnese`); a chave `key` é estável — renomear o rótulo
  não mexe nas respostas.
- `prontuario_salvar` (`views/prontuario.py` l. 308–326) passa a usar `validar_respostas(perguntas,
  POST)` — o `bool` aceita `sim`/`nao` (já está em `_BOOL_SIM/_BOOL_NAO`), então o formulário atual
  continua funcionando.
- **Convivência com `prontuario_versao`:** nada muda — a foto continua com a chave
  `respostas_extras` dentro de `dados` (é o atributo Python; histórico imutável não se reescreve). A
  tela de histórico rotula `extra:<key>` pelo `label` do formulário PRONTUARIO ativo e, para chave
  que saiu do formulário, deriva o rótulo da chave (como hoje).
- **`utils/saude.alertas_saude`:** passa a ler as perguntas do formulário PRONTUARIO (`key→label`)
  em vez da `Configuracao`; mesma heurística `_RISCO_RX` sobre `key + label`. Novo atributo opcional
  por pergunta `alerta_saude: true|false` (em qualquer tipo de formulário): `true` alerta sempre que
  a resposta for positiva, `false` nunca, ausente = heurística (compatível: nenhum dado migrado tem o
  atributo, então o resultado de `alertas_saude` é idêntico — teste de caracterização).

**Migração** (`RunPython`): lê `Configuracao` com `chave__iexact='prontuario_perguntas'` (mesmo
filtro de `perguntas_prontuario`); se o JSON é válido cria `formulario(nome='Questionário do
prontuário', tipo='PRONTUARIO', escopo='GLOBAL', perguntas=[{key: chave, label: texto, tipo: 'bool'
se BOOLEAN senão 'longtext', obrigatorio: false}])` e apaga a linha da configuração; JSON inválido →
não cria, mantém a linha e registra `LogAuditoria` (nada se perde). `longtext` (4 000) e não `text`
(1 000) para não reprovar resposta antiga longa. As respostas **não mudam**: já estão em
`prontuario.respostas_extras` com `chave` como key. Antes, pré-checagem dos CHECKs novos de
`formulario` (`escopo`/`escopo_coerente`), que hoje só existem nos choices do Python.
Reverso: formulário PRONTUARIO → volta a ser a linha de `Configuracao`.

**Impacto no código:** `models/anamnese.py` (TIPO_CHOICES + constraints + manager
`prontuario_ativo()`); `utils/saude.py` (`perguntas_prontuario`, `alertas_saude`); `views/prontuario.py`
(`_perguntas_configuradas`, `prontuario_salvar`, `_historico_prontuario`) e o template da ficha
(`p.chave/p.texto/p.tipo` → `key/label/tipo`); `views/admin_config.py` (sai `prontuario_perguntas`);
`views/admin_anamnese.py` (aceita PRONTUARIO e trava escopo/obrigatório); `services/anamnese.py`
(`alerta_saude` no schema); `seed.py`. `utils/fichas.py` e `services/alertas.py` já filtram
`tipo='ANAMNESE'` — sem mudança.

**Implementação (0051) — o que ficou diferente do desenho acima, e por quê:**

- **Tabela continua `formulario_anamnese`** (renomeia na 0054), mas os objetos novos já nascem com
  o nome final: `ck_formulario_tipo`, `ck_formulario_escopo`, `ck_formulario_escopo_coerente`,
  `ck_formulario_prontuario_global` (CHECKs do `Meta`, existem no SQLite também) e
  `uq_formulario_prontuario_ativo` (UNIQUE parcial = índice). Os 3 de regra têm
  `violation_error_message` (o `full_clean` do painel mostra a frase, sem `clean()` duplicado).
- **Pré-checagem** também reprova `tipo` fora do domínio (o desenho citava só o `escopo`; sem isso
  o `ck_formulario_tipo` falharia com erro genérico). Aborta com `RuntimeError` listando os ids
  (padrão 0048); SQL de pré-checagem no topo da migration e no runbook.
- **Conversão:** JSON válido = lista de objetos com `chave` texto não vazia e única; `texto`
  ausente → label derivado da chave (o mesmo rótulo que o leitor antigo exibia), `BOOLEAN` →
  `bool`, resto → `longtext`. Valor vazio ou `[]` → só apaga a linha (não havia pergunta).
  Linhas duplicadas da chave (`iexact`; o leitor usava a 1ª por pk) ficam intocadas e vão para o
  `LogAuditoria`. Inválido → mantém a linha + `LogAuditoria` (`registro_id` = a configuração).
- **`utils/saude`:** `perguntas_prontuario()` lê o PRONTUARIO ativo
  (`FormularioAnamnese.objects.prontuario_ativo()`); `deve_alertar(pergunta, key, label)`
  (`alerta_saude` > heurística) vale para prontuário **e** fichas de ANAMNESE;
  `rotulo_da_chave`; `alertas_saude(…, perguntas=None)` e `services/alertas.alertas_por_cliente`
  lê as perguntas 1× por lote. Caracterização: com os dados convertidos pela migration o resultado
  é idêntico ao da regra antiga (teste com cópia congelada). A ordem dos alertas segue a do JSON
  que o banco devolve (o `jsonb` do PG ordena as chaves) — como antes.
- **`prontuario_salvar`:** só as perguntas presentes no POST; `validar_respostas` (o mesmo das
  fichas) — **qualquer erro não salva nada** (antes um bool inválido era ignorado em silêncio);
  resposta apagada sai do JSON (antes ficava `''`); checkboxes usam o marcador
  `pergunta_<key>__enviado` (lista vazia não chega no POST). A ficha renderiza os 9 tipos.
- **Configurações:** `prontuario_perguntas` virou chave reservada (criar recusa com a mensagem
  "agora ficam em Anamneses"); sai das sugestões.
- **CRUD de anamneses:** tipo PRONTUARIO força `escopo=GLOBAL`/`obrigatorio=False`; 2º ativo é
  recusado com a mensagem do UNIQUE; a lista não oferece "Respostas" para PRONTUARIO; a lista do
  prontuário mostra as perguntas com o link de edição. `RespostaAnamnese.clean()` recusa
  formulário PRONTUARIO.
- **`seed`** cria "Questionário do prontuário" (3 perguntas sim/não: fuma, anticoagulante,
  tendência a queloide) só se não existe nenhum PRONTUARIO — **conteúdo a validar pelo dono**.
- **DSAR:** `prontuario.questionario` = respostas com o texto da pergunta (além de
  `respostas_extras`).
- **Testes:** `tests/test_questionario_prontuario.py` (modelo/CHECK/UNIQUE, ficha com os tipos,
  salvar pelo validador, erro não salva, apagar resposta, histórico rotulado, CRUD, alertas com
  `alerta_saude` e caracterização, lote com 1 leitura, conversão da migration, seed),
  `test_migrations_dados.py` (`FormularioProntuario0051Tests`, JSON inválido, reverso, aborto),
  `test_pg_ddl.py` (CHECKs/índice no PG).

**Ensaio PG:** EAV do prontuário no schema 0026 (perguntas ativas e inativa + respostas) → 0036 →
Configuração → 0051: formulário com as 3 perguntas ativas, respostas intactas (inclusive a da
pergunta inativa), alertas iguais; reverso devolve a Configuração.

### 4.e Pacote congelado (`compra_pacote_item`) — migration 0052

**Problema hoje:** `CompraPacote` não guarda o que foi vendido; saldo (`saldo_por_procedimento`),
finalização (`verificar_finalizacao`), débito (`signals.processar_mudanca_status`) e comissão
(`ComissaoService.base_calculo`) leem `pacote.itens` **na hora** — por isso
`views/pacotes.admin_editar_pacote` (l. 141–206) proíbe mexer em nome/itens de pacote vendido.

**Modelo** (DDL no §2.8): `compra_pacote.pacote_nome` (snapshot), `compra_pacote_item`
(procedimento + `procedimento_nome` + `qtd_sessoes` + `valor_unitario` congelados) e
`consumo_sessao.compra_pacote_item_id` (qual item a sessão consumiu). FK composta só-PG
`fk_consumo_sessao_compra_pacote_item_compra` garante que o item é da mesma compra;
`tg_consumo_sessao_validar` garante procedimento igual, não-retorno e saldo (§5.2).

**Venda:** `CompraPacote.vender(pacote, cliente, valor_pago)` (classmethod atômico) cria a compra
com `pacote_nome` e um item por `pacote.itens` com
`valor_unitario = (valor_pago / Σ qtd_sessoes).quantize(0.01, ROUND_HALF_UP)` — exatamente a
fórmula atual de `base_calculo` (`_centavos(compra.valor_pago / total_sessoes)`), então a comissão
de sessão de pacote não muda. `admin_vender_pacote` chama `vender`.

**Saldo via itens congelados:**
`saldo_por_procedimento()` = para cada `self.itens`: `total = qtd_sessoes`, `usadas = consumos do
item` (1 query agregada por `compra_pacote_item_id`), `restantes = max(0, total − usadas)`;
`verificar_finalizacao()` idem sob `select_for_update`; o débito em `signals.py` (l. 113–137)
escolhe `pc.itens.filter(procedimento=…)` e grava `compra_pacote_item`; `base_calculo` lê
`consumo.compra_pacote_item.valor_unitario`. Espelho SQL: `fn_saldo_pacote` / `vw_pacote_saldo`
(paridade).

**Catálogo liberado:** sai a trava de "pacote vendido" em `admin_editar_pacote` e o modo só-leitura
em `templates/painel/pacotes.html`; editar nome/itens/preço/validade só afeta vendas futuras.

**Migração** (`RunPython`): para cada compra, `pacote_nome = pacote.nome` e um item por
`item_pacote` atual do pacote (o catálogo de pacote vendido está congelado pela trava da UI desde a
auditoria; antes disso não há como saber o que mudou — risco anotado); cada `consumo_sessao` recebe
o item do mesmo procedimento; consumo sem item correspondente (item removido do catálogo) →
**reconstrói** um item com `qtd_sessoes = nº de consumos daquele procedimento`, `valor_unitario =
0`, e registra `LogAuditoria` (`migration 0052: item reconstruido`); consumo acima da quantidade
(débito a mais de antes do lock) → só `LogAuditoria` (o trigger só vale para INSERT novo).
Colunas nascem nulas, são preenchidas e viram `NOT NULL` na mesma migration. Reverso: remove
itens e colunas (sem perda: o catálogo continua lá).

**Impacto no código:** `models/pacotes.py` (novo `CompraPacoteItem`, `vender`, `saldo_por_procedimento`,
`verificar_finalizacao`), `signals.py`, `services/comissao_service.base_calculo`, `views/pacotes.py`
(`admin_vender_pacote`, `admin_editar_pacote`), `templates/painel/pacotes.html`,
`views/admin_management._contexto_cliente` (prefetch `itens` em vez de `pacote__itens__procedimento`),
`tasks.job_verificar_pacotes_expirando` (idem), `services/lgpd.exportar_dados_cliente` (`pacote_nome`
+ itens), `admin.py` (inline só-leitura).

### 4.f Comissão por lançamento (`movimento_comissao` append-only) — migration 0053

**Hoje:** 1 linha por atendimento com `status` PENDENTE→PAGA|ESTORNADA mudado por UPDATE
(`ComissaoService.marcar_paga` l. 161, `estornar_comissao` l. 185); por isso a 0038 deixou o
ledger de comissão fora da imutabilidade.

**Alvo:** livro-razão com 3 tipos (DDL no §2.9):

| tipo | quando | referencia | efeito no saldo a pagar |
|---|---|---|---|
| CREDITO | `calcular_comissao` (AtendimentoRealizado) | — (`regra_comissao_id`) | + valor |
| PAGAMENTO | baixa no painel (`marcar_paga`) | o CREDITO (`movimento_comissao_origem_id`) | − valor |
| ESTORNO | AtendimentoCancelado (`estornar_comissao`) | o CREDITO | − valor |

- Status é **derivado**: ESTORNADA se existe ESTORNO do crédito; senão PAGA se existe PAGAMENTO;
  senão PENDENTE (`vw_comissao`). Saldo por profissional = Σ CREDITO − Σ ESTORNO − Σ PAGAMENTO
  (`vw_comissao_saldo`, `fn_saldo_comissao`) — fica **negativo** quando uma comissão já paga é
  estornada (o profissional deve à clínica), o que hoje some ao mudar PAGA→ESTORNADA.
- Invariantes no banco: 1 CREDITO por atendimento (idempotência atual: "nunca cria 2ª comissão,
  mesmo após ESTORNADA"), 1 ESTORNO e 1 PAGAMENTO por crédito (UNIQUE parciais);
  `tg_movimento_comissao_validar` exige origem = CREDITO do mesmo profissional/atendimento, valor =
  valor do crédito (sem baixa parcial) e bloqueia PAGAMENTO de crédito estornado (trava a linha do
  crédito `FOR UPDATE` → serializa pagar × estornar); `tg_movimento_comissao_imutavel` bloqueia
  UPDATE/DELETE. `regra_comissao` usada deixa de poder ser apagada (`SET_NULL→PROTECT`, senão o
  `SET NULL` bateria no trigger — mesma lição da 0041).

**Serviço:** `calcular_comissao` cria CREDITO (igual); `marcar_paga(movimento_id, usuario)` trava o
crédito, confere que não há PAGAMENTO/ESTORNO e cria PAGAMENTO com `usuario_autor`;
`estornar_comissao(atendimento)` cria ESTORNO para o crédito ainda não estornado (pago ou não).
Leitura no painel (`relatorios.painel_comissoes`, `admin_financeiro` l. 121): queryset
`MovimentoComissao.objects.creditos_com_status()` anota `status` (Case/Exists) e `pago_em`
(Subquery) — o template (`painel/comissoes.html` l. 102–112 usa `c.status`, `c.pago_em`,
`c.get_status_display`) continua funcionando com um `get_status_display` sobre a anotação.

**Migração** (ordem importa): `RemoveConstraint(uniq_comissao_ativa_por_atendimento)` →
`AddField` `tipo` (default CREDITO), `movimento_origem`, `usuario_autor` → `RunPython`:

| Linha atual | Vira |
|---|---|
| PENDENTE | CREDITO (a própria linha, mesmo `id`) |
| PAGA | CREDITO + PAGAMENTO (`criado_em = pago_em`, `observacao='migração 0053'`) |
| ESTORNADA com `pago_em` | CREDITO + PAGAMENTO(`pago_em`) + ESTORNO |
| ESTORNADA sem `pago_em` | CREDITO + ESTORNO (`criado_em = atendimento.atualizado_em`, `observacao='migração 0053: data estimada'` — o sistema nunca gravou quando estornou) |

→ `RemoveField(status, pago_em)` → `AddConstraint` (UNIQUE/CHECK) → triggers (só-PG, por último).
Aborta (lista) se houver linha com `valor <= 0` (o validator aceitava 0; o serviço nunca cria) ou
2 linhas do mesmo atendimento. Reverso: recria `status/pago_em` a partir do ledger e apaga as
linhas não-CREDITO (o reverso derruba os triggers antes).

**Impacto no código:** `models/extras.py` (`MovimentoComissao`: tipos, FK de origem, autor,
queryset), `services/comissao_service.py`, `views/relatorios.py` (`painel_comissoes`,
`admin_comissao_pagar` passa `request.user`), `views/admin_financeiro.py`,
`views/admin_agendamento.py` (l. 348: `filter(atendimento=at)` → `filter(atendimento=at, tipo=CREDITO)`),
`templates/painel/comissoes.html`, `dashboard_financeiro.html`, `admin.py` (só leitura, sem
excluir), testes (`test_retorno_cashback_comissao.py`, `test_comissao_resolver_regra.py`,
`test_relatorios.py`).

---

## 5. Lógica no banco

Convenções de implementação (lições das 0038/0046):
- todo SQL de função/trigger/procedure vai pelo `schema_editor.execute(sql, None)` (**`params=None`**:
  com `params=()` o psycopg2 faz *mogrify* e lê o `%` do `RAISE` como placeholder);
- funções de leitura em SQL puro usam corpo **`BEGIN ATOMIC`** (PG ≥ 14): o corpo é analisado na
  criação e guarda dependências — renomear/dropar coluna usada **falha** em vez de quebrar em
  produção. PL/pgSQL (triggers, procedures, laços) não tem essa proteção: o teste de paridade/DDL é
  a rede;
- funções/procedures fixam `SET search_path = public, pg_catalog` (contra *search_path hijack*),
  **exceto** procedure que dá `COMMIT` (o PG proíbe controle de transação em rotina com `SET`) —
  essa qualifica tudo com `public.`;
- erros de regra levantam SQLSTATE de classe 23 (`integrity_constraint_violation`/`check_violation`):
  o Django converte em `IntegrityError` (capturável), não `InternalError` (500);
- o texto de cada função fica numa constante Python num módulo versionado
  (`aranha_estetica/migrations/sql/v0056_triggers.py` etc.), importado só pela migration daquela
  versão — mudar uma função = módulo novo + migration nova com `CREATE OR REPLACE`.

### 5.1 Funções utilitárias

```sql
CREATE OR REPLACE FUNCTION fn_fuso_clinica() RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN 'America/Sao_Paulo';                 -- = settings.TIME_ZONE (teste compara)

CREATE OR REPLACE FUNCTION fn_data_local(p_instante timestamptz) RETURNS date
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
RETURN (p_instante AT TIME ZONE fn_fuso_clinica())::date;

-- unaccent() é STABLE; o wrapper com dicionário explícito pode ser IMMUTABLE (padrão da doc do
-- pg_trgm) — é o que permite o índice ix_cliente_nome_trgm.
CREATE OR REPLACE FUNCTION fn_sem_acento(p_texto text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
RETURN lower(public.unaccent('public.unaccent'::regdictionary, p_texto));
```

A conexão do Django roda com `TimeZone = UTC` (`USE_TZ=True`); por isso nenhuma função depende do
`TimeZone` da sessão — o fuso é sempre explícito.

### 5.2 Triggers

#### `atualizado_em` (22 tabelas)

```sql
CREATE OR REPLACE FUNCTION fn_definir_atualizado_em() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- O ORM (auto_now) já manda o valor: só preenche quando o UPDATE não tocou na coluna
    -- (QuerySet.update(), procedures, SQL manual). Assim o valor em memória do Django não
    -- diverge do banco (a trava otimista do prontuário compara atualizado_em).
    IF NEW.atualizado_em IS NOT DISTINCT FROM OLD.atualizado_em THEN
        NEW.atualizado_em := now();
    END IF;
    RETURN NEW;
END $$;

-- para cada t em: usuario, assinatura_push, cliente, profissional, procedimento, preco, promocao,
-- agenda_horario, agenda_excecao, atendimento, notificacao, lista_espera, avaliacao_nps, prontuario,
-- formulario, resposta_formulario, versao_termo, pacote, compra_pacote, carteira, regra_comissao,
-- configuracao
CREATE TRIGGER tg_<t>_atualizado_em BEFORE UPDATE ON <t>
    FOR EACH ROW EXECUTE FUNCTION fn_definir_atualizado_em();
```

Resolve o que a 0043 deixou de fora ("cosmético"): hoje `QuerySet.update()` (ex.:
`tasks.job_expirar_pacotes`, as migrations 0035/0043, `LgpdService`) não mexe no `atualizado_em`.

#### Imutabilidade (10 tabelas)

```sql
CREATE OR REPLACE FUNCTION fn_bloquear_mutacao() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% e append-only: % bloqueado', TG_TABLE_NAME, TG_OP
        USING DETAIL = TG_TABLE_NAME || '.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
END $$;

CREATE TRIGGER tg_movimento_carteira_imutavel BEFORE UPDATE OR DELETE ON movimento_carteira
    FOR EACH ROW EXECUTE FUNCTION fn_bloquear_mutacao();
CREATE TRIGGER tg_movimento_comissao_imutavel BEFORE UPDATE OR DELETE ON movimento_comissao
    FOR EACH ROW EXECUTE FUNCTION fn_bloquear_mutacao();
CREATE TRIGGER tg_anotacao_sessao_imutavel BEFORE UPDATE OR DELETE ON anotacao_sessao
    FOR EACH ROW EXECUTE FUNCTION fn_bloquear_mutacao();
CREATE TRIGGER tg_compra_pacote_item_imutavel BEFORE UPDATE ON compra_pacote_item
    FOR EACH ROW EXECUTE FUNCTION fn_bloquear_mutacao();

CREATE OR REPLACE FUNCTION fn_aceite_termo_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'aceite_termo e prova legal (LGPD art. 8): exclusao bloqueada'
            USING DETAIL = 'aceite_termo.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.id, NEW.cliente_id, NEW.versao_termo_id, NEW.ip_origem, NEW.user_agent, NEW.conteudo_hash, NEW.criado_em)
       IS DISTINCT FROM
       (OLD.id, OLD.cliente_id, OLD.versao_termo_id, OLD.ip_origem, OLD.user_agent, OLD.conteudo_hash, OLD.criado_em)
       OR (NEW.atendimento_id IS DISTINCT FROM OLD.atendimento_id AND NEW.atendimento_id IS NOT NULL) THEN
        RAISE EXCEPTION 'aceite_termo e prova legal (LGPD art. 8): registro imutavel'
            USING DETAIL = 'aceite_termo.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;                                  -- só o SET NULL de atendimento_id passa
END $$;
CREATE TRIGGER tg_aceite_termo_imutavel BEFORE UPDATE OR DELETE ON aceite_termo
    FOR EACH ROW EXECUTE FUNCTION fn_aceite_termo_imutavel();

CREATE OR REPLACE FUNCTION fn_versao_termo_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.id, NEW.tipo, NEW.procedimento_id, NEW.titulo, NEW.conteudo, NEW.versao)
       IS DISTINCT FROM (OLD.id, OLD.tipo, OLD.procedimento_id, OLD.titulo, OLD.conteudo, OLD.versao)
       AND EXISTS (SELECT 1 FROM aceite_termo WHERE versao_termo_id = OLD.id) THEN
        RAISE EXCEPTION 'versao_termo ja aceita e imutavel: publique uma nova versao'
            USING DETAIL = 'versao_termo.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;                                  -- eh_ativo / data_inicio_vigencia podem mudar
END $$;
CREATE TRIGGER tg_versao_termo_imutavel BEFORE UPDATE ON versao_termo
    FOR EACH ROW EXECUTE FUNCTION fn_versao_termo_imutavel();

CREATE OR REPLACE FUNCTION fn_prontuario_versao_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'prontuario_versao e historico clinico: exclusao bloqueada'
            USING DETAIL = 'prontuario_versao.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.id, NEW.prontuario_id, NEW.usuario_autor_nome, NEW.criado_em, NEW.dados)
       IS DISTINCT FROM (OLD.id, OLD.prontuario_id, OLD.usuario_autor_nome, OLD.criado_em, OLD.dados)
       OR (NEW.usuario_autor_id IS DISTINCT FROM OLD.usuario_autor_id AND NEW.usuario_autor_id IS NOT NULL) THEN
        RAISE EXCEPTION 'prontuario_versao e historico clinico: registro imutavel'
            USING DETAIL = 'prontuario_versao.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER tg_prontuario_versao_imutavel BEFORE UPDATE OR DELETE ON prontuario_versao
    FOR EACH ROW EXECUTE FUNCTION fn_prontuario_versao_imutavel();

CREATE OR REPLACE FUNCTION fn_consentimento_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'consentimento e historico append-only: exclusao bloqueada'
            USING DETAIL = 'consentimento.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    -- única mutação aceita: minimização LGPD (sp_anonimizar_cliente apaga IP e user-agent)
    IF (NEW.id, NEW.cliente_id, NEW.finalidade, NEW.canal, NEW.eh_concedido, NEW.origem, NEW.texto,
        NEW.usuario_autor_id, NEW.registrado_em, NEW.criado_em)
       IS DISTINCT FROM
       (OLD.id, OLD.cliente_id, OLD.finalidade, OLD.canal, OLD.eh_concedido, OLD.origem, OLD.texto,
        OLD.usuario_autor_id, OLD.registrado_em, OLD.criado_em)
       OR (NEW.ip_origem IS DISTINCT FROM OLD.ip_origem AND NEW.ip_origem IS NOT NULL)
       OR (NEW.user_agent IS DISTINCT FROM OLD.user_agent AND NEW.user_agent <> '') THEN
        RAISE EXCEPTION 'consentimento e historico append-only: registro imutavel'
            USING DETAIL = 'consentimento.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER tg_consentimento_imutavel BEFORE UPDATE OR DELETE ON consentimento
    FOR EACH ROW EXECUTE FUNCTION fn_consentimento_imutavel();

CREATE OR REPLACE FUNCTION fn_link_acesso_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- DELETE livre (purga/cascata). UPDATE: só marcar usado_em e revogado_em, uma vez cada.
    IF (NEW.id, NEW.finalidade, NEW.atendimento_id, NEW.notificacao_id, NEW.resposta_formulario_id,
        NEW.cliente_id, NEW.profissional_id, NEW.token_hash, NEW.expira_em, NEW.criado_em)
       IS DISTINCT FROM
       (OLD.id, OLD.finalidade, OLD.atendimento_id, OLD.notificacao_id, OLD.resposta_formulario_id,
        OLD.cliente_id, OLD.profissional_id, OLD.token_hash, OLD.expira_em, OLD.criado_em)
       OR (OLD.usado_em IS NOT NULL AND NEW.usado_em IS DISTINCT FROM OLD.usado_em)
       OR (OLD.revogado_em IS NOT NULL AND NEW.revogado_em IS DISTINCT FROM OLD.revogado_em) THEN
        RAISE EXCEPTION 'link_acesso: so usado_em/revogado_em podem ser preenchidos (uma vez)'
            USING DETAIL = 'link_acesso.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER tg_link_acesso_imutavel BEFORE UPDATE ON link_acesso
    FOR EACH ROW EXECUTE FUNCTION fn_link_acesso_imutavel();

CREATE OR REPLACE FUNCTION fn_historico_alteracao_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        -- só a purga de retenção (sp_purgar_dados_expirados liga a flag local da transação)
        IF coalesce(current_setting('app.expurgo_autorizado', true), '') = 'on' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'historico_alteracao e trilha append-only: exclusao bloqueada'
            USING DETAIL = 'historico_alteracao.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.id, NEW.tabela, NEW.registro_pk, NEW.operacao, NEW.colunas_alteradas, NEW.dados_antes,
        NEW.dados_depois, NEW.ip_origem, NEW.origem, NEW.transacao_ref, NEW.criado_em)
       IS DISTINCT FROM
       (OLD.id, OLD.tabela, OLD.registro_pk, OLD.operacao, OLD.colunas_alteradas, OLD.dados_antes,
        OLD.dados_depois, OLD.ip_origem, OLD.origem, OLD.transacao_ref, OLD.criado_em)
       OR (NEW.usuario_autor_id IS DISTINCT FROM OLD.usuario_autor_id AND NEW.usuario_autor_id IS NOT NULL) THEN
        RAISE EXCEPTION 'historico_alteracao e trilha append-only: registro imutavel'
            USING DETAIL = 'historico_alteracao.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;                                  -- só o SET NULL do autor passa
END $$;
CREATE TRIGGER tg_historico_alteracao_imutavel BEFORE UPDATE OR DELETE ON historico_alteracao
    FOR EACH ROW EXECUTE FUNCTION fn_historico_alteracao_imutavel();
```

Nota: a flag `app.expurgo_autorizado` não é fronteira de segurança (quem tem SQL também pode
`DROP TRIGGER`); ela protege contra bug de aplicação/ORM — o mesmo nível de garantia dos triggers
da 0046.

#### Validação (2)

```sql
CREATE OR REPLACE FUNCTION fn_consumo_sessao_validar() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
DECLARE
    v_item_compra    bigint;
    v_item_proc      bigint;
    v_item_qtd       smallint;
    v_atend_proc     bigint;
    v_atend_retorno  boolean;
    v_usadas         integer;
BEGIN
    SELECT compra_pacote_id, procedimento_id, qtd_sessoes
      INTO v_item_compra, v_item_proc, v_item_qtd
      FROM compra_pacote_item WHERE id = NEW.compra_pacote_item_id
       FOR UPDATE;                                -- serializa débitos concorrentes do mesmo item
    SELECT procedimento_id, eh_retorno INTO v_atend_proc, v_atend_retorno
      FROM atendimento WHERE id = NEW.atendimento_id;
    IF v_item_compra IS NULL OR v_atend_proc IS NULL THEN
        RETURN NEW;                               -- a FK (deferida) acusa
    END IF;
    IF v_item_compra <> NEW.compra_pacote_id THEN
        RAISE EXCEPTION 'consumo_sessao: item % nao pertence a compra %', NEW.compra_pacote_item_id, NEW.compra_pacote_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF v_atend_retorno THEN
        RAISE EXCEPTION 'consumo_sessao: retorno gratuito nao consome sessao de pacote (atendimento %)', NEW.atendimento_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_atend_proc <> v_item_proc THEN
        RAISE EXCEPTION 'consumo_sessao: procedimento do atendimento % difere do item %', NEW.atendimento_id, NEW.compra_pacote_item_id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT count(*) INTO v_usadas FROM consumo_sessao WHERE compra_pacote_item_id = NEW.compra_pacote_item_id;
    IF v_usadas >= v_item_qtd THEN
        RAISE EXCEPTION 'consumo_sessao: item % sem saldo (% de %)', NEW.compra_pacote_item_id, v_usadas, v_item_qtd
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER tg_consumo_sessao_validar BEFORE INSERT ON consumo_sessao
    FOR EACH ROW EXECUTE FUNCTION fn_consumo_sessao_validar();

CREATE OR REPLACE FUNCTION fn_movimento_comissao_validar() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
DECLARE
    v_origem movimento_comissao%ROWTYPE;
BEGIN
    IF NEW.tipo = 'CREDITO' THEN
        RETURN NEW;
    END IF;
    SELECT * INTO v_origem FROM movimento_comissao WHERE id = NEW.movimento_comissao_origem_id
       FOR UPDATE;                                -- serializa PAGAMENTO x ESTORNO do mesmo crédito
    IF v_origem.id IS NULL THEN
        RETURN NEW;                               -- a FK (deferida) acusa
    END IF;
    IF v_origem.tipo <> 'CREDITO' THEN
        RAISE EXCEPTION 'movimento_comissao: % deve referenciar um CREDITO (origem %)', NEW.tipo, v_origem.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.profissional_id, NEW.atendimento_id) IS DISTINCT FROM (v_origem.profissional_id, v_origem.atendimento_id)
       OR NEW.valor <> v_origem.valor THEN
        RAISE EXCEPTION 'movimento_comissao: % deve repetir profissional, atendimento e valor do credito %', NEW.tipo, v_origem.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.tipo = 'PAGAMENTO' AND EXISTS (
           SELECT 1 FROM movimento_comissao
            WHERE movimento_comissao_origem_id = v_origem.id AND tipo = 'ESTORNO') THEN
        RAISE EXCEPTION 'movimento_comissao: credito % estornado nao pode ser pago', v_origem.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER tg_movimento_comissao_validar BEFORE INSERT ON movimento_comissao
    FOR EACH ROW EXECUTE FUNCTION fn_movimento_comissao_validar();
```

(`SELECT … FOR UPDATE` numa tabela com trigger de imutabilidade é seguro: trava de linha não
dispara trigger de UPDATE.)

### 5.3 Trilha de alterações (`historico_alteracao`)

**Tabelas com trilha (11):** `prontuario` (dado clínico editável — `prontuario_versao` só pega o que
passa pela view; a trilha pega Django admin/SQL), `atendimento` (status, valores, horário,
profissional), `compra_pacote` (status, reembolso), `carteira` (saldo), `regra_comissao`, `preco`,
`promocao`, `pacote` (dinheiro/regra), `versao_termo` (texto antes do 1º aceite, ativação),
`usuario` (papel/ativação — escalada de privilégio; `senha_hash` redigida), `configuracao`.
**Sem trilha, de propósito:** tabelas append-only/imutáveis (`aceite_termo`, `prontuario_versao`,
`consentimento`, `movimento_*`, `anotacao_sessao`, `compra_pacote_item`, `historico_alteracao`) — a
própria linha já é o registro com autor; `cliente` — é PII pura e o direito ao esquecimento
obrigaria a expurgar a trilha (a `log_auditoria` "Editou cliente" já cobre, com pseudonimização);
tabelas efêmeras (`codigo_otp`, `link_acesso`, `notificacao`).

```sql
CREATE OR REPLACE FUNCTION fn_registrar_historico() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
DECLARE
    v_ignorar  text[] := CASE WHEN TG_NARGS > 0 AND TG_ARGV[0] <> '' THEN string_to_array(TG_ARGV[0], ',') ELSE '{}'::text[] END;
    v_redigir  text[] := CASE WHEN TG_NARGS > 1 AND TG_ARGV[1] <> '' THEN string_to_array(TG_ARGV[1], ',') ELSE '{}'::text[] END;
    v_antes    jsonb;
    v_depois   jsonb;
    v_colunas  jsonb := '[]'::jsonb;
    v_chave    text;
    v_pk       bigint;
    v_cfg      text;
    v_usuario  bigint;
    v_ip       inet;
BEGIN
    IF TG_OP = 'DELETE' THEN v_pk := OLD.id; ELSE v_pk := NEW.id; END IF;
    IF TG_OP <> 'INSERT' THEN v_antes  := to_jsonb(OLD) - v_ignorar; END IF;
    IF TG_OP <> 'DELETE' THEN v_depois := to_jsonb(NEW) - v_ignorar; END IF;

    IF TG_OP = 'UPDATE' THEN
        SELECT coalesce(jsonb_agg(d.key ORDER BY d.key), '[]'::jsonb)
          INTO v_colunas
          FROM jsonb_each(v_depois) AS d
         WHERE d.value IS DISTINCT FROM (v_antes -> d.key);
        IF v_colunas = '[]'::jsonb THEN
            RETURN NULL;                          -- nada relevante mudou (ex.: só atualizado_em)
        END IF;
        SELECT jsonb_object_agg(c, v_antes -> c), jsonb_object_agg(c, v_depois -> c)
          INTO v_antes, v_depois
          FROM jsonb_array_elements_text(v_colunas) AS c;   -- guarda só o que mudou (menos PII duplicada)
    END IF;

    FOREACH v_chave IN ARRAY v_redigir LOOP                -- registra QUE mudou, não o valor
        IF v_antes  ? v_chave THEN v_antes  := jsonb_set(v_antes,  ARRAY[v_chave], '"[redigido]"'); END IF;
        IF v_depois ? v_chave THEN v_depois := jsonb_set(v_depois, ARRAY[v_chave], '"[redigido]"'); END IF;
    END LOOP;

    -- contexto da aplicação (middleware). A auditoria NUNCA pode derrubar a escrita de negócio:
    -- valor inválido/usuário inexistente vira NULL (a FK é deferida e só estouraria no COMMIT).
    v_cfg := current_setting('app.usuario_id', true);
    IF v_cfg ~ '^[0-9]{1,18}$' THEN
        v_usuario := v_cfg::bigint;
        PERFORM 1 FROM usuario WHERE id = v_usuario;
        IF NOT FOUND THEN v_usuario := NULL; END IF;
    END IF;
    BEGIN
        v_ip := NULLIF(current_setting('app.ip_origem', true), '')::inet;
    EXCEPTION WHEN invalid_text_representation THEN
        v_ip := NULL;
    END;

    INSERT INTO historico_alteracao
        (tabela, registro_pk, operacao, colunas_alteradas, dados_antes, dados_depois,
         usuario_autor_id, ip_origem, origem, transacao_ref)
    VALUES
        (TG_TABLE_NAME, v_pk, TG_OP, v_colunas, v_antes, v_depois,
         v_usuario, v_ip, left(coalesce(current_setting('app.origem', true), ''), 60),
         pg_current_xact_id()::text::bigint);
    RETURN NULL;                                  -- AFTER trigger
END $$;

CREATE TRIGGER tg_prontuario_historico AFTER INSERT OR UPDATE OR DELETE ON prontuario
    FOR EACH ROW EXECUTE FUNCTION fn_registrar_historico('atualizado_em', '');
-- idem (mesmos argumentos) para: atendimento, compra_pacote, carteira, regra_comissao, preco,
-- promocao, pacote, versao_termo, configuracao
CREATE TRIGGER tg_usuario_historico AFTER INSERT OR UPDATE OR DELETE ON usuario
    FOR EACH ROW EXECUTE FUNCTION fn_registrar_historico('atualizado_em', 'senha_hash');
```

(O `?` é operador jsonb; como o SQL vai com `params=None`, o psycopg2 não o confunde com
parâmetro.)

**Middleware (`aranha_estetica/middleware.py`)** — logo depois de
`django_otp.middleware.OTPMiddleware` no `MIDDLEWARE`:

```python
from django.db import DatabaseError, connections

SQL_CONTEXTO = ("SELECT set_config('app.usuario_id', %s, false), "
                "set_config('app.ip_origem', %s, false), "
                "set_config('app.origem', %s, false)")


def definir_contexto_banco(usuario_id='', ip='', origem='', alias='default'):
    conn = connections[alias]
    if conn.vendor != 'postgresql':
        return
    with conn.cursor() as cur:
        cur.execute(SQL_CONTEXTO, [str(usuario_id or ''), ip or '', (origem or '')[:60]])


class ContextoBancoMiddleware:
    """Autor/IP/origem da request p/ fn_registrar_historico (GUCs app.*).

    set_config(..., false) = nível de SESSÃO: o Django roda em autocommit (ATOMIC_REQUESTS=False),
    então SET LOCAL morreria no fim do próprio statement. Como CONN_MAX_AGE=600 reaproveita a
    conexão no próximo request da mesma thread, o contexto é SEMPRE definido no início (''
    p/ anônimo) e LIMPO no fim; se a limpeza falhar, a conexão é fechada (não volta com o
    usuário de outra pessoa). Roda fora de transaction.atomic: um rollback do request não
    desfaz o valor no meio do caminho.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        conn = connections['default']
        if conn.vendor != 'postgresql':
            return self.get_response(request)
        from .utils.audit import _ip_valido
        from .utils.security import client_ip
        user = getattr(request, 'user', None)
        uid = str(user.pk) if user is not None and user.is_authenticated else ''
        definir_contexto_banco(uid, _ip_valido(client_ip(request)) or '', 'web')
        try:
            return self.get_response(request)
        finally:
            try:
                definir_contexto_banco()
            except DatabaseError:
                conn.close()
```

Complementos: (1) `connection_created` (em `AppConfig.ready`) chama `definir_contexto_banco` na
conexão nova (valor inicial vazio, defesa em profundidade); (2) `views/cron.py` chama
`definir_contexto_banco('', ip, f'cron:{job}')` antes de rodar o job (o `finally` do middleware
limpa); (3) Celery real (`CELERY_WORKER_ENABLED`): sinais `task_prerun`/`task_postrun` com
`origem=f'celery:{task.name}'`; (4) `migrate_atomico`: `SET LOCAL app.origem = 'migracao'` logo após o
`lock_timeout` (é transação única, então `LOCAL` basta). Cuidados: gunicorn com threads = uma conexão
por thread (conexões do Django são *thread-local*) ✓; resposta em *streaming* roda depois do
`finally` (nenhuma escreve hoje); **PgBouncer em modo transação quebra GUC de sessão** — o Railway
não usa hoje; se entrar, trocar para `ATOMIC_REQUESTS` + `SET LOCAL`. Custo: 2 round-trips por
request dinâmico (estáticos saem antes, pelo WhiteNoise).

Teste (`tests/test_pg_historico.py`, só PG): usuário logado salva o prontuário → linha com
`usuario_autor_id` dele e `origem='web'`; em seguida request anônimo **na mesma conexão** cria um
agendamento → trilha do `atendimento` com `usuario_autor_id NULL` (prova do reset); UPDATE só de
`atualizado_em` não gera linha; `senha_hash` aparece como `"[redigido]"`.

### 5.4 Views de relatório (9)

Para relatório/BI/SQL ad hoc. **O app não lê views** (o SQLite dos testes não as tem); cada uma
tem teste de paridade contra a agregação ORM equivalente (§5.8).

```sql
-- Agenda por dia local (filtre por data_hora_inicio em intervalo p/ usar índice; data_local é derivada)
CREATE OR REPLACE VIEW vw_agenda_dia AS
SELECT a.id                                              AS atendimento_id,
       fn_data_local(a.data_hora_inicio)                 AS data_local,
       (a.data_hora_inicio AT TIME ZONE fn_fuso_clinica())::time AS hora_inicio_local,
       (a.data_hora_fim    AT TIME ZONE fn_fuso_clinica())::time AS hora_fim_local,
       a.profissional_id, pf.nome AS profissional_nome,
       a.cliente_id, c.nome AS cliente_nome, c.telefone AS cliente_telefone,
       a.procedimento_id, pr.nome AS procedimento_nome,
       a.status, a.eh_retorno, a.valor_cobrado,
       (cs.id IS NOT NULL)                               AS eh_sessao_pacote,
       a.data_hora_inicio, a.data_hora_fim
  FROM atendimento a
  JOIN profissional pf ON pf.id = a.profissional_id
  JOIN cliente c       ON c.id  = a.cliente_id
  JOIN procedimento pr ON pr.id = a.procedimento_id
  LEFT JOIN consumo_sessao cs ON cs.atendimento_id = a.id;

-- Faturamento por mês local — mesma regra de admin_financeiro._calcular_metricas (D20):
-- avulso = REALIZADO, não retorno, valor > 0, sem consumo de pacote; pacote = líquido do reembolso
CREATE OR REPLACE VIEW vw_faturamento_mensal AS
WITH avulsos AS (
    SELECT date_trunc('month', a.data_hora_inicio AT TIME ZONE fn_fuso_clinica())::date AS mes,
           sum(a.valor_cobrado) AS valor, count(*) AS qtd
      FROM atendimento a
      LEFT JOIN consumo_sessao cs ON cs.atendimento_id = a.id
     WHERE a.status = 'REALIZADO' AND NOT a.eh_retorno AND a.valor_cobrado > 0 AND cs.id IS NULL
     GROUP BY 1
), pacotes AS (
    SELECT date_trunc('month', cp.criado_em AT TIME ZONE fn_fuso_clinica())::date AS mes,
           sum(cp.valor_pago - cp.valor_reembolsado) AS valor, count(*) AS qtd
      FROM compra_pacote cp
     WHERE cp.valor_pago > cp.valor_reembolsado
     GROUP BY 1
)
SELECT coalesce(a.mes, p.mes)                                        AS mes,
       coalesce(a.valor, 0)::numeric(12,2)                           AS valor_atendimentos,
       coalesce(a.qtd, 0)                                            AS qtd_atendimentos,
       coalesce(p.valor, 0)::numeric(12,2)                           AS valor_pacotes,
       coalesce(p.qtd, 0)                                            AS qtd_pacotes,
       (coalesce(a.valor, 0) + coalesce(p.valor, 0))::numeric(12,2)  AS valor_total,
       CASE WHEN a.qtd > 0 THEN round(a.valor / a.qtd, 2) END        AS valor_ticket_medio
  FROM avulsos a
  FULL JOIN pacotes p ON p.mes = a.mes;

CREATE OR REPLACE VIEW vw_nps_mensal AS
SELECT date_trunc('month', n.criado_em AT TIME ZONE fn_fuso_clinica())::date AS mes,
       count(*)                                      AS qtd_respostas,
       count(*) FILTER (WHERE n.nota >= 9)           AS qtd_promotores,
       count(*) FILTER (WHERE n.nota BETWEEN 7 AND 8) AS qtd_neutros,
       count(*) FILTER (WHERE n.nota <= 6)           AS qtd_detratores,
       round((count(*) FILTER (WHERE n.nota >= 9) - count(*) FILTER (WHERE n.nota <= 6)) * 100.0
             / count(*))::integer                    AS nps,
       round(avg(n.nota), 1)                         AS nota_media
  FROM avaliacao_nps n
 GROUP BY 1;

-- 1 linha por CREDITO com status derivado (os UNIQUE parciais garantem ≤ 1 pagamento e ≤ 1 estorno)
CREATE OR REPLACE VIEW vw_comissao AS
SELECT c.id AS movimento_comissao_id, c.profissional_id, c.atendimento_id, c.regra_comissao_id,
       c.valor, c.criado_em,
       p.criado_em AS pago_em,
       e.criado_em AS estornado_em,
       CASE WHEN e.id IS NOT NULL THEN 'ESTORNADA'
            WHEN p.id IS NOT NULL THEN 'PAGA'
            ELSE 'PENDENTE' END AS status
  FROM movimento_comissao c
  LEFT JOIN movimento_comissao p ON p.movimento_comissao_origem_id = c.id AND p.tipo = 'PAGAMENTO'
  LEFT JOIN movimento_comissao e ON e.movimento_comissao_origem_id = c.id AND e.tipo = 'ESTORNO'
 WHERE c.tipo = 'CREDITO';

CREATE OR REPLACE VIEW vw_comissao_saldo AS
SELECT pf.id AS profissional_id, pf.nome AS profissional_nome,
       coalesce(sum(m.valor) FILTER (WHERE m.tipo = 'CREDITO'), 0)::numeric(12,2)   AS valor_creditado,
       coalesce(sum(m.valor) FILTER (WHERE m.tipo = 'PAGAMENTO'), 0)::numeric(12,2) AS valor_pago,
       coalesce(sum(m.valor) FILTER (WHERE m.tipo = 'ESTORNO'), 0)::numeric(12,2)   AS valor_estornado,
       coalesce(sum(CASE WHEN m.tipo = 'CREDITO' THEN m.valor ELSE -m.valor END), 0)::numeric(12,2)
                                                                                   AS valor_saldo_a_pagar,
       (SELECT coalesce(sum(v.valor), 0) FROM vw_comissao v
         WHERE v.profissional_id = pf.id AND v.status = 'PENDENTE')::numeric(12,2) AS valor_pendente
  FROM profissional pf
  LEFT JOIN movimento_comissao m ON m.profissional_id = pf.id
 GROUP BY pf.id, pf.nome;

CREATE OR REPLACE VIEW vw_cliente_resumo AS
SELECT c.id AS cliente_id, c.nome, c.telefone, c.email,
       c.eh_ativo, c.eh_bloqueado_online, c.qtd_faltas_consecutivas,
       (c.deletado_em IS NOT NULL)                        AS eh_excluido,
       (c.nome LIKE '[ANONIMIZADO-%')                     AS eh_anonimizado,
       c.criado_em,
       coalesce(ag.qtd_realizados, 0)                     AS qtd_realizados,
       coalesce(ag.qtd_faltas, 0)                         AS qtd_faltas,
       coalesce(ag.qtd_cancelados, 0)                     AS qtd_cancelados,
       ag.primeiro_realizado_em, ag.ultimo_realizado_em, ag.proximo_atendimento_em,
       coalesce(ag.valor_avulsos, 0)::numeric(12,2)       AS valor_avulsos,
       coalesce(pc.valor_pacotes, 0)::numeric(12,2)       AS valor_pacotes,
       (coalesce(ag.valor_avulsos, 0) + coalesce(pc.valor_pacotes, 0))::numeric(12,2) AS valor_total_gasto,
       coalesce(pc.qtd_pacotes_ativos, 0)                 AS qtd_pacotes_ativos,
       coalesce(ct.valor_saldo, 0)                        AS valor_saldo_carteira,
       EXISTS (SELECT 1 FROM prontuario p WHERE p.cliente_id = c.id
                  AND (p.alergias <> '' OR p.contraindicacoes <> '' OR p.historico_saude <> ''
                       OR p.medicamentos_uso <> '' OR p.observacoes_gerais <> '' OR p.respostas <> '{}'::jsonb))
                                                          AS tem_prontuario
  FROM cliente c
  LEFT JOIN LATERAL (
       SELECT count(*) FILTER (WHERE a.status = 'REALIZADO')                         AS qtd_realizados,
              count(*) FILTER (WHERE a.status = 'FALTOU')                            AS qtd_faltas,
              count(*) FILTER (WHERE a.status = 'CANCELADO')                         AS qtd_cancelados,
              min(a.data_hora_inicio) FILTER (WHERE a.status = 'REALIZADO')          AS primeiro_realizado_em,
              max(a.data_hora_inicio) FILTER (WHERE a.status = 'REALIZADO')          AS ultimo_realizado_em,
              min(a.data_hora_inicio) FILTER (WHERE a.status IN ('PENDENTE', 'AGENDADO', 'CONFIRMADO')
                                                AND a.data_hora_inicio >= now())      AS proximo_atendimento_em,
              sum(a.valor_cobrado) FILTER (WHERE a.status = 'REALIZADO' AND NOT a.eh_retorno
                                             AND a.valor_cobrado > 0 AND cs.id IS NULL) AS valor_avulsos
         FROM atendimento a
         LEFT JOIN consumo_sessao cs ON cs.atendimento_id = a.id
        WHERE a.cliente_id = c.id) ag ON true
  LEFT JOIN LATERAL (
       SELECT sum(cp.valor_pago - cp.valor_reembolsado)   AS valor_pacotes,
              count(*) FILTER (WHERE cp.status = 'ATIVO') AS qtd_pacotes_ativos
         FROM compra_pacote cp WHERE cp.cliente_id = c.id) pc ON true
  LEFT JOIN carteira ct ON ct.cliente_id = c.id;

CREATE OR REPLACE VIEW vw_consentimento_atual AS
SELECT DISTINCT ON (cliente_id, finalidade, canal)
       cliente_id, finalidade, canal, eh_concedido, registrado_em, origem, id AS consentimento_id
  FROM consentimento
 ORDER BY cliente_id, finalidade, canal, registrado_em DESC, id DESC;

CREATE OR REPLACE VIEW vw_pacote_saldo AS
SELECT i.compra_pacote_id, cp.cliente_id, cp.pacote_nome, cp.status, cp.data_expiracao,
       i.id AS compra_pacote_item_id, i.procedimento_id, i.procedimento_nome,
       i.qtd_sessoes                                AS qtd_total,
       count(cs.id)                                 AS qtd_usada,
       greatest(i.qtd_sessoes - count(cs.id), 0)    AS qtd_restante
  FROM compra_pacote_item i
  JOIN compra_pacote cp ON cp.id = i.compra_pacote_id
  LEFT JOIN consumo_sessao cs ON cs.compra_pacote_item_id = i.id
 GROUP BY i.id, cp.id;

-- o que tasks.job_limpeza_status_atendimentos só joga no log (D14: a equipe decide falta/realizado)
CREATE OR REPLACE VIEW vw_atendimento_sem_desfecho AS
SELECT a.id AS atendimento_id, a.profissional_id, a.cliente_id, a.status,
       a.data_hora_inicio, a.data_hora_fim
  FROM atendimento a
 WHERE a.status IN ('AGENDADO', 'CONFIRMADO')
   AND a.data_hora_fim < now() - interval '24 hours';
```

**Achados ao escrever as views (divergências de regra que já existem no código):**
1. `dashboard.painel_overview` calcula o ticket médio com `realizados_mes` **sem** `valor_cobrado > 0`
   (conta atendimento sem valor), enquanto `admin_financeiro._calcular_metricas` usa `valor > 0` — as
   duas telas mostram ticket médio diferente para o mesmo mês. As views seguem o financeiro.
2. `admin_management._contexto_cliente` mostra "LTV" = Σ `valor_cobrado` de **todo** REALIZADO,
   incluindo sessão de pacote (preço cheio) — conta a receita do pacote duas vezes (contra a D20).
   `vw_cliente_resumo.valor_total_gasto` segue a D20.
3. NPS: `round()` do Python é arredondamento **bancário** (`round(12.5) == 12`, conferido no Python
   do projeto) e o `round(numeric)` do PG arredonda para longe do zero (13). Paridade exige trocar o
   Python (`dashboard.py` l. 135, `relatorios.py` l. 67–68/100) por um helper
   `utils/nps.calcular_nps` com `Decimal.quantize(..., ROUND_HALF_UP)` — muda o número exibido em 1
   ponto nos casos .5 (D7 no §7).

### 5.5 Funções de leitura (espelho do Python, com teste de paridade)

```sql
-- Subconjunto de RRULE: casa um dia com a regra (DTSTART = p_inicio)
CREATE OR REPLACE FUNCTION fn_recorrencia_casa_dia(
    p_freq text, p_intervalo integer, p_byday text[], p_bymonthday integer,
    p_inicio date, p_dia date)
RETURNS boolean
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
DECLARE
    v_codigo text := (ARRAY['MO','TU','WE','TH','FR','SA','SU'])[extract(isodow FROM p_dia)::int];
    v_item   text;
    v_ord    text;
BEGIN
    IF p_dia < p_inicio THEN
        RETURN false;
    END IF;
    IF p_freq = 'DAILY' THEN
        RETURN (p_dia - p_inicio) % p_intervalo = 0
           AND (p_byday IS NULL OR v_codigo = ANY (p_byday));
    ELSIF p_freq = 'WEEKLY' THEN            -- semanas contadas a partir da segunda (WKST=MO, padrão)
        IF ((date_trunc('week', p_dia::timestamp)::date
             - date_trunc('week', p_inicio::timestamp)::date) / 7) % p_intervalo <> 0 THEN
            RETURN false;
        END IF;
        IF p_byday IS NULL THEN
            RETURN extract(isodow FROM p_dia) = extract(isodow FROM p_inicio);
        END IF;
        RETURN v_codigo = ANY (p_byday);
    ELSIF p_freq = 'MONTHLY' THEN
        IF ((extract(year FROM p_dia) - extract(year FROM p_inicio)) * 12
            + extract(month FROM p_dia) - extract(month FROM p_inicio))::int % p_intervalo <> 0 THEN
            RETURN false;
        END IF;
        IF p_bymonthday IS NOT NULL THEN
            RETURN extract(day FROM p_dia) = p_bymonthday;
        END IF;
        IF p_byday IS NULL THEN                 -- mesmo dia do mês do DTSTART (31 pula meses curtos)
            RETURN extract(day FROM p_dia) = extract(day FROM p_inicio);
        END IF;
        FOREACH v_item IN ARRAY p_byday LOOP
            CONTINUE WHEN right(v_item, 2) <> v_codigo;
            v_ord := left(v_item, length(v_item) - 2);
            IF v_ord = '' THEN
                RETURN true;
            ELSIF v_ord = '-1' THEN
                IF extract(month FROM p_dia + 7) <> extract(month FROM p_dia) THEN RETURN true; END IF;
            ELSIF (extract(day FROM p_dia)::int - 1) / 7 + 1 = v_ord::int THEN
                RETURN true;
            END IF;
        END LOOP;
        RETURN false;
    END IF;
    RAISE EXCEPTION 'FREQ fora do subconjunto suportado: %', p_freq;
END $$;

-- Ocorrências de um bloqueio recorrente que tocam [p_inicio, p_fim) — replica
-- BloqueioAgenda.expandir_ocorrencias: rrule.between(inicio - duração, fim_busca, inc=True),
-- descarta início >= fim_busca, mantém as que intersectam; DTSTART e horários na hora LOCAL;
-- DTSTART que não casa com a regra NÃO é ocorrência (comportamento do dateutil, conferido).
CREATE OR REPLACE FUNCTION fn_ocorrencias_recorrencia(
    p_regra text, p_dtstart timestamptz, p_duracao interval, p_data_fim_recorrencia date,
    p_inicio timestamptz, p_fim timestamptz)
RETURNS SETOF tstzrange
LANGUAGE plpgsql STABLE SET search_path = public, pg_catalog AS $$
DECLARE
    v_tz         constant text := fn_fuso_clinica();
    v_d0         timestamp := p_dtstart AT TIME ZONE v_tz;
    v_freq       text;
    v_intervalo  integer := 1;
    v_byday      text[];
    v_bymonthday integer;
    v_parte      text;
    v_valor      text;
    v_limite     timestamptz := p_fim;
    v_dia        date;
    v_ini        timestamptz;
BEGIN
    FOREACH v_parte IN ARRAY string_to_array(p_regra, ';') LOOP
        v_valor := split_part(v_parte, '=', 2);
        CASE split_part(v_parte, '=', 1)
            WHEN 'FREQ'       THEN v_freq := v_valor;
            WHEN 'INTERVAL'   THEN v_intervalo := v_valor::int;
            WHEN 'BYDAY'      THEN v_byday := string_to_array(v_valor, ',');
            WHEN 'BYMONTHDAY' THEN v_bymonthday := v_valor::int;
            ELSE RAISE EXCEPTION 'regra fora do subconjunto suportado: %', p_regra;
        END CASE;
    END LOOP;
    IF p_data_fim_recorrencia IS NOT NULL THEN     -- inclusivo: até o fim do dia local
        v_limite := least(p_fim, (p_data_fim_recorrencia + 1)::timestamp AT TIME ZONE v_tz);
    END IF;
    FOR v_dia IN
        SELECT d::date FROM generate_series(
            greatest(v_d0::date, ((p_inicio - p_duracao) AT TIME ZONE v_tz)::date)::timestamp,
            (v_limite AT TIME ZONE v_tz)::date::timestamp,
            interval '1 day') AS d
    LOOP
        CONTINUE WHEN NOT fn_recorrencia_casa_dia(v_freq, v_intervalo, v_byday, v_bymonthday, v_d0::date, v_dia);
        v_ini := (v_dia + v_d0::time) AT TIME ZONE v_tz;
        IF v_ini >= p_inicio - p_duracao AND v_ini < v_limite
           AND v_ini + p_duracao > p_inicio AND v_ini < p_fim THEN
            RETURN NEXT tstzrange(v_ini, v_ini + p_duracao, '[)');
        END IF;
    END LOOP;
END $$;

-- Espelho de SlotService.slots_livres (depois da reestruturação (a)). p_agora permite teste
-- determinístico (o Python usa timezone.now()).
CREATE OR REPLACE FUNCTION fn_horarios_livres(
    p_profissional_id         bigint,
    p_data                    date,
    p_procedimento_id         bigint      DEFAULT NULL,
    p_atendimento_ignorado_id bigint      DEFAULT NULL,
    p_agora                   timestamptz DEFAULT now())
RETURNS SETOF time
LANGUAGE plpgsql STABLE SET search_path = public, pg_catalog AS $$
DECLARE
    v_tz          constant text := fn_fuso_clinica();
    v_passo       constant interval := interval '30 minutes';
    v_agora_local timestamp := p_agora AT TIME ZONE v_tz;
    v_dia_inicio  timestamptz := p_data::timestamp AT TIME ZONE v_tz;
    v_dia_fim     timestamptz := (p_data + 1)::timestamp AT TIME ZONE v_tz;
    v_antec_min   smallint;
    v_antec_max   smallint;
    v_duracao     interval := interval '30 minutes';
    v_intervalo   interval := interval '0';
    v_limite_min  timestamptz;
    v_ocupados    tstzrange[] := '{}';
    v_bloqueios   tstzrange[] := '{}';
    v_livres      time[] := '{}';
    v_janela      record;
    v_bl          record;
    v_slot        timestamptz;
    v_fim_slot    timestamptz;
    v_fim_exped   timestamptz;
BEGIN
    SELECT antecedencia_minima_horas, antecedencia_maxima_dias INTO v_antec_min, v_antec_max
      FROM profissional WHERE id = p_profissional_id;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    IF p_data < v_agora_local::date
       OR p_data > (v_agora_local + make_interval(days => v_antec_max))::date THEN
        RETURN;                                           -- passado ou além da antecedência máxima
    END IF;
    v_limite_min := p_agora + make_interval(hours => v_antec_min);

    IF EXISTS (SELECT 1 FROM agenda_excecao e             -- folga do profissional ou feriado/recesso
                WHERE e.eh_ativo AND e.tipo = 'FOLGA'
                  AND (e.profissional_id = p_profissional_id OR e.profissional_id IS NULL)
                  AND p_data BETWEEN e.data_inicio AND e.data_fim) THEN
        RETURN;
    END IF;

    IF p_procedimento_id IS NOT NULL THEN
        SELECT CASE WHEN pr.duracao_minutos > 0 THEN make_interval(mins => pr.duracao_minutos) ELSE v_passo END,
               make_interval(mins => pr.intervalo_minutos)
          INTO v_duracao, v_intervalo
          FROM procedimento pr WHERE pr.id = p_procedimento_id;
        IF NOT FOUND THEN
            v_duracao := v_passo;
            v_intervalo := interval '0';
        END IF;
    END IF;

    SELECT coalesce(array_agg(tstzrange(a.data_hora_inicio,
                                        a.data_hora_fim + make_interval(mins => pr.intervalo_minutos), '[)')), '{}')
      INTO v_ocupados
      FROM atendimento a
      JOIN procedimento pr ON pr.id = a.procedimento_id
     WHERE a.profissional_id = p_profissional_id
       AND a.data_hora_inicio < v_dia_fim AND a.data_hora_fim > v_dia_inicio
       AND a.status IN ('PENDENTE', 'AGENDADO', 'CONFIRMADO')
       AND (p_atendimento_ignorado_id IS NULL OR a.id <> p_atendimento_ignorado_id);

    FOR v_bl IN
        SELECT e.data_hora_inicio, e.data_hora_fim, e.regra_recorrencia, e.data_fim_recorrencia
          FROM agenda_excecao e
         WHERE e.eh_ativo AND e.tipo = 'BLOQUEIO'
           AND (e.profissional_id = p_profissional_id OR e.profissional_id IS NULL)
           AND e.data_hora_inicio < v_dia_fim
           AND (e.regra_recorrencia IS NOT NULL OR e.data_hora_fim > v_dia_inicio)
    LOOP
        IF v_bl.regra_recorrencia IS NULL THEN
            v_bloqueios := v_bloqueios || tstzrange(v_bl.data_hora_inicio, v_bl.data_hora_fim, '[)');
        ELSE
            v_bloqueios := v_bloqueios || ARRAY(
                SELECT fn_ocorrencias_recorrencia(v_bl.regra_recorrencia, v_bl.data_hora_inicio,
                                                  v_bl.data_hora_fim - v_bl.data_hora_inicio,
                                                  v_bl.data_fim_recorrencia, v_dia_inicio, v_dia_fim));
        END IF;
    END LOOP;

    FOR v_janela IN
        WITH hd AS (
            SELECT e.hora_inicio, e.hora_fim FROM agenda_excecao e
             WHERE e.eh_ativo AND e.tipo = 'HORARIO_DIFERENTE'
               AND e.profissional_id = p_profissional_id AND e.data_inicio = p_data)
        SELECT hd.hora_inicio, hd.hora_fim FROM hd
        UNION ALL
        SELECT h.hora_inicio, h.hora_fim FROM agenda_horario h
         WHERE h.profissional_id = p_profissional_id
           AND h.dia_semana = extract(isodow FROM p_data)::int
           AND NOT EXISTS (SELECT 1 FROM hd)
    LOOP
        v_slot      := (p_data + v_janela.hora_inicio) AT TIME ZONE v_tz;
        v_fim_exped := (p_data + v_janela.hora_fim) AT TIME ZONE v_tz;
        WHILE v_slot + v_duracao <= v_fim_exped LOOP     -- o procedimento inteiro cabe no expediente
            IF v_slot >= v_limite_min THEN
                v_fim_slot := v_slot + v_duracao;
                IF NOT (tstzrange(v_slot, v_fim_slot + v_intervalo, '[)') && ANY (v_ocupados))
                   AND NOT (tstzrange(v_slot, v_fim_slot, '[)') && ANY (v_bloqueios)) THEN
                    v_livres := v_livres || (v_slot AT TIME ZONE v_tz)::time;
                END IF;
            END IF;
            v_slot := v_slot + v_passo;
        END LOOP;
    END LOOP;

    RETURN QUERY SELECT DISTINCT t FROM unnest(v_livres) AS t ORDER BY t;
END $$;

CREATE OR REPLACE FUNCTION fn_saldo_pacote(p_compra_pacote_id bigint)
RETURNS TABLE (compra_pacote_item_id bigint, procedimento_id bigint, procedimento_nome varchar,
               qtd_total smallint, qtd_usada bigint, qtd_restante bigint)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT i.id, i.procedimento_id, i.procedimento_nome, i.qtd_sessoes,
           count(cs.id), greatest(i.qtd_sessoes - count(cs.id), 0)
      FROM compra_pacote_item i
      LEFT JOIN consumo_sessao cs ON cs.compra_pacote_item_id = i.id
     WHERE i.compra_pacote_id = p_compra_pacote_id
     GROUP BY i.id
     ORDER BY i.id;
END;

CREATE OR REPLACE FUNCTION fn_nps_periodo(p_inicio timestamptz DEFAULT NULL, p_fim timestamptz DEFAULT NULL)
RETURNS TABLE (qtd_respostas bigint, qtd_promotores bigint, qtd_neutros bigint, qtd_detratores bigint,
               nps integer, nota_media numeric)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT count(*),
           count(*) FILTER (WHERE n.nota >= 9),
           count(*) FILTER (WHERE n.nota BETWEEN 7 AND 8),
           count(*) FILTER (WHERE n.nota <= 6),
           CASE WHEN count(*) > 0 THEN
                round((count(*) FILTER (WHERE n.nota >= 9) - count(*) FILTER (WHERE n.nota <= 6)) * 100.0
                      / count(*))::integer END,
           round(avg(n.nota), 1)
      FROM avaliacao_nps n
     WHERE (p_inicio IS NULL OR n.criado_em >= p_inicio)
       AND (p_fim IS NULL OR n.criado_em < p_fim);
END;

CREATE OR REPLACE FUNCTION fn_saldo_comissao(p_profissional_id bigint, p_ate timestamptz DEFAULT NULL)
RETURNS numeric
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT coalesce(sum(CASE WHEN m.tipo = 'CREDITO' THEN m.valor ELSE -m.valor END), 0)::numeric(12,2)
      FROM movimento_comissao m
     WHERE m.profissional_id = p_profissional_id
       AND (p_ate IS NULL OR m.criado_em <= p_ate);
END;

CREATE OR REPLACE FUNCTION fn_tem_consentimento(p_cliente_id bigint, p_finalidade text, p_canal text)
RETURNS boolean
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT coalesce((SELECT c.eh_concedido FROM consentimento c
                      WHERE c.cliente_id = p_cliente_id AND c.finalidade = p_finalidade AND c.canal = p_canal
                      ORDER BY c.registrado_em DESC, c.id DESC LIMIT 1), false);
END;

-- Espelho de LgpdService.candidatos_purga (usado por sp_aplicar_retencao_lgpd)
CREATE OR REPLACE FUNCTION fn_clientes_para_anonimizar(
    p_retencao_cliente_dias integer, p_carencia_soft_dias integer, p_retencao_saude_dias integer,
    p_agora timestamptz DEFAULT now())
RETURNS SETOF bigint
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT c.id FROM cliente c
     WHERE ((c.deletado_em IS NULL AND c.criado_em < p_agora - make_interval(days => p_retencao_cliente_dias))
            OR c.deletado_em < p_agora - make_interval(days => p_carencia_soft_dias))
       AND c.nome NOT LIKE '[ANONIMIZADO-%'
       AND NOT EXISTS (SELECT 1 FROM atendimento a WHERE a.cliente_id = c.id
                          AND a.data_hora_inicio >= p_agora - make_interval(days => p_retencao_cliente_dias))
       AND NOT EXISTS (SELECT 1 FROM atendimento a WHERE a.cliente_id = c.id AND a.status = 'REALIZADO'
                          AND a.data_hora_inicio >= p_agora - make_interval(days => p_retencao_saude_dias))
       AND NOT EXISTS (SELECT 1 FROM prontuario p WHERE p.cliente_id = c.id
                          AND (p.alergias <> '' OR p.contraindicacoes <> '' OR p.historico_saude <> ''
                               OR p.medicamentos_uso <> '' OR p.observacoes_gerais <> '' OR p.respostas <> '{}'::jsonb))
       AND NOT EXISTS (SELECT 1 FROM prontuario_versao v JOIN prontuario p ON p.id = v.prontuario_id
                        WHERE p.cliente_id = c.id)
       AND NOT EXISTS (SELECT 1 FROM compra_pacote cp WHERE cp.cliente_id = c.id)
     ORDER BY c.id;
END;
```

Notas de paridade: `timedelta(days=N)` do Python e `make_interval(days => N)` do PG coincidem
porque a sessão do Django está em UTC (sem horário de verão); o laço de slots usa aritmética
absoluta (`+ 30 min`) nos dois lados; hora local vem sempre do fuso explícito. Diferença conhecida
e aceita: em transição de horário de verão (não há no Brasil desde 2019) o `make_aware` do Python
(fold=0) e o `AT TIME ZONE` do PG resolvem hora inexistente de formas diferentes — a matriz de
teste usa datas atuais.

**Busca de cliente com trigram:** `utils/busca.q_busca_cliente` devolve um `Q` usado em vários
querysets; vira `filtrar_clientes(qs, termo)` que, no PG, anota
`nome_busca=Func(F('nome'), function='fn_sem_acento', output_field=TextField())` e filtra
`nome_busca__contains=Func(Value(termo), function='fn_sem_acento')` (a expressão do índice
`ix_cliente_nome_trgm` casa com a da query; `pg_trgm` atende `LIKE '%x%'` a partir de 3
caracteres); no SQLite mantém `nome__icontains`. Resultado: "joao" acha "João".

### 5.6 Procedures (fonte única no Postgres)

O Python chama por `utils/banco.py`:

```python
PROCEDURES = {'sp_anonimizar_cliente': 3, 'sp_aplicar_retencao_lgpd': 8,
              'sp_purgar_dados_expirados': 10, 'sp_expirar_pacotes': 2}

def usa_logica_pg(alias='default') -> bool:
    return connections[alias].vendor == 'postgresql' and not getattr(settings, 'FORCAR_LOGICA_PYTHON', False)

def chamar(nome, *args):
    """CALL com allowlist (nome nunca vem de input). INOUT vão como None e voltam na tupla."""
    if PROCEDURES.get(nome) != len(args):
        raise ValueError(nome)
    with connection.cursor() as cur:
        cur.execute(f"CALL {nome}({', '.join(['%s'] * len(args))})", list(args))
        return cur.fetchone() if cur.description else None
```

`FORCAR_LOGICA_PYTHON` existe **só** para o teste de paridade rodar o fallback no PG; em produção é
sempre `False`. O fallback Python fica apenas para o SQLite (testes locais).

```sql
CREATE OR REPLACE PROCEDURE sp_expirar_pacotes(
    IN    p_hoje          date    DEFAULT NULL,
    INOUT qtd_expirados   integer DEFAULT NULL)
LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
BEGIN
    UPDATE compra_pacote
       SET status = 'EXPIRADO'
     WHERE status = 'ATIVO'
       AND data_expiracao < coalesce(p_hoje, fn_data_local(now()));   -- o último dia ainda vale
    GET DIAGNOSTICS qtd_expirados = ROW_COUNT;
END $$;

CREATE OR REPLACE PROCEDURE sp_anonimizar_cliente(
    IN p_cliente_id        bigint,
    IN p_usuario_autor_id  bigint DEFAULT NULL,
    IN p_ip_origem         inet   DEFAULT NULL)
LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
DECLARE
    v_cli         cliente%ROWTYPE;
    v_pseudo      text := '[ANONIMIZADO-' || p_cliente_id || ']';
    v_agora       timestamptz := now();
    v_atend_ids   bigint[];
    v_compra_ids  bigint[];
    v_espera_ids  bigint[];
    v_pront_ids   bigint[];
    v_nps_ids     bigint[];
    v_notif_ids   bigint[];
    v_cart_ids    bigint[];
    v_autor_nome  text := '';
BEGIN
    SELECT * INTO v_cli FROM cliente WHERE id = p_cliente_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'sp_anonimizar_cliente: cliente % inexistente', p_cliente_id USING ERRCODE = 'no_data_found';
    END IF;

    -- ids antes de apagar (pseudonimização do django_admin_log)
    SELECT coalesce(array_agg(id), '{}') INTO v_atend_ids  FROM atendimento   WHERE cliente_id = p_cliente_id;
    SELECT coalesce(array_agg(id), '{}') INTO v_compra_ids FROM compra_pacote WHERE cliente_id = p_cliente_id;
    SELECT coalesce(array_agg(id), '{}') INTO v_espera_ids FROM lista_espera  WHERE cliente_id = p_cliente_id;
    SELECT coalesce(array_agg(id), '{}') INTO v_pront_ids  FROM prontuario    WHERE cliente_id = p_cliente_id;
    SELECT coalesce(array_agg(id), '{}') INTO v_cart_ids   FROM carteira      WHERE cliente_id = p_cliente_id;
    SELECT coalesce(array_agg(n.id), '{}') INTO v_nps_ids
      FROM avaliacao_nps n WHERE n.atendimento_id = ANY (v_atend_ids);
    SELECT coalesce(array_agg(n.id), '{}') INTO v_notif_ids
      FROM notificacao n WHERE n.atendimento_id = ANY (v_atend_ids);

    -- 1. cadastro
    UPDATE cliente
       SET nome = v_pseudo, cpf = NULL, rg = NULL, email = NULL, telefone = NULL, cep = NULL,
           endereco = NULL, profissao = NULL, data_nascimento = NULL, url_foto = '',
           eh_ativo = false, deletado_em = coalesce(deletado_em, v_agora)
     WHERE id = p_cliente_id;

    -- 2. consentimentos: revoga o que está concedido (histórico fica) e minimiza IP/user-agent
    INSERT INTO consentimento (cliente_id, finalidade, canal, eh_concedido, origem, texto, registrado_em)
    SELECT a.cliente_id, a.finalidade, a.canal, false, 'ESQUECIMENTO',
           'Revogacao automatica (direito ao esquecimento)', v_agora
      FROM vw_consentimento_atual a
     WHERE a.cliente_id = p_cliente_id AND a.eh_concedido;
    UPDATE consentimento SET ip_origem = NULL, user_agent = ''
     WHERE cliente_id = p_cliente_id AND (ip_origem IS NOT NULL OR user_agent <> '');

    -- 3. links públicos do titular deixam de funcionar
    UPDATE link_acesso SET revogado_em = v_agora
     WHERE revogado_em IS NULL
       AND (cliente_id = p_cliente_id
            OR atendimento_id = ANY (v_atend_ids)
            OR notificacao_id = ANY (v_notif_ids)
            OR resposta_formulario_id IN (SELECT id FROM resposta_formulario WHERE cliente_id = p_cliente_id));

    -- 4. OTP (telefone, pseudo-e-mail do fluxo só-telefone, e-mail)
    DELETE FROM codigo_otp
     WHERE (v_cli.telefone IS NOT NULL
            AND (telefone = v_cli.telefone OR identificador = 'sms+' || v_cli.telefone || '@shivazen.local'))
        OR (v_cli.email IS NOT NULL AND upper(identificador) = upper(v_cli.email));

    -- 5. lista de espera e texto das notificações
    DELETE FROM lista_espera WHERE cliente_id = p_cliente_id;
    UPDATE notificacao SET mensagem = '' WHERE id = ANY (v_notif_ids) AND mensagem <> '';

    -- 6. dado de saúde sem atendimento REALIZADO: fim da finalidade (cascata apaga o link_acesso)
    DELETE FROM resposta_formulario r
     WHERE r.cliente_id = p_cliente_id
       AND NOT EXISTS (SELECT 1 FROM atendimento a WHERE a.id = r.atendimento_id AND a.status = 'REALIZADO');

    -- 7. depoimento sai do site e perde o texto livre (a nota fica p/ estatística)
    UPDATE avaliacao_nps
       SET eh_publicacao_autorizada = false, eh_publicacao_aprovada = false, comentario = ''
     WHERE id = ANY (v_nps_ids);

    -- 8. trilha: o nome do titular vira o pseudônimo (autor, data, tabela, id ficam)
    IF length(btrim(v_cli.nome)) >= 3 AND v_cli.nome NOT LIKE '[ANONIMIZADO-%' THEN
        UPDATE log_auditoria SET acao = replace(acao, v_cli.nome, v_pseudo)
         WHERE strpos(acao, v_cli.nome) > 0
           AND ((tabela = 'cliente'       AND registro_pk = p_cliente_id)
             OR (tabela = 'prontuario'    AND registro_pk = ANY (array_append(v_pront_ids, p_cliente_id)))
             OR (tabela = 'atendimento'   AND registro_pk = ANY (v_atend_ids))
             OR (tabela = 'compra_pacote' AND registro_pk = ANY (v_compra_ids)));
    END IF;
    UPDATE log_auditoria SET acao = 'Editou cliente: ' || v_pseudo
     WHERE tabela = 'cliente' AND registro_pk = p_cliente_id AND acao LIKE 'Editou cliente:%';
    UPDATE log_auditoria SET detalhes = NULL
     WHERE tabela = 'cliente' AND registro_pk = p_cliente_id AND acao LIKE 'migration 0034%';

    -- 9. histórico do Django admin (object_repr = str(obj) = nome)
    UPDATE django_admin_log l SET object_repr = v_pseudo
      FROM django_content_type ct
     WHERE ct.id = l.content_type_id AND ct.app_label = 'aranha_estetica'
       AND ((ct.model = 'cliente'       AND l.object_id = p_cliente_id::text)
         OR (ct.model = 'atendimento'   AND l.object_id = ANY (v_atend_ids::text[]))
         OR (ct.model = 'comprapacote'  AND l.object_id = ANY (v_compra_ids::text[]))
         OR (ct.model = 'listaespera'   AND l.object_id = ANY (v_espera_ids::text[]))
         OR (ct.model = 'avaliacaonps'  AND l.object_id = ANY (v_nps_ids::text[]))
         OR (ct.model = 'notificacao'   AND l.object_id = ANY (v_notif_ids::text[]))
         OR (ct.model = 'carteira'      AND l.object_id = ANY (v_cart_ids::text[])));

    -- 10. registro LGPD (sem PII)
    SELECT left(CASE WHEN btrim(u.nome) <> '' AND btrim(u.email) <> '' THEN btrim(u.nome) || ' <' || btrim(u.email) || '>'
                     ELSE coalesce(nullif(btrim(u.nome), ''), btrim(u.email)) END, 255)
      INTO v_autor_nome FROM usuario u WHERE u.id = p_usuario_autor_id;
    INSERT INTO log_auditoria (usuario_autor_id, usuario_autor_nome, acao, tabela, registro_pk, ip_origem)
    VALUES (p_usuario_autor_id, coalesce(v_autor_nome, ''),
            'Cliente anonimizado (direito ao esquecimento / retencao LGPD)', 'cliente', p_cliente_id, p_ip_origem);
END $$;

CREATE OR REPLACE PROCEDURE sp_aplicar_retencao_lgpd(
    IN    p_retencao_cliente_dias  integer,
    IN    p_carencia_soft_dias     integer,
    IN    p_retencao_saude_dias    integer,
    IN    p_retencao_ficha_dias    integer,
    IN    p_confirmar_por_cliente  boolean DEFAULT true,
    INOUT qtd_clientes             integer DEFAULT NULL,
    INOUT qtd_fichas               integer DEFAULT NULL,
    INOUT qtd_esperas              integer DEFAULT NULL)
LANGUAGE plpgsql AS $$                -- sem SET search_path: rotina com SET não pode dar COMMIT
DECLARE
    v_id    bigint;
    v_agora timestamptz := now();
BEGIN
    qtd_clientes := 0;
    FOR v_id IN SELECT public.fn_clientes_para_anonimizar(p_retencao_cliente_dias, p_carencia_soft_dias,
                                                           p_retencao_saude_dias, v_agora)
    LOOP
        CALL public.sp_anonimizar_cliente(v_id, NULL, NULL);
        qtd_clientes := qtd_clientes + 1;
        IF p_confirmar_por_cliente THEN
            COMMIT;                   -- como o laço Python: um aborto no meio preserva os já feitos
        END IF;
    END LOOP;

    -- fichas de pedido não realizado (espelho de LgpdService.fichas_sem_atendimento_para_purga)
    DELETE FROM public.resposta_formulario r
     WHERE r.criado_em < v_agora - make_interval(days => p_retencao_ficha_dias)
       AND (EXISTS (SELECT 1 FROM public.atendimento a WHERE a.id = r.atendimento_id AND a.status = 'CANCELADO')
            OR EXISTS (SELECT 1 FROM public.atendimento a WHERE a.id = r.atendimento_id
                          AND a.status = 'PENDENTE' AND a.data_hora_inicio < v_agora)
            OR (r.atendimento_id IS NULL AND r.respondido_em IS NULL)
            OR (r.respondido_em IS NULL AND r.respostas = '{}'::jsonb
                AND EXISTS (SELECT 1 FROM public.atendimento a WHERE a.id = r.atendimento_id
                               AND a.data_hora_inicio < v_agora)));
    GET DIAGNOSTICS qtd_fichas = ROW_COUNT;
    IF qtd_fichas > 0 THEN
        INSERT INTO public.log_auditoria (acao, tabela, detalhes)
        VALUES ('Purga de fichas de pedido nao realizado', 'resposta_formulario', jsonb_build_object('qtd', qtd_fichas));
    END IF;

    DELETE FROM public.lista_espera WHERE data_desejada < public.fn_data_local(v_agora);
    GET DIAGNOSTICS qtd_esperas = ROW_COUNT;
    IF qtd_esperas > 0 THEN
        INSERT INTO public.log_auditoria (acao, tabela, detalhes)
        VALUES ('Purga da lista de espera vencida', 'lista_espera', jsonb_build_object('qtd', qtd_esperas));
    END IF;
END $$;

CREATE OR REPLACE PROCEDURE sp_purgar_dados_expirados(
    IN    p_retencao_otp_horas         integer,
    IN    p_retencao_notificacao_dias  integer,
    IN    p_retencao_log_dias          integer,
    IN    p_retencao_link_dias         integer DEFAULT 90,
    INOUT qtd_otp                      integer DEFAULT NULL,
    INOUT qtd_notificacoes             integer DEFAULT NULL,
    INOUT qtd_logs                     integer DEFAULT NULL,
    INOUT qtd_links                    integer DEFAULT NULL,
    INOUT qtd_historico                integer DEFAULT NULL,
    INOUT etapas_com_falha             text[]  DEFAULT NULL)
LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
DECLARE
    v_agora timestamptz := now();
BEGIN
    etapas_com_falha := '{}';
    -- cada etapa num sub-bloco (subtransação): falha de uma não impede as outras (= housekeeping)
    BEGIN
        DELETE FROM codigo_otp WHERE criado_em < v_agora - make_interval(hours => p_retencao_otp_horas);
        GET DIAGNOSTICS qtd_otp = ROW_COUNT;
    EXCEPTION WHEN OTHERS THEN
        etapas_com_falha := etapas_com_falha || 'otp'::text;
        RAISE WARNING 'sp_purgar_dados_expirados/otp: %', SQLERRM;
    END;
    BEGIN
        UPDATE notificacao SET mensagem = ''
         WHERE criado_em < v_agora - make_interval(days => p_retencao_notificacao_dias) AND mensagem <> '';
        GET DIAGNOSTICS qtd_notificacoes = ROW_COUNT;
    EXCEPTION WHEN OTHERS THEN
        etapas_com_falha := etapas_com_falha || 'notificacoes'::text;
        RAISE WARNING 'sp_purgar_dados_expirados/notificacoes: %', SQLERRM;
    END;
    BEGIN
        DELETE FROM log_auditoria WHERE criado_em < v_agora - make_interval(days => p_retencao_log_dias);
        GET DIAGNOSTICS qtd_logs = ROW_COUNT;
    EXCEPTION WHEN OTHERS THEN
        etapas_com_falha := etapas_com_falha || 'auditoria'::text;
        RAISE WARNING 'sp_purgar_dados_expirados/auditoria: %', SQLERRM;
    END;
    BEGIN
        DELETE FROM link_acesso
         WHERE coalesce(revogado_em, expira_em) < v_agora - make_interval(days => p_retencao_link_dias);
        GET DIAGNOSTICS qtd_links = ROW_COUNT;
    EXCEPTION WHEN OTHERS THEN
        etapas_com_falha := etapas_com_falha || 'links'::text;
        RAISE WARNING 'sp_purgar_dados_expirados/links: %', SQLERRM;
    END;
    BEGIN
        PERFORM set_config('app.expurgo_autorizado', 'on', true);       -- local à transação
        DELETE FROM historico_alteracao
         WHERE tabela <> 'prontuario'                                     -- clínico: 20 anos
           AND criado_em < v_agora - make_interval(days => p_retencao_log_dias);
        GET DIAGNOSTICS qtd_historico = ROW_COUNT;
        PERFORM set_config('app.expurgo_autorizado', 'off', true);
    EXCEPTION WHEN OTHERS THEN
        etapas_com_falha := etapas_com_falha || 'historico'::text;
        RAISE WARNING 'sp_purgar_dados_expirados/historico: %', SQLERRM;
    END;
END $$;
```

**Pontos do Python que passam a chamar cada procedure:**

| Procedure | Chamado de (PG) | Fallback (só SQLite) | Transação / locks |
|---|---|---|---|
| `sp_anonimizar_cliente(cliente, autor, ip)` | `LgpdService.esquecer_cliente` (ação LGPD do Django admin); depois `cliente.refresh_from_db()` | `LgpdService._esquecer_cliente_python` (o corpo atual, adaptado a `consentimento`/`link_acesso`) | roda dentro do `@transaction.atomic` do chamador; `FOR UPDATE` na linha do cliente serializa com um agendamento simultâneo do mesmo titular |
| `sp_aplicar_retencao_lgpd(…, true, NULL, NULL, NULL)` | `tasks.job_lgpd_purgar_inativos` (cron `lgpd_purgar`); parâmetros de `LgpdService.RETENCAO_*` / `CARENCIA_SOFT_DELETE_DIAS` | `purgar_inativos` + `purgar_fichas_sem_atendimento` + `purgar_lista_espera_vencida` | **precisa de autocommit** (`COMMIT` por cliente): o wrapper confere `not connection.in_atomic_block`, senão passa `p_confirmar_por_cliente=false` (é o que o teste faz, dentro do `TestCase`) |
| `sp_purgar_dados_expirados(…)` | `tasks_manutencao.job_housekeeping`: as etapas `otp`, `notificacoes`, `auditoria` viram uma etapa `banco`; `sessoes` e `axes` continuam Python (tabelas de terceiros) | `_purgar_otp`, `_limpar_texto_notificacoes`, `_purgar_auditoria` | uma transação; sub-blocos por etapa; `etapas_com_falha` não vazio → o job levanta `RuntimeError` (cron vê 500), como hoje |
| `sp_expirar_pacotes(hoje, NULL)` | `tasks.job_expirar_pacotes` (hoje `utils/datas.hoje()`) | o `update()` atual | 1 UPDATE; o `tg_compra_pacote_historico` registra cada linha |

### 5.7 Gating SQLite × Postgres

| Objeto | SQLite (suíte local) | Postgres (CI + prod) |
|---|---|---|
| Tabelas, colunas, FK, UNIQUE/CHECK do `Meta`, índices parciais e de expressão | ✅ (estado Django) | ✅ |
| CHECK regex/jsonb, EXCLUDE, FK composta, `ix_cliente_nome_trgm`, ação `ON DELETE`, renomeação de objetos automáticos | ❌ (`RunPython` sai se `vendor != 'postgresql'`) | ✅ |
| Triggers `atualizado_em`, imutabilidade, validação, histórico | ❌ — o Python cobre: `auto_now`, `save()`/`delete()` que recusam (ex.: `ProntuarioVersao`), serviços que validam antes do INSERT | ✅ |
| Views e funções `fn_*` | ❌ — o app não depende delas | ✅ (teste de paridade) |
| Procedures `sp_*` | ❌ — fallback Python | ✅ **fonte única** |
| Collation `pt_br` | shim `create_collation` (§3.6.3) | ICU |

O job `postgres` do CI (bloqueante) roda a suíte inteira no PG 18 depois do `migrate_atomico` do
zero; ali os `sp_*` são exercitados pelos próprios testes de serviço (mesma asserção do SQLite).

### 5.8 Testes de paridade (`tests/test_pg_paridade.py`, `skipUnless(vendor == 'postgresql')`)

| Alvo SQL | Referência Python | Como |
|---|---|---|
| `fn_horarios_livres` | `SlotService.slots_livres` | matriz: 2 profissionais (grade com 2 janelas/dia, antecedências diferentes) × 3 procedimentos (com/sem intervalo) × 60 dias × cenários (folga em intervalo, feriado global e inativo, horário diferente, bloqueio pontual cruzando meia-noite, bloqueios `FREQ=DAILY;INTERVAL=3;BYDAY=MO,TU`, `WEEKLY;INTERVAL=2;BYDAY=MO,FR`, `MONTHLY;BYDAY=1SA`, `MONTHLY;BYDAY=-1FR`, `MONTHLY` com DTSTART dia 31, com e sem `data_fim_recorrencia`, DTSTART que não casa com a regra), atendimentos PENDENTE/CANCELADO sobrepostos, `ignorar_atendimento_id`; `timezone.now` fixado com `mock.patch` e o mesmo instante passado em `p_agora`; compara `['HH:MM', …]` |
| `fn_ocorrencias_recorrencia` | `AgendaExcecao.ocorrencias` (dateutil) | as mesmas regras sobre janelas de 1 dia e de 1 mês |
| `fn_saldo_pacote`, `vw_pacote_saldo` | `CompraPacote.saldo_por_procedimento` | compras com consumos parciais, item reconstruído, compra cancelada |
| `fn_nps_periodo`, `vw_nps_mensal` | `utils/nps.calcular_nps` (helper novo, usado por `dashboard` e `relatorios`) | inclui casos .5 (1 promotor em 8) |
| `fn_saldo_comissao`, `vw_comissao`, `vw_comissao_saldo` | `MovimentoComissao.objects.creditos_com_status()` + agregação ORM | crédito pendente, pago, estornado, pago-e-estornado |
| `vw_consentimento_atual`, `fn_tem_consentimento` | `services/consentimentos.estado_atual` | concede → revoga → reconcede; empate de `registrado_em` |
| `vw_faturamento_mensal`, `vw_cliente_resumo` | agregação ORM com as regras de `admin_financeiro` (D20) | mês com pacote cancelado com reembolso, sessão de pacote, retorno |
| `fn_clientes_para_anonimizar` | `LgpdService.candidatos_purga` | um cliente por regra de exclusão |
| `sp_anonimizar_cliente` | `LgpdService._esquecer_cliente_python` | **dois titulares idênticos** (fábrica `cenario_titular(sufixo)`): Python no A, `CALL` no B, compara uma "foto" normalizada (ids trocados por marcadores, instantes ignorados) de cliente, consentimentos, links, OTP, espera, notificações, fichas, NPS, `log_auditoria`, `django_admin_log` |
| `sp_aplicar_retencao_lgpd` | as 3 funções Python | idem, com `p_confirmar_por_cliente=false` (o `TestCase` está numa transação) |
| `sp_purgar_dados_expirados`, `sp_expirar_pacotes` | funções Python do housekeeping / `job_expirar_pacotes` | contagens e linhas remanescentes iguais |

Regra de manutenção: mudou a regra no Python → o teste de paridade quebra no CI Postgres até a
função/procedure ser atualizada por uma migration nova (`CREATE OR REPLACE`). É isso que torna
aceitável ter "duas fontes": a leitura tem espelho testado; a escrita (procedures) tem **uma** fonte.

---

## 6. Plano de migrations (0048+)

Ordem obrigatória: reestruturações (a)–(f) → nomes → lógica. Cada fase = 1 PR com migration +
código + testes, **verde sozinha** no SQLite e no Postgres (a suíte e o `makemigrations --check`
passam ao fim de cada uma). Objetos **novos** já nascem com o nome final (inclusive quando a tabela
ainda tem nome antigo — ex.: `ck_formulario_tipo` na 0051, com a tabela renomeada só na 0054); a
fase de nomes mexe só no que já existia. Toda migration termina com `PadronizarNomes()` para as
tabelas que criou.

| Fase | Migration | Operações (em ordem) | Aborta (lista ids) se | Código na mesma PR |
|---|---|---|---|---|
| (a) | `0048_agenda_unificada` | `CreateModel AgendaHorario/AgendaExcecao` (constraints portáveis) → `RunPython` pré-checagem → cópia (bloqueios com id preservado, `setval`, exceções, feriados, grade ISO) + `LogAuditoria` do mapa de ids → `SET CONSTRAINTS ALL IMMEDIATE` → PG: `ex_agenda_horario_sobreposicao`, `ex_agenda_excecao_folga_sobreposta`, `ck_agenda_excecao_regra_recorrencia_suportada` → `DeleteModel` ×4 | janelas sobrepostas; RRULE fora do subconjunto | §4.a |
| (b) ✅ | `0049_link_acesso` | `CreateModel LinkAcesso` → `AlterField resposta_anamnese.token` NULL (p/ o reverso) → `RunPython` (sha256 dos 5 tipos de token) → `SET CONSTRAINTS…` → PG: `ck_link_acesso_token_hash_formato`, `fn_link_acesso_imutavel` + trigger → `PadronizarNomes` → `RemoveField` ×7 | — | §4.b (serviço `links`, rotas do portal, ICS) |
| (c) ✅ | `0050_consentimento` | `CreateModel Consentimento` → `RunPython` (histórico a partir de `consent_*`/`aceita_comunicacao`) → `SET CONSTRAINTS…` → PG: `fn_consentimento_imutavel` + trigger → `PadronizarNomes` → `RemoveField` ×10 | — | §4.c (serviço `consentimentos`, painel revoga, admin só leitura) |
| (d) ✅ | `0051_formulario_prontuario` | `AlterField tipo` (+PRONTUARIO) → pré-checagem → `AddConstraint` (`ck_formulario_*`, `uq_formulario_prontuario_ativo`) → `RunPython` (Configuracao → formulário) | `tipo`/`escopo` fora do domínio / incoerente | §4.d |
| (e) | `0052_compra_pacote_item` | `AddField pacote_nome` (nulo) + `CreateModel CompraPacoteItem` + `AddField compra_pacote_item` (nulo) → `RunPython` (snapshot, reconstrução, logs) → `SET CONSTRAINTS…` → `AlterField` NOT NULL → PG: FK composta, `fn_consumo_sessao_validar`, `tg_compra_pacote_item_imutavel` | — | §4.e |
| (f) | `0053_comissao_ledger` | `RemoveConstraint uniq_comissao_ativa_por_atendimento` → `AddField tipo/movimento_origem/usuario_autor` → `RunPython` (PAGAMENTO/ESTORNO) → `SET CONSTRAINTS…` → `RemoveField status/pago_em` → `AddConstraint` (UNIQUE/CHECK) → estado `regra` PROTECT → PG: `fn_movimento_comissao_validar`, `tg_movimento_comissao_imutavel` | `valor <= 0`; 2 linhas p/ o mesmo atendimento | §4.f |
| N1 | `0054_padrao_nomes_tabelas_colunas` | PG: **derruba** `trg_aceite_termo_imutavel`/`trg_prontuario_versao_imutavel`/`trg_versao_termo_imutavel`/`trg_movimento_carteira_imutavel` e as funções da 0038/0046 → `AlterModelTable` ×3 + sequências → 64 `RenomearColuna` (§3.6.2) → tipos (`registro_pk bigint`, `*_hash char(64)`, `''→NULL` do `conteudo_hash`) → `NULL→''` e `atualizado_em NOT NULL` (backfill) → `AddField criado_em/atualizado_em` onde faltam → `Positive*Field→SmallIntegerField` → `db_collation='pt_br'` (§3.6.3) → PG: recria `fn_bloquear_mutacao`, `fn_aceite_termo_imutavel`, `fn_versao_termo_imutavel`, `fn_prontuario_versao_imutavel` e os `tg_*` | — | só `db_column`/`db_table` nos models, `registrar_log` com `tabela or ''`, fixtures SQL de teste (`test_pg_ddl`, `test_aceite_termo_modelo`, `test_prontuario_versao_autoria` usam SQL cru com nomes antigos) |
| N2 | `0055_padrao_nomes_constraints_indices` | `RenameIndex` ×15 → `RenomearConstraint` (§3.3) → `SeparateDatabaseAndState` (unique/db_index de campo e `unique_together` → `Meta`) → `RemoveIndex` dos redundantes → `AddIndex` dos novos → pré-checagem + `AddConstraint` dos CHECKs novos → PG: `PadronizarNomes()` (PK, FK recriadas com `ON DELETE`, UNIQUE de OneToOne, sequências, `DROP` dos `_like`), `RENAME` + `VALIDATE` dos CHECK jsonb da 0043, `ex_atendimento_sobreposicao`, CHECK regex de cliente recriados → `db_table_comment`/`db_comment` (36 tabelas) | linha que viola CHECK novo ou jsonb da 0043 | `Meta` de todos os models; `tests/test_nomes_padrao.py` |
| L1 | `0056_logica_triggers_historico` | `CreateModel HistoricoAlteracao` → PG: `fn_definir_atualizado_em` + 22 triggers, `tg_anotacao_sessao_imutavel`, `fn_historico_alteracao_imutavel` + trigger, `fn_registrar_historico` + 11 triggers | — | `ContextoBancoMiddleware`, `connection_created`, `cron.py`, sinais Celery, `migrate_atomico` (`app.origem`), `tests/test_pg_historico.py` |
| L2 | `0057_logica_views_funcoes` | PG: `CREATE EXTENSION pg_trgm, unaccent` → `fn_fuso_clinica`, `fn_data_local`, `fn_sem_acento` → `ix_cliente_nome_trgm` → 8 funções de leitura → 9 views | — | `filtrar_clientes` (trigram), `utils/nps.calcular_nps` (arredondamento), `tests/test_pg_paridade.py` (leitura) |
| L3 | `0058_logica_procedures` | PG: 4 procedures | — | `utils/banco.py`, `LgpdService`, `tasks`, `tasks_manutencao`, paridade das procedures |

Cada `RunSQL`/`RunPython` só-PG tem reverso (`DROP … IF EXISTS` / restauração), e a sequência toda
precisa descer e subir: `tests/test_migrations_dados.py` migra de volta até a `0028` e sobe de novo.

### 6.1 Upgrade de produção 0026 → 0058 (uma transação)

O próximo deploy aplica **32 migrations** (0027–0058) de uma vez pelo `migrate_atomico` (transação
única, `SET LOCAL lock_timeout = '5s'`, `SET CONSTRAINTS ALL IMMEDIATE` + `DEFERRED` depois de cada
migration). Riscos e o que já está previsto:

1. **Eventos de trigger pendentes.** FK do Django são `DEFERRABLE INITIALLY DEFERRED`: DML deixa
   checagens pendentes e o `ALTER TABLE` seguinte falha ("pending trigger events"). Regra (já usada
   em 0034–0047): todo `RunPython` com DML termina com `SET CONSTRAINTS ALL IMMEDIATE` no PG, e o
   `migrate_atomico` repete entre migrations. Os triggers novos de trilha são `AFTER … FOR EACH ROW`
   **não deferidos** (disparam no fim do statement, não ficam pendentes).
2. **Triggers ativos durante migrations posteriores.** Os de imutabilidade da 0038/0046 bloqueariam
   o `''→NULL` do `conteudo_hash` na 0054 → a 0054 os derruba antes e recria no fim. Os triggers
   criados em (b)/(c)/(e)/(f) ficam **no fim** de cada fase; a trilha e o `atualizado_em` só nascem
   na 0056, depois de todo DML de migração (nenhuma linha de migração polui `historico_alteracao`;
   a partir da 0056, `app.origem = 'migracao'`).
3. **Locks.** A transação segura `ACCESS EXCLUSIVE` em quase todas as tabelas até o `COMMIT`
   (segundos, volume pequeno). `lock_timeout 5s`: se o deploy antigo estiver com transação aberta, o
   upgrade falha limpo (nada aplicado) e é só refazer.
4. **Janela entre o COMMIT e o container novo.** Desta vez quase toda coluna muda de nome: o código
   antigo quebra em praticamente toda página até o healthcheck do container novo passar (~1 min).
   **Janela de manutenção obrigatória** (horário sem movimento; opcional: página de manutenção no
   Railway durante o deploy). O WhatsApp/links públicos que chegarem nesse minuto dão erro — por isso
   links antigos continuam válidos depois (hash migrado).
5. **Pontos que abortam** (tudo é desfeito e o log diz a migration): os já conhecidos (0035
   sobreposição, 0043 retorno duplicado) + 0048 (janelas sobrepostas, RRULE), 0051 (`escopo`), 0053
   (`valor <= 0`), 0055 (CHECK novo/`VALIDATE`). Cada um tem a pré-checagem SQL no runbook (§4.a e
   `PROJECT.md` §14.a, a atualizar), rodada no **ensaio com o dump** antes do deploy.
6. **Extensões.** `pg_trgm`/`unaccent` exigem privilégio de `CREATE EXTENSION` (o usuário do Railway
   é dono do banco; o ensaio confirma). Sem elas a 0057 falha — preferível a seguir sem índice.
7. **Collation.** `db_collation='pt_br'` (0054) exige ICU; sem ICU a 0038 teria pulado a collation e
   a 0054 falha — o ensaio mostra antes.
8. **Rollback** continua sendo restaurar o `pg_dump` (os reversos existem para dev/teste, mas o dos
   tokens gera tokens novos e o da comissão recalcula status).

**Teste novo no CI Postgres (recomendado antes do go-live):** `tests/test_upgrade_producao.py`
(`TransactionTestCase`, padrão do `test_migrations_dados.py`): migra um banco vazio até a `0026`,
cria com os models históricos um cenário representativo (clientes com `consent_*` concedido e
revogado e `aceita_comunicacao=False`, tokens de todos os tipos, feriados ativos/inativos, bloqueio
recorrente, pacote vendido com consumo, comissões PENDENTE/PAGA/ESTORNADA, prontuário com
`prontuario_perguntas`), roda até a `0058` numa transação e confere: links antigos resolvem pelo
token em claro, estado de consentimento igual, saldo de pacote igual, status/valor de comissão
iguais, slots de uma semana iguais antes/depois, nenhum objeto fora do padrão. Hoje o CI só testa
"do zero", não o caminho 0026→final de produção.

Depois de implementar: atualizar `docs/PROJECT.md` §5 (tabelas/invariantes) e §14 (runbook),
`docs/ARCHITECTURE.md` §2, e regerar `docs/Requisitos-ShivaZen.docx/.pdf`
(`docs/gerar_requisitos.py`).

---

## 7. Riscos, decisões em aberto e o que não fazer

### 7.1 As 5 decisões que precisam da revisão do dono

| # | Tema | Risco / trade-off | Recomendação |
|---|---|---|---|
| 1 | **Upgrade big-bang 0026→0058** | 32 migrations, renomeação de quase todas as colunas, 6 migrations que podem abortar (0035, 0043, 0048, 0051, 0053, 0055); o minuto entre o COMMIT e o container novo quebra o código antigo em toda página | Janela de manutenção; ensaio com o dump de produção; teste `test_upgrade_producao.py` no CI antes do deploy. Alternativa conservadora: subir 0027–0047 primeiro (já pronto) e esta série num 2º deploy — dois ensaios, mas cada um menor |
| 2 | **UX dos links com hash** — ✅ decidido e implementado (0049) | ICS passa a ser exibido só na geração; "Meus agendamentos" por sessão; descadastro com token por e-mail; link do termo não pode ser reexibido | (D1) Link do termo: cada clique gera um novo e os anteriores **continuam válidos** até o fim do atendimento (não revogar: evita quebrar o link que a recepção já mandou). ICS: gerar = revogar o anterior (igual à rotação atual) |
| 3 | **Semântica de consentimento** — ✅ decidido e implementado (0050) | Sem `aceita_comunicacao`, quem se descadastrou e depois marca o opt-in no wizard **volta** a receber (hoje continua vetado — o que contraria a revogabilidade). Anonimização passa a manter o histórico (datas/finalidades) e apagar IP/UA | (D2) Aceitar o reconsentimento (é o comportamento correto na LGPD). (D3) Minimizar IP/UA na anonimização (mantém a prova de que houve base legal sem identificar o titular). Painel só **revoga**; conceder pelo painel exige texto/justificativa registrado |
| 4 | **"Duas fontes" na leitura** | `fn_horarios_livres` replica o `SlotService` incluindo recorrência; RRULE fica restrita a um subconjunto (DAILY/WEEKLY/MONTHLY, INTERVAL, BYDAY, BYMONTHDAY) e a 0048 aborta se houver regra fora dele; NPS muda de arredondamento bancário para "meio para cima" (1 ponto nos casos .5) | O app **continua** usando o Python para slots/saldos/NPS; o SQL é espelho para relatório/integração, travado por paridade no CI. (D7) Aceitar ROUND_HALF_UP no NPS (consistente com o PG e com a expectativa humana) |
| 5 | **Comissão como livro-razão** | Estornos legados ganham data **estimada** (o sistema nunca gravou); sem baixa parcial (PAGAMENTO = valor do crédito); saldo pode ficar negativo (pago e depois estornado = profissional deve); regra usada não se apaga (só desativa) | Aceitar; registrar no painel "saldo negativo = a descontar do próximo repasse". Baixa parcial/lote vira evolução (PAGAMENTO sem origem + CHECK próprio) quando existir |

### 7.2 Outros riscos (com mitigação)

- **Trilha com dado de saúde.** `historico_alteracao` duplica o antes/depois das colunas alteradas do
  prontuário (retenção de 20 anos, igual à ficha). Acesso só ADMIN no Django admin (somente leitura,
  com `LogAuditoria` de leitura, como `ProntuarioVersao`). `cliente` fica fora da trilha de propósito.
- **GUC por sessão.** Se um dia entrar PgBouncer em modo transação, o `app.usuario_id` vaza/some
  entre requests — trocar para `ATOMIC_REQUESTS` + `SET LOCAL` nesse dia (anotado no middleware).
- **FK recriada pelo Django** perde nome e `ON DELETE` → o teste de conformidade pega; adicionar
  `PadronizarNomes()` à migration.
- **`char(64)`** precisa de um campo próprio (`HashSha256Field`, `db_type='char(64)'`); comparação com
  parâmetro texto funciona (literal vira `bpchar`), mas qualquer SQL manual deve usar o hash inteiro.
- **Pacote vendido antes da trava de UI:** se alguém editou itens de pacote já vendido antes da
  auditoria, o snapshot da 0052 herda o catálogo atual (não há como recuperar o original); consumos
  sem item viram item reconstruído com log.
- **Estimativas de `criado_em`** nas tabelas que não tinham a coluna (valem a data da migration) —
  documentado no `COMMENT` da coluna.

### 7.3 Achados no caminho (fora do escopo, para registrar)

1. **PII em ledger imutável:** `FidelidadeService.liberar_cashback_indicacao` grava
   `observacoes=f'Indicacao: {cliente.nome} (cli #{cliente.pk})'` em `movimento_carteira`, que é
   imutável por trigger — o nome da indicada **nunca** é anonimizado pelo esquecimento. Corrigir para
   gravar só o id e sanear as linhas antigas numa migration que derruba/recria o trigger no mesmo passo.
2. Ticket médio diferente entre `dashboard.painel_overview` e `admin_financeiro` (§5.4 item 1).
3. "LTV" da ficha do cliente conta sessão de pacote pelo preço cheio (§5.4 item 2).
4. ~~`admin_calendar_mover` não expande bloqueio recorrente~~ — **resolvido na 0048** (usa
   `AgendaExcecao.ocorrencias`).
5. ~~Importação do Google duplica evento remarcado~~ — **resolvido na 0048** (upsert por `origem_ref`).
6. `profissional.gcal_token_renovacao` (refresh token OAuth) segue em texto puro: não dá para
   hashear. Recomendo cifrar em repouso (Fernet com chave em env, rotação documentada) — PR separada.
7. `log_auditoria.registro_id` é `integer` apontando para PKs `bigint` — corrigido na 0054.

### 7.4 O que eu recomendo NÃO fazer

- **Não** tornar o `fn_horarios_livres` a fonte do agendamento (nem chamá-lo do wizard): a regra de
  slots muda com frequência e o Python é testável no SQLite; o SQL é espelho.
- **Não** usar `ENUM` nativo para status/tipos (CHECK + choices, §1.2).
- **Não** usar HMAC/pepper com a `SECRET_KEY` no `token_hash` (rotação em incidente derrubaria todo
  link enviado), nem token assinado "stateless" em paralelo (dois mecanismos de link).
- **Não** colocar trilha (`historico_alteracao`) em `cliente` nem em tabela append-only.
- **Não** renomear tabelas de terceiros (§1.5).
- **Não** usar `CREATE INDEX CONCURRENTLY` nem migration `atomic = False`: quebram o
  `migrate_atomico` (volume não justifica).
- **Não** mover a FSM do atendimento, comissão por regra ou cashback para trigger — ficam nos
  serviços (eventos de domínio, e-mails, push); o banco só guarda invariantes.
- **Não** usar `UniqueConstraint(nulls_distinct=False)`/`NULLS NOT DISTINCT`: o SQLite dos testes
  não suporta; ficam os dois UNIQUE (`uq_preco_vigencia` + `uq_preco_base_vigencia`).
- **Não** adotar RLS/roles separadas agora (1 usuário no Railway); reavaliar se houver acesso de BI
  direto ao banco — aí criar uma role só-leitura com `GRANT SELECT` nas views `vw_*`.
- **Não** editar as migrations 0027–0047 (já aplicadas em dev/CI; prod aplica em sequência).
- **Não** depender de `pg_cron`: o cron HTTP (`/cron/run/<job>/`) continua chamando os jobs, que
  chamam as procedures.

_Última atualização: 2026-10-02 — fases (a) (0048, agenda 4→2) e (b) (0049, tokens em hash) implementadas; demais fases em design._
