# Regras de Negócio — Shiva Zen

> Referência funcional do sistema. Valores extraídos de `aranha_estetica/constants.py`,
> `models/` e `services/`. Mudou regra/constante? Atualize aqui e no código.
> Arquitetura e progresso em [`docs/ARCHITECTURE.md`](ARCHITECTURE.md).

---

## 1. Atores e papéis

`usuario.papel` (CharField + CHECK no banco):
- **ADMIN** — acesso total (= `is_staff`). A Jaqueline.
- **PROFISSIONAL** — agenda própria, prontuário, anotações.
- **RECEPÇÃO** — agenda, clientes, confirmações, aprovação de agendamentos.

Cliente **não** é usuário: identifica-se por **telefone** (chave natural) via OTP, sem senha.

---

## 2. Cliente

- **Identidade:** telefone canônico (só dígitos, 10–11; nacional). CPF opcional (11 dígitos).
  Email opcional. Unicidade garantida no banco (UNIQUE parcial entre ativos).
- **Indicação:** `indicado_por` aponta para o cliente que indicou (base do cashback).
- **Bloqueio por falta:** após **3 faltas consecutivas** (`MAX_FALTAS_ANTES_BLOQUEIO`),
  `bloqueado_online = True` → não consegue agendar online (recepção ainda agenda manual).
  Comparecer zera o contador.
- **Soft delete** (LGPD): cliente nunca é apagado fisicamente em fluxo normal.

---

## 3. Agendamento (booking público)

Fluxo: escolhe **procedimento → profissional → data/horário → dados + OTP → confirma**.

- **Antecedência mínima:** 2h (`ANTECEDENCIA_MINIMA_AGENDAMENTO`; por profissional, `min_notice_horas`, default 2, faixa 0–720h).
- **Antecedência máxima:** 90 dias (`JANELA_MAXIMA_AGENDAMENTO`; por profissional `max_advance_dias`, default 60, faixa 1–365).
- **Disponibilidade** = horários do profissional − bloqueios − exceções − feriados − slots já ocupados, respeitando duração do procedimento.
- **Anti double-booking:** garantido **no banco** (`EXCLUDE gist`) — dois atendimentos do mesmo profissional não podem se sobrepor. Corrida na borda → mensagem "horário acabou de ser reservado".
- **Aceite LGPD obrigatório** antes de persistir (registra `AceiteTermo` + consents).
- Cliente **bloqueado** (3+ faltas) não agenda online.
- **Máquina de estados (FSM)** do atendimento:
  `PENDENTE → AGENDADO → CONFIRMADO → REALIZADO | CANCELADO | FALTOU | REAGENDADO`.
  Booking público entra como **PENDENTE**; recepção **aprova** (→ AGENDADO, dispara e-mail) ou **rejeita** (→ CANCELADO).

---

## 4. Verificação por OTP (SMS/telefone)

- Código **hasheado** (sha256), nunca em texto plano.
- **Validade:** 10 minutos (`TTL_OTP_MINUTOS`).
- **Tentativas:** máx **3** por código (`MAX_TENTATIVAS_OTP`) → lockout.
- **Rate limit:** máx **3 OTP por hora** por telefone (`MAX_OTP_POR_HORA_TELEFONE`).
- **Propósito isolado:** AGENDAMENTO / LOGIN / DSAR — um OTP não vale para outro fluxo.
- Gerar novo invalida o anterior.

---

## 5. Reagendamento e cancelamento (self-service)

- Permitido até **24h antes** (`JANELA_MINIMA_REAGENDAMENTO` / `JANELA_MINIMA_CANCELAMENTO`).
- Link self-service válido por **48h** (`TTL_LINK_REAGENDAMENTO_HORAS`).
- Reagendar: atendimento antigo vira `REAGENDADO` **antes** de inserir o novo (evita falso conflito na constraint).

---

## 6. No-show (falta)

