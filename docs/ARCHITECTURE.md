# Arquitetura & Checkpoint — Shiva Zen

> **Documento vivo / check-point.** Decisões (ADR-lite), estado de cada frente e as
> pendências **reais** (conferidas no código). Ao concluir algo, marque `[x]` na seção 6 com
> commit/data. Referência técnica completa em [`PROJECT.md`](PROJECT.md); banco em
> [`specs/remodelagem-banco-v2.md`](specs/remodelagem-banco-v2.md); front em
> [`specs/fundacao-front-design.md`](specs/fundacao-front-design.md); regras em
> [`REGRAS-DE-NEGOCIO.md`](REGRAS-DE-NEGOCIO.md).

---

## 1. Visão geral do produto

Sistema de gestão da clínica de estética Jaqueline Aranha (biomédica esteta). Backend
**Django 5.2 + PostgreSQL 18** (Railway), server-render, servindo:

```
            ┌──────────────── Django (backend + templates) ────────────────┐
            │                                                               │
   SUPERFÍCIE A — SITE PÚBLICO                 SUPERFÍCIE B — EQUIPE (PWA)
   vitrine + agendamento online               painel ADMIN (app-shell) +
   + "Meus agendamentos" + LGPD                portal do PROFISSIONAL
   (a cliente vê; identidade = celular)       (login + 2FA)
```

Um único design system (tokens Tailwind v4) → duas "peles": site editorial e painel denso.

---

## 2. Banco de dados — CONCLUÍDO (remodelagem v2.1 + auditoria pré-produção + padrão SQL)

