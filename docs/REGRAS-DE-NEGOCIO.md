# Regras de Negócio — Shiva Zen

> Referência funcional do sistema, com os valores **reais** do código (branch `front-fundacao`).
> Fonte: `models/`, `services/`, `views/`, `constants.py`, `settings` e `Configuracao`. Mudou regra
> ou valor? Atualize aqui, no código e em `docs/_dados_requisitos.json` (requisitos do TCC).
> Arquitetura e pendências: [`ARCHITECTURE.md`](ARCHITECTURE.md). Acesso: [`AUTENTICACAO-E-ACESSO.md`](AUTENTICACAO-E-ACESSO.md).

---

## 1. Atores e papéis

- **ADMIN** — a Jaqueline/gestão: painel completo (inclui o que seria da recepção: agendar,
  aprovar, confirmar, clientes). 2FA obrigatório.
- **PROFISSIONAL** — portal `/profissional/`: própria agenda, aprova/rejeita os próprios
  pedidos, marca Realizado, anota a sessão, lê a ficha só de quem atende (vínculo — §20).
- **RECEPCAO** — papel reservado, **sem login** até existirem telas próprias.
- **Cliente** — nunca é usuário: identifica-se pelo **celular** + código OTP por SMS, sem senha.

---

## 2. Cliente

- **Identidade:** telefone canônico (só dígitos, 10–11, sem DDI/zero de tronco); e-mail e CPF
  opcionais. Únicos entre clientes ativos (UNIQUE parcial no banco). CPF/telefone com máscara são
  normalizados.
- **Idade mínima para agendar online: 18 anos** (data de nascimento obrigatória no wizard).
- **Bloqueio por faltas:** cada FALTOU soma 1 em `faltas_consecutivas`; ao atingir o limite
  (`Configuracao` `MAX_FALTAS_BLOQUEIO`, padrão **3** = `MAX_FALTAS_ANTES_BLOQUEIO`) a cliente
  fica `bloqueado_online` e o wizard recusa ("fale conosco pelo WhatsApp"). Comparecer
  (REALIZADO) zera o contador **e** desbloqueia. O painel ainda agenda cliente bloqueada.
- **Exclusão:** `Cliente.delete()` é soft delete (`deletado_em`); apagar de verdade só com
  `hard=True`. Anonimização: §22.

---

## 3. Agendamento público (wizard)

Fluxo: **procedimento → data/horário (e profissional) → dados + código SMS → confirmar**.

- **Código OTP obrigatório em todo agendamento** (cliente nova ou recorrente), preso ao celular
  informado; o telefone do formulário tem de ser o verificado na sessão (30 min).
- **Horários** vêm do `SlotService` (§5) e o servidor **revalida** no confirmar: procedimento e
  profissional ativos, profissional habilitado, horário futuro, não é feriado bloqueador, slot
  ainda livre para a duração inteira.
- **Antecedência mínima:** `profissional.min_notice_horas` (padrão **2 h**, 0–720).
- **Antecedência máxima:** `profissional.max_advance_dias` (padrão **60 dias**, 1–365). Não há
  teto global.
- **Anti double-booking:** trava de 30 s no cache + `SELECT FOR UPDATE` + EXCLUDE no Postgres;
  perdeu a corrida → "Este horário acabou de ser reservado".
- **Status inicial: PENDENTE** — a clínica aprova (§7). Não há auto-aprovação.
- **Preço gravado** = preço vigente na **data do atendimento** (profissional > base) com a
  promoção que vale nesse dia (§13): `valor_cobrado`, `valor_original` (cheio, se houve
  promoção), `promocao`, `descricao_preco`. É o mesmo valor mostrado no resumo, nos cards e em
  `/promocoes/`.
- **LGPD no wizard:** aceite obrigatório da Política de Privacidade (grava `AceiteTermo` da versão
  LGPD vigente com IP, navegador e SHA-256 do texto); termos de PROCEDIMENTO (do procedimento +
  gerais) aceitos ali mesmo (versão já aceita antes não é pedida de novo); ficha de anamnese
  (dado de saúde) só é gravada com o **consentimento específico do art. 11** marcado, registrado
  na auditoria com o texto e o IP.
