# Regras de Negócio — Revisão & Fonte Única (Registry)

> Spec única (sobrescrever a cada revisão). Status: **IMPLEMENTADA** (aprovada em 2026-06-14;
> concluída em 2026-11-03). Origem: revisão das regras + referências de mercado
> (Booksy/Fresha/Vagaro/Mangomint). Objetivo do dono: **simplificar a operação** (clareza + fonte
> única), não monetizar. Valores atuais de cada regra: [`../REGRAS-DE-NEGOCIO.md`](../REGRAS-DE-NEGOCIO.md)
> §24 (tabela gerada do código).

## 1. Objetivo & escopo

Acabar com regras espalhadas e divergentes (a mesma regra em `constants.py`, env, default de
model, `Configuracao` e doc, com valores diferentes). Alvo: **uma fonte da verdade** com edição
híbrida — a dona ajusta os valores operacionais no painel; o resto é estrutural e só muda no código.

## 2. Arquitetura implementada

Três peças, cada uma com um papel:

| Peça | O que faz |
|---|---|
| **Regras operacionais** — `models/configuracao.CHAVES` | Registro tipado (rótulo, explicação, tipo, faixa, padrão) do que a dona ajusta em **Painel > Configurações > Regras operacionais**: `MAX_FALTAS_BLOQUEIO`, `email_admin`, `anos_experiencia`. O código lê por `Configuracao.ler(chave)`; valor inválido no banco cai no padrão com aviso no log. |
| **Catálogo** — `aranha_estetica/regras.py` | Cada regra com valor aparece **uma vez**, com o valor **lido de onde o código o aplica** (constante, padrão de env do settings, default de model, CHECK do banco ou regra operacional) e onde mora. Nada é copiado: mudou a constante, muda o catálogo. |
| **Documento gerado** — `manage.py gerar_docs_regras` | Reescreve a tabela "Valores-chave" (§24) do `REGRAS-DE-NEGOCIO.md` entre marcadores; `--check` só confere. O teste `test_regras_catalogo` reprova o documento atrasado e regra operacional fora do catálogo. |

Os valores continuam definidos perto de quem os usa (um `get_regra()` central foi descartado: trocaria
leitura direta e tipada por chave em texto em dezenas de pontos, sem ganho para a operação). A fonte
única do **documento** é o catálogo; a dos **valores** é o código que o catálogo lê.

## 3. As 5 simplificações

| # | Regra | Alvo aprovado | Estado |
|---|---|---|---|
| 1 | Aprovação de agendamento | Auto-aprovar cliente OTP-validada e sem bloqueio | ✅ **Cliente de confiança** (`services/agenda.status_inicial_publico`): já atendida, sem falta em aberto e com menos de `LIMITE_AUTOAPROVACAO` (2) horários futuros → o pedido nasce **AGENDADO**; primeira vez ou acima do limite → **PENDENTE**. Pendente sem decisão é cancelado 1 h antes do início (`PRAZO_DECISAO_PENDENTE`). O agendamento interno nasce AGENDADO |
| 2 | Estados do atendimento | Semântica fixa: AGENDADO = marcado; CONFIRMADO = cliente respondeu | ✅ Aplicado (confirmação pelo link do D-1); transições na FSM do `Atendimento` |
| 3 | Sessão + OTP da cliente | Uma sessão de "cliente verificada"; regra única de OTP | ✅ **Regra única de OTP**: todo agendamento exige código, preso ao **telefone** (e-mail nunca é identidade); portal e DSAR também. **Sessão única** (`services/sessao_cliente`): qualquer código confirmado (wizard ou portal) abre a mesma sessão de 1 h; agendar exige código de até 30 min ainda não usado num pedido; baixar os dados, de até 10 min |
| 4 | Antecedência máxima | `min(profissional, teto global 90)` | ✅ Uma regra só: `Profissional.max_advance_dias` (padrão 60, faixa 1–365 no CHECK); **sem teto global**, por decisão |
| 5 | Fonte dos valores | Registry; `otp.max_tentativas` = 5 | ✅ Catálogo + documento gerado (§2); `OTP_MAX_TENTATIVAS` = 5 |

