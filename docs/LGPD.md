# LGPD — registro das operações, impacto e incidentes

> Rascunho técnico a partir do que o sistema faz hoje (código do branch `dev`). **A revisão jurídica e
> as decisões marcadas com ☐ são da dona da clínica.** Mudou um tratamento, um operador ou um prazo?
> Atualize aqui e no texto da política, que é o termo LGPD
> ([`conteudo/lgpd.py`](../aranha_estetica/conteudo/lgpd.py) antes do go-live; depois, só com versão
> nova em Painel > Termos). Revisão: uma vez por ano, no mínimo.
> Referência técnica: [`PROJECT.md`](PROJECT.md) §9 (bases legais, consentimentos, direitos, retenção).

> ⚠️ **Revisão jurídica obrigatória antes do go-live.** A Política de Privacidade v1.0 —
> o texto, as finalidades e bases legais (`TRATAMENTOS`), os operadores (`OPERADORES`) e os prazos
> (`PRAZOS_RETENCAO`) de [`aranha_estetica/conteudo/lgpd.py`](../aranha_estetica/conteudo/lgpd.py) —
> foi escrita a partir do que o sistema faz, não por advogado. **A dona da clínica (de preferência
> com um advogado) precisa revisá-la antes do primeiro cliente real**: o mesmo texto é a página
> `/politica-de-privacidade/`, o termo que a cliente aceita no agendamento (o SHA-256 dele vai na
> prova do aceite) e a declaração do "baixar meus dados". Corrija no arquivo **antes do primeiro
> deploy** (a `0045` grava esse texto no banco novo); depois do primeiro aceite a versão fica
> imutável e qualquer mudança vira versão nova. Prazo alterado tem de bater com
> `settings.RETENCAO_*` (check `aranha.W016`). ☐ revisado por ______ em __/__/____.

## 1. Controlador e encarregado

| | |
|---|---|
| Controlador | A clínica — razão social e CNPJ em Painel > Identidade visual (aparecem na moldura da política) ☐ preencher |
| Encarregado (art. 41) | Nome e e-mail em Painel > Identidade visual ☐ nomear (pode ser a própria dona); os pedidos de correção/exclusão do site vão para o e-mail dele |
| Canal do titular | Área **"Meus agendamentos"** do site, com código por SMS: preferências de comunicação, revogação do consentimento de saúde, pedido de correção ou exclusão (prazo de 15 dias na trilha e e-mail à clínica) e "baixar meus dados". **"Meus dados"** (`/lgpd/meus-dados/`): só a exportação. Nenhum dos dois entrega a ficha de saúde (o celular pode ter mudado de dono): ela sai só a pedido, pela equipe. Mais o e-mail/WhatsApp da clínica; resposta em até 15 dias |

Agente de pequeno porte pode dispensar o encarregado (Res. CD/ANPD nº 2/2022), mas o tratamento de
dado de saúde pesa contra a dispensa e nomear não custa nada: a recomendação é nomear.

## 2. Registro das operações de tratamento (art. 37)