- **Comunicação:** caixas de e-mail marketing, lembrete por WhatsApp e pesquisa por WhatsApp
  **nunca vêm marcadas**; marcar concede (data + IP); desmarcar revoga só quando a caixa mostrava
  o estado do cadastro.
- **E-mail:** o digitado por quem provou o celular substitui o do cadastro; e-mail de outro
  cadastro é recusado.
- Ao concluir: e-mail ao profissional com link para a agenda do dia; push ao profissional; sync
  do Google Calendar (se conectado).
- **Sem SMS configurado** o agendamento online fica indisponível e os botões "Agendar" levam ao
  WhatsApp da clínica.

---

## 4. Agendamento interno (recepção, no painel)

- Painel > Agendamentos > **Novo agendamento**: cliente existente (busca) ou nova (nome +
  telefone validado), procedimento, profissional habilitado, horário do **mesmo SlotService**.
- Sem OTP da cliente; nasce **AGENDADO** (a própria clínica confirmou); auditado.
- Valor = preço com promoção na data; a recepção pode informar outro valor combinado (fica
  registrado). Mostra aviso de **ficha de anamnese obrigatória pendente**, quando houver.

---

## 5. Disponibilidade (SlotService)

Slots de **30 min** no dia, considerando: feriado que bloqueia agendamento; exceção do
profissional (FOLGA ou HORÁRIO_DIFERENTE); expediente semanal (todos os turnos do dia);
atendimentos ativos (PENDENTE/AGENDADO/CONFIRMADO) sobrepostos na duração inteira + **buffer**
do procedimento (0–120 min); bloqueios pontuais e recorrentes (RRULE) do profissional **ou
globais** ("Todos os profissionais"); `min_notice_horas`; `max_advance_dias`.

---

## 6. Máquina de estados do atendimento (FSM)

| De | Para (permitido) |
|---|---|
| PENDENTE | AGENDADO, CONFIRMADO, CANCELADO, REAGENDADO |
| AGENDADO | CONFIRMADO, REALIZADO, CANCELADO, FALTOU, REAGENDADO |
| CONFIRMADO | REALIZADO, CANCELADO, FALTOU, REAGENDADO |
| REALIZADO, CANCELADO, FALTOU, REAGENDADO | — (terminais) |

Semântica: **PENDENTE** = pedido aguardando a clínica; **AGENDADO** = horário marcado/aprovado;
**CONFIRMADO** = a cliente disse que vai (link do lembrete) ou a equipe confirmou. Toda transição
trava a linha, valida a tabela acima, grava `LogAuditoria` e dispara os efeitos (§23). Transição
inválida → `Atendimento.TransicaoInvalida` (a tela mostra a mensagem, nunca 500).

**Termo pendente:** marcar REALIZADO com termo de procedimento ainda não aceito é recusado
(painel responde 409 `termo_pendente`); a equipe pode confirmar explicitamente **"Realizado sem
termo aceito"** (ex.: assinado em papel), o que fica na auditoria.

---

## 7. Aprovação e rejeição

- PENDENTE → **aprovar** = AGENDADO + e-mail de confirmação à cliente; **rejeitar** = CANCELADO +
  e-mail de cancelamento. Pelo painel (individual ou em lote) ou pelo portal (só os próprios
  pedidos do profissional). Auditado com IP.
- PENDENTE que passou 24 h do horário sem decisão → CANCELADO pelo job `limpeza_status`
  ("expirado sem aprovação").

---

## 8. Reagendamento e cancelamento

- **Pela cliente** (link `/reagendar/<token>/` ou "Meus agendamentos"): reagendar só com
  **≥ 24 h** de antecedência (`JANELA_MINIMA_REAGENDAMENTO`) e status ativo; cancelar a qualquer
  momento **antes do início**.
- Reagendar: o antigo vira **REAGENDADO** antes de o novo entrar (a constraint não acusa conflito
  consigo mesma); o novo herda preço/promoção, `eh_retorno` e `atendimento_origem` (retorno
  continua retorno, com a duração de retorno) e a **ficha de anamnese** passa para o novo
  atendimento. Status do novo: **AGENDADO** se o original já estava aprovado (AGENDADO/
  CONFIRMADO) **e** a profissional é a mesma; senão volta a **PENDENTE**.
- **Pelo painel:** mudança de status pela FSM; arrastar no calendário move o horário (não move
  REALIZADO/CANCELADO/FALTOU; conflito → 409).