- Recepção marca **FALTOU** → incrementa `faltas_consecutivas`.
- **3 faltas** consecutivas → `bloqueado_online`.
- **Taxa de no-show:** 50% (`TAXA_NO_SHOW_PERCENTUAL`) — política de cobrança.

---

## 7. Retorno obrigatório (F-RET)

- Se `procedimento.exige_retorno`, ao marcar o atendimento como **REALIZADO** o sistema
  cria automaticamente um **atendimento de retorno**:
  - `eh_retorno = True`, `valor_cobrado = 0` (**gratuito**), status **PENDENTE**.
  - Data sugerida = meio da janela `[retorno_minimo_dias, retorno_maximo_dias]` após o fim do original.
  - Duração = `duracao_retorno_minutos` (default 30).
- **Idempotente:** no máx **1 retorno por atendimento de origem**.
- Se o slot sugerido estiver ocupado → não cria; recepção agenda manualmente.
- Retorno **não gera comissão** nem cashback.

---

## 8. Fidelidade / Cashback por indicação (F-CSB)

- Quando a **indicada** tem seu **1º atendimento REALIZADO e pago** (`valor_cobrado > 0`, não-retorno),
  o **indicador** (`indicado_por`) recebe **R$ 50,00** (`VALOR_CASHBACK_INDICACAO`) na carteira.
- Crédito só no **primeiro** pago da indicada (não repete).
- **Idempotente:** UNIQUE(carteira, atendimento, origem `CASHBACK_INDICACAO`).
- **Estorno:** se o atendimento for cancelado → débito `CASHBACK_ESTORNO` (saldo nunca negativo).
- **Saldo mínimo para usar** cashback: R$ 10,00 (`SALDO_MINIMO_USO_CASHBACK`).

---

## 9. Comissão do profissional

- Calculada ao marcar **REALIZADO** (não-retorno, pago).
- **Resolução da regra** (`RegraComissao`), mais específica primeiro:
  1. (profissional + procedimento) → 2. (profissional, qualquer) → 3. (qualquer, procedimento) → 4. global.
- Valor = **percentual** sobre `valor_cobrado` **ou** **valor fixo**.
- **Idempotente:** UNIQUE(atendimento) enquanto status ∈ {PENDENTE, PAGA}.
- **Estorno:** cancelamento → status ESTORNADA (UPDATE de status; por isso `movimento_comissao` fica fora do ledger imutável).

---

## 10. Pacotes

- Cliente compra pacote (`compra_pacote`) com nº de sessões, valor e expiração.
- Cada atendimento consome **1 sessão** (`consumo_sessao`); **1 consumo por atendimento** (UNIQUE).
- Status: **ATIVO / FINALIZADO / CANCELADO / EXPIRADO**.
- Job avisa pacote expirando; expira automaticamente no vencimento.

---

## 11. Lista de espera

- Cliente entra na fila para um procedimento/profissional.
- Em **cancelamento**, o sistema notifica clientes compatíveis e **reserva o slot por 30 min**
  (`TTL_RESERVA_LISTA_ESPERA_MINUTOS`) para o primeiro da fila.
- Uma espera **ativa** por cliente/escopo (UNIQUE parcial).

---

## 12. NPS / pesquisa

- Disparada **24h após** o atendimento (`JANELA_NPS_POS_ATENDIMENTO`).
- Nota **0–10** (CHECK no banco). Coletada via WhatsApp ou página web.
- Pesquisa online tem janela de **2h** (`JANELA_PESQUISA_ONLINE`) por token.

---

## 13. Aniversário

- Cupom de **15% de desconto** (`DESCONTO_ANIVERSARIO_PERCENTUAL`), válido **7 dias**
  (`VALIDADE_CUPOM_ANIVERSARIO_DIAS`). E-mail automático.

---

## 14. Promoções e preços

- **Promoção:** desconto **0–100%** **XOR** preço promocional fixo (CHECK no banco — um ou outro, não ambos).
- **Preço por vigência:** UNIQUE por procedimento/período (sem sobreposição de vigência).
- **Habilitação:** profissional só executa procedimento em que está habilitado (`habilitacao`).

