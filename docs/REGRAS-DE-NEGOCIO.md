# Regras de Negócio — Shiva Zen

> Referência funcional do sistema, com os valores **reais** do código (branch `dev`).
> Fonte: `models/`, `services/`, `views/`, `constants.py`, `settings` e `Configuracao`. Mudou regra
> ou valor? Atualize aqui, no código e em `docs/_dados_requisitos.json` (requisitos do TCC).
> Arquitetura e pendências: [`ARCHITECTURE.md`](ARCHITECTURE.md). Acesso: [`AUTENTICACAO-E-ACESSO.md`](AUTENTICACAO-E-ACESSO.md).

---

## 1. Atores e papéis

Só dois papéis de equipe (`usuario.papel`, CHECK no banco):

- **ADMIN** — a Jaqueline/gestão: painel completo, inclusive a recepção (agendar, aprovar,
  confirmar, clientes, retornos). 2FA obrigatório.
- **PROFISSIONAL** — portal `/profissional/`: própria agenda, aprova/rejeita os próprios
  pedidos, marca Realizado, anota a sessão, lê a ficha só de quem atende (vínculo — §20), vê as
  próprias comissões. 2FA obrigatório em produção.
- **Cliente** — nunca é usuário: identifica-se pelo **celular** + código OTP por SMS, sem senha.

O papel RECEPCAO saiu (migration 0072): a dona faz a recepção pelo painel.

- **Usuários da equipe** (Painel > Usuários): e-mail único (sem diferença de caixa); papel
  PROFISSIONAL exige o profissional vinculado, e um profissional tem no máximo um login; ninguém
  muda o próprio papel nem desativa a própria conta, e o último ADMIN ativo não é rebaixado nem
  desativado (a equipe nunca fica trancada fora).
- **Profissionais / desligamento:** desativar a profissional, desativar o login dela ou trocar o
  profissional vinculado ao login **revoga o feed ICS** dela na hora (auditado): o calendário de
  quem saiu para de receber os nomes das clientes. Reativar não devolve o link — é preciso gerar um
  novo (Painel > Profissionais). A edição que deixaria atendimentos futuros dela sem quem atenda
  (desativar, tirar o dia ou mudar a janela para fora do horário marcado, tirar a habilitação do
  procedimento) é recusada listando-os: reagende ou cancele antes.

---

## 2. Cliente

- **Identidade:** telefone canônico (só dígitos, 10–11, sem DDI/zero de tronco); e-mail e CPF
  opcionais. Únicos entre clientes ativos (UNIQUE parcial no banco). CPF/telefone com máscara são
  normalizados (telefone: `utils/telefone`, a mesma forma no cadastro, no OTP e no WhatsApp).
- **Cadastro mínimo:** nome e celular (o nascimento é exigido no agendamento online); e-mail, CPF,
  CEP e endereço opcionais — CPF,
  CEP e endereço só pela finalidade (nota fiscal e contrato de pacote). RG, profissão e foto não são
  coletados (saíram na 0074).
