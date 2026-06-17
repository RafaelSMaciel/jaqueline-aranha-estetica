# Regras de Negócio — Revisão & Fonte Única (Registry)

> Spec única (sobrescrever a cada revisão). Status: **APROVADA em brainstorm 2026-06-14**.
> Origem: revisão das regras de negócio + referências de mercado (Booksy/Fresha/Vagaro/Mangomint).
> Objetivo escolhido pelo dono: **simplificar a operação** (clareza + fonte única), não monetizar.
> Cross-ref: [`../REGRAS-DE-NEGOCIO.md`](../REGRAS-DE-NEGOCIO.md) (estado atual), [`../ARCHITECTURE.md`](../ARCHITECTURE.md).

## 1. Objetivo & escopo

Acabar com **regras espalhadas e divergentes** (mesma regra em `constants.py`, env var, default de model,
tabela `Configuracao` e doc, com valores diferentes — ex.: OTP "3 vs 5"). Alvo: **uma fonte da verdade**
com **edição híbrida** (dono edita valores operacionais; dev trava estruturais/segurança).

**No escopo:** as 5 simplificações da seção 3 + o registry.
**Fora de escopo (parqueado, seção 6):** hardening de segurança, lembretes extras, monetização.

## 2. Decisão de arquitetura — Registry (Abordagem A)

Fonte canônica no código (`aranha_estetica/regras.py`): cada regra declarada **uma vez** com
`chave · tipo · default · categoria · ajuda`. Todo código lê por `get_regra('dominio.chave')`.

- **ESTRUTURAL** — segurança/estados/constraints. Só código, sem override.
- **OPERACIONAL** — prazos/taxas/cashback/textos. Override editável pelo dono.
- **Resolução única:** override-válido (se operacional) → senão default do código.
- **Override** apoiado em `Configuracao`, tipado/validado/cacheado, editado num painel "Configurações > Regras".
- **Docs gerados** do registry (comando `gerar_docs_regras`) → `REGRAS-DE-NEGOCIO.md` deixa de driftar.

## 3. As 5 simplificações (aprovadas)

| # | Regra | Hoje | Alvo (decisão travada) |
|---|---|---|---|
| 1 | **Aprovação de agendamento** | tudo entra `PENDENTE`; recepção aprova | **Auto-aprovar** cliente OTP-validado + sem bloqueio → entra `AGENDADO`. `PENDENTE` só em exceção (sem OTP, conflito, cliente bloqueado). Remove gargalo manual. |
| 2 | **Estados do atendimento** | 7 status; `AGENDADO`/`CONFIRMADO` ambíguos | Semântica fixa: `AGENDADO` = horário marcado; `CONFIRMADO` = cliente respondeu "vou" (link/WhatsApp). Documentar gatilho de cada transição. Sem novo estado. |
| 3 | **Sessão + OTP do cliente** | 2 sessões (`otp_agendamento_email`, `meus_agendamentos_email`); OTP só p/ cliente existente | **Uma** "sessão de cliente verificado" (telefone) que serve agendar + meus-agendamentos. Regra de OTP **única**: exigir OTP ao identificar cliente existente ou acessar dados. |
| 4 | **Antecedência máxima** | 90d global **e** 60d por profissional (diverge) | **Uma regra** `agenda.antecedencia_max_dias` (global = **teto 90d**); profissional pode ter valor **menor**. Resolução = `min(prof, global)`. |
| 5 | **Fonte dos valores** | `constants`/env/default/doc divergem | **Registry** (seção 2). Trava o `otp.max_tentativas` em **5** (1 valor). |

## 4. Catálogo de regras (registry)

Legenda: **E** estrutural (trava) · **O** operacional (editável) · ✏️ muda na revisão · ⚠️ gap conhecido (fora de escopo).

### Identidade / login
| chave | valor | cat |
|---|---|---|
| `cliente.identidade` | telefone digits-only (chave natural) | E |
| `cliente.login` | passwordless telefone+OTP, sem senha | E |
| `cliente.sessao` | ✏️ unificar 2→1 sessão de cliente verificado | E |

### Agendamento
| chave | valor | cat |
|---|---|---|
| `agenda.antecedencia_min_horas` | 2 | O |
| `agenda.antecedencia_max_dias` | ✏️ 90 (teto global); prof sobrescreve menor; efetivo = min(prof, global) | O |
| `agenda.aprovacao` | ✏️ auto-aprovar confiável; PENDENTE só exceção | E |
| `agenda.estados` | ✏️ AGENDADO=marcado · CONFIRMADO=cliente respondeu | E |
| `agenda.double_booking` | EXCLUDE no banco | E |
| `procedimento.duracao_minutos` | por procedimento | O |
| `procedimento.buffer_minutos` | por procedimento | O |

### OTP
| chave | valor | cat |
|---|---|---|
| `otp.max_tentativas` | ✏️ **5** (resolve 3≠5) | E |
| `otp.validade_min` | 10 | O |
| `otp.reenvio_min_seg` | 60 | O |
| `otp.sms_por_hora_telefone` | 3 | O |
| `otp.sms_por_hora_ip` | 10 | O |
| `otp.sms_global_hora` | 60 | O |
| `otp.proposito_isolado` | AGENDAMENTO/LOGIN/DSAR | E |
| `otp.exige` | ✏️ regra única (parte do item 3) | E |