---

## 15. Carteira (crédito do cliente)

- Saldo de cashback/vale/refund. **Saldo nunca negativo** (CHECK ≥ 0).
- **Ledger imutável:** `movimento_carteira` é append-only (trigger bloqueia UPDATE/DELETE).
  Estorno = novo movimento, nunca edição.
- Mutação de saldo usa `select_for_update` (sem corrida).

---

## 16. Prontuário e anamnese

- **Prontuário:** anamnese base permanente do cliente + `respostas_extras` (JSONB) =
  questionário configurável (schema em `configuracao.prontuario_perguntas`).
- **Anamnese pública:** formulário enviado ao cliente (link), respostas em JSONB.
- **Anotações de sessão:** registro clínico por atendimento, com autor.

---

## 17. Termos e LGPD

- **Termos versionados** (`versao_termo`, tipo LGPD ou procedimento); só **1 versão ativa** por escopo.
- **Aceite** (`aceite_termo`) guarda cliente, versão, IP, user-agent, data — **evidência legal**
  (`cliente` PROTECT, retenção ~20 anos / CFM).
- **Consentimentos granulares** no cliente: `email_marketing`, `whatsapp_nps`, `whatsapp_confirmacao`
  (cada um com data + IP).
- **DSAR / "meus dados":** cliente consulta/exporta os próprios dados (challenge por OTP DSAR).
- **Descadastro:** link com `token_descadastro` (unsubscribe).
- **Retenção:** OTP 24h, notificação 12m, auditoria/financeiro 5 anos, prontuário/aceite ~20 anos,
  cliente inativo 5 anos (job de expurgo).
- **Auditoria** (`log_auditoria`): toda ação sensível registra autor, tabela, registro, IP (art. 37 LGPD).

---

## 18. Notificações

- **Canais:** WhatsApp, Email, SMS, Push (PWA).
- **Tipos:** lembrete, confirmação, NPS, pesquisa, cancelamento, aprovação.
- **Lembrete D-1:** 1 dia antes do atendimento (`JANELA_LEMBRETE_D1`).
- Status: PENDENTE / ENVIADO / FALHOU.

---

## 19. Constantes-chave (resumo)

| Regra | Valor | Constante |
|---|---|---|
| Antecedência mín. agendamento | 2h | `ANTECEDENCIA_MINIMA_AGENDAMENTO` |
| Antecedência máx. agendamento | 90 dias | `JANELA_MAXIMA_AGENDAMENTO` |
| Reagendar/cancelar até | 24h antes | `JANELA_MINIMA_*` |
| Link reagendamento | 48h | `TTL_LINK_REAGENDAMENTO_HORAS` |
| Faltas até bloqueio | 3 | `MAX_FALTAS_ANTES_BLOQUEIO` |
| Taxa no-show | 50% | `TAXA_NO_SHOW_PERCENTUAL` |
| OTP validade | 10 min | `TTL_OTP_MINUTOS` |
| OTP tentativas | 3 | `MAX_TENTATIVAS_OTP` |
| OTP por hora/telefone | 3 | `MAX_OTP_POR_HORA_TELEFONE` |
| Login pré-lockout | 5 | `MAX_TENTATIVAS_LOGIN_PRE_LOCKOUT` |
| Cashback indicação | R$ 50,00 | `VALOR_CASHBACK_INDICACAO` |
| Saldo mín. uso cashback | R$ 10,00 | `SALDO_MINIMO_USO_CASHBACK` |
| Desconto aniversário | 15% / 7 dias | `DESCONTO_ANIVERSARIO_PERCENTUAL` |
| Reserva lista de espera | 30 min | `TTL_RESERVA_LISTA_ESPERA_MINUTOS` |
| NPS após atendimento | 24h | `JANELA_NPS_POS_ATENDIMENTO` |

---

_Última atualização: 2026-06-14 — criação do documento._
