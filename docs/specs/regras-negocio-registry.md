# Regras de Negócio — Revisão & Fonte Única (Registry)

> Spec única (sobrescrever a cada revisão). Status: **APROVADA em 2026-06-14; implementação
> PARCIAL** (estado conferido no código em 2026-09-23, seção 5). Origem: revisão das regras +
> referências de mercado (Booksy/Fresha/Vagaro/Mangomint). Objetivo do dono: **simplificar a
> operação** (clareza + fonte única), não monetizar.
> Estado atual das regras (valores reais): [`../REGRAS-DE-NEGOCIO.md`](../REGRAS-DE-NEGOCIO.md).

## 1. Objetivo & escopo

Acabar com regras espalhadas e divergentes (a mesma regra em `constants.py`, env, default de
model, `Configuracao` e doc, com valores diferentes). Alvo: **uma fonte da verdade** com edição
híbrida (dono edita valores operacionais; dev trava estruturais/segurança).

## 2. Arquitetura proposta — Registry (Abordagem A)

Fonte canônica em código (`aranha_estetica/regras.py`): cada regra declarada uma vez com
`chave · tipo · default · categoria · ajuda`; leitura por `get_regra('dominio.chave')`.
**ESTRUTURAL** (só código) × **OPERACIONAL** (override validado em `Configuracao`, tela
"Configurações > Regras"); docs gerados do registry (`gerar_docs_regras`).

**Não implementado.** Hoje a fonte de cada valor é: model (antecedência por profissional,
buffer, retorno), env (OTP, SMS, retenções), `constants.py` (janela de reagendamento, faltas,
cashback, termo LGPD v1.0), `Configuracao` (`MAX_FALTAS_BLOQUEIO`, `email_admin`,
`prontuario_perguntas`, Branding). A auditoria de 2026-09 removeu as constantes mortas/divergentes
e alinhou os valores duplicados, então o drift que motivou o registry diminuiu muito.

## 3. As 5 simplificações

| # | Regra | Alvo aprovado | Estado (2026-09-23) |
|---|---|---|---|
| 1 | Aprovação de agendamento | Auto-aprovar cliente OTP-validada e sem bloqueio | ❌ **Não implementado**: o agendamento público segue nascendo **PENDENTE** (ARCHITECTURE D8). O agendamento interno nasce AGENDADO; PENDENTE vencido há 24 h é cancelado pelo job |
| 2 | Estados do atendimento | Semântica fixa: AGENDADO = marcado; CONFIRMADO = cliente respondeu | ✅ Documentado e aplicado (confirmação pelo link do D-1). A FSM não mudou (PENDENTE → CONFIRMADO ainda é permitido) |
| 3 | Sessão + OTP da cliente | Uma sessão de "cliente verificada"; regra única de OTP | ◐ **Regra única de OTP feita**: todo agendamento exige OTP, preso ao **telefone** (e-mail nunca é identidade); portal e DSAR também pelo telefone. **Sessão única não**: wizard (30 min) e portal (1 h) têm chaves próprias, ambas presas ao telefone |
| 4 | Antecedência máxima | `min(profissional, teto global 90)` | ◐ Uma regra só: `profissional.max_advance_dias` (padrão 60, faixa 1–365); a constante global de 90 dias foi removida — **não há teto global** |
| 5 | Fonte dos valores | Registry; `otp.max_tentativas` = 5 | ◐ Valor único 5 (`OTP_MAX_TENTATIVAS`); constantes divergentes removidas; registry não criado |

## 4. Catálogo de regras (valores atuais)

Legenda: **E** estrutural · **O** operacional · fonte atual entre parênteses.

### Identidade / login
| chave | valor | cat |
|---|---|---|
| `cliente.identidade` | celular só dígitos (chave natural) | E |
| `cliente.login` | passwordless: celular + OTP por SMS (só celular) | E |
| `cliente.sessao` | wizard 30 min / portal 1 h (código) | E |
| `equipe.2fa` | obrigatório p/ ADMIN; opt-in p/ PROFISSIONAL (env) | E |

### Agendamento
| chave | valor | cat |
|---|---|---|
| `agenda.antecedencia_min_horas` | 2 (0–720) — `Profissional.min_notice_horas` | O |
| `agenda.antecedencia_max_dias` | 60 (1–365) — `Profissional.max_advance_dias` | O |
| `agenda.intervalo_slot_min` | 30 — `SlotService.INTERVALO` | E |
| `agenda.aprovacao` | público → PENDENTE; interno → AGENDADO | E |
| `agenda.double_booking` | EXCLUDE no banco | E |
| `agenda.idade_minima` | 18 anos (wizard) | E |
| `procedimento.duracao_minutos` / `buffer_minutos` | por procedimento (buffer 0–120) | O |

### OTP / SMS
| chave | valor | cat |
|---|---|---|
| `otp.max_tentativas` | 5 (env) | E |
| `otp.validade_min` | 10 (env `OTP_TTL_SEGUNDOS`) | O |
| `otp.reenvio_min_seg` | 60 (env) | O |
| `otp.sms_por_hora_telefone` / `_ip` / `_global` | 3 / 10 / 60 (env `SMS_MAX_*`) | O |
| `otp.proposito_isolado` | AGENDAMENTO / LOGIN_CLIENTE / DSAR | E |
| `otp.exige` | todo agendamento público + portal + DSAR | E |