| # | Operação | Dados | Titulares | Base legal | Onde fica | Retenção |
|---|---|---|---|---|---|---|
| 1 | Agendar, confirmar, remarcar, cancelar e realizar o atendimento, com os e-mails de confirmação, cancelamento e lembrete D-1 (aviso do serviço contratado, enviado quando há e-mail) | nome, celular; **nascimento obrigatório** no agendamento online (confirmar 18 anos ou mais e avaliar a segurança do procedimento); e-mail opcional; CPF, CEP e endereço só para nota fiscal e contrato de pacote (RG, profissão e foto **não são coletados**); procedimento, profissional, horário, faltas e cancelamentos | clientes | execução de contrato e procedimentos preliminares (art. 7º V); nota fiscal: obrigação legal (art. 7º II) | `cliente`, `atendimento`, `notificacao` | cadastro: **pseudonimizado** após 5 anos sem atendimento (salvo registro de saúde dentro dos 20 anos ou pacote a menos de 5 anos do fim — linhas 3 e 12); pedido de exclusão: 30 dias de carência; texto das mensagens: 12 meses |
| 2 | Confirmar a identidade no agendamento, no "Meus agendamentos" e no "Meus dados" | celular, código OTP (só o HMAC), IP | clientes | execução de contrato (art. 7º V) e prevenção a fraudes (art. 7º IX) | `codigo_otp` | 24 h |
| 3 | Ficha de avaliação (anamnese), prontuário, anotações dos atendimentos | **dado de saúde** (alergias, contraindicações, histórico, medicamentos, gestação, respostas) | clientes | **só consentimento específico e destacado (art. 11 I)**, com prova em `aceite_termo` (termo SAÚDE), **revogável** no "Meus agendamentos" ou a pedido (Django admin, com motivo) — a revogação vale dali em diante e grava `cliente.saude_revogada_em`; o registro já feito fica pelo prazo de guarda (art. 7º VI, art. 11 II "d", art. 16) | `prontuario`, `prontuario_versao`, `anotacao_sessao`, `resposta_formulario` — **cifrados** | **expurgo 20 anos depois do último evento de saúde** (atendimento realizado, edição/versão do prontuário, anotação ou ficha; etapa `saude` do `housekeeping`); ficha de pedido não realizado: 90 dias |
| 4 | Provas de aceite de termos e de consentimento (inclusive as revogações pelo "PARAR" no WhatsApp, origem WHATSAPP, e na área da cliente, origem PORTAL) | versão do termo, data, IP, user-agent, canal, origem | clientes | obrigação legal e exercício de direitos (art. 7º II e VI) | `aceite_termo`, `consentimento` | o aceite nunca é apagado: o do termo LGPD perde IP e user-agent na pseudonimização; os de SAÚDE e de procedimento, quando não há mais registro de saúde (pseudonimização sem saúde ou expurgo aos 20 anos). Consentimento: histórico append-only, IP e user-agent apagados na pseudonimização |
| 5 | Lembrete D-1 e pesquisa de satisfação **por WhatsApp**, aviso de vaga por WhatsApp | nome, celular, procedimento e horário | clientes com consentimento | consentimento (art. 7º I), revogável ("PARAR", "Meus agendamentos", descadastro para a pesquisa) | `consentimento`, `notificacao` | até revogar; sem ele o lembrete D-1 vai por e-mail (linha 1) |
| 6 | Pesquisa de satisfação (nota e comentário) e depoimento no site | nota, comentário, autorização de publicação | clientes | legítimo interesse (nota e comentário; responder é opcional); consentimento (publicação) | `avaliacao_nps` | comentário apagado na pseudonimização; a nota fica para estatística |
| 7 | Promoções e aniversário por e-mail | nome, e-mail, nascimento | clientes com consentimento | consentimento (art. 7º I) | `consentimento` + envio | até revogar (link de descadastro ou "Meus agendamentos") |
| 8 | Lista de espera e contato | lista: nome e celular — a inscrição pública é **anônima e não cria cadastro**; só se liga à cliente (e só aceita e-mail) com o celular verificado por SMS na sessão. Contato: nome, e-mail, telefone opcional e mensagem | visitantes/clientes | procedimentos preliminares a pedido do titular (art. 7º V) | `lista_espera`; o contato não fica no banco (vai ao e-mail da clínica) | lista: até a data desejada passar ou até a pessoa agendar o procedimento; o contato: a caixa de e-mail da clínica |
| 9 | Contas da equipe, 2FA, trilha de auditoria, contato do encarregado | nome, e-mail, papel, ações, IP; encarregado e e-mail do administrador em `configuracao` | equipe | execução de contrato / obrigação legal (art. 41 §1) | `usuario`, dispositivos OTP, `log_auditoria`, `historico_alteracao`, `configuracao` | trilha: 5 anos (mínimo do banco: 180 dias), com o IP; trilha do prontuário: sai com o expurgo de saúde aos 20 anos |
| 10 | Segurança do site | IP, data e hora, tentativas de login (e-mail mascarado no log) | visitantes/equipe | legítimo interesse (art. 7º IX) | django-axes, logs da aplicação (token de link, e-mail, CPF e telefone redigidos) | axes: 90 dias; logs da aplicação: a retenção do Railway (☐ conferir o prazo do plano) |
| 11 | Comissões, financeiro e programa de indicação | valores por atendimento e profissional; código de indicação, quem indicou e o crédito gerado | profissionais e clientes | execução de contrato | `movimento_comissao`, `carteira`, `movimento_carteira`; `cliente.codigo_indicacao`/`cliente_indicador_id` | livro-razão append-only; o código e quem indicou saem na pseudonimização. O Excel gerencial do mês tem nomes de cliente: a exportação vai para a `log_auditoria` com o mês (`{'mes': ...}`). O recibo do repasse e o "Minhas comissões" da profissional não mostram nome de cliente |
| 12 | Pacotes de sessões (contrato) | pacote comprado (nome e valores congelados na venda), sessões feitas | clientes | execução de contrato | `compra_pacote`, `compra_pacote_item`, `consumo_sessao` | a cliente não é pseudonimizada pela retenção **até 5 anos depois de o pacote expirar ou ser cancelado** (sem validade: da compra) |
| 13 | Links das mensagens (confirmar presença, avaliar, assinar termo, responder a ficha, descadastrar) e o feed ICS da profissional | só o hash do token + vínculo à mensagem, ficha, cliente ou profissional | clientes | execução de contrato | `link_acesso` | revogados na pseudonimização; vencido ou revogado: apagado após 90 dias |

