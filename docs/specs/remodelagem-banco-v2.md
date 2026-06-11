# Remodelagem do Banco — Shiva Zen v2.1

> Spec unica (sobrescrever a cada revisao). Status: APROVADA — em execucao.
> Origem: auditoria multi-dimensional (28 agentes, achados verificados adversarialmente).

## 1. Objetivo

Schema 50 → **32 tabelas de dominio**, toda invariante de negocio defendida NO BANCO,
naming PT-BR singular consistente, zero tabela morta/solta.

## 2. Decisoes fixadas

| Decisao | Valor |
|---|---|
| Naming | `substantivo[_qualificador]`, singular, PT-BR, sem acronimo puro |
| PK | BigAutoField `id` |
| Audit | `criado_em`/`atualizado_em` universal; `criado_por`/`atualizado_por` onde ha operador |
| Soft delete | so LGPD-sensitive (cliente, atendimento, prontuario) |
| Texto | NUNCA null (default `''`); excecao: colunas sob UNIQUE parcial (email, cpf) |
| Dinheiro | DecimalField(10,2) |
| Status | CharField + choices + CHECK no banco + FSM na aplicacao |
| IP | GenericIPAddressField sempre |
| Telefone | E.164 canonico + CHECK regex + UNIQUE parcial |
| Questionario | JSONB unico (FormularioAnamnese); EAV morto |
| OTP | sistema unico hasheado (`codigo_otp`); CodigoVerificacao morto |
| RBAC | `usuario.papel` CharField choices; Perfil/Funcionalidade mortos |
| Workflow engine | DEFERIDO (removido; volta em multi-tenant) |

## 3. Tabelas finais (32)

| Dominio | Tabelas |
|---|---|
| Acesso (3) | usuario, assinatura_push, log_auditoria |
| Pessoas (2) | cliente, profissional |
| Catalogo (4) | procedimento, habilitacao, preco, promocao |
| Agenda (4) | agenda_horario, agenda_excecao, feriado, atendimento |
| Clinico (4) | prontuario, anotacao_sessao, formulario_anamnese, resposta_anamnese |
| Comunicacao (3) | notificacao, lista_espera, avaliacao_nps |
| LGPD (3) | versao_termo, aceite_termo, codigo_otp |
| Financeiro (4) | carteira, movimento_carteira, regra_comissao, movimento_comissao |
| Pacotes (4) | pacote, item_pacote, compra_pacote, consumo_sessao |
| Infra (1) | configuracao |

## 4. Renames

### Tabelas
| Antes | Depois |
|---|---|
| profissional_procedimento | habilitacao |
| disponibilidade_profissional + bloqueio_agenda + excecao_disponibilidade | agenda_horario + agenda_excecao |
| aceite_privacidade + assinatura_termo_procedimento | aceite_termo |
| otp_code | codigo_otp |
| credito_cliente | carteira |
| movimento_credito | movimento_carteira |
| pacote_cliente | compra_pacote |
| sessao_pacote | consumo_sessao |
| web_push_subscription | assinatura_push |
| configuracao_sistema | configuracao |

### Colunas (principais)
| Antes | Depois |
|---|---|
| cliente.nome_completo | cliente.nome |
| cliente.unsubscribe_token | cliente.token_descadastro |
| cliente.consent_*_at | cliente.consent_*_em |
| atendimento.is_retorno | atendimento.eh_retorno |
| atendimento.data_hora_inicio/fim | atendimento.periodo (DateTimeRangeField) |
| procedimento.requer_retorno | procedimento.exige_retorno |
| procedimento.prazo_retorno_min/max_dias | procedimento.retorno_minimo/maximo_dias |
| notificacao.status_envio | notificacao.status |
| notificacao.resposta_cliente | notificacao.resposta |
| movimento_credito.saldo_apos | movimento_carteira.saldo_resultante |
| regra_comissao.valor_fixo | regra_comissao.valor |
| anotacao_sessao.usuario | anotacao_sessao.autor |
| usuario.two_factor_enabled | usuario.dois_fatores_obrigatorio |
| log_auditoria.tabela_afetada / id_registro_afetado | tabela / registro_id |

## 5. Cortes (50 → 32)

- **Mortas (zero refs):** patch_test, foto_antes_depois, produto, movimento_estoque,
  tag, cliente_tag, plano_tratamento, item_plano_tratamento
- **Legado substituido:** codigo_verificacao (→ codigo_otp), prontuario_pergunta +
  prontuario_resposta (→ prontuario.respostas_extras JSONB)
- **RBAC teatro:** perfil, funcionalidade, perfil_funcionalidade (→ usuario.papel)
- **Deferido:** workflow_regra, workflow_execucao

## 6. Invariantes no banco (novas)

1. **ExclusionConstraint** anti double-booking: `(profissional =, periodo &&)` WHERE status ativo — DEFERRABLE
2. ExclusionConstraint em agenda_horario (prof, dia, timerange) e agenda_excecao (prof, tstzrange)
3. UNIQUE parcial: telefone E.164 ativo, email Lower() ativo, cpf ativo, termo vigente por escopo, preco por vigencia, espera ativa, consumo por atendimento, 1 retorno por origem
4. CHECK: nps 0..10, promo desconto 0..100 + XOR preco, carteira saldo >= 0, telefone E.164 regex, cpf 11 digitos, jsonb_typeof = object, otp tentativas <= 10
5. **Triggers**: ledger imutavel (movimento_carteira/comissao bloqueiam UPDATE/DELETE), atualizado_em por trigger
6. Collation ICU pt-BR em colunas nome
7. db_comment / db_table_comment em 100% das tabelas

## 7. Operacao

- lock_timeout=5s + statement_timeout=60s nas migrations
- AddConstraintNotValid + ValidateConstraint p/ tabelas com dados
- Role runtime DML-only separada da role de migracao
- Politica de retencao: prontuario/aceite 20 anos (CFM), otp 24h (purge job),
  notificacao 12m, auditoria/financeiro 5 anos, cliente inativo 5 anos (job existente)

## 8. Fases de execucao

| Fase | Conteudo | Risco | Status |
|---|---|---|---|
| 1a | Cortar 8 extras mortas + indices redundantes | zero | em execucao |
| 1b | Migrar CodigoVerificacao → OtpCode, dropar legado | baixo | pendente |
| 1c | RBAC → usuario.papel; dropar perfil/funcionalidade | medio | pendente |
| 1d | Remover workflow engine (defer) | medio | pendente |
| 2 | Renames de tabelas e colunas | baixo-medio | pendente |
| 3 | Constraints novas + normalizacao telefone E.164 + dedup | medio | pendente |
| 4 | periodo range + ExclusionConstraint + btree_gist | medio-alto | pendente |
| 5 | EAV → JSONB prontuario | baixo | pendente |
| 6 | Unificacoes (agenda 3→2, aceites 2→1) + triggers + collation + comments | medio | pendente |