### Faltas / reagendamento / cancelamento
| chave | valor | cat |
|---|---|---|
| `no_show.faltas_bloqueio` | 3 (`Configuracao MAX_FALTAS_BLOQUEIO` > constants) | O |
| `no_show.marcacao` | só a equipe (job não marca FALTOU) | E |
| `reagendamento.antecedencia_min_horas` | 24 (constants) | O |
| `cancelamento.cliente` | até o início do atendimento | E |
| `link_magico.anamnese_ttl_dias` | 60 | O |
| `link_termo.validade` | até o fim do atendimento | E |

### Retorno (F-RET)
| chave | valor | cat |
|---|---|---|
| `retorno.exige` / `janela_min_dias` / `janela_max_dias` | por procedimento | O |
| `retorno.duracao_min` | 30 (padrão) | O |
| `retorno.gratuito` | valor 0; sem comissão, cashback ou débito de pacote | E |
| `retorno.um_por_origem` | UNIQUE no banco (0043) | E |

### Fidelidade / comissão / faturamento
| chave | valor | cat |
|---|---|---|
| `cashback.valor_indicacao` | R$ 50 (constants) — inerte sem tela de indicação | O |
| `cashback.gatilho` | 1º atendimento pago da indicada | E |
| `comissao.resolucao` | mais específica (4 níveis); empate = mais recente | E |
| `comissao.valor` | % (0–100) ou fixo; pacote = valor_pago ÷ sessões | O |
| `faturamento.pacote` | receita na venda; sessão de pacote não soma | E |

### Pacotes / lista de espera
| chave | valor | cat |
|---|---|---|
| `pacote.status` | ATIVO/FINALIZADO/CANCELADO/EXPIRADO | E |
| `pacote.validade_na_data_da_sessao` | sim | E |
| `pacote.cancelamento` | exige valor devolvido (0..valor_pago) + motivo | E |
| `lista_espera.aviso` | todos os compatíveis, FIFO, sem reserva de horário | E |
| `lista_espera.uma_ativa` | UNIQUE parcial | E |

### NPS / aniversário / preços
| chave | valor | cat |
|---|---|---|
| `nps.janela` | 24 h a 7 dias após o atendimento | O |
| `nps.token_ttl_dias` | 7 | O |
| `nps.escala` / `nps.detrator` | 0–10 / ≤ 6 | E |
| `depoimento.publicacao` | nota ≥ 9 + opt-in da cliente + aprovação | E |
| `aniversario.email` | felicitação sem desconto | E |
| `promocao.desconto_xor_preco` | 0–100 XOR preço; geral só percentual | E |
| `promocao.email` | sem cupom; validade ≤ `data_fim` | E |
| `preco.vigencia` | UNIQUE por procedimento/profissional/data | E |

### Carteira / LGPD
| chave | valor | cat |
|---|---|---|
| `carteira.saldo_nao_negativo` / `ledger_imutavel` | CHECK ≥ 0 / trigger | E |
| `termo.uma_versao_ativa_escopo` / `imutavel_apos_aceite` | UNIQUE / model + trigger | E |
| `aceite.prova` | IP + user-agent + SHA-256 do texto; imutável | E |
| `dsar.fatores` | 1 (SMS) | E |
| `retencao.*` | OTP 24h · notificação 12m · auditoria 5a · axes 90d · inativa 5a · soft delete 30d · ficha sem atendimento 90d · saúde 20a | O |
| `notif.lembrete` | D-1 por WhatsApp (AGENDADO + consentimento) | O |

Removidos do catálogo (não existem mais no código): reserva de 30 min da lista de espera, link de
reagendamento de 48 h, antecedência de cancelamento de 24 h, taxa de no-show de 50%, janela de 2 h
da pesquisa online, desconto/cupom de aniversário, saldo mínimo de uso do cashback.

## 5. Decomposição para implementação

| Spec | Cobre | Estado |
|---|---|---|
| 5.1 Registry (fonte única) | itens 4 + 5 | ❌ não iniciado (valores já alinhados à mão) |
| 5.2 Estados + auto-aprovação | itens 1 + 2 | ◐ semântica feita; auto-aprovação não implementada (D8) |
| 5.3 Identidade/sessão da cliente | item 3 | ◐ OTP único por telefone feito; sessão única pendente |

## 6. Fora de escopo — situação

- 🔒 **Segurança:** HMAC com a `SECRET_KEY` no OTP ✅, `compare_digest` ✅, rotação do token do
  ICS ✅, anti-enumeração no agendamento ✅; DSAR com 2º fator ❌; TTL dos tokens de
  cancelamento/descadastro ❌.
- 📣 **Lembretes:** D-1 ✅; confirmação imediata no booking por WhatsApp e lembrete 1 semana antes ❌.
- 💰 **Monetização:** sinal/depósito, cobrança de no-show, pontos/tier ❌.
- ⚠️ **Gaps do banco** desta spec: UNIQUE 1-retorno-por-origem ✅ (0043); "view de consumo da
  lista de espera" resolvido por decisão — não há reserva, o aviso leva ao agendamento;
  `token_reserva`/`expira_em` ficam como legado a remover.

## 7. Decisões fixadas

| # | Decisão | Estado |
|---|---|---|
| R1 | Registry como fonte única | pendente |
| R2 | Edição híbrida (E trava / O editável) | pendente (junto com R1) |
| R3 | `otp.max_tentativas` = 5 | ✅ |
| R4 | Antecedência máxima numa regra só | ✅ como valor do profissional (sem teto global) |
| R5 | Auto-aprovar agendamento confiável | ❌ não implementada — público continua PENDENTE (confirmar com o dono se mantém a decisão) |
| R6 | Docs de regras gerados do registry | pendente (hoje `REGRAS-DE-NEGOCIO.md` é mantido à mão, conferido no código) |

---

_Última atualização: 2026-09-23 — estado da implementação conferido no código._