A seção 11 da política publica os prazos de `conteudo/lgpd.PRAZOS_RETENCAO`. Os de cadastro,
carência, saúde, ficha sem atendimento e pacote ficam **fixos** em `settings.RETENCAO_*` — sem
variável de ambiente: mudar exige versão nova do termo LGPD. Os de OTP, texto das mensagens e
auditoria também estão publicados, mas seguem ajustáveis por env (`RETENCAO_OTP_HORAS`,
`RETENCAO_NOTIFICACAO_DIAS`, `RETENCAO_LOG_AUDITORIA_DIAS`); links e axes não aparecem no texto. O
check `aranha.W016` acusa qualquer prazo publicado cujo valor no settings diverge do texto da versão
vigente.

O inventário coluna a coluna (o que a pseudonimização limpa, apaga, purga por idade ou retém por
obrigação legal) fica em `aranha_estetica/inventario_lgpd.py`; o teste `test_inventario_lgpd`
reprova coluna nova com dado pessoal sem tratamento e tratamento que a procedure não faz. Tabelas
sem dado pessoal (catálogo e `execucao_job`, a trava diária dos jobs de mensagem) são classe
SISTEMA no inventário e ficam fora deste registro; a `configuracao` é classe EQUIPE (guarda o
contato do encarregado e o e-mail do administrador, retidos — linha 9).

**Pseudonimização, não anonimização.** O "esquecimento" (`sp_anonimizar_cliente`: ação
"Pseudonimizar" do Django admin e a retenção semanal) troca o nome por `[ANONIMIZADO-<id>]`, apaga
contato, documentos, nascimento, endereço, o código de indicação e quem indicou, revoga os
consentimentos (o histórico fica sem IP/user-agent), tira IP/user-agent do aceite LGPD, apaga OTPs,
a lista de espera (inclusive a inscrição anônima com o mesmo celular), o texto das mensagens, as
fichas de pedido não realizado, o comentário e o depoimento do NPS, revoga os links e pseudonimiza o
nome e os detalhes LGPD nas trilhas. O registro de saúde e os contratos **continuam ligados a esse
id** pelo prazo legal — por isso é pseudonimização (art. 13 §4), não anonimização.