- Cancelamento e reagendamento **liberam a vaga** para a lista de espera (§17).

---

## 9. Lembrete D-1 e confirmação de presença

- Job `lembrete_diario` (08:00): WhatsApp (template `confirmacao_d1`) para atendimentos
  **AGENDADO** de amanhã cuja cliente marcou `consent_whatsapp_confirmacao`; não reenvia a quem já
  recebeu.
- O lembrete traz dois links (`/confirmar/<token>/?acao=confirmar|cancelar`): confirmar
  (AGENDADO → CONFIRMADO) ou cancelar, **uma resposta só**; PENDENTE, terminal ou passado não é
  editável.
- O descadastro de marketing **não** desliga o lembrete (é aviso do serviço).

---

## 10. Faltas (no-show)

- Só a **equipe** marca FALTOU (AGENDADO/CONFIRMADO → FALTOU) — soma falta e pode bloquear o
  online (§2).
- O job `limpeza_status` **não** marca falta: AGENDADO/CONFIRMADO vencidos há 24 h sem desfecho
  vão só para o log, para a equipe decidir (FALTOU é terminal e bloquearia a correção para
  REALIZADO).
- Não há cobrança de taxa de no-show no sistema.

---

## 11. Retorno obrigatório (F-RET)

- Procedimento com `exige_retorno` (janela `retorno_minimo_dias`–`retorno_maximo_dias`,
  `duracao_retorno_minutos`, padrão 30): ao marcar o atendimento **REALIZADO** o sistema cria o
  retorno: `eh_retorno=True`, **valor 0**, status **PENDENTE**, no primeiro horário livre do
  profissional dentro da janela, a partir do **meio** da janela (ignora `max_advance_dias`/aviso
  mínimo, respeita expediente, folgas, feriados e bloqueios).
- Sem horário livre na janela → não cria (recepção agenda). Idempotente: no máximo **1 retorno
  vivo por origem** (UNIQUE no banco).
- Retorno **não gera comissão, não gera cashback, não consome sessão de pacote** e o valor fica
  sempre 0.

---

## 12. Pacotes

- Pacote = itens (procedimento × sessões), preço total e validade em meses. Pacote **já vendido**
  tem **nome e itens congelados** (preço, validade e ativo ainda editáveis); pacote inativo não é
  vendido.
- Venda (ficha da cliente ou Painel > Pacotes): `valor_pago`, expiração = data local da venda +
  validade.
- Sessão REALIZADA de procedimento do pacote **debita 1 sessão** (1 consumo por atendimento),
  escolhendo o pacote que vence primeiro. A validade é conferida na **data da sessão**: sessão
  feita até o último dia debita mesmo que o job já tenha marcado o pacote EXPIRADO; sessão depois
  da validade não debita. Retorno gratuito nunca debita.
- Status: **ATIVO** → **FINALIZADO** (todas as sessões usadas) | **EXPIRADO** (job
  `pacote_expirar`: vencimento < hoje) | **CANCELADO** (painel: exige o **valor devolvido**, de 0
  até o valor pago, e o motivo; auditado; sessões usadas continuam registradas).