## 4. Catálogo de regras

Os valores **não** são repetidos aqui (ficariam para trás): estão no
[`REGRAS-DE-NEGOCIO.md` §24](../REGRAS-DE-NEGOCIO.md#24-valores-chave), gerado de `regras.py`. Categorias:

- **Operacional (O)** — a dona ajusta no painel: as três chaves de `Configuracao.CHAVES` (marcadas
  *(painel)* na tabela).
- **Estrutural (E)** — muda só no código, com teste: todas as outras (antecedências e faixas por
  profissional, slots, OTP e cotas de SMS, sessões, janelas de alteração, NPS, links, retenções,
  lotes de e-mail).

Os prazos LGPD (`settings.RETENCAO_*` fixos) são estruturais **e** publicados na política: mudar
exige versão nova do termo LGPD (check `aranha.W016`).

Removidos do catálogo (não existem mais no código): reserva de 30 min da lista de espera, link de
reagendamento de 48 h, antecedência de cancelamento de 24 h, taxa de no-show de 50%, janela de 2 h
da pesquisa online, desconto/cupom de aniversário, saldo mínimo de uso do cashback,
`ListaEspera.token_reserva`/`expira_em`.

## 5. Decomposição para implementação

| Spec | Cobre | Estado |
|---|---|---|
| 5.1 Registry (fonte única) | itens 4 + 5 | ✅ catálogo `regras.py` + `gerar_docs_regras` + teste contra drift (2026-11-03) |
| 5.2 Estados + auto-aprovação | itens 1 + 2 | ✅ cliente de confiança (`status_inicial_publico`) |
| 5.3 Identidade/sessão da cliente | item 3 | ✅ OTP único por telefone + sessão única (`services/sessao_cliente`, 2026-11-03) |

## 6. Fora de escopo — situação

- 🔒 **Segurança:** HMAC com a `SECRET_KEY` no OTP ✅, `compare_digest` ✅, rotação do token do
  ICS ✅, anti-enumeração no agendamento, no portal e no DSAR ✅. DSAR com segundo fator além do
  SMS: não (decisão — a exportação não leva a ficha de saúde). Token de cancelamento: não existe
  mais (reagendar e cancelar só pela sessão do portal). Link de descadastro sem validade: por
  decisão (escopo só de opt-out, revogado na pseudonimização).
- 📣 **Lembretes:** D-1 por WhatsApp ou e-mail ✅; confirmação imediata por WhatsApp e lembrete 1
  semana antes ❌ (não pedidos).
- 💰 **Monetização:** sinal/depósito, cobrança de no-show, pontos/tier ❌ (fora do objetivo).
- ⚠️ **Banco:** UNIQUE 1-retorno-por-origem ✅ (0043); a lista de espera não reserva horário — o
  aviso leva ao agendamento.

## 7. Decisões fixadas

| # | Decisão | Estado |
|---|---|---|
| R1 | Registry como fonte única | ✅ catálogo `regras.py` (documento) + `Configuracao.CHAVES` (operacionais) |
| R2 | Edição híbrida (E trava / O editável) | ✅ só as chaves de `CHAVES` são editáveis no painel, com faixa validada |
| R3 | `otp.max_tentativas` = 5 | ✅ |
| R4 | Antecedência máxima numa regra só | ✅ valor do profissional (sem teto global) |
| R5 | Auto-aprovar agendamento confiável | ✅ cliente de confiança (§3 item 1) |
| R6 | Docs de regras gerados do registry | ✅ §24 do `REGRAS-DE-NEGOCIO.md` gerado; o resto do documento segue escrito à mão, conferido no código |

---

_Última atualização: 2026-11-03 — implementação conferida no código._