**Revogação pelo WhatsApp.** A cliente que responde "PARAR" (ou "sair", "stop", "cancelar
mensagens", "descadastrar" — a mensagem inteira) tem o lembrete e a pesquisa por WhatsApp
revogados na hora (linha nova em `consentimento`, origem WHATSAPP; art. 8º §5: revogar tão fácil
quanto consentir). A confirmação é uma resposta em texto livre, que a Meta só aceita dentro da
janela de 24 h aberta pela própria mensagem da cliente, e diz como voltar a receber.

**Decisão automatizada (art. 20).** Ao chegar ao número de faltas seguidas da regra operacional
`MAX_FALTAS_BLOQUEIO` (padrão 3), o agendamento online da cliente é pausado sem intervenção humana.
Conta como falta também o **cancelamento tardio** feito pela própria cliente (portal ou link do
lembrete) com menos de 24 h de um horário já aprovado (AGENDADO/CONFIRMADO; pedido PENDENTE não
conta) — o portal e a página do link avisam antes de confirmar. O wizard diz o motivo e o caminho de
revisão (WhatsApp da clínica) e a política de privacidade (§6, Decisões automatizadas) informa o
direito de pedir a revisão por uma pessoa e que a recepção continua agendando; comparecer
(REALIZADO) desfaz a pausa. A mesma seção declara a outra decisão automática: o pedido de quem já
foi atendida (sem falta em aberto, sem bloqueio, menos de 2 horários futuros) nasce aprovado.

**Cookies, fontes e mapa.** O site só usa cookies essenciais (sessão, segurança/CSRF, tema e o
registro do aviso). O aviso é informativo: "Entendi" grava `cookie_consent=essenciais` no próprio
navegador e nada é registrado no servidor — não há cookie de análise nem de terceiro a consentir.
As fontes são servidas pelo próprio site e o mapa da página de contato é uma ilustração local: o
Google Maps só abre se a pessoa clicar em "Abrir no Google Maps" (a CSP não libera frame do Google).

## 3. Operadores e transferência internacional

| Operador | Para quê | Dados que recebe | Fora do Brasil? | Contrato / DPA |
|---|---|---|---|---|
| Railway | hospedagem do site e do Postgres | todos (o dado de saúde chega cifrado ao banco) | sim (EUA/Europa, conforme a região) | ☐ conferir DPA e região |
| Zenvia | SMS com o código | celular, texto do SMS | não | ☐ contrato |
| Meta (WhatsApp Business) | lembrete D-1 com confirmação, NPS, aviso de vaga e as respostas da cliente (nota, "PARAR") | celular, nome, dados do horário | sim | ☐ termos de processamento da Meta |
| Provedor de e-mail (Resend, Brevo… o que for configurado) | e-mails transacionais e de marketing | e-mail, nome, conteúdo da mensagem | depende do provedor | ☐ DPA |
| Cloudflare (Turnstile) | verificação contra robôs nos formulários do site (pedido do código no wizard, no portal e no "Meus dados", contato e lista de espera) | IP, dados técnicos do navegador | sim | ☐ DPA |
| Sentry (se ligado) | erros do site | eventos sem contato nem saúde (filtrados em `utils/pii.opcoes_sentry`) | sim | ☐ DPA, região de dados |
| Apple/Microsoft/Google (feed ICS do calendário) | agenda da profissional — o único caminho da agenda para fora do sistema (sem integração por OAuth) | horário, procedimento e o nome público da cliente (primeiro nome + inicial) | sim | assinatura feita pela profissional |
| Provedor do backup (bucket) | cópia do banco | dump **cifrado com age** (sem a chave privada não abre) | depende | ☐ escolher e registrar |
| jsDelivr (CDN) | bibliotecas do painel interno (FullCalendar, Chart.js), com SRI | IP e dados técnicos do navegador **da equipe**; nenhum dado de cliente | sim | — |

O Google não é operador do site: não há mapa embutido nem fonte do Google (o "Abrir no Google Maps"
da página de contato é um link que a pessoa escolhe abrir). A lista acima é a mesma da seção 7 da
política (`conteudo/lgpd.OPERADORES`) — mudou aqui, muda lá.

Transferência internacional (art. 33): para cada operador fora do Brasil ☐ confirmar que o contrato
traz as cláusulas-padrão da ANPD (Res. CD/ANPD nº 19/2024) ou outra hipótese do art. 33.

## 4. Relatório de impacto (RIPD, art. 38) — dado de saúde

**Tratamento.** A clínica registra alergias, contraindicações, histórico de saúde, medicamentos em
uso, respostas da ficha de avaliação e anotações de cada atendimento, para decidir se o
procedimento estético é seguro e para manter o registro do que foi feito.

**Base e revogação.** O tratamento se apoia só no consentimento específico e destacado (art. 11
I): a caixa do wizard e da ficha pública grava o aceite do termo SAÚDE com IP, navegador e SHA-256
do texto. A cliente revoga no "Meus agendamentos" (origem PORTAL) ou pedindo à clínica (ação
"Registrar revogação do consentimento de saúde" do Django admin, com motivo obrigatório, origem
ADMIN): grava `cliente.saude_revogada_em` e a trilha `LGPD: consentimento de saúde revogado`. Dali
em diante os alertas de saúde (agenda e visão geral do painel, ficha da cliente, prontuários,
portal da profissional) mostram no topo "Consentimento de saúde revogado em dd/mm/aaaa"; o
registro já feito continua guardado pelo prazo (art. 7º VI, art. 11 II "d", art. 16). Um aceite
SAÚDE novo — uma ficha respondida com o consentimento marcado — reativa.
☐ Hoje a revogação **não** invalida a ficha ainda em dia (12 meses): o wizard só pede ficha nova se
ela estiver pendente ou vencida, embora a política, o portal e o próprio alerta digam que "um novo
atendimento pede uma nova ficha" (pendência no [`ARCHITECTURE.md`](ARCHITECTURE.md) §6.2).

**Necessidade e proporcionalidade.** Só as perguntas configuradas pela clínica (formulário
PRONTUARIO/ANAMNESE) e só da cliente atendida. A ficha de quem não chegou a ser atendida sai em 90
dias. O registro de saúde (prontuário, versões, anotações, fichas e a trilha do prontuário) é
**expurgado 20 anos depois do último evento de saúde** da cliente — último atendimento REALIZADO,
edição ou versão do prontuário, anotação ou ficha (`RETENCAO_ATENDIMENTO_SAUDE_DIAS`, etapa `saude`
do `sp_purgar_dados_expirados`, job `housekeeping`). Os triggers só aceitam esse DELETE com a flag
local `app.expurgo_autorizado` **e** registro com 20 anos ou mais: um prazo menor no settings faz a
etapa falhar, nunca apagar antes.

**Riscos e medidas.**

| Risco | Medida no sistema |
|---|---|
| Vazamento do banco ou de um backup | cifragem em repouso dos campos de saúde (Fernet, chave fora do banco — `CHAVES_CIFRAGEM`); backup cifrado com age e chave privada offline |
| Acesso indevido pela equipe | 2FA obrigatório para ADMIN e PROFISSIONAL; profissional só vê prontuário de cliente com vínculo de atendimento; cada leitura do prontuário vai para a `log_auditoria`, e a agenda (lista) e a visão geral do painel registram **"Leu alertas de saúde"** (tabela `prontuario`, detalhes `{clientes, tela}`, sem nome) quando a tela mostra alerta. ☐ A agenda do portal da profissional, a lista de prontuários e a ficha da cliente também mostram alertas e ainda não registram essa leitura |
| Alteração ou apagamento da prova | versões do prontuário e anotações append-only (trigger; DELETE só no expurgo aos 20 anos); aceite só perde IP/user-agent (minimização); `log_auditoria` e `historico_alteracao` append-only, expurgo só por idade (0059, 0077) |
| Envio a terceiros | Sentry sem saúde nem contato; e-mails e WhatsApp sem dado de saúde |
| Identificação por SIM-swap no "Meus agendamentos"/"Meus dados" | o export do site **não leva a ficha de saúde** (só cadastro, agendamentos, consentimentos, aceites, pacotes etc. e o aviso de como pedir a ficha); no portal, baixar exige código confirmado há até 10 min. ☐ Risco aceito no resto do cadastro (1 fator). A ficha completa sai só pela equipe a pedido (`LgpdService.exportar_dados_cliente`, que ainda não tem tela) |
| Perda da chave de cifragem | ☐ cópia offline da chave com a dona; rotação documentada (`rotacionar_chave_cifragem`) |

**Risco residual.** Quem controla a hospedagem (conta do Railway) ou uma conta de ADMIN tem a chave
em uso. Mitigação: 2FA nessas contas, poucos membros, revisão de acessos (§6).

☐ Aprovação da dona (data e assinatura).

## 5. Incidente de segurança (art. 48; Res. CD/ANPD nº 15/2024)

1. **Detectar e registrar** a hora em que a clínica soube (o prazo conta daí).
2. **Conter**: revogar sessões (rotacionar `DJANGO_SECRET_KEY`), resetar 2FA (`setup_2fa --force`),
   rotacionar `OTP_HMAC_KEY`, tokens de provedores e, se a chave de cifragem vazou, `CHAVES_CIFRAGEM`
   (`rotacionar_chave_cifragem`); revogar links públicos (a pseudonimização e o painel revogam).
3. **Avaliar** pela `log_auditoria` e `historico_alteracao`: quais titulares, quais dados, se o dado de
   saúde estava cifrado (dado ininteligível reduz o risco — art. 48 §3).
4. **Comunicar** à ANPD e aos titulares afetados, quando houver risco ou dano relevante, em até
   **3 dias úteis** do conhecimento: o que aconteceu, dados e titulares envolvidos, medidas tomadas,
   riscos e o contato do encarregado.
5. **Registrar** todo incidente (mesmo sem comunicação) e guardar o registro por **5 anos**: data,
   descrição, dados, titulares, avaliação de risco, medidas, comunicação feita ou o motivo de não ter
   feito. ☐ definir onde fica o registro (fora do próprio sistema).

Modelo de aviso ao titular: *"Em [data] identificamos [o que aconteceu]. Seus dados de [categorias]
podem ter sido acessados. Já [medidas]. Recomendamos [o que a pessoa pode fazer]. Dúvidas: [contato
do encarregado]."*

## 6. Rotina

- ☐ **Antes do go-live (uma vez):** revisão jurídica da Política de Privacidade em
  `conteudo/lgpd.py` (aviso no topo deste documento), antes do primeiro deploy de produção.
- ☐ Anual: revisar este documento, a política, os operadores e os contratos.
- ☐ Trimestral: revisar quem tem acesso (painel, Railway, GitHub, DNS, provedores) e remover quem saiu.
- ☐ Mensal: testar a restauração de um backup (`scripts/backup/testar_restauracao.sh`).