### No-show / reagendamento
| chave | valor | cat |
|---|---|---|
| `no_show.faltas_bloqueio` | 3 | O |
| `no_show.taxa_percentual` | 50% (declarado; cobrança = fase 💰) | O |
| `reagendamento.antecedencia_min_horas` | 24 | O |
| `cancelamento.antecedencia_min_horas` | 24 | O |
| `reagendamento.link_ttl_horas` | 48 | O |
| `link_magico.ttl_dias` | 60 | O |

### Retorno (F-RET)
| chave | valor | cat |
|---|---|---|
| `retorno.exige` | por procedimento | O |
| `retorno.janela_min_dias` / `max_dias` | por procedimento | O |
| `retorno.duracao_min` | 30 | O |
| `retorno.gratuito` | valor 0 | E |
| `retorno.um_por_origem` | lógica no service | E · ⚠️ falta UNIQUE no banco |

### Fidelidade / comissão
| chave | valor | cat |
|---|---|---|
| `cashback.valor_indicacao` | R$50 | O |
| `cashback.saldo_minimo_uso` | R$10 | O |
| `cashback.gatilho` | 1º pago do indicado | E |
| `comissao.resolucao` | regra mais específica (4 níveis) | E |
| `comissao.valor` | % ou fixo (por regra) | O |
| `comissao.retorno_nao_gera` | retorno grátis não comissiona | E |

### Pacotes / lista de espera
| chave | valor | cat |
|---|---|---|
| `pacote.status` | ATIVO/FINALIZADO/CANCELADO/EXPIRADO | E |
| `pacote.um_consumo_por_atendimento` | UNIQUE | E |
| `lista_espera.reserva_min` | 30 | O |
| `lista_espera.notifica_cancelamento` | sim | E · ⚠️ verificar view de consumo |
| `lista_espera.uma_ativa_por_cliente` | UNIQUE parcial | E |

### NPS / aniversário / preços
| chave | valor | cat |
|---|---|---|
| `nps.janela_pos_horas` | 24 | O |
| `nps.escala` | 0–10 | E |
| `nps.token_ttl_dias` | 7 | O |
| `pesquisa.janela_horas` | 2 | O |
| `aniversario.desconto_percentual` | 15 | O |
| `aniversario.cupom_validade_dias` | 7 | O |
| `promocao.desconto_xor_preco` | 0–100 XOR preço | E |
| `preco.vigencia_sem_sobreposicao` | UNIQUE | E |

### Carteira / LGPD / notificação
| chave | valor | cat |
|---|---|---|
| `carteira.saldo_nao_negativo` | CHECK ≥0 | E |
| `carteira.ledger_imutavel` | trigger | E |
| `termo.uma_versao_ativa_escopo` | UNIQUE | E |
| `dsar.fatores` | 1 (SMS) — 🔒 fase futura: 2º fator | E |
| `retencao.*` | otp 24h · notif 12m · auditoria/financ 5a · prontuário/aceite 20a · inativo 5a | O |
| `notif.lembrete` | D-1 — 📣 fase futura: + imediato + nudge 1 sem. | O |

## 5. Decomposição para implementação (3 specs → planos)

| Spec de implementação | Cobre | Risco | Ordem |
|---|---|---|---|
| **5.1 Registry (fonte única)** | itens 4 + 5; migra `constants`/env/defaults p/ o registry; gera docs | baixo | 1º |
| **5.2 Estados + auto-aprovação** | itens 1 + 2 (FSM do atendimento) | médio | 2º |
| **5.3 Identidade/sessão do cliente** | item 3 (unificar OTP + sessão) | médio | 3º |

Cada uma vira plano (`writing-plans`) + implementação própria, na ordem acima.

## 6. Fora de escopo (parqueado — fases futuras)

- 🔒 **Segurança:** salt+pepper no OTP, `hmac.compare_digest`, DSAR com 2º fator, TTL/revogação nos tokens mágicos.
- 📣 **Lembretes:** confirmação imediata no booking + nudge 1 semana antes (ref: corta no-show 40–50%).
- 💰 **Monetização:** sinal/depósito p/ no-show ter o que cobrar; pontos/tier de fidelidade.
- ⚠️ **Gaps do banco** (em `remodelagem-banco-v2.md`): UNIQUE 1-retorno-por-origem; verificar view de consumo da lista de espera.

## 7. Decisões fixadas

| # | Decisão | Razão |
|---|---|---|
| R1 | Registry (Abordagem A) como fonte única | mata divergência sem botar segurança no banco |
| R2 | Edição híbrida (E trava / O editável) | autonomia do dono sem risco em regra crítica |
| R3 | `otp.max_tentativas` = 5 | 1 valor; mantém comportamento real atual |
| R4 | `agenda.antecedencia_max_dias` = min(prof, global 90) | 1 regra, sem divergência |
| R5 | Auto-aprovar agendamento confiável | remove gargalo manual da recepção |
| R6 | Docs de regras gerados do registry | nunca mais driftam |

---

_Última atualização: 2026-06-14 — criação (brainstorm aprovado)._
