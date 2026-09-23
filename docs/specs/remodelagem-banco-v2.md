# Remodelagem do Banco — Shiva Zen v2.1 (+ auditoria pré-produção)

> Spec única (sobrescrever a cada revisão). Status: **EXECUTADA** até a migration `0046`;
> pendente só a Fase 7 (agenda 3→2). Produção ainda está na `0026` — o upgrade e o rollback
> estão no runbook ([`../PROJECT.md` §14](../PROJECT.md#14-go-live--operação-runbook)).
> Origem: auditoria multi-dimensional de 2026-06 + auditoria pré-produção de 2026-09.

## 1. Objetivo

Schema enxuto (50 → **34 tabelas de domínio**; alvo 33 após a Fase 7), toda invariante de
negócio defendida **no banco**, naming PT-BR singular consistente, upgrade de produção
tudo-ou-nada e sem perda de dado.

## 2. Decisões fixadas

| Decisão | Valor |
|---|---|
| Naming | `substantivo[_qualificador]`, singular, PT-BR; `db_table` explícito |
| PK | `BigAutoField id` |
| Auditoria de linha | `criado_em`/`atualizado_em` onde há edição; autoria via `LogAuditoria` (+ snapshot `usuario_nome`) |
| Soft delete | só `cliente` (`deletado_em`; `Cliente.delete()` = soft; `hard=True` explícito) |
| Texto | nunca nulo (default `''`), exceto colunas sob UNIQUE parcial (e-mail, CPF, telefone) |
| Dinheiro | `Decimal(10,2)`; percentuais `Decimal(5,2)` com CHECK 0–100 |
| Status | `CharField` + choices + CHECK + FSM na aplicação |
| IP | `GenericIPAddressField` (valor inválido vira NULL, nunca inventado) |
| Telefone | só dígitos, nacional (10–11) + CHECK regex (PG) + UNIQUE parcial; DDI só na borda de envio |
| Período do atendimento | **mantidas** as colunas `data_hora_inicio/fim`; a EXCLUDE usa a expressão `tstzrange(...)` (zero query muda) — o campo `periodo` do plano original foi descartado |
| Questionário | JSONB (`prontuario.respostas_extras`, `formulario_anamnese.schema_json`); EAV morto |
| OTP | sistema único hasheado (`codigo_otp`, HMAC-SHA256 com a SECRET_KEY) |
| RBAC | `usuario.papel` (ADMIN/PROFISSIONAL/RECEPCAO); Perfil/Funcionalidade mortos |
| Histórico clínico/financeiro | FKs `PROTECT`; ledgers e provas imutáveis por trigger (PG) |
| DDL só-Postgres | `RunPython` com checagem de vendor (SQLite dos testes não tem GiST/regex/trigger) |
| Upgrade | `migrate_atomico` (transação única + `lock_timeout 5s`); migration de dados aborta com a lista de ids quando precisa de decisão humana |

## 3. Tabelas atuais (34)

| Domínio | Tabelas |
|---|---|
| Acesso (3) | usuario, assinatura_push, log_auditoria |
| Pessoas (2) | cliente, profissional |
| Catálogo (4) | procedimento, habilitacao, preco, promocao |
| Agenda (5 → 4 na Fase 7) | disponibilidade_profissional, excecao_disponibilidade, bloqueio_agenda, feriado, atendimento |
| Clínico (5) | prontuario, prontuario_versao, anotacao_sessao, formulario_anamnese, resposta_anamnese |
| Comunicação (3) | notificacao, lista_espera, avaliacao_nps |
| LGPD (3) | versao_termo, aceite_termo, codigo_otp |
| Financeiro (4) | carteira, movimento_carteira, regra_comissao, movimento_comissao |
| Pacotes (4) | pacote, item_pacote, compra_pacote, consumo_sessao |
| Infra (1) | configuracao |

## 4. Renames aplicados

### Tabelas / models (0032, 0037)
| Antes | Depois |
|---|---|
| otp_code (OtpCode) | codigo_otp (CodigoOtp) |
| credito_cliente (CreditoCliente) | carteira (Carteira) |
| movimento_credito (MovimentoCredito) | movimento_carteira (MovimentoCarteira) |
| pacote_cliente (PacoteCliente) | compra_pacote (CompraPacote) |
| sessao_pacote (SessaoPacote) | consumo_sessao (ConsumoSessao) |
| web_push_subscription | assinatura_push (AssinaturaPush) |
| configuracao_sistema | configuracao (Configuracao) |
| profissional_procedimento | habilitacao (Habilitacao) |
| aceite_privacidade + assinatura_termo_procedimento | aceite_termo (AceiteTermo) — 0037 |

### Colunas (0031, 0032, 0033)
| Antes | Depois |
|---|---|
| cliente.nome_completo | cliente.nome (0033) |
| cliente.unsubscribe_token | cliente.token_descadastro |
| cliente.consent_*_at | cliente.consent_*_em |
| atendimento.is_retorno | atendimento.eh_retorno |
| procedimento.requer_retorno | procedimento.exige_retorno |
| procedimento.prazo_retorno_min/max_dias | procedimento.retorno_minimo/maximo_dias |
| notificacao.status_envio / resposta_cliente | notificacao.status / resposta |
| movimento_credito.saldo_apos · .credito | movimento_carteira.saldo_resultante · .carteira |
| sessao_pacote.pacote_cliente | consumo_sessao.compra_pacote |
| regra_comissao.valor_fixo | regra_comissao.valor |
| anotacao_sessao.usuario | anotacao_sessao.autor |
| log_auditoria.tabela_afetada / id_registro_afetado | tabela / registro_id |

(O rename `usuario.two_factor_enabled → dois_fatores_obrigatorio` da versão anterior desta spec
nunca existiu no código: o 2FA obrigatório é regra de settings, não coluna.)

## 5. Cortes (50 → 33 → +1)

- **Mortas (0027):** patch_test, foto_antes_depois, produto, movimento_estoque, tag, cliente_tag,
  plano_tratamento, item_plano_tratamento (+ 3 índices redundantes).
- **Legado substituído:** codigo_verificacao → codigo_otp (0028); prontuario_pergunta +
  prontuario_resposta → `prontuario.respostas_extras` (0036); aceite_privacidade +
  assinatura_termo_procedimento → aceite_termo (0037).
- **RBAC teatro (0029):** perfil, funcionalidade, perfil_funcionalidade → `usuario.papel`.
- **Deferido (0030):** workflow_regra, workflow_execucao.
- **Nova (0046):** prontuario_versao (histórico append-only da ficha).

## 6. Migrations 0027–0046 — o que cada uma faz

| # | Nome | Faz | Pode abortar? |
|---|---|---|---|
| 0027 | fase1a_corta_mortas | Remove 8 models sem uso e 3 índices redundantes | Não |
| 0028 | fase1b_otp_unico | Remove `CodigoVerificacao`; propósitos do OTP (AGENDAMENTO/LOGIN_CLIENTE/DSAR) | Não |
| 0029 | fase1c_papel_usuario | `usuario.papel` + CHECK; copia o papel do perfil (Administrador→ADMIN, Profissional→PROFISSIONAL, resto→RECEPCAO); remove Perfil/Funcionalidade | Não |
| 0030 | fase1d_remove_workflow | Remove o workflow engine | Não |
| 0031 | fase2a_renames_colunas | ~15 renames de coluna (tabela da seção 4) | Não |
| 0032 | fase2b_renames_tabelas | 8 renames de model/tabela + 2 FKs; depende de `contenttypes 0002` (rename de ContentType) | Não |
| 0033 | fase2c_cliente_nome | `cliente.nome_completo → nome` | Não |
| 0034 | fase3_constraints | Normaliza telefone (tira DDI/zero; inválido vira NULL com `telefone_original` no log), CPF e e-mail; **dedup** entre ativos (o cliente com mais atendimentos fica com o valor, os outros ficam ativos com o campo NULL + `LogAuditoria` para mesclagem); dedup/clamp de termo, preço, lista de espera; promoção fora do CHECK é **desativada** (valor original no log). Cria UNIQUE parciais (telefone/e-mail LOWER/CPF ativos, termo ativo, preço por vigência, espera ativa), CHECKs (carteira ≥ 0, promoção 0–100/XOR/preço ≥ 0, OTP tentativas ≤ 10) e, no PG, CHECK regex de telefone/CPF | Não |
| 0035 | fase4_exclusion_booking | `btree_gist` + `excl_atendimento_sobreposicao` (EXCLUDE por profissional × `tstzrange` onde status ativo, DEFERRABLE). Antes cancela, com log, PENDENTE vencido ou retorno PENDENTE sobreposto | **Sim** — pares ativos sobrepostos que não são PENDENTE vencido/retorno sugerido (lista os pares) |
| 0036 | fase5_prontuario_jsonb | EAV → `respostas_extras` (1 UPDATE por prontuário; respostas de perguntas desativadas preservadas); schema → `Configuracao prontuario_perguntas`; GIN index (PG) | Não |
| 0037 | fase6_aceite_termo | Unifica aceites em `aceite_termo`, **preservando a data original** do aceite; IP inválido vira NULL | Não |
| 0038 | fase6_triggers_collation_comments | Trigger `trg_movimento_carteira_imutavel`; collation ICU **determinística** `pt_br` em `cliente/profissional/procedimento.nome` (sem ICU: pula com aviso); `COMMENT ON TABLE` em 13 tabelas | Não |
| 0039 | valor_pago_nota_validators_check | CHECK `valor_pago ≥ 0` na compra de pacote; validators da nota NPS | Não |
| 0040 | avaliacao_nps_publicacao_depoimento | `autoriza_publicacao` (opt-in da cliente) e `aprovado_publicacao` (moderação) no NPS | Não |
| 0041 | on_delete_protect_indice_email_upper | FKs de histórico clínico/financeiro → `PROTECT` (anotação, carteira, compra de pacote, movimentos, prontuário, ficha); índice `UPPER(email)`; remove UNIQUE duplicado do consumo | Não |
| 0042 | desativar_contas_demo | Contas `admin@shivazen.com`/`ana@shivazen.com` ainda com a senha pública padrão → desativadas, senha inutilizada, **2FA apagado**, log. Se o painel ficar sem ADMIN e `ADMIN_EMAIL`/`ADMIN_PASSWORD` não estiverem definidos, avisa em stderr **só no commit** | Não |
| 0043 | retorno_unico_checks_jsonb | `uniq_retorno_por_origem` (1 retorno vivo/realizado por origem; duplicata PENDENTE → CANCELADO com log); CHECK `jsonb_typeof` **NOT VALID** em `prontuario.respostas_extras`/`resposta_anamnese.respostas_json` (objeto) e `formulario_anamnese.schema_json` (lista) | **Sim** — 2+ retornos já aprovados/realizados para a mesma origem |
| 0044 | prova_aceite_autoria_anotacao_ajustes | `aceite_termo.conteudo_sha256` (vazio = aceite antigo); `anotacao_sessao.autor` PROTECT + `autor_nome`; `lista_espera.email_contato`; `notificacao.tipo` += TERMO; `preco.vigente_desde` default data local; `usuario.papel` default PROFISSIONAL; comissão 0–100 (valor fora é travado em 0/100 com log) + CHECK | Não |
| 0045 | dados_termo_lgpd_autoria | Backfill de `autor_nome`; notificações LEMBRETE/EMAIL (links de termo antigos) → TERMO; cria o **termo LGPD v1.0** (texto de `constants.TERMO_LGPD_*`) se o banco já tem clientes e nenhum termo LGPD ativo (banco novo: `seed` ou Painel > Termos) | Não |
| 0046 | prontuario_versao_autoria_log_prova_aceite | Model `ProntuarioVersao` (append-only; `ProntuarioVersao.registrar()` grava a foto anterior a cada edição); `log_auditoria.usuario_nome` + backfill; `respondida_em` das fichas legadas do booking; triggers PG `trg_aceite_termo_imutavel`, `trg_versao_termo_imutavel` (só se já houver aceite), `trg_prontuario_versao_imutavel` | Não |

**Reverse:** as operações de schema têm reverse, mas as de dados são `noop` ou parciais
(dedup da 0034 — cujo reverse não recria o UNIQUE global de CPF com dados sujos —, contas demo,
termo criado, promoções desativadas). **Rollback de produção = restaurar o `pg_dump`.**

## 7. Invariantes no banco — estado

| Planejado | Estado |
|---|---|
| EXCLUDE anti double-booking no atendimento | ✅ 0035 (expressão, sem campo range) |
| EXCLUDE em agenda_horario/agenda_excecao | ⏳ Fase 7 |
| UNIQUE parciais (telefone, e-mail, CPF, termo, preço, espera, consumo, 1 retorno por origem) | ✅ 0034/0041/0043 |
| CHECK NPS 0–10, promoção, carteira ≥ 0, telefone/CPF regex, OTP ≤ 10, JSON objeto/lista, comissão 0–100, valor pago ≥ 0 | ✅ (JSON como `NOT VALID` — validar depois de inspecionar os dados) |
| Ledger imutável `movimento_carteira` | ✅ 0038 |
| Ledger imutável `movimento_comissao` | ❌ por decisão (fluxo usa UPDATE de status PENDENTE→PAGA/ESTORNADA) |
| Prova de aceite, termo aceito e histórico do prontuário imutáveis | ✅ 0046 |
| Trigger de `atualizado_em` | ❌ por decisão (cosmético; saves com `update_fields` incluem o campo) |
| Collation ICU pt-BR em `nome` | ✅ 3 colunas (cliente, profissional, procedimento) — **fora do estado do Django**: `AlterField` futuro reseta; reaplicar via `RunSQL` |
| `COMMENT ON TABLE` | ✅ 13 tabelas centrais (não 100%) |

## 8. Operação

- `migrate_atomico` no pre-deploy: transação única, `SET LOCAL lock_timeout = '5s'`,
  `SET CONSTRAINTS ALL IMMEDIATE` após cada migration; falha = nada aplicado.
- Migrations com UPDATE seguido de DDL chamam `SET CONSTRAINTS ALL IMMEDIATE` (sem *pending
  trigger events*); SQL com `%` do plpgsql vai com `params=None`.
- CHECK novo em tabela com dados: `NOT VALID` + `ALTER TABLE ... VALIDATE CONSTRAINT` manual.
- Role de runtime só DML separada da role de migração: **não adotado** (Railway usa um usuário).
- Retenção (jobs `housekeeping` e `lgpd_purgar`): OTP 24h, texto de notificação 12m, auditoria 5
  anos, axes 90 dias, cliente inativa 5 anos, soft delete 30 dias, ficha de pedido não realizado 90
  dias; saúde/aceite retidos (~20 anos).
- CI: suíte inteira no Postgres 18 + `tests/test_pg_ddl.py` (objetos só-PG; ICU obrigatório com
  `PG_EXIGE_ICU=1`). Pendente: incluir os triggers da 0046 em `TRIGGERS_SO_PG`.

## 9. Fases

| Fase | Conteúdo | Risco | Status |
|---|---|---|---|
| 1a | Cortar 8 mortas + índices redundantes | zero | ✅ 0027 |
| 1b | OTP único | baixo | ✅ 0028 |
| 1c | RBAC → `usuario.papel` | médio | ✅ 0029 |
| 1d | Remover workflow engine | médio | ✅ 0030 |
| 2a–2c | Renames de coluna/tabela, `cliente.nome` | médio | ✅ 0031–0033 |
| 3 | Constraints + telefone canônico + dedup | médio | ✅ 0034 |
| 4 | EXCLUDE por expressão + btree_gist | médio | ✅ 0035 |
| 5 | EAV → JSONB | baixo | ✅ 0036 |
| 6 | Aceite unificado, trigger do ledger, collation, comments | médio | ✅ 0037/0038 |
| A1 | Auditoria SWE (validators/CHECK) | baixo | ✅ 0039 |
| A2 | Auditoria pré-produção (depoimentos, PROTECT, contas demo, retorno único, JSON, prova de aceite, termo LGPD, histórico do prontuário, triggers) | médio | ✅ 0040–0046 |
| 7 | Agenda 3→2 (`agenda_horario` + `agenda_excecao`; bloqueio vira exceção) | médio-alto | ⏳ PR dedicada (mexe no SlotService; testes de caracterização em `test_slots_disponibilidade.py`) |

_Última atualização: 2026-09-23._