- Aviso por e-mail 7 e 1 dia antes do vencimento, se houver saldo ("Sessões realizadas até dd/mm
  contam no seu pacote").
- A ficha da cliente mostra por pacote: sessões usadas/total por procedimento e validade.

---

## 13. Preços e promoções

- **Preço** versionado por `vigente_desde`: vale a vigência mais recente ≤ hoje (fuso local); só
  vigência futura → usa a mais próxima. Preço do profissional > preço base; sem base, o menor
  preço vigente entre profissionais.
- **Promoção:** desconto **0–100% XOR** preço fixo (CHECK no banco), período `data_inicio`–
  `data_fim`, ligada a um procedimento. Promoção **geral** (sem procedimento) só vale como
  **percentual** — preço fixo geral é ignorado pelo agendamento e escondido em `/promocoes/`.
  Só se aplica quando **reduz** o valor; nunca deixa negativo.
- O agendamento grava a promoção **da data do atendimento**; `/promocoes/` e `/especialidades/`
  mostram exatamente o preço que o agendamento gravaria.

---

## 14. Valor cobrado e faturamento

- Painel > Agendamentos > **valor**: registra o valor combinado (guarda o anterior em
  `valor_original`). Recusado para CANCELADO/FALTOU/REAGENDADO, para retorno (sempre 0) e para
  REALIZADO que já tem comissão lançada. Em REALIZADO sem comissão, calcula a comissão e o
  cashback na hora.
- **Faturamento** (financeiro/overview) = atendimentos **avulsos** REALIZADOS com valor > 0 (sem
  retornos, **sem sessões de pacote**) + **vendas de pacote** na data da compra. Venda CANCELADA
  sai inteira do faturamento (o reembolso fica só na auditoria — pendência no ARCHITECTURE).
- Taxa de faltas do mês = FALTOU / (REALIZADO + FALTOU).

---

## 15. Comissão do profissional

- Calculada ao marcar **REALIZADO** (não em retorno). Regra mais específica primeiro
  (`RegraComissao` ativa): (profissional + procedimento) → (profissional) → (procedimento) →
  global; empate = a editada por último.
- Valor = **percentual** (0–100) **ou** valor fixo, sobre a **base**: `valor_cobrado` no avulso;
  na sessão de pacote, **`valor_pago` do pacote ÷ total de sessões do pacote** (rateio).
- Idempotente (1 comissão PENDENTE/PAGA por atendimento). Cancelamento posterior → ESTORNADA.
  Pagamento: Painel > Comissões > pagar (PENDENTE → PAGA, auditado).
- Regras de comissão são cadastradas no Django admin.

---

## 16. Cashback por indicação (F-CSB) e carteira

- Quando a **indicada** tem o **primeiro** atendimento REALIZADO **pago** (valor > 0, não retorno),
  a **indicadora** (`indicado_por`) ganha **R$ 50,00** (`VALOR_CASHBACK_INDICACAO`) na carteira.
  Idempotente; cancelamento do atendimento estorna (`CASHBACK_ESTORNO`), saldo nunca negativo.
- Carteira é ledger **imutável** (trigger): correção = novo movimento.
- **Hoje inerte:** nenhuma tela preenche `indicado_por` e não há fluxo de uso do saldo (pendência
  no ARCHITECTURE).

---

## 17. Lista de espera

- Formulário público: nome, celular, procedimento, data desejada (futura), turno e profissional
  (opcionais), e-mail opcional. 1 pedido ativo por cliente/procedimento/data (UNIQUE parcial).
- O e-mail digitado vai só para a inscrição (`email_contato`), **nunca** para o cadastro; para
  telefone já cadastrado, só se o telefone foi provado por SMS na sessão (senão o aviso usa o
  contato do cadastro). A página de sucesso é igual em todos os casos (sem enumeração).
- **Aviso de vaga** (mecanismo único): quando um atendimento ativo futuro vira CANCELADO ou
  REAGENDADO, todos os pedidos compatíveis (mesmo procedimento, mesmo dia, profissional vazio ou
  igual), em ordem de chegada, recebem e-mail (`email_contato` ou do cadastro) e WhatsApp
  (template `lista_espera_vaga`, só com `consent_whatsapp_confirmacao`) com link para agendar.
  Não há reserva do horário: quem agendar primeiro leva. `notificado` só com entrega real; sem
  entrega o pedido fica para contato manual (Painel > Lista de espera > notificar).
- Pedido com data passada é apagado pelo job semanal de LGPD.

---

## 18. NPS e depoimentos

- Job `nps_24h` (10:00): WhatsApp (template `nps_pos_atendimento`) para atendimento REALIZADO
  que terminou **entre 24 h e 7 dias** atrás, sem avaliação, com `consent_whatsapp_nps` e sem
  opt-out geral; até 3 tentativas com falha.
- Link `/nps/<token>/` vale **7 dias**; 1 avaliação por atendimento; nota **0–10** (CHECK). A nota
  também pode chegar respondendo o WhatsApp (0–10).
- **Detrator** (nota ≤ 6): e-mail de alerta para `email_admin` (Configurações) > `ADMIN_EMAIL` >
  e-mail de contato do Branding (job `detrator_alerta`, 10:30).
- **Depoimento no site** (`/depoimentos/`): só com opt-in da cliente (`autoriza_publicacao`),
  aprovação da equipe (Painel > NPS > publicação), nota ≥ 9 e comentário; cliente excluída ou
  anonimizada nunca aparece. Autor exibido com nome abreviado.

---

## 19. Aniversário e e-mails de promoção

- **Aniversário** (job 09:00): e-mail de **felicitação, sem desconto** (não há mecanismo de
  cupom), só com `consent_email_marketing` e sem opt-out geral; 29/02 comemora em 28/02 em ano
  não bissexto. Não há WhatsApp de aniversário.
- **Promoção por e-mail** (Painel > Promoções > Disparar): só para consentimento de marketing,
  **sem cupom** (a promoção vale sozinha no agendamento), validade anunciada ≤ `data_fim` da
  promoção, HTML sanitizado, link e header de descadastro; envio em lotes de 25 com cursor (sem
  reenvio).

---

## 20. Ficha (prontuário), histórico, anotações, anamnese e alertas

- **Prontuário** 1:1 com a cliente: alergias, contraindicações, histórico de saúde, medicamentos,
  observações + perguntas configuráveis (`Configuracao` `prontuario_perguntas`, JSON, respostas em
  `respostas_extras`).
- **Histórico:** cada edição grava antes a foto do estado anterior em `prontuario_versao`
  (append-only, imutável no banco) com autor; a tela mostra o histórico. Edição concorrente é
  recusada (trava otimista) e um POST parcial não apaga campos.
- **Acesso:** ADMIN lê tudo; profissional só com **vínculo** — atendimento REALIZADO nos últimos
  **180 dias** ou ativo entre ontem e **+60 dias** (escrita sem PENDENTE). Toda leitura, gravação
  e negativa é auditada.
- **Anotação de sessão:** por atendimento, com autor preservado (PROTECT + `autor_nome`).
- **Fichas de anamnese** (`FormularioAnamnese`, schema JSON, tipos ANAMNESE/PESQUISA, escopo
  global/categoria/procedimento/modalidade, `obrigatorio`): respondidas no wizard ou por link
  `/anamnese/<token>/` (60 dias; consentimento art. 11 obrigatório). Formulário com respostas cujo
  schema muda vira nova versão (o antigo é desativado).
- **Alertas de saúde** (texto visível no portal, na agenda do painel e na ficha da cliente): o que
  a equipe registrou no prontuário (alergias, contraindicações, medicamentos, perguntas extras) +
  respostas de risco declaradas pela cliente nas fichas (alergia, gestação, anticoagulante,
  diabetes, etc.). Marcador **"ficha pendente"** quando a ficha obrigatória do procedimento não foi
  respondida.

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
- **Termo LGPD v1.0** (resumo da política de privacidade, `constants.TERMO_LGPD_*`): criado pela
  migration 0045 em banco que já tem clientes e nenhum termo LGPD ativo; instalação nova recebe
  pelo `seed` ou Painel > Termos.
- **Consentimentos granulares** no cadastro (`consent_email_marketing`,
  `consent_whatsapp_confirmacao`, `consent_whatsapp_nps`, cada um com data e IP) + opt-out geral
  (`aceita_comunicacao`). **Descadastro** (link) desliga marketing e pesquisas e mantém o lembrete
  D-1.
- **Auditoria** (`log_auditoria`): ação sensível com autor (+ nome gravado), tabela, registro,
  detalhes (PII mascarada) e IP — LGPD art. 37.

---

## 22. Retenção e esquecimento

| Dado | Regra |
|---|---|
| Código OTP | Apagado após 24 h |
| Texto de notificação | Limpo após 12 meses |
| Log de auditoria | Apagado após 5 anos |
| Logs de login (axes) | Apagados após 90 dias |
| Cliente sem atendimento há 5 anos (`LGPD_RETENCAO_CLIENTE_DIAS`) | Anonimizada (job semanal) — **exceto** com prontuário com conteúdo, pacote comprado ou atendimento REALIZADO nos últimos 20 anos |
| Cliente excluída (soft delete) | Anonimizada após 30 dias (mesmas exceções) |
| Ficha de anamnese de pedido não realizado (CANCELADO, PENDENTE vencido, convite não respondido) | Apagada após **90 dias** |
| Lista de espera com data passada | Apagada |
| Atendimento realizado, prontuário e histórico, aceites | Retidos (registro de saúde, ~20 anos); nunca apagados |

**Esquecimento** (ação no Django admin e job): nome → `[ANONIMIZADO-<pk>]`, dados de contato e
documentos zerados, consentimentos revogados, soft delete; apaga OTPs, lista de espera, texto das
notificações e as fichas que não são de atendimento REALIZADO; tira o depoimento do site e apaga o
comentário do NPS; troca o nome do titular pelo pseudônimo nos textos da auditoria. Atendimentos,
aceites, prontuário e pacotes ficam, ligados a um titular não identificável.

---

## 23. Efeitos automáticos por transição

| Evento | Efeito |
|---|---|
| Atendimento criado | Push ao profissional + sync Google Calendar (após o commit) |
| → REALIZADO | Zera faltas e desbloqueia; debita sessão de pacote (não em retorno); comissão; cashback da indicadora (1º pago); cria retorno se o procedimento exige |
| → FALTOU | Soma falta (bloqueio online no limite) |
| → CANCELADO | Estorna comissão e cashback; avisa lista de espera (se futuro) |
| → REAGENDADO | Avisa lista de espera (se futuro) |
| → CONFIRMADO | Só log |

Canais de mensagem: **SMS** só para OTP; **WhatsApp** para lembrete D-1, NPS e aviso de vaga
(templates aprovados, com consentimento); **e-mail** para transacionais e marketing com
consentimento; **push** para a equipe. Sem credencial do canal nada é marcado como enviado.

---

## 24. Valores-chave

| Regra | Valor | Onde |
|---|---|---|
| Antecedência mínima | 2 h (0–720) | `Profissional.min_notice_horas` |
| Antecedência máxima | 60 dias (1–365) | `Profissional.max_advance_dias` |
| Intervalo dos slots | 30 min | `SlotService.INTERVALO` |
| Buffer do procedimento | 0–120 min | `Procedimento.buffer_minutos` |
| Idade mínima (online) | 18 anos | `views/booking_public` |
| Reagendar pela cliente | ≥ 24 h antes | `constants.JANELA_MINIMA_REAGENDAMENTO` |
| Faltas até bloquear online | 3 | `Configuracao MAX_FALTAS_BLOQUEIO` > `constants.MAX_FALTAS_ANTES_BLOQUEIO` |
| OTP: validade / tentativas / reenvio | 10 min / 5 / 60 s | env `OTP_TTL_SEGUNDOS`, `OTP_MAX_TENTATIVAS`, `OTP_REENVIO_MINIMO_SEG` |
| SMS por hora: telefone / IP / global | 3 / 10 / 60 | env `SMS_MAX_POR_HORA`, `SMS_MAX_POR_IP_HORA`, `SMS_MAX_GLOBAL_HORA` |
| Verificação OTP no wizard / sessão do portal | 30 min / 1 h | `services/otp`, `views/booking_otp` |
| Duração padrão do retorno | 30 min | `Procedimento.duracao_retorno_minutos` |
| Cashback por indicação | R$ 50,00 | `constants.VALOR_CASHBACK_INDICACAO` |
| NPS: janela / validade do link / tentativas | 24 h–7 dias / 7 dias / 3 | `tasks.py`, `views/admin_management` |
| Detrator | nota ≤ 6 | `tasks.job_alerta_detrator_nps` |
| Depoimento | nota ≥ 9 + opt-in + aprovação | `views/public.depoimentos` |
| Link de ficha/pesquisa | 60 dias | `views/anamnese_publica` |
| Link do termo | até o fim do atendimento | `services/termos.aceita_assinatura` |
| Retenção: OTP / notificação / auditoria / axes | 24 h / 12 meses / 5 anos / 90 dias | env `RETENCAO_*` |
| Retenção: cliente inativa / soft delete / ficha sem atendimento / saúde | 5 anos / 30 dias / 90 dias / 20 anos | `LgpdService` (+ env `LGPD_RETENCAO_CLIENTE_DIAS`) |
| Lote de e-mail de promoção | 25 | `views/admin_promotions.LOTE_PROMOCAO` |
| Sessão da equipe (prod) | 8 h deslizantes | env `SESSION_COOKIE_AGE` |

---

_Última atualização: 2026-09-23 — reescrito a partir do código após a auditoria pré-produção
(removidos valores que não existem mais: antecedência máxima global de 90 dias, reserva de 30 min
da lista de espera, cupom de aniversário, taxa de no-show, saldo mínimo de cashback)._