Migrations `0027`–`0078` (não há produção: o go-live cria o banco novo e aplica `0001`→`0078` de
uma vez; o banco de desenvolvimento antigo está na `0026` — runbook em
[`PROJECT.md` §14](PROJECT.md#14-go-live--operação-runbook)). Da `0048` em diante:
padrão SQL, reestruturações e lógica no banco em [`specs/padrao-sql.md`](specs/padrao-sql.md)
(estado atual do banco: [`PROJECT.md` §5](PROJECT.md#5-banco-de-dados)).

- **50 → 34 tabelas de domínio** (33 na v2.1 + `prontuario_versao` na 0046). Alvo 33 após a
  Fase 7 (agenda 3→2).
- Naming PT-BR singular; RBAC → `usuario.papel`; workflow engine removido; EAV do prontuário →
  JSONB; OTP único hasheado.
- Invariantes no banco: EXCLUDE anti double-booking, UNIQUE parciais (telefone/e-mail/CPF
  ativos, termo vigente, preço por vigência, espera ativa, **1 retorno por origem**), CHECKs
  (NPS 0–10, promoção, carteira ≥ 0, formato de telefone/CPF, comissão 0–100%, JSON `NOT VALID`),
  triggers de imutabilidade (ledger da carteira, **prova de aceite, termo aceito, histórico do
  prontuário**), collation ICU `pt_br`, `COMMENT ON TABLE`.
- Upgrade de produção seguro: `migrate_atomico` (transação única + `lock_timeout`), migrations
  0034–0038 reescritas in-place antes de qualquer PG aplicá-las (dedup sem perda, datas de aceite
  preservadas, `%` no plpgsql), abortos explícitos com a lista de ids (0035, 0043).

| Fase | Conteúdo | Status |
|---|---|---|
| 1a–1d | Cortes (mortas, OTP, RBAC, workflow) | ✅ 0027–0030 |
| 2a–2c | Renames coluna/tabela + `cliente.nome` | ✅ 0031–0033 |
| 3 | Constraints + telefone canônico + dedup | ✅ 0034 |
| 4 | ExclusionConstraint booking | ✅ 0035 |
| 5 | EAV → JSONB prontuário | ✅ 0036 |
| 6 | Aceite unificado + trigger + collation + comments | ✅ 0037/0038 |
| — | Auditoria SWE (validators/CHECK) | ✅ 0039 |
| — | Auditoria pré-produção (depoimentos, PROTECT, contas demo, retorno único, prova de aceite, termo LGPD, histórico do prontuário) | ✅ 0040–0046 |
| — | Termo SAÚDE, reembolso, promoção geral | ✅ 0047 |
| 7 / (a) | Agenda 4→2 (`agenda_horario` + `agenda_excecao`) — substituiu a "Fase 7" | ✅ 0048 |
| (b)–(f) | Tokens em hash (`link_acesso`), consentimentos com histórico, questionário único, pacote congelado, comissão em livro-razão | ✅ 0049–0053 |
| N1–N2 | Padrão de nomes (tabelas, colunas, constraints, índices, PK/FK com `ON DELETE` real) | ✅ 0054–0055 |
| L1–L3 | Lógica no banco: trilha `historico_alteracao` + triggers, funções de leitura e views com paridade, procedures como fonte única | ✅ 0056–0058 |
| — | Trilhas append-only, cifragem do dado de saúde, fila de e-mail na `notificacao` | ✅ 0059–0061 |
| — | Revisão funcional: habilitação obrigatória, crédito de comissão ativo único, competência mensal, snapshot e regras de comissão, origens da carteira (0062–0067); disparo de promoção, origem WHATSAPP, `execucao_job` (0068–0070); remoções — Google Agenda, RECEPCAO e link legado, modalidade e notificações sem produtor, RG/profissão/foto (0071–0074) | ✅ 0062–0074 |
| — | LGPD e área da cliente: origem PORTAL e consentimento de saúde revogável (0075), lista de espera sem cadastro (0076), retenção de saúde (expurgo aos 20 anos) e de pacote (5 anos) com pseudonimização completa (0077), política v1.0 completa como termo LGPD (0078) | ✅ 0075–0078 |

---

## 3. Front-end — CONCLUÍDO (Ondas 1–4)

Stack em uso: **Vite 6 + Tailwind v4 + Alpine.js (`@alpinejs/csp`) + django-cotton**. HTMX foi
avaliado e **removido** (nenhum `hx-*` no projeto e injetava `<style>` sem nonce). Bootstrap,
jQuery, AOS, Swiper, FontAwesome/Bootstrap Icons: fora. Ícones SVG inline.

- Site público 100% em `estrutura/base_v2.html`; painel 100% em `painel/base_v2.html`
  (app-shell com drawer Alpine no cromo escuro quente da marca, manifest próprio, busca instantânea
  com Ctrl+K); portal da profissional em `profissional/base.html`, no mesmo design system.
- **Design system das telas da equipe** (painel e portal, D50): `static/src/css/componentes.css`
  em `@layer components` + componentes cotton `<c-botao>`, `<c-campo>`, `<c-card>`, `<c-selo>`,
  `<c-nota>`, `<c-kpi>`, `<c-vazio>`, `<c-icone>`, `<c-menu-acoes>`, `<c-dialogo>`, `<c-tabela>` e
  `<c-abas>` (atributo `class` mesclado via `c-vars`), com o comportamento no `painel.js` (vanilla).
  O site tem o chrome (`cabecalho`, `rodape`, `mensagens`, `cookie_consent`), `<c-cta-agendar>` e
  `<c-servico.*>`, e o CSS das páginas no `site.css`. `<c-badge>`, `<c-toast>` e `<c-modal>`
  saíram — status usa `partials/_status_badge.html`, mensagens usam `cotton/chrome/mensagens.html`.
- CSP estrita: sem `unsafe-inline` em `script-src`/`style-src`, sem handlers inline (sem
  `script-src-attr`) e sem CDN (FullCalendar e Chart.js vêm do npm no bundle). Resta
  `style-src-attr 'unsafe-inline'` só para o que não tem bundle nem nonce: o widget
  `agendamento/embed.html`, as páginas de erro autossuficientes (400/403_csrf/500), a página
  offline do `sw.js` e os e-mails (onde a CSP não se aplica) — painel, portal e site estão sem `style=""`.
- Páginas de erro próprias (400/403/403_csrf/404/429/500), página pt-BR do bloqueio do axes.
- Service worker v10 (`/duvidas/` entrou na lista): cacheia só páginas públicas e estáticos. Rodapé com coluna "Para você".
  O portal da profissional tem casca própria (`profissional/base.html`: sem rodapé, CTA, WhatsApp,
  SEO nem aviso de cookies); o menu do painel é uma lista só (`painel_tags.MENU_PAINEL`).
- E-mails: 10 templates sobre `email/base_email.html` (email-safe, dark mode), sem cupom/desconto
  prometido sem mecanismo.

---

## 4. Ondas (histórico)

| Onda | Entrega | Status |
|---|---|---|
| 0 | Pré-requisitos (cores, fotos, stack) | ✅ cor da marca confirmada (#C9A84C); fotos reais em `static/assets/clinica/` |
| 1 | Fundação (tokens, casca, componentes, tema sem FOUC) | ✅ (T9 parcial — seção 6) |
| 2 | Site público (home, wizard, serviços, marca, auto-serviço, formulários, legal, auth) | ✅ |
| 3 | Painel/PWA (30 telas, 6 arquétipos) | ✅ |
| 4 | E-mails (consistência + dark mode) | ✅ |

O plano do wizard (`plans/onda2-wizard.md`) foi concluído e removido: o contrato preservado
(nomes de campos, ids `#step-1..3`, endpoints `api_dias_disponiveis`,
`api_horarios_disponiveis`, `solicitar_otp_agendamento`, `verificar_otp_agendamento`,
`confirmar_agendamento`) segue válido; `static/src/js/wizard.js` é uma entrada do Vite,
cards e dias são `<button>` (teclado) e o estado sobrevive a recusa do servidor
(`sessionStorage` + reidratação).

---

## 5. Decisões fixadas (ADR-lite)

| # | Decisão | Razão |
|---|---|---|
| D1 | Backend Django server-render mantido | Sólido, SEO grátis, 1 mantenedor |
| D2 | Não vira SPA; **Alpine CSP, sem HTMX** | Nenhuma tela precisava de parcial por HTMX; CSP estrita |
| D3 | Tailwind v4 (não Bootstrap) | 1 config de tokens para site + painel |
| D4 | Direção visual "Clínico-premium" (dourado #C9A84C, serif + sans) | Confiança; afunila para o agendamento |
| D5 | Painel como PWA app-shell | Uso como app instalado |
| D6 | Fase 7 do banco em PR dedicada | Mexe no cálculo de slots |
| D7 | **Todo agendamento público exige OTP do celular**; a identidade é o telefone, e-mail nunca é identidade | Anti-sequestro de cadastro; sem enumeração |
| D8 | Agendamento público nasce **PENDENTE** (clínica aprova), exceto cliente de confiança (já atendida, sem falta, < 2 horários futuros) que nasce AGENDADO; interno nasce AGENDADO. Pelo site, no máximo 3 horários futuros ativos por cliente (`MAX_ATIVOS_ONLINE`; a recepção marca além disso). Toda decisão sobre o pedido passa pelo `AgendamentoService` (botão ou seletor) | Tira o gargalo da aprovação de quem já é cliente sem abrir a agenda a desconhecidos (o PENDENTE trava o horário: sem teto, um celular verificado reservaria a semana); a cliente sempre recebe a resposta |
| D9 | **2FA obrigatório para ADMIN e PROFISSIONAL** em prod (válvulas `ADMIN_2FA_OBRIGATORIO=false` / `PROFISSIONAL_2FA_OBRIGATORIO=false`, avisos W008/W012); códigos de backup gerados no painel | Painel e portal leem dado de saúde (LGPD art. 11/46) |
| D10 | Rotas do django-two-factor (`/account/*`) e ReDoc não publicadas | 2ª tela de login burlava o 2FA; ReDoc exige `unsafe-inline` |
| D11 | ~~RECEPCAO sem login até existirem telas próprias~~ — substituída pela D27 (o papel saiu) | Papel sem superfície própria = acesso indevido ao painel |
| D12 | Prova de aceite imutável (SHA-256 do texto + trigger) e termo aceito imutável | Evidência legal (LGPD art. 8) |
| D13 | Histórico append-only do prontuário (`prontuario_versao`) | CFM 1.638/2002 — edição não apaga o anterior |
| D14 | O sistema nunca marca FALTOU sozinho: atendimento sem desfecho vira um push diário à profissional (job `sem_desfecho`, 08:00), não falta | Falta é terminal e bloqueia online; só a equipe decide — e a pendência precisa chegar a quem decide, não só ao log |
| D15 | Aniversário = felicitação sem desconto; promoção sem cupom, anunciada pela **data do atendimento** (o período em que o preço vale), disparo único em lotes com o cursor na própria promoção (reenvio só confirmado) | Nada é prometido sem mecanismo que aplique; clique duplo ou queda no meio nunca repetem e-mail |
| D16 | Deploy por Dockerfile + `migrate_atomico` no pre-deploy; **rollback = restaurar dump** | Upgrade 0026→0046 tudo-ou-nada; reverses não recuperam dados |
| D17 | **Sem Celery** (saiu em 2026-10-27): jobs são funções com horário na `jobs.JOB_AGENDA` (fonte única), rodadas por um serviço Cron do Railway (`rodar_jobs_devidos` a cada 15 min), pelo cron HTTP (`X-Cron-Token`) ou `rodar_job`; job de mensagem sem marca por destinatário roda 1×/dia (`execucao_job`, 409 na 2ª); e-mail transacional é fila na própria `notificacao` (0061, reenvio pelo job `emails`) | Custo; um serviço de jobs no lugar de um por horário; nada roda "em segundo plano" sem rastro nem manda duas vezes |
| D18 | `/anamnese/<token>/` e `/pesquisa/<token>/` **com produtor**: "Link da ficha" na agenda do painel (cliente agendada pela recepção) e convite da pesquisa no fim do NPS | Ficha obrigatória chega a quem não passou pelo wizard; a pesquisa reaproveita o link do NPS (sem segunda mensagem) |
| D19 | Site só pt-BR (sem LocaleMiddleware); sessão da equipe 8h deslizantes | Sem traduções; 30 min fixos deslogavam no meio do atendimento |
| D20 | Faturamento = avulsos + venda de pacote; sessão de pacote não soma; comissão da sessão de pacote = `valor_pago / total de sessões` (congelado na venda, `compra_pacote_item.valor_unitario`); ticket médio e LTV da ficha pela mesma regra (`services/financeiro`) | Evita contar a receita duas vezes e inflar comissão |
| D21 | Dado de saúde **cifrado na aplicação** (Fernet, `CHAVES_CIFRAGEM`, subchave por finalidade; 0060), sem busca no banco por conteúdo (o token OAuth do Google, também cifrado, saiu com a D26) | Dump/backup vazado não expõe a ficha (LGPD art. 46/48 §3); pgcrypto deixaria a chave no SQL de toda consulta |
| D22 | Trilhas append-only no banco: `log_auditoria` imutável e expurgo só com 180+ dias (0059) | Mesmo usuário do banco p/ app e migrations: SQL injetado não apaga a prova (art. 37) |
| D23 | **Postgres é a fonte única da escrita** de esquecimento, retenção LGPD, limpeza e expiração de pacotes (procedures 0058, sem fallback Python); funções de leitura seguem como espelho testado do Python | Decisão do dono (2026-10-27): uma regra, um lugar; custa exigir Postgres (Docker) para rodar esses testes e rotinas localmente |
| D24 | Ambiente de testes publicado = **perfil `staging`** (`prod.py` + travas: SMS só em log com a prévia, e-mail/WhatsApp só p/ `DESTINATARIOS_TESTE`, noindex, faixa, `seed --demo`); `DJANGO_ENV=dev` no Railway derruba o boot e produção sem Postgres é Error (`aranha.E002`) | Testar o fluxo inteiro (wizard + OTP) na internet sem mandar mensagem a cliente real nem expor DEBUG |
| D25 | Financeiro por **competência mensal** (`services/financeiro.competencia`): pacote = vendas pela data da venda − reembolsos pela data do cancelamento (`compra_pacote.cancelado_em`); comissão pela data do atendimento; rateio da venda **ponderado pelo preço de tabela**; CRÉDITO de comissão congela base e percentual; regra usada é imutável (trigger) | Mês fechado não muda; o valor de cada sessão reflete o procedimento; comissão explicável depois |
| D26 | **Google Agenda removido** (0071): a agenda da profissional sai só pelo feed ICS assinado (`link_acesso` FEED_ICS, nome público da cliente); sem OAuth, sem importação de eventos como bloqueio | O feed cobre Google, Apple e Outlook sem guardar token de terceiro nem manter sincronização em dois sentidos; um operador e um segredo a menos |
| D27 | Papéis da equipe só **ADMIN e PROFISSIONAL** (0072; substitui a D11): a dona faz a recepção pelo painel | YAGNI: o papel RECEPCAO não tinha tela nem login e só existia como risco de acesso indevido |
| D28 | **Todo atendimento é presencial** (0073): sem modalidade nem link de videoconferência; a ficha vale por 3 escopos (global, categoria, procedimento) | Nenhum serviço online no catálogo; o escopo MODALIDADE só repetia o GLOBAL |
| D29 | A cliente tem **um acesso só**: o portal "Meus agendamentos" por OTP (reagendar/cancelar por id + sessão). O link legado de reagendar (GERENCIAR_AGENDAMENTO) e o alvo atendimento do `link_acesso` saíram (0072) | Os tokens legados vinham do banco da 0026, descartado no go-live; dois caminhos eram duas superfícies de ataque para a mesma ação |
| D30 | **Minimização do cadastro** (0074): RG, profissão e foto da cliente não são coletados; CPF, CEP e endereço ficam pela finalidade (nota fiscal, contrato de pacote) | LGPD art. 6º III (necessidade): dado sem finalidade é risco sem benefício e entra no esquecimento à toa |
| D31 | **Cookies só essenciais**: o aviso é informativo e "Entendi" grava só no navegador; nada é registrado no servidor (saiu `/lgpd/aceitar-cookies/`) | Sem cookie de análise ou de terceiro não há consentimento a provar; guardar a resposta era dado pessoal sem finalidade |
| D32 | **Runner único** `manage.py test` (pytest, pytest-django e conftest saíram; teste em função solta reprova) | Dois runners coletavam conjuntos diferentes: teste só do pytest ficava fora do CI |
| D33 | **Áreas de views independentes** (publico, painel, profissional, conta, infra não se importam — import-linter); o que mais de uma área renderiza igual mora em `views/comum.py`, a regra em `services/` | Mudar uma tela não quebra outra área; o acoplamento entre áreas fica explícito num lugar só |
| D34 | **Configuração só nos settings**: a env é lida e validada em `clinica/settings` e o código lê `settings.X` na chamada (ruff `TID251`); o `.env.example` é conferido contra o código; segredo fraco num perfil publicado é erro (E003) | Valor inválido derruba o boot com o nome da variável em vez de virar 500 no meio de um fluxo; `override_settings` vale nos testes; o exemplo não sobe segredo para o Railway |
| D35 | **`utils/telefone` é a fonte única do telefone**: forma canônica sem DDI (a do cadastro, do OTP e das buscas), E.164 só para telefone BR válido, máscara de log | O telefone é a identidade da cliente: cópias da normalização em OTP, lista de espera, formulário e webhook divergiam, e número estrangeiro virava destino de SMS |
| D36 | **Contrato JSON único** (`utils/respostas`): `{"ok": true, ...}` ou `{"ok": false, "codigo", "mensagem"}`; o front decide pelo `codigo` | Cada view respondia num formato; o texto da mensagem não é contrato |
| D37 | **Logs redigidos e imagem endurecida**: filtro `RedigirLog` (token de link, DETAIL do Postgres, e-mail, CPF, telefone) no log da aplicação; imagem sem `curl`, usuário sem shell, código do root só leitura, testes e scripts fora | O `django.request` levava `/confirmar/<token>/` para o log do Railway em todo 4xx/5xx; um RCE não reescreve código nem baixa ferramenta |
| D38 | **Pedido online como caso de uso**: `services/agendamento_online.solicitar(PedidoAgendamento)` tem a regra inteira (termo LGPD, horário, fichas válidas por 12 meses, termos, cadastro da titular do celular, teto, reserva, e-mails e provas); `forms/agendamento.AgendamentoPublicoForm` lê o POST e confere o OTP; a view só liga os dois. Recusa = `RegraDeNegocio` com código (`sem_termo_lgpd`, `ficha_invalida`, `consent_saude_ausente`, `termo_pendente`, `email_de_outro_cadastro`, `horario_em_confirmacao`, `bloqueado_online`, `limite_online`, `horario_ocupado`) | A view tinha complexidade ciclomática 71 (hoje 9) e a regra só era testável por HTTP; outra porta (painel, API) reusa o service |
| D39 | **Regras operacionais em registro tipado** (`models/configuracao.CHAVES`: `email_admin`, `MAX_FALTAS_BLOQUEIO` 1–10 e, com a D51, `anos_experiencia` 1–60 opcional), lidas por `Configuracao.ler`; a tela grava só essas chaves (sem chave livre) e a marca tem a tela Identidade visual | A chave-valor livre listava estado interno, aceitava chave inventada e um valor inválido caía no padrão em silêncio |
| D40 | **Desligamento revoga o feed ICS**: desativar a profissional, o login dela ou trocar o vínculo do login revoga o link na hora (auditado); reativar exige link novo | O calendário de quem saiu continuaria recebendo nome de cliente (o feed é um operador fora da clínica) |
| D41 | **Política de Privacidade como fonte única** (`conteudo/lgpd.py`): o mesmo texto é o termo LGPD aceito no wizard (0045/0078 e `seed`), a página `/politica-de-privacidade/` (renderiza a versão LGPD **vigente**) e a declaração do "baixar meus dados" (finalidades, bases, operadores, prazos). Os prazos LGPD ficam fixos em `settings.RETENCAO_*`, sem env, e o check `aranha.W016` compara com o texto da versão vigente. Revisão jurídica da dona antes do go-live | A página e o termo eram dois textos que divergiam, e o prazo que a procedure aplicava podia não ser o que a cliente leu; a prova do aceite (SHA-256) agora cobre exatamente o que a página mostra |
| D42 | **Retenção por janela e pseudonimização completa** (0077): o registro de saúde é expurgado 20 anos depois do último evento de saúde (etapa `saude` do `sp_purgar_dados_expirados`), o pacote retém a cliente 5 anos depois de expirar/cancelar, e a pseudonimização tira também o código de indicação, quem indicou, IP/user-agent do aceite LGPD e a inscrição anônima da lista de espera. Os triggers só aceitam o expurgo com a flag local **e** 20 anos de idade | Quem tinha ficha ou pacote ficava identificada para sempre (contra os arts. 15/16 e a política); o mínimo legal do registro de saúde fica garantido pelo banco mesmo com um prazo errado no settings. É pseudonimização (art. 13 §4), não anonimização: a saúde e os contratos seguem ligados ao id |
| D43 | **Consentimento de saúde revogável** (art. 11 I, art. 8 §5): a concessão é o aceite SAÚDE e a revogação é `cliente.saude_revogada_em` (portal ou Django admin com motivo), fora da tabela `consentimento`; os alertas mostram a revogação e um aceite SAÚDE novo reativa | Com base só no consentimento, revogar tem de ser possível e tão fácil quanto dar; a ficha já feita fica pelo prazo de guarda (exercício de direitos) |
| D44 | **Direitos pela área da cliente, sem a ficha de saúde no site**: "Meus agendamentos" concentra preferências (origem PORTAL), revogação de saúde, pedido de correção/exclusão (trilha com prazo de 15 dias + e-mail ao encarregado) e "baixar meus dados"; o export self-service (portal e `/lgpd/meus-dados/`) não leva saúde e, no portal, exige código de até 10 min; a sessão do portal abre no fim do wizard sem novo SMS | O celular é um fator só (SIM-swap): o dado sensível não sai pelo site. Um lugar para tudo o que a titular pede, sem e-mail solto nem segundo login |
| D45 | **Cancelamento tardio conta falta**: a cliente sempre pode cancelar online antes do início (a vaga volta para a lista), mas com menos de 24 h de um horário aprovado soma 1 falta para o bloqueio online, sem virar FALTOU (`agenda.cancelar_pela_cliente`, portal e link); pedido PENDENTE não conta | Cancelar em cima da hora tem o mesmo efeito de faltar para a agenda; o aviso antes de confirmar e a política (art. 20) deixam a regra explícita |
| D46 | **Lista de espera sem cadastro e formulários públicos com anti-abuso**: a inscrição é anônima (nome + celular) e só se liga à cliente — e só aceita e-mail — com o celular verificado por OTP; o aviso não tira da fila e agendar encerra a inscrição. Lista e contato com captcha, honeypot e limite por IP (contato: teto global diário); o contato não tem caixa de marketing e o mapa não carrega nada do Google | Um formulário sem login não pode criar nem alterar cadastro, nem prender um e-mail ao telefone de outra pessoa; a cota de e-mail é compartilhada com o reset de senha; opt-in de marketing só onde há prova |
| D47 | **Agenda do painel por casos de uso**: `agenda.agendar_pela_recepcao` (cliente nova na mesma transação, preço do dia ou combinado, AGENDADO, e-mail de confirmação), `atendimento.marcar_realizado` (a regra do termo igual no painel e no portal, `TermoPendente`), `atendimento.registrar_valor_cobrado` e `AgendamentoService.decidir_em_lote` (item a item, cada um na sua transação); a entrada é Django Form (`forms/agenda`) e a view só traduz HTTP | A view do novo agendamento tinha 191 linhas (hoje 43; complexidade do mccabe 27 → 7) e o lote numa transação só desfazia tudo por um item decidido em outra tela |
| D48 | **Seletor de status só com o que há para decidir**: sem PENDENTE (decide nos botões, pelo `AgendamentoService`) nem status final; Realizado/Faltou só a partir do início (a FSM recusa com `desfecho_antes_do_inicio`, 409). O JS da agenda e do calendário são entradas do Vite (`painel-status`, `painel-calendario`, `lib/requisicao.js`) no contrato `utils/respostas`; as ações da agenda (status, valor, desfazer realização, aprovar/recusar/lote, mover, exceções e bloqueios) usam `@escrita_painel` (POST + 30/min por usuário) | Um clique na agenda da semana seguinte gerava comissão, debitava pacote e contava falta; o mesmo JS existia em duas cópias inline |
| D49 | **Leitura de alerta de saúde auditada**: toda tela que mostra alerta (agenda e visão geral do painel, ficha da cliente, lista de prontuários, agenda do portal da profissional) grava "Leu alertas de saúde" (tabela `prontuario`, `{clientes, tela}`, sem nome) | O RIPD promete que cada leitura de dado de saúde vai para a trilha (art. 37); o alerta na lista também é leitura |
| D50 | **Design system das telas da equipe** (painel e portal da profissional): classes de componente em `static/src/css/componentes.css` na camada `@layer components` (o utilitário do template sempre vence, sem `!important`), emitidas por componentes cotton (`<c-botao>`, `<c-campo>`, `<c-card>`, `<c-selo>`, `<c-nota>`, `<c-kpi>`, `<c-vazio>`, `<c-icone>`, `<c-menu-acoes>`, `<c-dialogo>`, `<c-tabela>`, `<c-abas>`); abas WAI-ARIA, menu "⋯", modais, Ctrl+K e foco da tabela que rola num `painel.js` **vanilla** por delegação, não em Alpine; lib de terceiro só pelo npm no bundle do Vite (FullCalendar 6.1.21, Chart.js 4.5.1 — nenhum CDN na CSP); o ARIA de grade do FullCalendar achatado para `presentation`; caixa de frase, texto base 15px, rótulo 13px e nada abaixo de 12px, sem `style=""`; toda rota pública, da equipe e da profissional varrida no CI (1366px claro e 390px escuro) com axe-core serious/critical | As telas repetiam classe utilitária longa e ~160 `style=""`, com cor fixa de tema claro que o escuro não alcançava, e cada uma resolvia modal e menu do seu jeito: o componente é um ponto de mudança só, com o padrão acessível embutido. Sem JS as abas são âncoras para seções visíveis e o "⋯" é `<details>` nativo; com Alpine cada instância pediria `x-data` e nada funcionaria antes do JS. O jsDelivr serviria qualquer pacote npm com o nonce da página. O FullCalendar 6 desenha um `role="grid"` de layout sem linhas (axe `aria-required-children`/`aria-required-parent`): como tabela de layout, a informação segue no texto. A varredura pega erro de JS/CSP, rolagem lateral no celular e regressão de acessibilidade que só apareceriam olhando tela por tela |
| D51 | **Prova social só com dado real** (`services/prova_social`): nota = média do NPS ÷ 2 (escala de 5), só com **10+ respostas**, e as estrelas desenhadas em meias, sem arredondar 4,7 para 5; clientes atendidas = clientes distintas com atendimento REALIZADO, só a partir de **50** e arredondadas **para baixo** ("127" vira "120+", "1.234" vira "1.200+"); tratamentos = procedimentos agendáveis online (no total e por categoria); anos de experiência = regra operacional `anos_experiencia` (1–60, opcional; em branco não aparece). O que não atinge o mínimo some do template. Uma conta a cada 10 min (cache), zerada quando `Configuracao` muda. A "decisão do dono: manter" o selo fixo "5.0/5 · 32 avaliações" (e os "5+ / 500+ / 20+") foi revertida a pedido do dono ao aplicar a revisão do front | Número sem lastro no site de uma clínica de estética é promessa que a cliente não tem como conferir (e o selo fixo nunca mudava); com 3 notas 10 a média "5,0" diz mais sobre o acaso que sobre a clínica, e o "+" nunca promete mais do que houve. O cache evita 4 agregações a cada visita da home |

---

## 6. Progresso (checkpoint)

### 6.1 Concluído na auditoria pré-produção (2026-09-23, `b5fd681..HEAD`, ~33 commits)

Três rodadas de auditoria multi-agente (achados em `audit_r1/r2/final`) + verificação de
fidelidade dos commits → ~460 correções em quatro ondas, com testes junto (1.144 testes; suíte
verde em SQLite e Postgres).

- **Deploy/infra:** Dockerfile multi-stage com Vite, `requirements.lock`, `migrate_atomico`,
  `bootstrap_admin`, healthcheck `/healthz/` que reprova sem o manifest, settings de prod
  coerentes (DEBUG forçado, e-mail dummy sem backend, SMS fail-closed, Celery eager sem retry
  síncrono, IP do cliente por `X-Real-IP`), system checks `aranha.W001–W009`, log do gunicorn
  sem tokens, CI com Postgres 18 bloqueante + build do front + `docker build`. Procfile removido.
- **Banco:** 0029 e 0034–0038/0045 corrigidas in-place; 0040–0047 (depoimentos com opt-in,
  PROTECT no histórico, contas demo desativadas, retorno único + CHECK jsonb, prova de aceite,
  termos LGPD v1.0 e SAUDE v1.0 sempre presentes, autoria na auditoria, `prontuario_versao`,
  triggers de imutabilidade, `valor_reembolsado` do pacote, promoção geral só percentual).
- **Auth:** 2FA obrigatório do ADMIN avaliado a cada request, `/account/*` fora, QR em SVG,
  "trocar de aparelho" exige código, códigos de backup aceitos no desafio, login do PROFISSIONAL
  em `/admin-login/` → `/profissional/`, logout só via POST + `Clear-Site-Data`, `no-store` nas
  áreas privadas, `same-origin` nas rotas com token (sem Referer para outro domínio), rotação do link ICS.
- **Booking:** OTP preso ao telefone e exigido de todo agendamento (só celular), cota de SMS
  checada antes de gerar código, slots revalidados no servidor, preço com promoção da data,
  aceite LGPD + consentimento art. 11 + termos do procedimento no wizard, opt-ins nunca
  pré-marcados (desmarcar revoga), reagendamento preserva retorno e move a ficha, e-mail do
  cadastro substituído só com telefone provado.
- **Painel:** agendamento interno pela recepção, valor cobrado, link do termo por atendimento,
  "Termo pendente" + override auditado "Realizado sem termo aceito", pacotes na ficha do cliente
  (saldo, validade, cancelamento com reembolso auditado), venda congela nome/itens/valor por
  sessão (catálogo editável, 0052), comissão em livro-razão com saldo negativo visível (0053), bloqueio global "Todos os profissionais", alertas de saúde visíveis (portal,
  agendamentos, ficha), prontuário com acesso restrito por vínculo + trilha de leitura +
  histórico de versões, moderação de depoimentos, Branding sem upload de logo (Configuracao >
  env > padrão), Regras operacionais = registro tipado de 2 chaves (`email_admin`,
  `MAX_FALTAS_BLOQUEIO` 1–10), sem chave livre (D39; as perguntas do prontuário viraram o
  formulário PRONTUARIO na 0051).
- **Serviços/LGPD:** canais falham fechado (nada marcado como enviado sem entrega), lista de
  espera em mecanismo único (signal → service, envio no `on_commit`, `notificado` só com
  entrega), retenção efetiva (fichas de pedido não realizado 90 dias, lista de espera vencida,
  exceções de saúde/pacote), esquecimento completo (fichas, NPS, pseudônimo na auditoria),
  descadastro mantém o D-1, sem WhatsApp de aniversário, webhooks robustos.
- **Site:** conteúdo honesto (sem promessas), FAQ revisado, depoimentos só com consentimento,
  vitrine = preço do agendamento, CTA "Agendar" vira WhatsApp sem SMS, SEO/PWA, aviso LGPD na
  lista de espera (e-mail digitado só na inscrição; cliente existente só com OTP).
- **Removidos:** HTMX, `/servicos/produtos/`, `<c-badge>/<c-toast>`, `NotificacaoService`,
  `EmailService`, `WhatsAppService`, `utils/cache`, `utils/structured_logging`, `exceptions.py`
  (hierarquia `DomainError`), `PacoteService`, eventos órfãos, `job_notificar_fila_espera`,
  `/ajax/verificar-telefone/`, `/ajax/buscar-procedimentos/`, `/ajax/buscar-horarios/`,
  `/painel/cancelar-agendamento/`, `/api/schema/redoc/`, `seed_jaqueline` (→ `seed`), constantes
  sem uso (`DESCONTO_ANIVERSARIO_PERCENTUAL`, `TTL_RESERVA_LISTA_ESPERA_MINUTOS`...).

### 6.2 Pendências reais (verificadas no código em 2026-09-23)

**Operação / go-live (fora do código)**
- [ ] **Revisão jurídica da Política de Privacidade** (`aranha_estetica/conteudo/lgpd.py`: texto,
      `TRATAMENTOS`, `OPERADORES`, `PRAZOS_RETENCAO`) pela dona, de preferência com um advogado,
      **antes do primeiro deploy de produção** — a `0045` grava esse texto no banco novo e, depois do
      1º aceite, mudar exige versão nova (runbook §14 a; [`LGPD.md`](LGPD.md))
- [ ] Backup configurado antes do 1º cliente real (o go-live cria o banco novo; ensaio
      0026→0079 só se o banco antigo for reaproveitado — runbook §14 a.1)
- [ ] Env no Railway (`DATABASE_URL` por referência, ADMIN_*, SITE_URL, ZENVIA_*, EMAIL_BACKEND +
      provedor, WHATSAPP_*, TURNSTILE_*, SENTRY_DSN, CLINIC_EMAIL; CRON_TOKEN só com o cron HTTP)
      e remover `STATIC_ROOT` e as legadas (`GOOGLE_OAUTH_*`, `AWS_*`)
- [ ] Provedor de e-mail: `django-anymail` já instalado — escolher o provedor (ex.: Resend) e
      definir `EMAIL_BACKEND` + chave + `DEFAULT_FROM_EMAIL` de domínio verificado (check W011)
- [x] Merge `front-fundacao` → `main` (fast-forward) — 2026-09-23
- [ ] Serviço Cron dos jobs (`railway.cron.json` + `rodar_jobs_devidos`, runbook §13.2) e o
      monitor (`CRON_MONITOR_URL`)
- [ ] Pós-deploy: 2FA do ADMIN, apagar `ADMIN_PASSWORD`, `axes_reset`, Branding real (razão
      social, CNPJ e encarregado aparecem na moldura da política), conferir que o termo LGPD v1.0
      ativo é o texto revisado, mesclar duplicatas logadas pela 0034, `VALIDATE CONSTRAINT` dos
      CHECKs `NOT VALID` da 0043

**Produto / código**
- [x] ~~**Fase 7** — agenda 3→2~~ feita como fase (a) do padrão SQL: agenda 4→2 (grade,
      exceção, bloqueio e feriado → `agenda_horario` + `agenda_excecao`, migration 0048,
      [`specs/padrao-sql.md`](specs/padrao-sql.md) §4.a). Fase (b) feita: tokens de link em hash
      (`link_acesso`, migration 0049, §4.b). Fases (c) e (d) feitas: consentimentos com histórico
      (`consentimento`, 0050, §4.c) e questionário único (formulário PRONTUARIO, 0051, §4.d).
      Fases (e) e (f) feitas: pacote congelado na venda (`compra_pacote_item`, 0052, §4.e) e
      comissão como livro-razão (`movimento_comissao` CREDITO/PAGAMENTO/ESTORNO, 0053, §4.f).
      Próximas: nomes (N1–N2) e lógica no banco (L1–L3), mesma spec, §6.
- [x] ~~**T9 — calibração de marca**~~ cor #C9A84C e fontes Playfair Display + Lato servidas
      pelo próprio site (@fontsource no bundle do Vite, 2026-10-27; sem Google Fonts)
- [x] ~~**Pacote cancelado some do faturamento:**~~ resolvido (0047 `valor_reembolsado`; receita = pago − reembolsado).
- [x] ~~**Janela do vínculo profissional ↔ prontuário**~~ resolvido (janela = max(60, `max_advance_dias`)).
- [x] ~~**Link da ficha de anamnese** no agendamento interno~~ resolvido (D18: botão "Link da ficha" na agenda).
- [x] ~~**Purga LGPD × histórico do prontuário:**~~ resolvido (`candidatos_purga` retém quem tem `prontuario_versao`).
- [x] ~~**Cashback de indicação**~~ resolvido: crédito visível na ficha e no portal (código + saldo) e
      usado no valor do atendimento, devolvido se o atendimento não acontece (2026-10-28).
- [x] ~~`Promocao.clean()` e promoção geral de preço fixo~~ resolvido (clean + CHECK na 0047).
- [x] ~~`migrate_atomico` sem aviso de falha~~ resolvido (escreve FALHOU + migration corrente).
- [x] ~~`tests/test_pg_ddl.py` sem os triggers da 0046~~ resolvido.
- [x] ~~Django admin: `ProntuarioVersao` somente leitura~~ resolvido (com trilha de leitura).
- [x] ~~Consentimento de saúde só em log~~ resolvido (termo SAUDE v1.0 + `AceiteTermo` com prova).
- [ ] Hardening opcional do 2FA: settings de teste com `ADMIN_2FA_OBRIGATORIO=False` e tirar a
      dependência do marcador `usuario_id` no `Enforce2FAMiddleware`
- [x] ~~Baixa: `isdigit()` aceita "²"~~ resolvido (`utils/parse.id_int` e nota NPS por regex).
- [x] ~~Baixa: `ListaEspera.token_reserva`/`expira_em` e o fallback `LEMBRETE/EMAIL` de
      `services/termos.Q_NOTIF_TERMO`~~ saíram (o lembrete D-1 por e-mail é LEMBRETE/EMAIL de verdade).
- [x] ~~Baixa: docstring de `utils/dois_fatores.verificar_token` descrevia o `--force` antigo~~
      corrigida (2026-10-29: o `--force` apaga todos os devices)
- [ ] Baixa: `painel/termo_link.html` oferece `mailto:` para o e-mail do cadastro (não verificado)
- [x] ~~Revogar o consentimento de saúde não invalidava a ficha em dia~~ resolvido: `valida_desde`
      considera `saude_revogada_em` (wizard e marcador do painel) — o próximo atendimento pede ficha
      e aceite SAÚDE novos.
- [x] ~~Export completo com a ficha de saúde sem tela~~ resolvido: ação "Exportar dados completos da
      titular (LGPD, com saúde)" no Django admin (uma por vez, JSON, na trilha).
- [x] ~~"Leu alertas de saúde" só no painel~~ resolvido: também na agenda do portal da profissional,
      na lista de prontuários e na ficha da cliente.
- [x] ~~`style-src-attr 'unsafe-inline'`: migrar ~160 atributos `style=""` (fora dos e-mails) para
      classes~~ resolvido (2026-10-29, D50): painel, portal e site sem `style=""` (guarda
      `test_a11y_front.SitePublicoSemEstiloInlineTests` no site). A diretiva fica por design: o
      widget `embed.html` (sem bundle), as páginas de erro 400/403_csrf/500 (renderizadas sem
      contexto, sem nonce), a página offline do `sw.js` e os e-mails ainda usam `style=""`.
- [x] ~~Libs do painel no jsDelivr (FullCalendar 6.1.11, Chart.js 4.4.0, com SRI) e a grade do
      FullCalendar reprovada no axe~~ resolvido (2026-10-29, D50): FullCalendar 6.1.21 e Chart.js 4.5.1
      do npm no bundle do Vite, jsDelivr fora da CSP; ARIA de grade achatado no `painel-calendario.js`;
      varredura visual com axe-core no CI (`tests/test_crawl_visual.py`).
- [ ] Tirar o jsDelivr da lista de operadores da política (`conteudo/lgpd.OPERADORES`, cobrado em
      `tests/test_politica_dsar.py`) e da tabela de [`LGPD.md`](LGPD.md) §3: desde a D50 nenhuma
      página carrega nada dele. Junto com a revisão jurídica da política (item da operação, acima)
- [ ] Registry de regras (spec 5.1) e sessão única de cliente verificado (5.3) — ver
      [`specs/regras-negocio-registry.md`](specs/regras-negocio-registry.md)
- [ ] Decidido não fazer (por ora): UNIQUE de regra de comissão ativa (quebraria o desempate por
      "mais recente"); consentimento de WhatsApp para marketing. (O trigger de `updated_at`
      entrou na 0056; nomes em inglês na 0079.)
- [x] Revisão de 2026-10 (fases 0–5: bugs, testes/CI, arquitetura, front, banco e performance)
      concluída — o que entrou está no [`PROJECT.md` §16](PROJECT.md#16-histórico); fora do código
      ficaram as ações do dono e o go-live (Railway em stand-by).

### 6.3 Histórico anterior (resumo)

- **2026-06-11 → 06-14:** remodelagem v2.1 (0027–0038), revisão de regras, spec do front.
- **2026-06-14 → 06-21:** Ondas 1–4 do front; auditoria SWE de backend (149 achados: 31 alta,
  71 média, 47 baixa — todos endereçados; 0039). Decisões pendentes daquela auditoria foram
  resolvidas nesta rodada: `AgendamentoService` único, `domain/` só com os handlers reais,
  senha por link (nunca por e-mail), `DJANGO_ENV` explícito, SlotService extraído, 2FA num
  mecanismo só, OTP por telefone para todos, `Cliente.delete()` = soft delete, `limpeza_status`
  sem FALTOU automático.

---

_Última atualização: 2026-10-29 — decisões D26–D49 dos pacotes de revisão (0062–0078: pedido online,
cadastros, LGPD e área da cliente, agenda do painel), D50–D51 dos pacotes de front (design system das
telas da equipe; site com números reais) e pendências da seção 6.2 conferidas no código._