- **Idade mínima para agendar online: 18 anos** (data de nascimento obrigatória no wizard).
- **Bloqueio por faltas:** cada FALTOU soma 1 em `faltas_consecutivas` — e cada **cancelamento
  tardio** da própria cliente também (§8: menos de 24 h de um horário já aprovado); ao atingir o limite
  (regra operacional `MAX_FALTAS_BLOQUEIO`, Painel > Regras operacionais: inteiro de **1 a 10**,
  padrão **3** = `MAX_FALTAS_ANTES_BLOQUEIO`; valor não numérico ou menor que 1 gravado por fora da
  tela vale como 3) a cliente fica `bloqueado_online` e o wizard recusa com o motivo e o caminho de
  revisão ("suspenso depois de faltas seguidas sem aviso ou por decisão da clínica; para pedir a
  revisão ou marcar, fale conosco pelo WhatsApp" — decisão automatizada, LGPD art. 20). Comparecer
  (REALIZADO) zera o contador **e** desbloqueia. O painel ainda agenda cliente bloqueada.
- **Exclusão:** `Cliente.delete()` é soft delete (`deletado_em`); apagar de verdade só com
  `hard=True`. Pseudonimização (o "esquecimento"): §22.

---

## 3. Agendamento público (wizard)

Fluxo: **procedimento → data/horário (e profissional) → dados + código SMS → confirmar**.

Caso de uso único: `services/agendamento_online.solicitar` (o form do wizard só lê o POST e
confere o OTP; a regra inteira mora no service, e cada recusa tem um `codigo`).

- **Código OTP obrigatório em todo agendamento** (cliente nova ou recorrente), preso ao celular
  informado; o telefone do formulário tem de ser o verificado na sessão (30 min).
- **O pedido é da titular do celular:** com cadastro existente, o nome do cadastro fica — nome
  diferente digitado no wizard é ignorado (vai só para o log; renomear é pelo painel, auditado).
  Assim quem agenda para outra pessoa não mistura a ficha de saúde dela no cadastro da titular.
  Nascimento vazio no cadastro é preenchido; o e-mail segue a regra abaixo.
- **Teto de pedidos pelo site:** quem já tem **3 horários futuros ativos** (`MAX_ATIVOS_ONLINE`;
  PENDENTE/AGENDADO/CONFIRMADO, de qualquer canal) não marca outro pelo site ("fale conosco pelo
  WhatsApp") — o PENDENTE trava a agenda, e sem teto um celular verificado reservaria a semana. A
  recepção segue marcando pelo painel.
- **Horários** vêm do `SlotService` (§5) e o servidor **revalida** no confirmar: procedimento e
  profissional ativos, profissional habilitado, horário futuro, não é feriado/recesso ativo, slot
  ainda livre para a duração inteira.
- **Antecedência mínima:** `profissional.min_notice_horas` (padrão **2 h**, 0–720).
- **Antecedência máxima:** `profissional.max_advance_dias` (padrão **60 dias**, 1–365). Não há
  teto global.
- **Anti double-booking:** trava de 30 s no cache + `SELECT FOR UPDATE` + EXCLUDE no Postgres;
  perdeu a corrida → "Este horário acabou de ser reservado".
- **Status inicial** (`services/agenda.status_inicial_publico`, D8): cliente **de confiança** — já
  atendida (1+ REALIZADO), sem falta em aberto, sem bloqueio e com menos de
  `LIMITE_AUTOAPROVACAO` (**2**) horários futuros — nasce **AGENDADO** e recebe o e-mail de
  confirmação na hora; os demais (primeira vez incluída) nascem **PENDENTE** e a profissional
  recebe o e-mail de aprovação (§7). A página de sucesso diz qual dos dois aconteceu.
- **Preço gravado** = preço vigente na **data do atendimento** (profissional > base) com a
  promoção que vale nesse dia (§13): `valor_cobrado`, `valor_original` (cheio, se houve
  promoção), `promocao`, `descricao_preco`. É o mesmo valor mostrado no resumo do wizard (preço na
  data por profissional).
- **Preço de vitrine** ("A partir de", nos cards do wizard, no widget, em `/especialidades/`, na
  página do serviço e em `/promocoes/`): o preço **base do dia** (sem base, o menor entre os
  profissionais) com a promoção que o agendamento aplicaria — e só se ela reduz o valor
  (`services/precos.precos_de_vitrine`). Sem preço: "A consultar".
- **LGPD no wizard:** aceite obrigatório da Política de Privacidade (grava `AceiteTermo` da versão
  LGPD vigente com IP, navegador e SHA-256 do texto; sem versão LGPD vigente o pedido é recusado);
  termos de PROCEDIMENTO (do procedimento + gerais) aceitos ali mesmo (versão já aceita antes não é
  pedida de novo); ficha de anamnese (dado de saúde) só é gravada com o **consentimento específico
  do art. 11** marcado, registrado na auditoria com o texto e o IP.
- **Ficha vale 12 meses** (`VALIDADE_FICHA_MESES`): a obrigatória que a titular respondeu nos 12
  meses antes do horário escolhido não é exigida de novo (o wizard mostra "Sua ficha de dd/mm/aaaa
  está em dia — algo mudou?"); só a obrigatória pendente ou vencida é exigida. Se a cliente começa a
  responder uma ficha em dia ou opcional ("Atualizar"), as respostas são validadas e gravadas como
  uma resposta nova.
- **Dia sem horário livre:** a lista de horários oferece "Avise-me se vagar", que abre a lista de
  espera (§17) já com o procedimento, a data e a profissional (quando escolhida) preenchidos.
- **Comunicação:** caixas de e-mail marketing, lembrete por WhatsApp e pesquisa por WhatsApp
  **nunca vêm marcadas**; marcar concede (data + IP); desmarcar revoga só quando a caixa mostrava
  o estado do cadastro.
- **E-mail:** o digitado por quem provou o celular substitui o do cadastro; e-mail de outro
  cadastro é recusado.
- Ao concluir: push "Novo agendamento" à profissional (se ela ativou) e o e-mail do status —
  pedido PENDENTE = e-mail de aprovação à profissional com link para a agenda do dia; de confiança
  = e-mail de confirmação à cliente (§7). A cliente sai da lista de espera daquele procedimento
  (§17) e a área **"Meus agendamentos"** já abre sem novo SMS (o celular acabou de ser verificado;
  §8).
- **Sem SMS configurado** o agendamento online fica indisponível e os botões "Agendar" levam ao
  WhatsApp da clínica.

---

## 4. Agendamento interno (recepção, no painel)

Caso de uso `services/agenda.agendar_pela_recepcao`; a tela valida pelos Forms
(`forms/agenda.AgendamentoPainelForm`, `forms/cliente.ClienteRapidoForm`).

- Painel > Agendamentos > **Novo agendamento**: cliente existente (busca sem acento) ou nova
  (nome, telefone obrigatório com DDD, e-mail opcional — gravada na mesma transação do
  atendimento), procedimento, profissional **que realiza o procedimento**, dia e um dos horários
  livres do **mesmo SlotService** do site; no "agendar" o servidor revalida tudo
  (`validar_horario`, com o motivo detalhado para a equipe). Cliente inativa não agenda: a tela
  avisa e leva à ficha para reativar; telefone ou e-mail de cliente já cadastrada é recusado
  ("busque a cliente cadastrada").
- Sem OTP da cliente; nasce **AGENDADO** (a própria clínica confirmou), a cliente recebe o e-mail
  de **confirmação** (data, valor e links) e fica na auditoria (`Agendou pelo painel`).
- Valor = preço da data com a promoção (`preco_da_recepcao`); valor informado diferente vira
  "Valor combinado pela recepção" (o preço cheio fica em `valor_original`, sem promoção). Depois de
  agendar, avisa quando há **ficha de anamnese obrigatória pendente** ("Link da ficha", §20).
- Corrida pelo horário (outro pedido reservou no meio) → "Este horário acabou de ser reservado";
  outro cadastro com o mesmo telefone/e-mail/CPF no meio do caminho → recusa
  (`cliente_duplicada`).

---

## 5. Disponibilidade (SlotService)

Slots de **30 min** no dia, considerando: folga da clínica ativa (feriado nacional/local,
recesso — `AgendaExcecao` FOLGA sem profissional; inativa = a clínica abre); exceção do
profissional (FOLGA em intervalo de datas ou HORÁRIO_DIFERENTE, que substitui a grade do dia);
grade semanal `AgendaHorario` (todas as janelas do dia, sem sobreposição);
atendimentos ativos (PENDENTE/AGENDADO/CONFIRMADO) sobrepostos na duração inteira + **buffer**
do procedimento (0–120 min); bloqueios pontuais e recorrentes (RRULE) do profissional **ou
globais** ("Todos os profissionais"); `min_notice_horas`; `max_advance_dias`.

---

## 6. Máquina de estados do atendimento (FSM)

| De | Para (permitido) |
|---|---|
| PENDENTE | AGENDADO, CANCELADO, REAGENDADO |
| AGENDADO | CONFIRMADO, REALIZADO, CANCELADO, FALTOU, REAGENDADO |
| CONFIRMADO | REALIZADO, CANCELADO, FALTOU, REAGENDADO |
| REALIZADO, CANCELADO, FALTOU, REAGENDADO | — (terminais) |

Semântica: **PENDENTE** = pedido aguardando a clínica; **AGENDADO** = horário marcado/aprovado;
**CONFIRMADO** = a cliente disse que vai (link do lembrete) ou a equipe confirmou. Toda transição
trava a linha, valida a tabela acima, grava `LogAuditoria` e dispara os efeitos (§23). Transição
inválida → `Atendimento.TransicaoInvalida` (a tela mostra a mensagem, nunca 500).

**Desfecho só depois do início:** REALIZADO e FALTOU só valem a partir do início do atendimento
(menos `TOLERANCIA_DESFECHO`, **30 min**, para quem marca na hora) — a FSM recusa antes
(`desfecho_antes_do_inicio`; o painel responde 409): um clique na agenda da semana seguinte não
gera comissão, não debita pacote nem conta falta.

**Seletor de status** (listas do painel — agenda e visão geral, `partials/_select_status.html`): só
aparece para **AGENDADO** (Confirmar presença, Realizado, Cancelar, Faltou) e **CONFIRMADO**
(Realizado, Cancelar, Faltou); Realizado e Faltou só entram na lista a partir do mesmo limite. Pedido
PENDENTE mostra o selo "Aguardando aprovação" e se decide nos botões (§7); status final não tem
seletor (Realizado tem o "Desfazer realização", com motivo: estorna a comissão pendente e o
cashback, devolve a sessão do pacote; comissão já paga recusa). Cancelar pelo seletor avisa a
cliente por e-mail.

**Termo pendente:** marcar REALIZADO com termo de procedimento ainda não aceito é recusado
(`excecoes.TermoPendente`; o painel responde 409 `termo_pendente` com a lista dos termos); a equipe
pode confirmar explicitamente **"Realizado sem termo aceito"** (ex.: assinado em papel), o que fica
na auditoria. A regra é a mesma no painel e no portal da profissional
(`services/atendimento.marcar_realizado`).

---

## 7. Aprovação e rejeição

- PENDENTE → **aprovar** = AGENDADO + e-mail de confirmação à cliente; **rejeitar** = CANCELADO +
  e-mail de cancelamento. Pelo painel (botões Aprovar/Recusar da agenda e da visão geral, ou **em
  lote**: marcar os pendentes e aprovar/recusar todos) ou pelo portal (só os próprios pedidos do
  profissional), sempre pelo `AgendamentoService`. Auditado com IP. O lote decide **item a item**
  (`decidir_em_lote`, cada um na sua transação): o pedido que outra tela decidiu no meio do caminho
  (ou que falhou no banco) fica de fora sem desfazer os outros, e a tela diz quantos foram
  decididos. Pedido já decidido em outra tela vira aviso ("já está como …"), nunca erro. Pedido não
  pode ir direto a CONFIRMADO (confirmar presença é da cliente, depois de aprovado).
- PENDENTE sem decisão a **menos de 1 h do início** (`PRAZO_DECISAO_PENDENTE`) → CANCELADO pelo
  job `limpeza_status` (de hora em hora; "expirado sem aprovação") + e-mail à cliente "não
  conseguimos confirmar o seu pedido a tempo", e a vaga volta para a lista de espera.

**Quem é avisado de quê** (e-mail pela fila; push só se a profissional ativou):

| Evento | Cliente | Profissional |
|---|---|---|
| Pedido pelo site (cliente nova) | página "Solicitação recebida" | e-mail de aprovação + push |
| Pedido pelo site (cliente de confiança) | e-mail de confirmação | push "Novo agendamento" |
| Clínica aprova / recusa / cancela | e-mail de confirmação / cancelamento | — |
| Clínica agenda pelo painel | e-mail de confirmação | push "Novo agendamento" |
| Cliente cancela ou remarca (portal/link) | tela de sucesso | push "Cliente cancelou" / "Cliente remarcou" |
| Pedido vence sem aprovação | e-mail de cancelamento | — |

---

## 8. Reagendamento e cancelamento

- **Pela cliente**, só pelo portal **"Meus agendamentos"** (id do atendimento + sessão do OTP, só
  agendamentos do telefone verificado; não há link de reagendar — o legado saiu na 0072): reagendar
  só com **≥ 24 h** de antecedência (`JANELA_MINIMA_ALTERACAO`) e status ativo; cancelar a
  qualquer momento **antes do início** (também pelo link do lembrete D-1, §9, que só mexe em
  AGENDADO/CONFIRMADO), pela regra única `services/agenda.cancelar_pela_cliente` (linha travada:
  clique duplo não cancela nem conta falta duas vezes).
- **Cancelamento tardio:** cancelar com **menos de 24 h** um horário **já aprovado** (AGENDADO ou
  CONFIRMADO) conta como falta — soma 1 em `faltas_consecutivas` e pode suspender o agendamento
  online (§2) —, sem virar FALTOU (o atendimento fica CANCELADO e a vaga volta para a lista de
  espera). Pedido ainda PENDENTE não conta (a clínica não tinha confirmado). O modal do portal e a
  página do link avisam antes; a resposta diz quando contou como falta, e a trilha registra
  "Cancelado pela cliente (portal|link) — cancelamento tardio, conta como falta".
- **Área da cliente** ("Meus agendamentos", sessão do OTP de 1 h, aberta também no fim do wizard
  sem novo SMS): agendamentos futuros e passados, reagendar e cancelar, retornos sem custo a
  combinar, código e saldo de indicação, **Minhas preferências** (as 3 autorizações de
  comunicação, §21), **revogar o consentimento de saúde** (§21), **pedir correção ou exclusão** e
  **baixar meus dados** (§22).
- Reagendar: o antigo vira **REAGENDADO** antes de o novo entrar (a constraint não acusa conflito
  consigo mesma); o novo herda preço/promoção, `eh_retorno` e `atendimento_origem` (retorno
  continua retorno, com a duração de retorno) e a **ficha de anamnese** passa para o novo
  atendimento. Status do novo: **AGENDADO** se o original já estava aprovado (AGENDADO/
  CONFIRMADO) **e** a profissional é a mesma; senão volta a **PENDENTE**.
- **Pelo painel:** mudança de status pela FSM (seletor, §6); arrastar no calendário move o horário
  no lugar (`services/agenda.mover`: só PENDENTE/AGENDADO/CONFIRMADO; duração e preço combinados
  ficam), com a mesma validação do site e o motivo da recusa para a equipe — conflito, bloqueio,
  folga, feriado, fora do expediente (409); status final → 400.
- Cancelamento e reagendamento **liberam a vaga** para a lista de espera (§17).

---

## 9. Lembrete D-1 e confirmação de presença

- Job `lembrete_diario` (**08:00 e 18:00** — a rodada da noite pega quem marcou depois das 8h)
  para atendimentos **AGENDADO** de amanhã:
  - **WhatsApp** (template `confirmacao_d1`) se a cliente autoriza hoje o lembrete por WhatsApp
    (LEMBRETE_AGENDAMENTO/WHATSAPP vigente no histórico de consentimento — o WhatsApp é opt-in pela
    política da Meta), tem telefone e o canal está configurado;
  - senão, **e-mail** (se houver), pela fila de e-mail, com os mesmos links — aviso do serviço
    contratado, não marketing (`services/lembretes`).
  Uma vez por atendimento: WhatsApp enviado ou e-mail na fila/enviado = já lembrado; WhatsApp que
  falhou é tentado de novo na rodada seguinte. Lembrete de atendimento já cancelado ou passado não
  sai.
- O lembrete traz dois links (`/confirmar/<token>/?acao=confirmar|cancelar`): confirmar
  (AGENDADO → CONFIRMADO) ou cancelar, **uma resposta só**; PENDENTE, terminal ou passado não é
  editável. Cancelar por ali com menos de 24 h é cancelamento tardio e conta como falta (§8): a
  página avisa antes.
- O descadastro de marketing **não** desliga o lembrete (é aviso do serviço); o "PARAR" no
  WhatsApp desliga só o WhatsApp — o lembrete passa a ir por e-mail (§21).

---

## 10. Faltas (no-show)

- Só a **equipe** marca FALTOU (AGENDADO/CONFIRMADO → FALTOU, a partir do início — §6) — soma
  falta e pode bloquear o online (§2). O cancelamento tardio da própria cliente (§8) também soma
  falta, sem virar FALTOU.
- O sistema **não** marca falta sozinho (FALTOU é terminal e bloquearia a correção para
  REALIZADO): todo dia às 08:00 (job `sem_desfecho`) cada profissional recebe um **push** com
  "N atendimentos aguardam desfecho" — os AGENDADO/CONFIRMADO dela que já terminaram sem
  Realizado/Faltou/Cancelado. O painel mostra os mesmos no card "sem desfecho" da visão geral.
  Sem desfecho não gera comissão, não debita pacote, não dispara NPS nem entra no faturamento.
- Não há cobrança de taxa de no-show no sistema.

---

## 11. Retorno obrigatório (F-RET)

- Procedimento com `exige_retorno` (janela `retorno_minimo_dias`–`retorno_maximo_dias` após o
  fim do atendimento, `duracao_retorno_minutos`, padrão 30): o **REALIZADO** deixa o retorno
  **devido** — o sistema **não reserva** horário sozinho (a cliente não escolhia o dia e, sem
  aprovação, recebia "cancelado").
- **Quem marca:** a recepção, em **Painel › Retornos a marcar** (card na visão geral e na ficha da
  cliente): só horários livres **dentro da janela** (ignora `max_advance_dias`/aviso mínimo,
  respeita expediente, folgas, feriados e bloqueios) → `eh_retorno=True`, **valor 0**, status
  **AGENDADO** (combinado com a cliente). A cliente vê em **Meus agendamentos** "Seu retorno de X é
  sem custo — agende entre dd/mm e dd/mm", com atalho para o WhatsApp da clínica.
- Fora da janela, origem não realizada ou retorno já marcado → recusa com o motivo. No máximo
  **1 retorno vivo por origem** (UNIQUE no banco). Janela vencida sai da lista.
- Retorno **não gera comissão, não gera cashback, não consome sessão de pacote** e o valor fica
  sempre 0.

---

## 12. Pacotes

- Pacote = itens (procedimento × sessões), preço total e validade em meses. O **catálogo é
  editável a qualquer momento** (nome, itens, preço, validade, ativo): a **venda congela** o que foi
  vendido — nome do pacote e, por item, procedimento, nome, sessões e **valor de 1 sessão**.
  O valor pago é **rateado em proporção ao preço de tabela** de cada procedimento na data da venda
  (`rateio_ponderado`: peso = preço × sessões; centavos meio p/ cima, o resto de centavos no item de
  maior peso); se algum item não tem preço de tabela, o rateio é uniforme (valor ÷ sessões). Ex.:
  R$ 1.800 por 1 toxina + 5 drenagens → 1.107,70 e 138,46 por sessão. Editar o catálogo só vale
  para as próximas vendas; quem já comprou mantém o que comprou. Pacote inativo não é vendido.
- Venda (ficha da cliente ou Painel > Pacotes): `valor_pago`, expiração = data local da venda +
  validade.
- Sessão REALIZADA de procedimento **do que foi vendido** **debita 1 sessão** daquele item
  (1 consumo por atendimento; no banco o consumo só entra se o item é da compra, o procedimento é o
  mesmo, não é retorno e ainda há saldo), escolhendo o pacote que vence primeiro. A validade é conferida na **data da sessão**: sessão
  feita até o último dia debita mesmo que o job já tenha marcado o pacote EXPIRADO; sessão depois
  da validade não debita. Retorno gratuito nunca debita.
- **Sessão prevista** (`services/pacotes.sessoes_previstas`, só leitura): antes do Realizado, o
  atendimento que um pacote da cliente vai cobrir (mesma ordem do débito, descontando as sessões
  já marcadas) aparece como "Pacote · restam N" no painel e "Sessão do pacote X" no portal, no
  e-mail de confirmação e na página de sucesso — nunca o preço cheio.
- Status: **ATIVO** → **FINALIZADO** (todas as sessões usadas) | **EXPIRADO** (job
  `pacote_expirar`: vencimento < hoje) | **CANCELADO** (`services/pacotes.cancelar_compra`: valor
  devolvido **em dinheiro** de 0 até o valor pago — sugerido = sessões restantes × valor da sessão,
  editável —, motivo obrigatório, auditado sem o nome da cliente; grava `cancelado_em`; sessões
  usadas continuam registradas). Reembolso nunca vira crédito na carteira.
- **Prorrogar validade** (só ADMIN, `prorrogar_validade`): compra ATIVA ou EXPIRADA, nova data
  depois da atual e não no passado, motivo obrigatório, auditoria de/para; EXPIRADA volta a ATIVA.
- Aviso por e-mail 7 e 1 dia antes do vencimento, se houver saldo ("Sessões realizadas até dd/mm
  contam no seu pacote").
- A ficha da cliente mostra por pacote (nome da venda): sessões usadas/total por item vendido e
  validade. O DSAR exporta o mesmo (nome, itens, sessões usadas/restantes, valor por sessão).
- Compras de antes da 0052 herdaram o catálogo da época da migração; sessão sem item correspondente
  virou item reconstruído (valor 0) e consumo acima da quantidade ficou só registrado — ambos na
  auditoria (`migration 0052: …`).

---

## 13. Preços e promoções

- **Preço** versionado por `vigente_desde`: vale a vigência mais recente ≤ hoje (fuso local); só
  vigência futura → usa a mais próxima. Preço do profissional > preço base; sem base, o menor
  preço vigente entre profissionais.
- **Mudar o preço base** (Painel > Procedimentos, `services/catalogo`): abre uma vigência nova a
  partir de hoje, só se o valor mudou (salvar sem mexer no preço não cria vigência; já havendo uma
  de hoje, ela é ajustada); o histórico fica e o atendimento já marcado guarda o próprio valor.
- **Quem realiza** = as profissionais marcadas no procedimento (habilitação); tirar a habilitação
  de quem ainda tem atendimento futuro dele é recusado (reagende ou cancele antes).
- **Promoção:** desconto **0–100% XOR** preço fixo (CHECK no banco), período `data_inicio`–
  `data_fim`, ligada a um procedimento. Promoção **geral** (sem procedimento) só vale como
  **percentual** — preço fixo geral é ignorado pelo agendamento e escondido em `/promocoes/`.
  Só se aplica quando **reduz** o valor; nunca deixa negativo.
- O agendamento grava a promoção **da data do atendimento** (não da data em que se agenda);
  `/promocoes/` e `/especialidades/` mostram exatamente o preço que o agendamento gravaria. Por isso
  o e-mail da promoção anuncia o período dos atendimentos (§19), nunca "válido por N dias".

---

## 14. Valor cobrado e faturamento

- Painel > Agendamentos > **valor** (`services/atendimento.registrar_valor_cobrado`): registra o
  valor combinado ("Valor combinado pela recepção"; guarda o anterior em `valor_original`) e pode
  abater o crédito de indicação da cliente (até o saldo e o valor). Recusado para
  CANCELADO/FALTOU/REAGENDADO e para retorno (sempre 0). Em REALIZADO, recalcula a comissão — o
  crédito PENDENTE é estornado e lançado de novo com a base nova; comissão já **paga** recusa (o
  repasse saiu) — e libera o cashback da indicadora se ainda não tinha saído.
- **Competência mensal** (`services/financeiro.competencia`, `?mes=AAAA-MM`, padrão o mês local
  corrente): o financeiro, a visão geral e o Excel usam o mesmo período, escrito na tela.
- **Faturamento do mês** = atendimentos **avulsos** REALIZADOS com valor > 0 (sem retornos, **sem
  sessões de pacote**) pela data do atendimento + **pacotes**: vendas pela data da venda **menos
  reembolsos pela data do cancelamento** (`cancelado_em`). Mês fechado não muda; um mês pode ter
  pacotes negativos (reembolso de venda antiga). Tabela "últimos 12 meses" no financeiro.
- **Ticket médio** = média dos avulsos faturados (mesma regra acima). Uma função só
  (`services/financeiro`) para a visão geral, o financeiro e a ficha da cliente.
- **Excel mensal** (`?mes=`): abas Resumo, Atendimentos (origem avulso/pacote/retorno, faturado?,
  comissão), Pacotes (vendas +, reembolsos −) e Comissões; total igual ao card; exportação auditada.
- **LTV da ficha** = avulsos faturados + pacotes líquidos: a sessão de pacote não soma de novo a
  preço cheio (a receita dela já entrou na venda).
- Taxa de faltas do mês = FALTOU / (REALIZADO + FALTOU).

---

## 15. Comissão do profissional

- Calculada ao marcar **REALIZADO** (não em retorno). Regra mais específica primeiro
  (`RegraComissao` ativa): (profissional + procedimento) → (profissional) → (procedimento) →
  global; empate = a editada por último.
- Valor = **percentual** (0–100) **ou** valor fixo (limitado à base), sobre a **base**:
  `valor_cobrado` no avulso; na sessão de pacote, o **valor de 1 sessão congelado no item** da
  venda (rateio ponderado, §12). Cada CRÉDITO guarda a base e o percentual aplicados (explica o
  valor mesmo se a regra mudar).
- **Livro-razão** (append-only, imutável no banco): o cálculo lança um **CRÉDITO** (no máximo 1
  ativo por atendimento — só depois de estornado pode nascer outro, 0063); a baixa no Painel > Comissões > pagar lança
  um **PAGAMENTO** (valor integral, sem baixa parcial; autor e auditoria); o cancelamento posterior
  lança um **ESTORNO** do crédito, pago ou não. Status derivado: ESTORNADA (há estorno) > PAGA
  (há pagamento) > PENDENTE. Estornada não pode ser paga.
- **Comissão paga nunca é estornada**: cancelar ou desfazer a realização de atendimento com
  comissão paga é recusado (o acerto é no repasse); valor corrigido antes do pagamento = estorno +
  novo crédito. Comissões no mês contam pela **data do atendimento**.
- **Repasse em lote**: Painel > Comissões > "Pagar pendentes até dd/mm" por profissional — um
  PAGAMENTO por crédito pendente na mesma transação, auditado, com **recibo para imprimir** (sem
  nome de cliente). O filtro inclui profissional inativa com saldo. No portal, "Minhas comissões"
  (só leitura).
- **Regras de comissão no painel** (Comissões › Regras): percentual XOR fixo, especificidade e
  número de usos à vista; regra **já usada não muda nem se exclui** — só desativa (o model e o
  trigger `tg_regra_comissao_validar` recusam); o Django admin é só leitura para ela. Comissões e
  financeiro avisam quando não há regra ativa.

---

## 16. Cashback por indicação (F-CSB) e carteira

- Quando a **indicada** tem o **primeiro** atendimento REALIZADO **pago** (valor > 0, não retorno),
  a **indicadora** (`indicado_por`) ganha **R$ 50,00** (`VALOR_CASHBACK_INDICACAO`) na carteira.
  Idempotente; cancelamento do atendimento estorna (`CASHBACK_ESTORNO`), saldo nunca negativo.
- Carteira é ledger **imutável** (trigger): correção = novo movimento. A observação do cashback
  guarda só o **id** da indicada (`Indicacao (cli #<id>)`), nunca o nome — o ledger não pode ser
  pseudonimizado (a 0053 saneou as linhas antigas).
- A ficha da cliente registra quem indicou (código de indicação) e mostra saldo, extrato e as
  indicadas; o portal mostra o código e o saldo. O crédito é **usado** no valor de um atendimento
  (até o saldo e o valor; 1 uso por atendimento; não em retorno) e **volta** se o atendimento é
  cancelado, falta ou é remarcado. A comissão continua sobre o valor cheio.
- Origens da carteira: só as que têm fluxo (cashback de indicação, estorno, uso e devolução).

---

## 17. Lista de espera

Regra em `services/lista_espera` (form público em `forms/publico.ListaEsperaForm`).

- **Formulário público** (`/lista-espera/`, ou "Avise-me se vagar" do wizard, que já chega com
  procedimento, data e profissional): nome, celular, procedimento, data desejada (a partir de
  hoje), turno (manhã, tarde, noite ou qualquer) e profissional (opcionais), e-mail opcional.
  Anti-abuso: captcha (Turnstile, quando configurado), campo-isca (honeypot: robô é descartado em
  silêncio) e **20 inscrições por dia por IP**. A página de sucesso é igual em todos os casos
  (inscrição nova, repetida ou descartada — sem enumeração).
- **Não cria cadastro.** A inscrição é **anônima** (`nome_contato` + `telefone_contato`, sem
  e-mail); só se liga à cliente — e só então guarda o e-mail digitado em `email_contato`, nunca no
  cadastro — quando o celular foi **verificado por SMS nesta sessão** (o OTP do wizard). Uma
  inscrição por pessoa (cliente ou celular) + procedimento + dia (UNIQUE); repetir devolve a que
  já existe.
- **Aviso de vaga** (mecanismo único): quando um atendimento ativo futuro vira CANCELADO ou
  REAGENDADO, os pedidos compatíveis — mesmo procedimento e dia, profissional vazio ou igual,
  turno vazio ou o da vaga (manhã antes das 12h, tarde das 12h às 18h, noite a partir das 18h) e
  só se a vaga respeita a antecedência mínima da profissional (o site não deixaria marcar antes) —,
  em ordem de chegada. Recebem aviso automático só as inscrições **ligadas ao cadastro**: e-mail
  (`email_contato` ou o do cadastro; o nome só vai quando o destino é o e-mail do cadastro) e
  WhatsApp (template `lista_espera_vaga`, só com o lembrete por WhatsApp autorizado), com link para
  o wizard já no procedimento, na profissional e no dia. Não há reserva do horário: quem agendar
  primeiro leva. A entrega grava `ultimo_aviso_em` e **a inscrição continua na fila** (quem não
  conseguiu a vaga segue esperando).
- **Inscrição anônima** não tem canal automático: a recepção vê nome e celular no painel e faz o
  contato. **Painel > Lista de espera > Avisar**: e-mail quando há destino (mesma regra) e o aviso
  fica registrado mesmo sem e-mail (a recepção completa pelo WhatsApp ou telefone); auditado.
- **Sai da fila** quando a data passa (job semanal de LGPD) ou quando a pessoa **agenda** o
  procedimento (por qualquer porta: as inscrições dela — pelo cadastro ou pelo mesmo celular — para
  aquele procedimento, de hoje em diante, são apagadas). A pseudonimização apaga também a inscrição
  anônima com o celular da titular.

---

## 18. NPS e depoimentos

- Job `nps_24h` (10:00, uma vez por dia): WhatsApp (template `nps_pos_atendimento`) para
  atendimento REALIZADO que terminou **entre 24 h e 7 dias** atrás, sem avaliação, com a pesquisa
  por WhatsApp autorizada (PESQUISA_SATISFACAO/WHATSAPP vigente — o descadastro e o "PARAR"
  revogam); até 3 tentativas com falha. **Nunca em retorno** (gratuito: distorce a média e cansa
  quem faz pacote) e **no máximo 1 pesquisa por cliente a cada 90 dias** (`NPS_INTERVALO_DIAS`);
  duas sessões da mesma cliente na rodada geram uma pesquisa só.
- Link `/nps/<token>/` vale **7 dias**; 1 avaliação por atendimento (clique duplo devolve a que já
  existe); nota **0–10** (CHECK) e comentário até 2.000 caracteres. Depois da nota a cliente vai para
  `/nps/<token>/obrigado/` (cumprimenta pelo primeiro nome e oferece a pesquisa pós-atendimento, se
  houver — §20). A nota também pode chegar respondendo o WhatsApp (0–10): as duas portas passam pelo
  mesmo `services/nps.registrar_avaliacao`.
- **Detrator** (nota ≤ 6): e-mail de alerta para `email_admin` (Painel > Regras operacionais; a
  tela só aceita e-mail válido, e um inválido gravado antes conta como vazio) > `ADMIN_EMAIL` >
  e-mail de contato da Identidade visual (job `detrator_alerta`, 10:30).
- **Depoimento no site** (`/depoimentos/`): só com opt-in da cliente (`autoriza_publicacao`),
  aprovação da equipe (Painel > NPS > publicação), nota ≥ 9 e comentário; cliente excluída ou
  pseudonimizada nunca aparece. Autor exibido com nome abreviado.

---

## 19. Aniversário e e-mails de promoção

- **Aniversário** (job 09:00, uma vez por dia): e-mail de **felicitação, sem desconto** (não há
  mecanismo de cupom), só com marketing por e-mail autorizado (MARKETING/EMAIL vigente); 29/02
  comemora em 28/02 em ano não bissexto. Não há WhatsApp de aniversário nem de marketing.
- **Promoção por e-mail** (só pelo painel: Promoções > Disparar; não há job): só para cliente
  ativa com e-mail e marketing por e-mail autorizado, **sem cupom** (a promoção vale sozinha no
  agendamento). O e-mail diz a oferta e **"para atendimentos de dd/mm/aaaa a dd/mm/aaaa"** (o preço
  vale pela data do atendimento — §13), com o "Agendar" já no procedimento; HTML sanitizado, link e
  header de descadastro. Só promoção vigente hoje é disparada.
- **Disparo único** (`services/marketing`): o envio roda no request, em lotes de 25 por clique
  ("Continuar envio"), com o cursor na própria promoção (`envio_cursor`). Cada lote é reservado
  (cursor gravado, linha travada) **antes** de sair: clique duplo ou queda no meio nunca repetem um
  e-mail — no pior caso quem estava no lote interrompido fica sem a oferta. Concluído o envio, mandar
  de novo exige marcar **"reenviar para todos"** (confirmação explícita). Não há "validade em dias"
  na tela.

---

## 20. Ficha (prontuário), histórico, anotações, anamnese e alertas

- **Prontuário** 1:1 com a cliente: alergias, contraindicações, histórico de saúde, medicamentos,
  observações + perguntas extras do **questionário do prontuário** — o formulário tipo PRONTUARIO
  (Painel > Anamneses, mesmo editor/validador das fichas; 1 ativo, vale para todas, nunca
  obrigatório). Respostas em `respostas_extras` pela `key` da pergunta: mudar o texto da pergunta
  não mexe nas respostas; resposta inválida (opção fora da lista, texto longo demais) não salva
  nada.
- **Histórico:** cada edição grava antes a foto do estado anterior em `prontuario_versao`
  (append-only, imutável no banco) com autor; a tela mostra o histórico. Edição concorrente é
  recusada (trava otimista) e um POST parcial não apaga campos.
- **Acesso:** ADMIN lê tudo; profissional só com **vínculo** — atendimento REALIZADO nos últimos
  **180 dias** ou ativo entre ontem e **+60 dias** (escrita sem PENDENTE). Toda leitura, gravação
  e negativa é auditada.
- **Anotação de sessão:** por atendimento, com autor preservado (PROTECT + `autor_nome`).
- **Fichas de anamnese** (`FormularioAnamnese`, schema JSON, tipos ANAMNESE/PESQUISA, escopo
  global/categoria/procedimento, `obrigatorio`): respondidas no wizard ou por link
  `/anamnese/<token>/` (60 dias; consentimento art. 11 obrigatório). Formulário com respostas cujo
  schema muda vira nova versão (o antigo é desativado).
- **Onde a ficha vale** — regra única `FormularioAnamnese.aplica_a` / `aplicaveis_a` (wizard,
  alertas do painel, convites), **3 escopos**: global; categoria do procedimento; o procedimento.
  Todo atendimento é presencial (o escopo por modalidade saiu na 0073). O wizard recebe do servidor
  a lista de procedimentos de cada ficha (não repete a regra no JS).
- **Cliente agendada pelo painel** (não passa pelo wizard): o botão **"Link da ficha"** na agenda
  gera o convite de cada ficha obrigatória pendente (WhatsApp, e-mail ou "abrir no balcão"); o
  segundo clique reaproveita o convite em aberto.
- **Pesquisa pós-atendimento** (tipo PESQUISA, opcional): oferecida na página de agradecimento do
  NPS (§ NPS), no mesmo link que a cliente já recebeu — sem mensagem extra. Uma resposta por
  atendimento.
- **Alertas de saúde** (texto visível no portal, na agenda do painel e na ficha da cliente): o que
  a equipe registrou no prontuário (alergias, contraindicações, medicamentos, perguntas extras) +
  respostas de risco declaradas pela cliente nas fichas (alergia, gestação, anticoagulante,
  diabetes, etc.). Marcador **"ficha pendente"** quando a ficha obrigatória do procedimento não foi
  respondida. Cliente que **revogou o consentimento de saúde** (§21) tem no topo dos alertas
  "Consentimento de saúde revogado em dd/mm/aaaa" (o registro já feito continua visível). Os
  marcadores da linha (alerta, termo pendente, ficha pendente e, na agenda, pacote e crédito) saem
  em lote (`services/alertas.marcar_atendimentos`, quantidade fixa de consultas por página); a
  agenda (lista) e a visão geral do painel registram na auditoria **"Leu alertas de saúde"** (tabela
  `prontuario`, com os ids das clientes e a tela, sem nome) quando mostram algum alerta.
- **Retorno devido na agenda:** o atendimento REALIZADO com retorno obrigatório ainda dentro da
  janela e sem retorno marcado mostra o botão **"Agendar retorno (até dd/mm)"**, que abre a mesma
  tela de Retornos a marcar (§11).

---

## 21. Termos e consentimentos (LGPD)

- **Termo versionado** (`versao_termo`): tipo **LGPD** (global) ou **PROCEDIMENTO** (de um
  procedimento ou geral). **1 versão ativa por escopo.** Versão com aceite é **imutável**
  (título, conteúdo, versão, tipo, procedimento) — mudar = publicar nova versão; desativar é
  permitido.
- **Aceite** (`aceite_termo`): cliente, versão, atendimento, IP, navegador, **SHA-256 do texto**,
  data. 1 por cliente/versão; nunca editado nem apagado (trigger).
- Termos que valem para um atendimento: LGPD vigente + PROCEDIMENTO geral + PROCEDIMENTO do
  procedimento. Faltou algum → marcador **"Termo pendente"** no painel/portal e **link do termo**
  por atendimento (Painel > Agendamentos > termo-link): válido **até o fim do atendimento**, para
  aceitar no balcão ou enviar por WhatsApp/e-mail.
- **Política de Privacidade = termo LGPD.** O texto completo da v1.0 (dados, finalidades e bases
  legais, saúde, mensagens, decisões automatizadas, operadores, cookies, segurança, direitos e a
  tabela de prazos) mora em `conteudo/lgpd.py` — fonte única do termo, da página
  `/politica-de-privacidade/` (que mostra o texto da **versão LGPD vigente**, com versão e data, só
  emoldurado pela identificação da clínica e o contato do encarregado da tela Identidade visual) e
  da declaração do "baixar meus dados". A migration 0045 grava a v1.0 sempre que não há termo LGPD
  ativo (banco novo inclusive) e o `seed` também; a 0078 reescreveu a v1.0 sem aceite. **Antes do
  go-live o texto precisa da revisão jurídica da dona** (de preferência com advogado); depois do
  primeiro aceite, mudar = versão nova. Os prazos publicados têm de bater com `settings.RETENCAO_*`
  (check `aranha.W016`).
- **Consentimentos de comunicação** com histórico (tabela `consentimento`, append-only): promoções
  por e-mail, pesquisa por WhatsApp e lembrete por WhatsApp. Cada concessão/revogação é uma linha
  com texto exibido, IP, user-agent, origem e data; **vale o registro mais recente** (quem se
  descadastrou e marca de novo no agendamento volta a receber). **Descadastro** (link) revoga
  marketing e pesquisa e mantém o lembrete D-1. A cliente muda as três em **"Minhas preferências"**
  do portal (o checkbox mostra o estado vigente; marcar concede com o mesmo texto do agendamento,
  desmarcar revoga; origem PORTAL, com IP e navegador; par sem mudança não grava nada). A equipe, na
  ficha do cliente, **revoga** a pedido; registrar autorização pelo painel exige justificativa
  (fica com o autor).
- **Consentimento de dado de saúde** (art. 11 I) fica fora da tabela `consentimento`: a concessão é
  o aceite do termo SAÚDE (na ficha do wizard ou do link) e a **revogação** é
  `cliente.saude_revogada_em` (`services/consentimentos.revogar_saude`) — pelo portal ("Revogar
  consentimento de saúde", origem PORTAL) ou pela equipe a pedido da cliente (ação do Django admin,
  com motivo obrigatório, origem ADMIN), com a trilha `LGPD: consentimento de saude revogado`. A
  revogação vale dali em diante: a ficha já preenchida fica pelo prazo de guarda (§22) e os alertas
  mostram a revogação (§20). Um aceite SAÚDE novo (ficha respondida com a caixa marcada) reativa.
  O wizard **não** volta a exigir a ficha só por causa da revogação: a ficha em dia (12 meses, §3)
  continua valendo.
- **"PARAR" no WhatsApp:** a cliente que responde com a mensagem inteira "parar", "sair", "stop",
  "cancelar mensagens" ou "descadastrar" (sem diferença de acento, caixa ou pontuação final) tem o
  lembrete e a pesquisa **por WhatsApp** revogados (origem WHATSAPP; revogar tão fácil quanto
  consentir — LGPD art. 8º §5), auditado, e recebe a confirmação dizendo como voltar (marcar ao
  agendar ou pedir à recepção). "Vou sair mais cedo" não revoga nada. O e-mail não muda: o lembrete
  D-1 segue por e-mail (§9).
- **Auditoria** (`log_auditoria`): ação sensível com autor (+ nome gravado), tabela, registro,
  detalhes (PII mascarada) e IP — LGPD art. 37.

---

## 22. Retenção e esquecimento

Os prazos de cadastro, carência, saúde, ficha sem atendimento e pacote são os publicados na
política (§21) e ficam **fixos** em `settings.RETENCAO_*`, sem variável de ambiente (mudar = versão
nova do termo). OTP, texto das mensagens e auditoria também estão publicados, mas seguem ajustáveis
por env; axes e links não aparecem no texto. `aranha.W016` acusa prazo publicado diferente do
settings.

| Dado | Regra |
|---|---|
| Código OTP | Apagado após 24 h |
| Texto de notificação | Limpo após 12 meses |
| Log de auditoria (com o IP) | Apagado após 5 anos |
| Logs de login (axes) | Apagados após 90 dias |
| Cliente sem atendimento há 5 anos (`RETENCAO_CLIENTE_INATIVO_DIAS`) | **Pseudonimizada** (job semanal) — **exceto** enquanto houver registro de saúde dentro da janela de 20 anos (atendimento REALIZADO, versão do prontuário ou prontuário com conteúdo editado nesse período) ou pacote a menos de **5 anos** do fim (cancelamento, validade ou, sem validade, a compra — `RETENCAO_PACOTE_APOS_EXPIRAR_DIAS`). Aceite de termo não retém |
| Cliente excluída (soft delete) | Pseudonimizada após **30 dias** de carência (`RETENCAO_CARENCIA_SOFT_DELETE_DIAS`; mesmas exceções) |
| Ficha de anamnese de pedido não realizado (CANCELADO, PENDENTE vencido, convite não respondido ou vazio de horário passado) | Apagada após **90 dias** (`RETENCAO_FICHA_SEM_ATENDIMENTO_DIAS`); a respondida de FALTOU/REAGENDADO fica |
| Lista de espera | Apagada quando a data passa ou quando a pessoa agenda o procedimento (§17) |
| Registro de saúde (prontuário, versões, anotações, fichas de atendimento realizado e a trilha do prontuário) | **Expurgado 20 anos depois do último evento de saúde** da cliente — último REALIZADO, edição ou versão do prontuário, anotação ou ficha (`RETENCAO_ATENDIMENTO_SAUDE_DIAS`, etapa `saude` do `housekeeping`); o banco só aceita esse DELETE com 20 anos ou mais |
| Aceites de termo | Nunca apagados; o do termo LGPD perde IP e navegador na pseudonimização; os de SAÚDE/procedimento, quando não há mais registro de saúde |
| Atendimentos e pacotes | Ficam (contrato), ligados ao titular pseudonimizado |

**Pseudonimização** (o "esquecimento": ação "Pseudonimizar" do Django admin e o job semanal, a
mesma procedure): nome → `[ANONIMIZADO-<pk>]`; contato, documentos, nascimento, endereço, **código
de indicação e quem indicou** zerados; consentimentos vigentes revogados (o histórico fica, sem
IP/user-agent); IP e navegador do aceite LGPD apagados; soft delete; apaga OTPs, a lista de espera
(inclusive a inscrição anônima com o mesmo celular), o texto das notificações e as fichas que não
são de atendimento REALIZADO; tira o depoimento do site e apaga o comentário do NPS; revoga os
links; troca o nome pelo pseudônimo nos textos da auditoria e apaga os detalhes das trilhas LGPD da
titular (pedidos e revogações). Atendimentos, aceites, pacotes e o registro de saúde (até o expurgo)
ficam ligados a esse id — por isso é pseudonimização, não anonimização.

**Direitos pela área da cliente** ("Meus agendamentos", §8):

- **Baixar meus dados** (JSON): cadastro, preferências, consentimentos (estado e histórico),
  atendimentos, NPS, aceites, pacotes, carteira, lista de espera (inclusive a anônima pelo mesmo
  celular), notificações e a declaração do tratamento (finalidades, bases, operadores e prazos
  efetivos — a mesma fonte da política). **Sem a ficha de saúde** (o celular pode ter mudado de
  dono): o arquivo diz que ela sai por e-mail ou na clínica, a pedido. No portal, exige o código
  confirmado há **até 10 minutos** (sessão mais antiga pede código novo); `/lgpd/meus-dados/` faz
  a mesma exportação só com o código. Auditado (`DSAR: exportacao de dados`).
- **Pedir correção ou exclusão** (com descrição livre opcional): fica na auditoria como `LGPD:
  pedido de correcao|exclusao` com o prazo de resposta de **15 dias** (art. 19) — a descrição vai
  só no e-mail — e o pedido vai por e-mail ao encarregado (Identidade visual) > `email_admin` >
  `ADMIN_EMAIL` > e-mail da marca. A equipe confirma a identidade e corrige pela ficha ou
  pseudonimiza pelo Django admin.

---

## 23. Efeitos automáticos por transição

| Evento | Efeito |
|---|---|
| Atendimento criado | Push "Novo agendamento" ao profissional (após o commit); as inscrições da cliente na lista de espera do procedimento (de hoje em diante) saem da fila |
| → REALIZADO | Zera faltas e desbloqueia; debita sessão de pacote (não em retorno); comissão; cashback da indicadora (1º pago); retorno fica devido se o procedimento exige (a recepção marca) |
| → FALTOU | Soma falta (bloqueio online no limite) |
| → CANCELADO | Lança o estorno da comissão e do cashback; avisa lista de espera (se futuro); pela própria cliente com menos de 24 h de horário aprovado, soma 1 falta (cancelamento tardio, §8) |
| → REAGENDADO | Avisa lista de espera (se futuro) |
| → CONFIRMADO | Só log |

Canais de mensagem: **SMS** só para OTP; **WhatsApp** para lembrete D-1, NPS e aviso de vaga
(templates aprovados, com consentimento); **e-mail** para transacionais (inclusive o lembrete D-1
de quem não autorizou o WhatsApp) e marketing com consentimento; **push** para a equipe (novo
agendamento, cliente cancelou/remarcou, sem desfecho). Sem credencial do canal nada é marcado como
enviado. A agenda da profissional vai para o calendário dela só pelo feed ICS (sem Google Agenda).

---

## 24. Valores-chave

| Regra | Valor | Onde |
|---|---|---|
| Antecedência mínima | 2 h (0–720) | `Profissional.min_notice_horas` |
| Antecedência máxima | 60 dias (1–365) | `Profissional.max_advance_dias` |
| Intervalo dos slots | 30 min | `SlotService.INTERVALO` |
| Buffer do procedimento | 0–120 min | `Procedimento.buffer_minutos` |
| Idade mínima (online) | 18 anos | `forms/agendamento.IDADE_MINIMA` |
| Horários futuros ativos para pedir pelo site | até 3 (o 4º é recusado) | `constants.MAX_ATIVOS_ONLINE` |
| Validade da ficha de anamnese respondida | 12 meses | `services/anamnese.VALIDADE_FICHA_MESES` |
| Reagendar pela cliente | ≥ 24 h antes | `constants.JANELA_MINIMA_ALTERACAO` |
| Cancelamento tardio (conta falta) | < 24 h de horário AGENDADO/CONFIRMADO (PENDENTE não) | `constants.JANELA_MINIMA_ALTERACAO`, `services/agenda.cancelamento_tardio` |
| Realizado/Faltou a partir de | início − 30 min | `constants.TOLERANCIA_DESFECHO` |
| Faltas até bloquear online | 3 (ajustável de 1 a 10) | regra operacional `MAX_FALTAS_BLOQUEIO` (`models/configuracao.CHAVES`) > `constants.MAX_FALTAS_ANTES_BLOQUEIO` |
| OTP: validade / tentativas / reenvio | 10 min / 5 / 60 s | env `OTP_TTL_SEGUNDOS`, `OTP_MAX_TENTATIVAS`, `OTP_REENVIO_MINIMO_SEG` |
| SMS por hora: telefone / IP / global | 3 / 10 / 60 | env `SMS_MAX_POR_HORA`, `SMS_MAX_POR_IP_HORA`, `SMS_MAX_GLOBAL_HORA` |
| Verificação OTP no wizard / sessão do portal / código para baixar os dados | 30 min / 1 h / até 10 min | `services/otp`, `services/portal_cliente` (`DURACAO_SESSAO_SEGUNDOS`, `JANELA_DADOS`) |
| Pedido de correção/exclusão: prazo de resposta | 15 dias | `services/portal_cliente.PRAZO_RESPOSTA` |
| Lista de espera pelo site | 20 inscrições/dia por IP | `views/publico/lista_espera.LIMITE_DIARIO_POR_IP` |
| Formulário de contato | 5/min e 20/dia por IP; teto de 50 mensagens/dia no site todo | `views/publico/contato` (`TETO_DIARIO_GLOBAL`) |
| Duração padrão do retorno | 30 min | `Procedimento.duracao_retorno_minutos` |
| Cashback por indicação | R$ 50,00 | `constants.VALOR_CASHBACK_INDICACAO` |
| NPS: janela / validade do link / tentativas | 24 h–7 dias / 7 dias / 3 | `services/nps` (`NPS_JANELA_DIAS`, `NPS_MAX_FALHAS`), `views/publico/nps.py` |
| NPS: intervalo mínimo por cliente | 90 dias (nunca em retorno) | `constants.NPS_INTERVALO_DIAS` |
| Detrator | nota ≤ 6 | `services/nps.NOTA_MAXIMA_DETRATOR` (job `detrator_alerta`) |
| Lembrete D-1 | 08:00 e 18:00 (WhatsApp ou e-mail) | `jobs.JOB_AGENDA`, `services/lembretes` |
| Depoimento | nota ≥ 9 + opt-in + aprovação | `views/publico/site.depoimentos` |
| Link de ficha/pesquisa | 60 dias | `views/publico/formularios` |
| Link do termo | até o fim do atendimento | `services/termos.aceita_assinatura` |
| Retenção: OTP / notificação / auditoria / axes | 24 h / 12 meses / 5 anos / 90 dias | env `RETENCAO_*` |
| Retenção LGPD: cliente inativa / soft delete / ficha sem atendimento / saúde / pacote | 5 anos / 30 dias / 90 dias / 20 anos do último evento / 5 anos do fim | `settings.RETENCAO_*` fixos (sem env; publicados na política — `aranha.W016`) |
| Lote de e-mail de promoção | 25 por clique | `services/marketing.LOTE_PROMOCAO` |
| Sessão da equipe (prod) | 8 h deslizantes | env `SESSION_COOKIE_AGE` |

---

_Última atualização: 2026-10-29 — conferido no código até a migration 0078: pedido online e
cadastros (papéis, lembrete por e-mail, sem desfecho, NPS, promoção, "PARAR", ficha em 3 escopos,
acesso da cliente só pelo portal), LGPD e área da cliente (política como fonte única,
pseudonimização, saúde revogável e expurgo aos 20 anos, cancelamento tardio, lista de espera sem
cadastro) e a agenda do painel (agendamento pela recepção, seletor de status, lote item a item)._
