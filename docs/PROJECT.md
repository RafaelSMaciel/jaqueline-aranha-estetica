# Jaqueline Aranha Estética (Shiva Zen) — Documentação do Projeto

> **Fonte viva textual do projeto.** Reflete o código do branch `dev`
> (migrations `0001`–`0078`). Mudou model, rota, env var, job ou deploy? Atualize aqui
> no mesmo commit. Regras funcionais detalhadas: [`REGRAS-DE-NEGOCIO.md`](REGRAS-DE-NEGOCIO.md).
> Acesso/login: [`AUTENTICACAO-E-ACESSO.md`](AUTENTICACAO-E-ACESSO.md). Decisões e
> pendências: [`ARCHITECTURE.md`](ARCHITECTURE.md). Documento do TCC (requisitos, UML,
> arquitetura): [`gerar_documentacao.py`](gerar_documentacao.py) → `ShivaZen-Documentacao.docx/.pdf`;
> diagramas em [`uml/`](uml/README.md).

## Sumário

1. [Visão geral](#1-visão-geral)
2. [Stack](#2-stack)
3. [Arquitetura](#3-arquitetura)
4. [Setup local e comandos](#4-setup-local-e-comandos)
5. [Banco de dados](#5-banco-de-dados)
6. [Rotas](#6-rotas)
7. [Integrações externas](#7-integrações-externas)
8. [Segurança](#8-segurança)
9. [LGPD](#9-lgpd)
10. [Jobs periódicos](#10-jobs-periódicos)
11. [Variáveis de ambiente](#11-variáveis-de-ambiente)
12. [Testes e CI](#12-testes-e-ci)
13. [Deploy (Railway)](#13-deploy-railway)
14. [Go-live / Operação (runbook)](#14-go-live--operação-runbook)
15. [Convenções de código](#15-convenções-de-código)
16. [Histórico](#16-histórico)

---

## 1. Visão geral

Sistema single-tenant da clínica de estética Jaqueline Aranha (São José do Rio Preto/SP).
Um único projeto Django, renderizado no servidor, com duas superfícies:

| Superfície | Quem usa | Base de template |
|---|---|---|
| **Site público** — vitrine, agendamento online, "Meus agendamentos", lista de espera, NPS, termos, LGPD | Clientes (sem conta/senha; identidade = celular + OTP por SMS) | `estrutura/base_v2.html` |
| **Painel da equipe** — agenda, clientes, ficha/prontuário, pacotes, promoções, comissões, NPS, termos, auditoria, Branding | ADMIN (painel completo, 2FA obrigatório) | `painel/base_v2.html` (app-shell PWA) |
| **Portal do profissional** — agenda própria, aprovar/rejeitar pedidos, "Realizado", anotações, ficha das próprias clientes, "Minhas comissões" | PROFISSIONAL | `profissional/base.html` (casca própria: sem rodapé, CTA, WhatsApp, SEO nem aviso de cookies do site; mesmos tokens e bundle) |

Só dois papéis de equipe: **ADMIN** e **PROFISSIONAL** (a dona faz a recepção pelo painel; o papel
RECEPCAO saiu na 0072 — ver [`AUTENTICACAO-E-ACESSO.md`](AUTENTICACAO-E-ACESSO.md)). Todo
atendimento é presencial.

### 1.1 Glossário (termo do negócio → código)

| Termo | No código | O que é |
|---|---|---|
| Cliente | `Cliente` | Quem é atendida (nunca "paciente"); identidade = celular + OTP |
| Atendimento | `Atendimento` | Uma sessão marcada. Status: **PENDENTE** (pedido esperando a profissional), **AGENDADO** (aprovado), **CONFIRMADO** (a cliente confirmou presença), **REALIZADO**, **CANCELADO**, **FALTOU**, **REAGENDADO** (substituído por outro) — FSM em `Atendimento.TRANSICOES` |
| Retorno | `eh_retorno`, `atendimento_origem` | Sessão gratuita de acompanhamento criada ao marcar REALIZADO um procedimento com retorno obrigatório |
| Habilitação | `Habilitacao` | A profissional realiza o procedimento — só atende quem está habilitado (sem ninguém, o procedimento sai do agendamento; 0062) |
| Grade / exceção | `AgendaHorario` / `AgendaExcecao` | Expediente semanal / o que muda numa data: **FOLGA** (da profissional, ou feriado/recesso da clínica), **HORARIO_DIFERENTE**, **BLOQUEIO** |
| Horário livre (slot) | `SlotService`, `services/agenda` | Horário oferecido (grade, exceções, buffer, antecedência); reservar/reagendar/mover só por `services/agenda` |
| Pacote | `Pacote` → `CompraPacote` + `CompraPacoteItem` + `ConsumoSessao` | Combo de sessões; na venda o nome e os valores ficam **congelados** e cada REALIZADO debita uma sessão |
| Preço vigente / promoção | `Preco`, `Promocao`, `utils/precos` | Preço da data do atendimento (profissional > base) com a promoção daquele dia |
| Carteira / cashback | `Carteira`, `MovimentoCarteira` | Crédito da cliente (vale, indicação); livro-razão |
| Comissão | `RegraComissao`, `MovimentoComissao` | Livro-razão da profissional: CRÉDITO (realizado; congela base e percentual), PAGAMENTO (baixa ou repasse em lote), ESTORNO (cancelado) |
| Competência | `services/financeiro.competencia`, `?mes=AAAA-MM` | O mês do financeiro, da visão geral e do Excel: avulso pela data do atendimento, pacote pela venda menos o reembolso pelo cancelamento |
| Lista de espera | `ListaEspera`, `services/lista_espera` | Quem quer vaga num dia (turno opcional): inscrição anônima (nome + celular, sem cadastro) ou ligada à cliente com o celular verificado; avisada quando um horário compatível libera e continua na fila até a data passar ou ela agendar |
| Cancelamento tardio | `agenda.cancelar_pela_cliente`, `JANELA_MINIMA_ALTERACAO` | A cliente cancela com menos de 24 h um horário já aprovado: o atendimento fica CANCELADO e soma 1 falta para o bloqueio online |
| Área da cliente | `views/publico/portal`, `services/portal_cliente` | O "Meus agendamentos" por OTP: agendamentos, reagendar/cancelar, preferências, revogação de saúde, pedidos LGPD e "baixar meus dados" |
| Política de privacidade | `conteudo/lgpd.py`, `VersaoTermo` LGPD | Texto único da política v1.0 = termo LGPD aceito no wizard = página `/politica-de-privacidade/` = declaração do DSAR |
| Notificação | `Notificacao` | Toda mensagem ligada a um atendimento (lembrete, NPS, termo, e-mails); também a fila do e-mail |
| Link de acesso | `LinkAcesso`, `services/links` | Link público das mensagens (confirmar, avaliar, termo, ficha, descadastro, feed ICS); no banco só o hash do token. Reagendar/cancelar não tem link: é pelo portal com OTP |
| Ficha / prontuário / anotação | `RespostaAnamnese` (`resposta_formulario`) / `Prontuario` / `AnotacaoSessao` | Questionário da cliente / ficha clínica da equipe / nota de cada sessão — dado de saúde, cifrado |
| Termo / aceite | `VersaoTermo` / `AceiteTermo` | Texto versionado (LGPD, saúde, procedimento) / prova do aceite (IP, user-agent, hash) |
| Consentimento | `Consentimento`, `services/consentimentos` | Autorização por finalidade × canal (marketing/e-mail, lembrete/WhatsApp, pesquisa/WhatsApp); histórico append-only, o mais recente vale |
| NPS / detrator / depoimento | `AvaliacaoNPS` | Nota 0–10 pós-atendimento; ≤ 6 alerta a clínica; comentário publicado só com autorização e aprovação |
| Esquecimento / retenção | `sp_anonimizar_cliente`, `sp_aplicar_retencao_lgpd`, `sp_purgar_dados_expirados` | **Pseudonimizar** a pedido da titular / por prazo, e expurgar o registro de saúde aos 20 anos (`inventario_lgpd.py` diz o que acontece com cada coluna) |
| Ator / trilhas | `ator.py`; `LogAuditoria`, `HistoricoAlteracao` | Quem agiu (usuário, IP, origem) / registro das ações e das alterações de dado |
| Recusa de regra | `excecoes.RegraDeNegocio` (`HorarioIndisponivel`, `TransicaoInvalida`) | O "não" de um service, com a mensagem pronta para a tela |
| Job | `jobs.py` (`JOB_MAP`, `JOB_AGENDA`), `rodar_jobs_devidos`, `/cron/run/<job>/`, `rodar_job` | Tarefa periódica: o serviço Cron do Railway roda os devidos a cada 15 min (horários na `JOB_AGENDA`) |
| Branding / identidade visual | `Configuracao`, `utils/branding`, `forms/cadastros.MarcaForm` | Nome, contatos e dados legais da clínica editáveis no painel (tela Identidade visual) |
| Regras operacionais | `models/configuracao.CHAVES`, `Configuracao.ler` | As poucas regras que a dona ajusta (registro tipado: `email_admin`, `MAX_FALTAS_BLOQUEIO`, `anos_experiencia`); sem chave livre |
| Números do site | `services/prova_social.numeros_do_site` | Nota média, clientes atendidas, tratamentos e anos de experiência do site público — cada um só com dado real (abaixo do mínimo, não aparece) |
| Pedido online | `services/agendamento_online.solicitar`, `PedidoAgendamento` | O caso de uso do wizard: o pedido da titular do celular verificado, do termo LGPD às provas |

---

## 2. Stack

| Camada | Tecnologia (versões em `requirements.txt` / `package.json`) |
|---|---|
| Linguagem | Python 3.12 (`.python-version`), Node 22 no build (`.nvmrc`; Tailwind v4 exige ≥ 20) |
| Web | Django 5.2 (MVT, server-render), gunicorn 23, WhiteNoise 6 (estáticos `CompressedManifest` em prod) |
| Banco | PostgreSQL 18 em produção e CI; SQLite (`db_dev.sqlite3`; testes em `.cache/db_test.sqlite3`) em dev e testes rápidos |
| Front-end | Vite 6 + Tailwind CSS v4 (`@tailwindcss/vite`) + Alpine.js via `@alpinejs/csp` + django-vite 3 + django-cotton 2. **Sem HTMX, Bootstrap ou jQuery** |
| Libs de terceiros no front | FullCalendar 6.1.21 (`painel/calendar.html`) e Chart.js 4.5.1 (`painel/overview.html`): dependências npm com versão exata, empacotadas pelo Vite (entradas `painel-calendario` e `painel-visao-geral`) — **nenhum CDN** |
| Fontes | Playfair Display + Lato servidas pelo próprio site (`@fontsource`, subconjunto latin, no build do Vite; sem Google Fonts) — tokens em `static/src/css/tokens.css` |
| Jobs / fila | Sem Celery: jobs são funções (`jobs.py`) com horário na `JOB_AGENDA`, chamadas pelo serviço Cron do Railway (`manage.py rodar_jobs_devidos`, a cada 15 min), pelo cron HTTP `/cron/run/<job>/` ou por `manage.py rodar_job`; e-mail transacional com fila no banco (`Notificacao` EMAIL, reenvio pelo job `emails`) |
| Cache / sessão | LocMemCache + sessão no banco (padrão, 1 worker). `REDIS_URL` troca para django-redis + sessão em cache |
| Auth | Usuário próprio (`AbstractBaseUser`, e-mail), django-axes 8, django-otp + django-two-factor-auth (só models/TOTP; rotas `/account/*` **não** publicadas), django-ratelimit 4 |
| API | Sem API REST (a DRF saiu: não tinha consumidor). Endpoints JSON do próprio site (wizard, painel) são views Django |
| Integrações | Zenvia SMS (OTP), WhatsApp Cloud API (Meta), e-mail (backend Django), pywebpush (VAPID), Cloudflare Turnstile, Sentry. A agenda da profissional sai só pelo feed ICS assinado (sem Google Calendar) |
| Outros | bleach (HTML de promoção), openpyxl (Excel), python-dateutil (RRULE), python-json-logger (logs JSON em prod), qrcode (QR SVG do 2FA, sem Pillow) |
| Deploy | Railway (Dockerfile multi-stage + `railway.json` do serviço web e `railway.cron.json` do serviço Cron) |
| CI | GitHub Actions `.github/workflows/ci.yml` |

`requirements.lock` fixa as dependências transitivas (usado com `-c` no CI e no Docker).

---

## 3. Arquitetura

### 3.1 Estrutura de pastas

```
aranha_estetica/
├── models/          acesso, clientes, profissionais, agenda (AgendaHorario/AgendaExcecao), procedimentos,
│                    agendamentos (Atendimento/FSM), notificacoes, links (LinkAcesso), consentimentos,
│                    prontuario, anamnese, termos, nps, pacotes, sistema, push, carteira, comissao,
│                    historico (trilha historico_alteracao), execucoes (ExecucaoJob), configuracao
│                    (Configuracao + o registro tipado das regras operacionais), mixins
├── db/              peças de banco usadas por migrations (Indice, PadronizarNomes) — congelado
├── migrations/sql/  texto SQL das rotinas por versão (v0056 triggers, v0057 leitura/views, v0058
│                    procedures, v0059 trilhas append-only, v0065 faturamento por competência, v0066
│                    comissão, v0072 links, v0074 anonimização, v0077 retenção de saúde/pacote e
│                    pseudonimização) — congelado, importado só pela migration da versão
├── urls/            uma lista de rotas por área (publico, conta, painel, profissional, infra), todas no
│                    namespace `aranha` — espelha views/
├── views/publico/   site (páginas institucionais, `/duvidas/`), agendamento + agendamento_api (wizard e horários),
│                    verificacao (OTP), portal (meus agendamentos: reagendar/cancelar pela sessão do OTP,
│                    preferências, revogação de saúde, pedidos LGPD, baixar meus dados), confirmacao
│                    (presença), agenda_profissional (agendar por profissional/embed), formularios
│                    (anamnese/pesquisa), nps, termos (aceite), lgpd (política, "Meus dados",
│                    descadastro), lista_espera, contato
├── views/conta/     autenticacao (login, logout, recuperação de senha), dois_fatores (2FA TOTP)
├── views/painel/    um módulo por tela da equipe: visao_geral, agendamentos, aprovacao, calendario,
│                    retornos, clientes, prontuario, anamneses, procedimentos, profissionais, excecoes,
│                    bloqueios, pacotes, promocoes, lista_espera, financeiro, comissoes, nps, termos,
│                    conformidade_termos, notificacoes, usuarios, configuracoes, marca, previa_email,
│                    auditoria
├── views/profissional/  portal (agenda do profissional, aprovar, realizar, anotar, link do ICS, minhas comissões)
├── views/infra/     healthcheck, cron (HTTP), webhooks (WhatsApp/Zenvia), webpush, feed_ics
├── views/comum.py   helpers de view usados por mais de uma área (link do feed ICS do painel e do portal);
│                    as áreas não se importam (contrato "Áreas de views independentes" do import-linter)
├── services/        agenda (reservar/reagendar/mover), atendimento (efeitos de criar/mudar status),
│                    agendamento_online (o pedido pelo site), comunicacao (canais, fila de e-mail),
│                    agendamento (avisos), disponibilidade (SlotService, horários agrupados, dias com
│                    expediente), precos (vitrine), catalogo (preço base e habilitações), otp, lgpd,
│                    termos, alertas (alertas e marcadores das listas em lote, leitura auditada),
│                    prontuario (vínculo clínico), comissao, fidelidade, retorno, lista_espera
│                    (inscrição e aviso de vaga), portal_cliente (sessão e pedidos LGPD da área da
│                    cliente), financeiro (competência), relatorios (Excel), indicadores, pacotes,
│                    links, consentimentos (inclusive o de saúde), anamnese, push, lembretes (D-1 e
│                    sem desfecho), nps (cálculo, envio, detrator), marketing (promoção e aniversário),
│                    feriados, prova_social (números do site público só com dado real, cache de 10 min)
├── utils/           branding, email, sms, whatsapp, telefone (forma canônica, E.164, máscara), precos,
│                    saude (alertas em lote e o marcador de consentimento revogado), fichas, seguranca,
│                    dois_fatores, pii, captcha (Turnstile e `captcha_invalido`), datas (inclusive
│                    `ler_instante_iso` do FullCalendar), parse, busca (trigram no PG), auditoria,
│                    recorrencia (RRULE do subconjunto), respostas (contrato JSON), banco (GUCs app.* e
│                    CALL das procedures), cifragem, formatos
├── conteudo/        textos que também são regra, gerados de constantes (sem Django): lgpd.py (Política
│                    de Privacidade v1.0 — texto, TRATAMENTOS, OPERADORES, PRAZOS_RETENCAO: fonte do
│                    termo LGPD, da página e do DSAR) e faq.py (perguntas frequentes de `/duvidas/`;
│                    `em_destaque` = as 5 da home)
├── forms/           agendamento.py (AgendamentoPublicoForm: o POST do wizard → PedidoAgendamento),
│                    agenda.py (AgendamentoPainelForm do novo agendamento pela recepção,
│                    ValorAtendimentoForm, AgendaExcecaoForm e TIPOS_PAINEL), cadastros.py
│                    (Usuario/UsuarioEdicao, Profissional, GradeSemanal, Procedimento, Configuracao,
│                    Marca), cliente.py (ClientePainelForm, ClienteRapidoForm), painel.py (BloqueioForm,
│                    TermoForm, CampoPreco/parse_preco, EscolhaPorId, ProfissionalOuTodos,
│                    avisar_erros), publico.py (ListaEsperaForm, ContatoForm: honeypot), financeiro.py
│                    (pacote, cancelar/prorrogar compra, regra de comissão, repasse), promocoes.py, nps.py
├── management/commands/  seed, bootstrap_admin, migrate_atomico, setup_2fa, carregar_feriados, rodar_job,
│                    rodar_jobs_devidos, gerar_chave_cifragem, rotacionar_chave_cifragem,
│                    reaplicar_esquecimentos, consultas_lentas
├── templatetags/    painel_tags (`MENU_PAINEL`: menu do painel; filtro `iniciais` do avatar), site_tags
│                    (inclusive o filtro `texto_termo` da política, a tag `{% foto %}` e o monograma da
│                    profissional sem foto), formatos, dict_extras
├── templates/       estrutura/ (bases), cotton/ (componentes do site e do design system da equipe),
│                    publico/ (site; servicos/), agendamento/ (wizard, portal da cliente, links), conta/
│                    (login, senha), painel/ (`base_v2.html` = casca), profissional/ (`base.html` = casca
│                    do portal), partials/, email/, pwa/, erros (400/403/403_csrf/404/429/500),
│                    axes_bloqueio.html, robots.txt
├── static/src/      fonte do Vite — css/tokens.css, css/app.css, css/componentes.css (design system das
│                    telas da equipe) e css/site.css (páginas do site público), e uma entrada JS por tela
│                    (app.js, wizard.js, painel.js — abas, menu "⋯", modais, Ctrl+K —, painel-busca.js,
│                    painel-status.js — seletor de status e lote da agenda —, painel-calendario.js
│                    (FullCalendar), painel-visao-geral.js (Chart.js), webpush.js; nome com hash no build)
│                    + lib/requisicao.js (POST JSON com CSRF no contrato utils/respostas) — fora do
│                    collectstatic
├── static/assets/clinica/variantes/  fotos do site em WebP + JPEG (480/768/1024 px) e o manifest.json
│                    lido pelo `{% foto %}` — geradas por `scripts/imagens.py` e versionadas
├── static/dist/     build do Vite (gitignored; gerado no Docker/CI)
├── signals.py       só os caches que dependem de Configuracao (post_save/post_delete): Branding e números
│                    do site (`services/prova_social`)
├── jobs.py / jobs_manutencao.py     jobs periódicos (`JOB_MAP`, `JOB_AGENDA` = horários, `executar`)
├── middleware.py    Enforce2FAMiddleware, SecurityHeadersMiddleware, ContentSecurityPolicyMiddleware,
│                    ContextoBancoMiddleware (abre o Ator do request)
├── checks.py        system checks de produção aranha.W001–W016 e erros E001–E004 (§14 f)
├── excecoes.py      RegraDeNegocio (mensagem + codigo), HorarioIndisponivel, TransicaoInvalida, TermoPendente
├── ator.py          Ator (usuário, IP, origem) da borda em ContextVar → LogAuditoria + GUCs app.*
├── inventario_lgpd.py  tabela → classe (titular/equipe/sistema); coluna pessoal → anonimiza/apaga/purga/retém
├── context_processors.py · decorators.py · validators.py · constants.py · sitemaps.py · admin.py
clinica/             settings/{base,dev,prod,staging,test}.py (leem e validam a env; §15), urls.py,
                     wsgi.py, apps.py, gunicorn_logger.py, logging_filtros.py (redação de token/PII no log)
scripts/             backup/ (dump cifrado e teste de restauração, §14 h) e imagens.py (variantes
                     responsivas das fotos do site; Pillow do requirements-dev) — fora da imagem Docker
```

### 3.2 Camadas e fluxo de uma requisição

```
Navegador ──HTTPS──▶ gunicorn (1 worker × 4 threads)
  └─ middleware: Security → GZip → WhiteNoise → Session → CSRF → Auth → OTP → Messages
                 → XFrame → CSP (nonce) → SecurityHeaders (no-store/no-referrer) → Enforce2FA
                 → Axes → Ratelimit
      └─ view (validação + permissão) ──▶ services/ e utils/ (regra de negócio)
            └─ models (FSM do Atendimento, managers, constraints) ──▶ PostgreSQL
                  └─ services/atendimento (na mesma transação da FSM): faltas, débito de
                      pacote, comissão, cashback, retorno, lista de espera; push do novo
                      atendimento no on_commit
```

- **FSM do atendimento** (`models/agendamentos.py`): toda mudança de status passa por
  `aprovar/confirmar/cancelar/marcar_realizado/marcar_falta/marcar_reagendado`, que travam a
  linha (`select_for_update`), validam `TRANSICOES`, gravam `LogAuditoria` e aplicam os
  efeitos (`services/atendimento.ao_mudar_status`). `status = X; save()` pula tudo isso e é
  proibido no código.
- **Efeitos do atendimento** (`services/atendimento.py`, chamados explicitamente — sem
  post_save nem EventBus): `ao_mudar_status` roda dentro da transação da FSM, em ordem —
  REALIZADO zera as faltas, debita a sessão do pacote (item congelado na venda; nunca de
  retorno; validade pela data da sessão), lança a comissão, libera o cashback de indicação e
  sugere o retorno obrigatório; CANCELADO estorna comissão e cashback; FALTOU conta a falta
  (3 seguidas suspendem o agendamento online); CANCELADO/REAGENDADO de um ativo avisa a lista
  de espera (savepoint, best-effort). Efeito que falha desfaz a transição inteira (status +
  livro-razão gravam juntos ou nada grava). `ao_criar` (chamado por `services.agenda.reservar`
  e pelo "adicionar" do Django admin) manda o push à profissional no `on_commit` e tira da lista
  de espera as inscrições da cliente para aquele procedimento (savepoint, best-effort).
  `status = X; save()` não aplica regra nenhuma. `cancelar(..., pela_cliente=True)` só nos
  fluxos da própria cliente (portal e link de confirmação, por `agenda.cancelar_pela_cliente`) —
  fica na trilha (`[pela cliente]`). No mesmo módulo, os casos de uso da equipe:
  `marcar_realizado` (painel e portal da profissional: termo de procedimento não aceito levanta
  `TermoPendente`; com `aceitar_sem_termo` marca e grava "Realizado sem termo aceito") e
  `registrar_valor_cobrado` (valor combinado, crédito de indicação abatido; em REALIZADO recalcula
  a comissão pendente e libera o cashback; status final e retorno recusam).
- **Aviso de cancelamento** (`services/agendamento.avisar_cancelamento`): e-mail à
  cliente quando a clínica cancela — painel, rejeição, Django admin e pedido PENDENTE que venceu
  sem aprovação. Cancelamento pela própria cliente não avisa.
- **Disponibilidade**: fonte única `services/disponibilidade.py::SlotService` (slots de 30
  min; grade semanal `AgendaHorario` e exceções `AgendaExcecao` — folga/feriado/recesso,
  horário diferente, bloqueios pontuais/recorrentes do profissional ou globais —, buffers,
  `min_notice_horas`, `max_advance_dias`). Usada pelo wizard, pelo
  reagendamento, pelo agendamento interno e pela validação do servidor. Em volta dele, no mesmo
  módulo: `horarios_agrupados` (horários do dia com as profissionais livres e o preço na data — o
  reagendamento omite o preço) e `dias_com_expediente` (o calendário do mês do wizard em 2
  consultas: fora feriado/recesso, folga, `max_advance` e dia todo bloqueado);
  `procedimentos_agendaveis` = ativos com ao menos 1 profissional ativa habilitada.
- **Agenda** (`services/agenda.py`): as 5 portas que gravam horário (wizard, agendamento pelo
  painel, reagendamento pelo portal, arrasto do calendário, retorno marcado pela recepção) passam por
  `validar_horario` (habilitação, horário futuro, feriado/recesso, SlotService; `detalhado=True`
  dá o motivo à equipe — conflito, bloqueio, folga, expediente — e o site vê a mensagem
  genérica), `reservar` (conflito com `select_for_update`, preço da data, INSERT em savepoint;
  corrida perdida para o EXCLUDE vira `HorarioIndisponivel`), `reagendar` (antigo REAGENDADO +
  novo com preço/promoção e ficha) e `mover`. Moram aqui também `agendar_pela_recepcao` (painel: cliente nova gravada na mesma transação, preço do dia ou o
  combinado — `preco_da_recepcao` —, AGENDADO, e-mail CONFIRMACAO à cliente e a trilha; outro
  cadastro com o mesmo telefone/e-mail/CPF no meio vira `cliente_duplicada`) e
  `cancelar_pela_cliente` (portal e link do lembrete, linha travada: com menos de
  `JANELA_MINIMA_ALTERACAO` de um horário AGENDADO/CONFIRMADO é **cancelamento tardio** —
  `Cliente.registrar_cancelamento_tardio` soma 1 falta sem virar FALTOU; recusas `nao_encontrado`,
  `passado`, `nao_cancelavel`). O que é só de uma porta fica nela (o pedido do wizard, a validação
  do formulário do painel em `forms/agenda`, e-mails do portal).
- **Pedido online** (caso de uso, `services/agendamento_online.solicitar(PedidoAgendamento,
  request=)`): o `forms/agendamento.AgendamentoPublicoForm` lê o POST do wizard, valida formato,
  idade e o gate do OTP (o celular tem de ser o verificado **nesta** sessão) e entrega um
  `PedidoAgendamento`; o service tem a regra inteira, testável sem HTTP — termo LGPD vigente →
  `validar_horario` → fichas (validade de 12 meses; consentimento de saúde se houver resposta) →
  termos do procedimento → e-mail de outro cadastro → trava do horário no cache → numa transação:
  cadastro da **titular** do celular, bloqueio online, teto de 3 horários ativos, opt-ins,
  `reservar`, e-mail (APROVACAO ou CONFIRMACAO), aceites com prova e fichas com a trilha do art. 11.
  Toda recusa é `RegraDeNegocio` com `codigo` (`sem_termo_lgpd`, `ficha_invalida`,
  `consent_saude_ausente`, `termo_pendente`, `email_de_outro_cadastro`, `horario_em_confirmacao`,
  `bloqueado_online`, `limite_online`, `horario_ocupado`...); a view (`views/publico/agendamento`)
  só faz honeypot, form, service e a página de sucesso.
- **Catálogo** (`services/catalogo`): o preço base muda abrindo uma vigência nova a partir de hoje
  (só quando o valor muda; o histórico fica) e tirar a habilitação de quem ainda tem atendimento
  futuro do procedimento é recusado (`habilitacao_em_uso`).
- **Recusas** (`excecoes.py`): service diz "não" levantando `RegraDeNegocio(mensagem, codigo=)`
  (ou `HorarioIndisponivel`/`TransicaoInvalida`/`TermoPendente`, que leva os termos pendentes); a
  view só mostra `exc.mensagem` (ou `json_recusa(exc)`). `Atendimento.TransicaoInvalida` é a
  mesma classe; a FSM recusa REALIZADO/FALTOU antes do início (menos `TOLERANCIA_DESFECHO`, 30
  min) com `desfecho_antes_do_inicio`.
- **Agenda do painel** (`views/painel/agendamentos`, `aprovacao`, `calendario`, `visao_geral`):
  views finas sobre os casos de uso acima e o `AgendamentoService` (aprovar/rejeitar e
  `decidir_em_lote`, item a item, cada um na sua transação). Os marcadores de cada linha (alerta
  de saúde, termo e ficha pendentes, pacote e crédito) saem em lote de
  `services/alertas.marcar_atendimentos` e a tela grava "Leu alertas de saúde"
  (`alertas.registrar_leitura_saude`, tabela `prontuario`, `{clientes, tela}`) quando mostra algum
  alerta; o botão "Agendar retorno (até dd/mm)" vem de `retorno.devidos_na_lista` (1 consulta).
  Exceções de agenda pelo `forms/agenda.AgendaExcecaoForm` (reenviar o mesmo dia/período
  atualiza a vigente — `AgendaExcecao.objects.vigente_para`) e `AgendaExcecao.clean` com uma regra
  por método.
- **Área da cliente** (`views/publico/portal` + `services/portal_cliente`): sessão do OTP (do
  portal ou aberta no fim do wizard sem novo SMS) presa ao telefone do cadastro, 1 h, com a hora
  da verificação; cada atendimento só pelo id dentro dela (anti-IDOR). Preferências e revogação de
  saúde em `services/consentimentos` (origem PORTAL); pedido de correção/exclusão =
  `registrar_pedido` (trilha com o prazo de 15 dias + e-mail ao encarregado > `email_admin` >
  `ADMIN_EMAIL` > e-mail da marca); "baixar meus dados" = o export sem saúde de
  `services/lgpd.exportar_dados`, só com verificação de até 10 min.
- **Respostas JSON** (`utils/respostas.py`, contrato único das APIs do site — inclusive as do
  wizard, `/ajax/dias-disponiveis/`, `/ajax/horarios-disponiveis/` e o OTP —, do painel e da infra):
  sucesso `{"ok": true, ...}`, erro `{"ok": false, "codigo": "<estável>", "mensagem": "<texto>"}`
  (`json_ok`, `json_erro`, `json_recusa`) — o front decide pelo `codigo`, nunca pelo texto. Webhooks
  de provedor respondem no formato que o provedor espera.
- **Telefone** (`utils/telefone.py`, fonte única): forma canônica gravada em `Cliente.telefone` e
  usada no OTP, nas buscas e no webhook do WhatsApp (só dígitos, DDD + número, sem DDI/zero de
  tronco), E.164 só para telefone BR válido (destino de SMS/WhatsApp) e a máscara de log.
- **Preço**: `utils/precos.py` — preço vigente por data (profissional > base) e promoção
  vigente na data; `preco_com_promocao()` é o valor gravado no atendimento. A vitrine (cards do
  wizard, widget embutido, `/especialidades/`, página do serviço, `/promocoes/`) sai de uma função
  só, `services/precos.precos_de_vitrine` (2 consultas para o catálogo inteiro): preço base do dia
  (sem base, o menor entre os profissionais) com a promoção que o agendamento aplicaria — e só se
  ela reduz o valor (a vitrine nunca anuncia o que o atendimento não grava).
- **Branding**: `utils/branding.get_branding()` — precedência **tela Identidade visual ("Branding";
  tabela `configuracao`, `forms/cadastros.MarcaForm`) > env > padrão**; contatos vazios escondem o
  item no site; campo igual ao valor em uso não conta como alteração e apagar o ajuste volta ao
  padrão. As regras operacionais (`email_admin`, `MAX_FALTAS_BLOQUEIO`, `anos_experiencia`) são
  outra tela, com o registro tipado de `models/configuracao.py`.
- **Números do site** (`services/prova_social.numeros_do_site`, home, "Sobre" e páginas de
  serviço): só o que o banco ou a dona sustentam — nota média do NPS em 5 estrelas (÷ 2) só com 10+
  respostas, clientes distintas com atendimento REALIZADO só a partir de 50 e arredondadas para
  baixo ("120+"), tratamentos = procedimentos agendáveis online, anos de experiência = regra
  `anos_experiencia` (em branco, some). Uma conta a cada 10 min (cache), zerada quando `Configuracao`
  muda (`signals.py`); o template esconde o número que vier `None`. Regras em
  [`REGRAS-DE-NEGOCIO.md` §18](REGRAS-DE-NEGOCIO.md).
- **Comunicação** (`services/comunicacao.py`): toda mensagem ligada a um atendimento é uma
  `Notificacao` (tipo × canal × status) — o histórico do que a cliente e a equipe receberam.
  Os e-mails de confirmação, cancelamento, lembrete D-1 (quem não autorizou o WhatsApp) e o pedido
  de aprovação à profissional nascem `Notificacao` EMAIL PENDENTE **na transação de quem pediu**
  (outbox) e saem no `on_commit`; se falhar, o job `emails` reenvia com espera crescente
  (5/10/20/40 min) até 5 tentativas ou 48 h → FALHOU (lembrete de horário que já passou ou foi
  cancelado vira FALHOU sem envio). A fila não guarda cópia de PII: destino e texto saem do
  atendimento na hora do envio. Marketing (`services/marketing`) e avisos em lote saem direto
  (disparo da promoção no request, em lotes; aniversário e pacote pelos jobs). Sem Celery (nada
  roda em segundo plano).
- **Front**: `static/src/js/app.js` registra componentes Alpine CSP-safe (`temaToggle`,
  `modal`, `navMenu`, `cookieConsent`, `adminShell`, `alertaDismiss`, `anotacaoModal`,
  `pacoteCriar`) + `data-confirm` global. As telas da equipe (painel e portal da profissional)
  carregam também `painel.js` — **vanilla, por delegação no `document`, sem Alpine**: abas no padrão
  WAI-ARIA (setas, Home/End, aba lembrada por página, aba com erro do servidor abre sozinha), menu
  "⋯" (`<details>` nativo: fecha os outros, no clique fora e no Esc devolvendo o foco), modais
  (`data-abrir-dialogo`/`data-fechar-dialogo`), Ctrl+K na busca do topo e foco/nome na tabela que
  rola na horizontal. A agenda tem módulos próprios: `painel-status.js` (seletor de status de
  `partials/_select_status.html` — só AGENDADO/CONFIRMADO, Realizado/Faltou só a partir do início —
  e a barra de aprovar/recusar em lote; `termo_pendente` pede a confirmação e reenvia com
  `sem_termo`) e `painel-calendario.js` (FullCalendar do npm: recusa do arrasto desfaz o movimento e
  mostra a `mensagem`; o ARIA de grade que o FullCalendar não fecha vira `presentation`), ambos com
  `lib/requisicao.js` (POST JSON com o CSRF da `<meta>`; resposta não-JSON vira `falha_http`); os
  gráficos da visão geral são `painel-visao-geral.js` (Chart.js do npm, cores dos tokens). Libs de
  terceiros entram pelo `package.json` (as do painel com versão exata) e saem no bundle — nenhum
  `<script src>` externo (`test_csp` reprova).
  **CSS em duas camadas sobre os tokens**: `componentes.css` (design system das telas da equipe:
  `.btn`, `.campo`, `.cartao`, `.selo`, `.nota`, `.kpi`, `.tabela`, `.aba`, `.menu-acoes`, `.dialogo-*`,
  `.vazio`... em `@layer components`, então o utilitário do template sempre vence sem `!important`;
  texto base 15px, rótulo/meta 13px, nada abaixo de 12px) e `site.css` (o CSS das páginas do site
  público que antes vinha em `<style>` por template — wizard, "Meus agendamentos", reagendar, aviso de
  cookies, arte da marca, números —, só `var(--cor-*)`, sem hex). Componentes cotton do site:
  `<c-cta-agendar>` (o "Agendar" do site: wizard ou, sem SMS, o WhatsApp — tag `{% agendar_link %}`,
  builtin, usada em todo link p/ o wizard), `<c-servico.passo>`, `<c-servico.cartao>`/`<c-servico.item>`
  (páginas de serviço) e o chrome (`cabecalho` com o menu de `{% menu_publico %}` — uma lista p/
  desktop e celular, `aria-current` —, `rodape`, `mensagens`, `cookie_consent` — faixa fina no pé,
  informativa: o site só usa cookies essenciais; "Entendi" grava `cookie_consent=essenciais` no próprio
  navegador e nada vai ao servidor). Componentes do design system da equipe: `<c-botao>` (`variante`
  primario/secundario/perigo/perigo-contorno/sucesso/fantasma/link, `tamanho` sm/lg, `href` vira
  `<a>`, `icone`, `so_icone`, `loading`), `<c-campo>`, `<c-card>`, `<c-selo>`, `<c-nota>`, `<c-kpi>`
  (variação com seta e texto), `<c-vazio>`, `<c-icone>`, `<c-menu-acoes>` ("⋯"), `<c-dialogo>`,
  `<c-tabela>` (`responsiva`: tabela no desktop, cartões empilhados < 768px com `data-rotulo`) e
  `<c-abas>` + `<c-abas.aba>`/`<c-abas.painel>` (sem JS, links de âncora para seções visíveis).
  Fotos do site pela tag `{% foto 'nome' alt=... sizes=... prioridade=... %}` (`site_tags`): `<picture>`
  com WebP no `srcset` e JPEG de reserva das variantes de `scripts/imagens.py`, `width`/`height` reais
  (sem salto de layout), `loading="lazy"` — ou `fetchpriority="high"` na foto do topo; moldura sem foto
  real usa a arte da marca (`partials/_arte_marca.html`), nunca foto inventada. Casca do painel
  `painel/base_v2.html` (menu lateral no cromo escuro quente da marca — café/ouro, tokens `--cor-cromo-*`
  —, barra do topo com a busca global, cabeçalho de página igual em toda tela) e do portal
  `profissional/base.html` (mesmo design system, abas de navegação na barra escura). Telas-chave: a
  agenda é tabela no desktop e cartões no celular, com as ações secundárias no "⋯" e valor/desfazer
  realização em modal; a ficha da cliente em abas (Dados · Agendamentos · Pacotes e crédito · Saúde ·
  Consentimentos); a visão geral abre com os atalhos "sem desfecho" e "retornos a marcar", depois
  Hoje/Amanhã e pendentes de aprovação, depois os KPIs comparados com o período anterior. Painel,
  portal e páginas do site sem `style="..."` — sobram só o widget `agendamento/embed.html` (fora do
  bundle), as páginas de erro autossuficientes (400/403_csrf/500), a página offline do `sw.js` e os
  e-mails; no site, `test_a11y_front.SitePublicoSemEstiloInlineTests` barra atributo, `<style>` por
  página e cor crua em classe. Títulos, botões e rótulos em caixa de frase.
  Menu do painel numa lista só, `templatetags/painel_tags.MENU_PAINEL` (Operação: visão geral,
  agenda, calendário, retornos, clientes, prontuário, notificações; Cadastros; Relatórios:
  financeiro, comissões, NPS; Sistema). Tema: sem escolha segue o sistema (`prefers-color-scheme`); o
  botão grava o cookie `tema` (`data-theme="claro|escuro"`) sem FOUC.
  Tokens em `tokens.css` (contraste AA conferido nos 2 temas): texto, `--cor-borda-campo` (3:1 nos
  inputs), `--cor-foco` (anel `:focus-visible` global), erro/sucesso/aviso/info/destaque com `-fundo`,
  o status do atendimento (`--st-<status>-fundo/-texto`, classes `.status-<STATUS>`) e os do design
  system da equipe (cromo, `--cor-fundo-realce`, `--cor-borda-forte`, `--cor-marca-suave`,
  `--cor-perigo-forte`, sombras); o verde do WhatsApp é um token do `site.css` (`--site-whatsapp` =
  o verde de ação, 5,3:1). Nada de cor crua do Tailwind (`bg-red-50`…) fora de preenchimento
  sólido. `prefers-reduced-motion` zera animações. Modal = `<dialog class="dialogo">` nativo
  (`showModal`: foco preso, Esc fecha, fundo inerte): `<c-dialogo>` aberto pelo `painel.js` ou, no
  Alpine, componente `modal` (ou `comDialogo({...})` em `app.js`) com `x-ref="dialogo"`.
  Formulário: todo campo tem nome acessível — `<label for>` (o `app.js` associa o rótulo solto ao campo
  seguinte e a dica no meio vira `aria-describedby`) ou `aria-label` nos filtros. Toast do painel:
  erro = `role="alert"`, o resto `role="status"`; nenhum some sozinho.
- **PWA**: `/manifest.json` + `/sw.js` (service worker v10: cacheia só páginas públicas — lista
  fixa, inclusive `/duvidas/`, e `/servicos/detalhe/` — e estáticos; nunca HTML privado; sem rede,
  página offline com a marca) e `/painel/manifest.json` para
  o app da equipe. Registrado no site público e no painel. Ícones em `static/assets/pwa/` (192 e 512
  "any" e 512 "maskable" com margem de segurança — só o monograma "JA"; o texto da logo some em ícone
  pequeno). Subir `VERSION` no `sw.js` a cada mudança dele.

---

## 4. Setup local e comandos

Passo a passo em [`README.md`](../README.md#rodando-localmente); dia a dia (venv Python 3.12 igual
ao lock, `--keepdb`, paralelo, Postgres, navegador, ganchos, regra de fuso) em
[`DESENVOLVIMENTO.md`](DESENVOLVIMENTO.md). Resumo:

```bash
uv venv --python 3.12 .venv              # ou py -3.12 -m venv .venv
pip install -r requirements-dev.txt -c requirements.lock
pre-commit install
cp .env.example .env                    # DEBUG=True
python manage.py migrate
python manage.py seed --demo
ADMIN_EMAIL=... ADMIN_PASSWORD=... python manage.py bootstrap_admin
npm ci && npm run dev                   # terminal 1 (Vite :5173)
python manage.py runserver              # terminal 2
```

**Fotos do site:** foto nova (ou trocada) em `aranha_estetica/static/assets/clinica/*.webp` →
`python scripts/imagens.py` gera as variantes WebP + JPEG (480/768/1024 px, sem ampliar) e o
`manifest.json` em `static/assets/clinica/variantes/` (versionados no git; Pillow vem do
`requirements-dev.txt`, a imagem de produção não precisa dele). `python scripts/imagens.py
--verificar` só confere (sai 1 se faltar variante ou o manifest estiver velho) — a suíte faz a mesma
conferência (`test_site_tags.FotoResponsivaTests`), então foto nova sem as variantes reprova no CI.
Com a mesma versão do Pillow, rodar de novo sem foto nova não muda nenhum arquivo. Em produção, foto
sem variante ainda aparece: o `{% foto %}` cai na original, sem `srcset`.

### Comandos de gerenciamento próprios

| Comando | O que faz |
|---|---|
| `seed [--demo] [--force]` | Idempotente e não destrutivo: profissional Jaqueline Aranha, catálogo real (23 procedimentos, com preço base quando há valor — um é "sob consulta"), habilitações, agenda seg–sex 9h–18h e sáb 9h–15h, termo LGPD v1.0 (se não houver), anamnese padrão e feriados. **Não cria usuários.** `--demo` = 3 clientes e atendimentos fictícios (recusado em produção). Em produção exige `--force`. |
| `bootstrap_admin [--reset-senha]` | Cria/reativa o ADMIN a partir de `ADMIN_EMAIL`/`ADMIN_PASSWORD`/`ADMIN_NOME` (e-mail em minúsculas; senha passa pelos validadores). Senha de conta existente só muda se estava desativada/sem senha ou com `ADMIN_PASSWORD_RESET=true`. Reativação apaga o 2FA antigo da conta. Nunca derruba o pre-deploy; ao final escreve **ERRO** em stderr se não sobrar ADMIN utilizável. |
| `migrate_atomico [--noinput]` | `migrate` numa transação única no Postgres (tudo ou nada), `SET LOCAL lock_timeout = '5s'`, `SET LOCAL app.origem = 'migracao'` (trilha `historico_alteracao`), `SET CONSTRAINTS ALL IMMEDIATE` entre migrations. Fora do Postgres vira `migrate` normal. Usado no pre-deploy e na CI. |
| `setup_2fa <email> [--force] [--name]` | Cria TOTP confirmado, imprime URI `otpauth://` + QR ASCII e 10 códigos de backup. `--force` = reset completo (apaga todos os devices OTP do usuário; auditado). Recuperação de acesso da equipe. |
| `carregar_feriados [--ano N]` | Feriados nacionais (fixos + móveis via Páscoa, `services/feriados`), idempotente; sem `--ano` = ano atual e seguinte. |
| `rodar_job <job> [--forcar] [--lista]` | Roda um job do §10 uma vez (o mesmo do `/cron/run/<job>/`; trilha `comando:<job>`; erro = exit 1). Job de mensagem que já rodou hoje não roda de novo (aviso, exit 0) — `--forcar` passa por cima, depois de uma falha. |
| `rodar_jobs_devidos [--somente a,b] [--agora AAAA-MM-DDTHH:MM]` | Roda os jobs cujo horário da `JOB_AGENDA` caiu nos últimos 15 min (comando do serviço Cron, §13.2); um job que falha não impede os outros e o exit code no fim é 1. `--agora` simula o relógio (fuso da clínica). |
| `reaplicar_esquecimentos [--listar --desde AAAA-MM-DD] [arquivo.json]` | Em volta de uma restauração de backup: lista (antes, no banco atual) e reaplica (depois, no restaurado) as pseudonimizações feitas depois do backup (§14 g). |
| `gerar_chave_cifragem` | Imprime uma chave nova para `CHAVES_CIFRAGEM`. |
| `consultas_lentas [--top N] [--ordem total\|media\|chamadas\|linhas] [--zerar]` | Top do `pg_stat_statements` (texto normalizado, sem valores): mede antes de criar índice; `--zerar` recomeça a contagem. Sem a extensão, explica como ligar (§14 h). Só Postgres. |
| `rotacionar_chave_cifragem [--dry-run]` | Recifra tudo com a 1ª chave de `CHAVES_CIFRAGEM` (inclusive a trilha do prontuário), numa transação, com os triggers de usuário das tabelas desligados durante o UPDATE; auditado. Passo do meio da rotação (nova na frente → rodar → tirar a antiga). |

Úteis do Django/libs: `check`, `check --deploy`, `check --database default` (roda também o
W006), `makemigrations --check --dry-run`, `createsuperuser` (cria ADMIN), `axes_reset`
(limpa bloqueios do django-axes), `clearsessions`.

---

## 5. Banco de dados

### 5.1 Resumo

- **37 tabelas de domínio** (+ tabelas do Django, django-otp, django-axes). PK `BigAutoField`.
- Migrations `0001`–`0078`. Ainda não há produção: o primeiro deploy cria o banco do zero (§14).
  O Postgres do ambiente de desenvolvimento no Railway (na `0026`) está em stand-by. Histórico em
  [`specs/remodelagem-banco-v2.md`](specs/remodelagem-banco-v2.md) e, da `0048` em diante, o padrão
  SQL em [`specs/padrao-sql.md`](specs/padrao-sql.md).
- **Lógica no banco** (0056–0059, revista na 0065–0066, 0072, 0074 e 0077; só Postgres — §5.4): 47
  triggers (`atualizado_em`, imutabilidade, validação, trilha `historico_alteracao`), 25 funções
  `fn_*`, 9 views `vw_*` de relatório e 4 procedures `sp_*` que são a **fonte única** da escrita de
  retenção/LGPD. Exige as extensões `btree_gist`, `pg_trgm` e `unaccent` (o PG 18 do Railway tem).
- **Padrão SQL em todas as tabelas** (0054/0055): nomes ASCII snake_case PT-BR no singular;
  colunas `eh_`/`tem_` (booleano), `data_`/`hora_`/`_em`, `valor_`, `qtd_`, `url_`, `_hash`
  (`char(64)`), `_ref` (id externo), FK `<tabela>[_papel]_id` e autoria `usuario_autor_id`;
  objetos `pk_`/`fk_` (com `ON DELETE` real, `DEFERRABLE INITIALLY DEFERRED`)/`uq_`/`ck_`/`ix_`/
  `ex_`/`tg_`/`fn_`; sem índice `_like`; `COMMENT` em toda tabela; todo default também no banco
  (`db_default`); textos `NOT NULL DEFAULT ''` (exceto e-mail/CPF/telefone sob UNIQUE parcial e
  dado pessoal opcional: ausente = `NULL`, nunca `''`). Conformidade testada no Postgres por
  `tests/test_nomes_padrao.py`.
- **Nome só no banco**: atributos e classes Python não mudaram — o model aponta a coluna/tabela
  por `db_column`/`db_table` (ex.: `Cliente.ativo` → `eh_ativo`, `Procedimento.buffer_minutos` →
  `intervalo_minutos`, `ItemPacote` → `pacote_item`, `FormularioAnamnese` → `formulario`). SQL
  cru usa o nome do banco; mapa completo no §3.2 da spec.
- Nenhum token de link público fica em texto puro: `link_acesso` guarda só o `sha256` (0049).

### 5.2 Tabelas

| Domínio | Tabela (model) | Para que serve |
|---|---|---|
| Acesso | `usuario` (Usuario) | Equipe. Login por e-mail; `papel` ADMIN/PROFISSIONAL (CHECK; RECEPCAO saiu na 0072); `is_staff` = ADMIN; 1:1 opcional com Profissional; `eh_ativo` (desativar, nunca excluir) |
| | `assinatura_push` (AssinaturaPush) | Inscrições Web Push (VAPID) por usuário (`usuario_id`, `chave_p256dh`, `chave_auth`) |
| | `log_auditoria` (LogAuditoria) | Trilha LGPD art. 37: autor (`usuario_autor_id`) + snapshot `usuario_autor_nome` ("Nome <email>"), ação, `tabela` + `registro_pk` (bigint), detalhes JSON (PII mascarada), IP. Append-only no PG (0059): só a purga com 180+ dias, a pseudonimização do esquecimento e o `SET NULL` do autor |
| | `historico_alteracao` (HistoricoAlteracao) | **Trilha de dados** gravada por trigger (0056, só PG) em prontuario, atendimento, compra_pacote, carteira, regra_comissao, preco, promocao, pacote, versao_termo, usuario e configuracao: operação, só as `colunas_alteradas` com antes/depois (`senha_hash` = `"[redigido]"`), autor/IP/origem da sessão (`web`, `cron:<job>`, `comando:<job>`, `migracao`), `transacao_ref`. Append-only; Django admin só leitura (leitura auditada). `cliente` fica fora de propósito (PII; o esquecimento obrigaria a expurgar a trilha) |
| Pessoas | `cliente` (Cliente) | Cliente da clínica. Telefone canônico (`utils/telefone`, 10–11 dígitos) é a chave natural; e-mail, CPF, nascimento, CEP e endereço opcionais (ausente = `NULL`) — CPF/CEP/endereço só pela finalidade (nota fiscal e contrato de pacote); RG, profissão e foto não são coletados (saíram na 0074); consentimentos de comunicação na tabela `consentimento` (histórico, 0050); `saude_revogada_em` (revogação do consentimento de dado de saúde; `NULL` = vigente ou nunca dado — a concessão é o aceite SAÚDE, e um aceite novo limpa a data; 0075); `qtd_faltas_consecutivas` (FALTOU e cancelamento tardio somam)/`eh_bloqueado_online`; soft delete (`deletado_em`); `cliente_indicador_id` (cashback); `nome` com collation `pt_br` |
| | `profissional` (Profissional) | Quem atende. `antecedencia_minima_horas` (0–720, padrão 2), `antecedencia_maxima_dias` (1–365, padrão 60), vitrine (especialidade, foto, biografia). A agenda vai para o calendário dela só pelo feed ICS (`link_acesso` FEED_ICS; o Google Agenda saiu na 0071) |
| Catálogo | `procedimento` (Procedimento) | Serviço: duração, `intervalo_minutos` (folga após, 0–120), categoria, retorno obrigatório (`tem_retorno_obrigatorio` + janela em dias + duração do retorno). Todo atendimento é presencial (modalidade e link de videoconferência saíram na 0073) |
| | `habilitacao` (Habilitacao) | Profissional × procedimento (M2M through) |
| | `preco` (Preco) | Preço versionado por `data_inicio_vigencia`, base (sem profissional) ou por profissional |
| | `promocao` (Promocao) | Desconto 0–100% **XOR** preço fixo, período (vale pela data do atendimento), procedimento (nulo = geral, só percentual vale); disparo por e-mail com o estado na própria linha (`envio_cursor` = último cliente já reservado num lote em andamento, `disparada_em` = início do último disparo; 0068) |
| Agenda | `agenda_horario` (AgendaHorario) | Grade semanal: várias janelas por dia, sem sobreposição; `dia_semana` **ISO** (1=seg … 7=dom = `isoweekday()`) |
| | `agenda_excecao` (AgendaExcecao) | Tudo que muda a grade numa data: **FOLGA** (intervalo `data_inicio..data_fim`; sem profissional = clínica inteira: feriado nacional/local, recesso), **HORARIO_DIFERENTE** (substitui a grade num dia) e **BLOQUEIO** (`data_hora_*`, pontual ou recorrente por RRULE no subconjunto DAILY/WEEKLY/MONTHLY + `data_fim_recorrencia`; sem profissional = "Todos"). `origem` MANUAL/FERIADO_NACIONAL/FERIADO_LOCAL/RECESSO (GCAL e `origem_ref` saíram na 0071; as importadas viraram MANUAL); `eh_ativo=false` = registrada e ignorada (ex.: feriado em que a clínica abre). 0048 unificou as 4 tabelas antigas |
| | `atendimento` (Atendimento) | Agendamento/sessão. FSM de 7 estados, preço gravado (`valor_cobrado`, `valor_original`, `promocao`, `descricao_preco`), retorno (`eh_retorno` + `atendimento_origem_id`), `atendimento_reagendado_id` (o antigo que este substitui) |
| | `notificacao` (Notificacao) | Mensagem por atendimento: tipo (LEMBRETE — D-1 por WhatsApp ou e-mail —, CONFIRMACAO, CANCELAMENTO, NPS, APROVACAO, TERMO; LEMBRETE_2H e PESQUISA, sem produtor, saíram na 0073), canal, status PENDENTE/ENVIADO/FALHOU; também a fila do e-mail (`tentativas`, `proxima_tentativa_em`, índice parcial `ix_notificacao_fila_email`, 0061); o link público (/confirmar/, /nps/, /termo/) vive em `link_acesso` |
| | `lista_espera` (ListaEspera) | Pedido de aviso de vaga (procedimento, data, `turno_desejado` MANHA/TARDE/NOITE ou `NULL` = qualquer, `profissional_desejado_id` opcional). **Anônima** (`nome_contato` + `telefone_contato`, sem cadastro) ou ligada à cliente (`cliente_id`, nulável) quando o celular foi verificado por OTP — só então aceita `email_contato` (CHECK); `ultimo_aviso_em` = último aviso entregue (a inscrição continua na fila; era `eh_notificado` até a 0076). Sai quando a data passa ou a cliente agenda |
| Clínico | `prontuario` (Prontuario) | Ficha base 1:1 com a cliente: alergias, contraindicações, histórico, medicamentos, observações + `respostas` (JSONB `{key: valor}` do questionário do prontuário = formulário PRONTUARIO ativo, 0051; atributo Python `respostas_extras`) |
| | `prontuario_versao` (ProntuarioVersao) | **Histórico append-only** da ficha: foto do estado anterior a cada edição, com autor (`usuario_autor_id` + `usuario_autor_nome`, 0046); DELETE só no expurgo de saúde aos 20 anos (0077) |
| | `anotacao_sessao` (AnotacaoSessao) | Nota clínica por atendimento; autor PROTECT + snapshot `usuario_autor_nome`; append-only, DELETE só no expurgo de saúde aos 20 anos (0077) |
| | `formulario` (FormularioAnamnese) | Questionário único em `perguntas` (`[{key, tipo, label, obrigatorio, opcoes?, alerta_saude?}]`): ANAMNESE e PESQUISA (respondidas pela cliente) e PRONTUARIO (perguntas extras da ficha, preenchidas pela equipe — no máximo 1 ativo, sempre GLOBAL, nunca obrigatório; 0051). Escopo GLOBAL/CATEGORIA/PROCEDIMENTO (o MODALIDADE saiu na 0073) + `eh_obrigatorio`. Era `formulario_anamnese` até a 0054 |
| | `resposta_formulario` (RespostaAnamnese) | Ficha respondida (`respostas`, `respondido_em`) por cliente/atendimento; link público do convite em `link_acesso` (FORMULARIO, 60 dias). Era `resposta_anamnese` até a 0054 |
| LGPD | `versao_termo` (VersaoTermo) | Termo versionado (LGPD, SAUDE ou PROCEDIMENTO); 1 ativo por escopo; imutável depois do 1º aceite. A v1.0 LGPD é a Política de Privacidade completa (`conteudo/lgpd.TERMO_LGPD_CONTEUDO`; 0045 e `seed`, reescrita sem aceite pela 0078) e a página `/politica-de-privacidade/` mostra a versão vigente (`VersaoTermo.lgpd_vigente`) |
| | `aceite_termo` (AceiteTermo) | Prova do aceite: cliente, versão, atendimento, `ip_origem`, user-agent, `conteudo_hash` (SHA-256 do texto, `char(64)`; `NULL` = aceite anterior à 0044), data. Imutável (trigger), salvo a minimização LGPD (IP → `NULL`, user-agent → `''`; 0077). Aceite SAÚDE novo reativa o consentimento de saúde revogado |
| | `codigo_otp` (CodigoOtp) | Desafio OTP (`codigo_hash` HMAC-SHA256 com a SECRET_KEY, `char(64)`) por `identificador` (e-mail ou `sms+<dígitos>@shivazen.local`), propósito AGENDAMENTO/LOGIN_CLIENTE/DSAR, TTL, `qtd_tentativas` |
| | `consentimento` (Consentimento) | **Histórico append-only** de concessão/revogação por finalidade × canal (MARKETING/EMAIL, PESQUISA_SATISFACAO/WHATSAPP, LEMBRETE_AGENDAMENTO/WHATSAPP): origem (AGENDAMENTO_ONLINE, DESCADASTRO, PAINEL, ESQUECIMENTO, MIGRACAO, ADMIN, WHATSAPP — o "PARAR" respondido no WhatsApp, 0069 —, PORTAL — "Minhas preferências" da área da cliente, 0075), texto exibido, IP, user-agent, autor, `registrado_em`. Estado atual = linha mais recente do par; sem linha = não concedido (0050). O consentimento de dado de saúde fica fora daqui (aceite SAÚDE + `cliente.saude_revogada_em`) |
| | `link_acesso` (LinkAcesso) | Link público com token ("link mágico"): `finalidade` (CONFIRMAR_PRESENCA, NPS, TERMO, FORMULARIO, DESCADASTRO, FEED_ICS) + exatamente 1 alvo (notificação, ficha, cliente ou profissional; o legado GERENCIAR_AGENDAMENTO e o alvo atendimento saíram na 0072); só o **SHA-256** do token (`token_hash char(64)`, sem pepper: rotacionar a SECRET_KEY não derruba link enviado), `expira_em`, `usado_em`, `revogado_em`. O token em claro só existe na URL entregue (0049) |
| NPS | `avaliacao_nps` (AvaliacaoNPS) | Nota 0–10 + comentário, `eh_publicacao_autorizada` (opt-in da cliente) e `eh_publicacao_aprovada` (moderação; CHECK: só com o opt-in), `eh_alerta_enviado` |
| Pacotes | `pacote` / `pacote_item` | **Catálogo** (editável a qualquer momento): pacote (`valor_total`, validade em meses) e seus itens (procedimento × `qtd_sessoes`) — só vale p/ vendas futuras. `pacote_item` era `item_pacote` até a 0054 |
| | `compra_pacote` (CompraPacote) | Venda a uma cliente (`CompraPacote.vender`): `pacote_nome` congelado, `valor_pago`, `valor_reembolsado`, status ATIVO/FINALIZADO/CANCELADO/EXPIRADO, `data_expiracao` (prorrogável pelo ADMIN), `cancelado_em` (instante do cancelamento: o reembolso deduz a receita do mês dele — competência, 0065) |
| | `compra_pacote_item` (CompraPacoteItem) | **O que foi vendido** (snapshot imutável, 0052): procedimento, `procedimento_nome`, `qtd_sessoes`, `valor_unitario` (valor pago rateado pelo preço de tabela de cada procedimento no dia da venda; uniforme se falta preço — base da comissão da sessão). Saldo, débito, finalização, e-mail de expiração e DSAR leem daqui |
| | `consumo_sessao` (ConsumoSessao) | 1 sessão consumida por atendimento (OneToOne) de 1 item da compra (`compra_pacote_item`) |
| Financeiro | `carteira` / `movimento_carteira` | Saldo da cliente (`valor_saldo`, CHECK ≥ 0) e ledger append-only (trigger; `valor` > 0, `valor_saldo_resultante` ≥ 0, autor `usuario_autor_id`) — programa de indicação: origem CASHBACK_INDICACAO/CASHBACK_ESTORNO, USO_AGENDAMENTO (crédito usado no valor do atendimento), CANCELAMENTO (devolução) e AJUSTE_MANUAL, sempre explícita (sem default; VALE_PRESENTE e OUTRO saíram na 0067). Reembolso de pacote é em dinheiro, nunca crédito |
| | `regra_comissao` / `movimento_comissao` | Regra (`percentual` 0–100 XOR `valor_fixo`; usada não muda nem se exclui — só desativa: `tg_regra_comissao_validar`, 0066) e **livro-razão** de comissão (0053): CREDITO (no máximo 1 ativo — não estornado — por atendimento, 0063; `regra_comissao_id` + snapshot `valor_base_calculo`/`percentual_aplicado`, 0066), PAGAMENTO (baixa ou repasse em lote) e ESTORNO (referenciam o crédito, `usuario_autor`). Status PENDENTE/PAGA/ESTORNADA e saldo (pode ficar negativo) são derivados (`creditos_com_status`, `ComissaoService.saldos_por_profissional`) |
| Infra | `configuracao` (Configuracao, `models/configuracao.py`) | Classe EQUIPE no inventário LGPD (guarda o contato do encarregado e o `email_admin`). Chave-valor com dois donos: a marca e os contatos (`utils/branding.BRANDING_FIELDS`, tela Identidade visual, validação própria) e as **regras operacionais** — registro tipado `CHAVES` (`email_admin`: e-mail válido ou vazio; `MAX_FALTAS_BLOQUEIO`: inteiro de 1 a 10, padrão 3; `anos_experiencia`: inteiro **opcional** de 1 a 60, padrão em branco — vazio esconde o número no site), lido por `Configuracao.ler` (valor em branco ou recusado pelo registro → padrão, com aviso no log). A tela Regras operacionais lista e grava só o registro, sem chave livre; "Restaurar padrão" apaga a linha. As perguntas do prontuário saíram daqui na 0051 (formulário PRONTUARIO) e o cursor do disparo de promoção na 0068 (vai na `promocao`) |
| | `execucao_job` (ExecucaoJob) | Uma linha por job de mensagem sem marca por destinatário (`aniversario`, `pacote_expirando`, `nps_24h`) e dia local: UNIQUE `job`+`dia` faz a 2ª chamada do dia receber 409 em vez de reenviar tudo (`jobs.executar`); `concluido_em` NULL = em andamento ou falhou, `resultado` = resumo ou tipo do erro. Sem dado pessoal (0070) |

### 5.3 Invariantes garantidas pelo banco

| Invariante | Objeto | Migration |
|---|---|---|
| Profissional não atende 2 clientes ao mesmo tempo (status PENDENTE/AGENDADO/CONFIRMADO) | `ex_atendimento_sobreposicao` — EXCLUDE gist com `btree_gist`, DEFERRABLE (**só Postgres**) | 0035 / 0055 |
| Telefone/e-mail/CPF únicos entre clientes ativos (sem caixa no e-mail); vazio não existe (ausente = `NULL`) | `uq_cliente_telefone_ativo`, `uq_cliente_email_ativo` (`UPPER(email)`: também é o índice do `email__iexact`), `uq_cliente_cpf_ativo`, `ck_cliente_email_nao_vazio` | 0034 / 0055 |
| Formato de telefone (10–11 dígitos) e CPF (11) | `ck_cliente_telefone_formato`, `ck_cliente_cpf_formato` (**só Postgres**) | 0034 / 0055 |
| 1 termo ativo por escopo; 1 preço por vigência | `uq_versao_termo_ativa_por_procedimento`/`_global`, `uq_preco_vigencia`/`uq_preco_base_vigencia` | 0034 / 0055 |
| Lista de espera: 1 inscrição por pessoa (cliente ou celular de contato) + procedimento + dia; anônima tem nome e celular (10–11 dígitos); e-mail de contato só com cliente verificada | `uq_lista_espera_cliente`, `uq_lista_espera_telefone_contato`, `ck_lista_espera_cliente_ou_contato`, `ck_lista_espera_telefone_contato_formato`, `ck_lista_espera_email_contato_so_verificado` | 0076 |
| Promoção: desconto 0–100, preço ≥ 0, desconto XOR preço, geral só percentual | `ck_promocao_*` | 0034 / 0047 / 0055 |
| Carteira nunca negativa; ledger imutável com valor > 0, tipo/origem no domínio e saldo resultante ≥ 0 | `ck_carteira_valor_saldo_nao_negativo`, `ck_movimento_carteira_*`; trigger `tg_movimento_carteira_imutavel` (`fn_bloquear_mutacao`, **só PG**) | 0034 / 0038 / 0054 / 0055 |
| Valor pago de pacote ≥ 0; nota NPS 0–10 (aprovar depoimento exige o opt-in); comissão 0–100%, valor fixo ≥ 0 | `ck_compra_pacote_valor_pago_nao_negativo`, `ck_avaliacao_nps_nota_0_10`, `ck_avaliacao_nps_aprovacao_exige_autorizacao`, `ck_regra_comissao_*` | 0039 / 0044 / 0055 |
| No máximo 1 retorno vivo por atendimento de origem; valores do atendimento ≥ 0 | `uq_atendimento_retorno_por_origem`, `ck_atendimento_valores_nao_negativos` | 0043 / 0055 |
| Notificação e OTP com tipo/canal/status/propósito no domínio; tentativas 0–10; hashes `sha256` hex | `ck_notificacao_{tipo,canal,status,resposta}`, `ck_codigo_otp_{canal,proposito,qtd_tentativas_0_10}`; `ck_codigo_otp_codigo_hash_formato`, `ck_aceite_termo_conteudo_hash_formato` (**só PG**) | 0055 |
| JSON no formato certo (objeto/lista) | `ck_prontuario_respostas_objeto`, `ck_resposta_formulario_respostas_objeto`, `ck_formulario_perguntas_lista` (validados na 0055), `ck_log_auditoria_detalhes_objeto`, `ck_prontuario_versao_dados_objeto` (**só PG**) | 0043 / 0055 |
| Prova de aceite imutável (só a minimização LGPD de IP/user-agent passa); termo aceito imutável; histórico da ficha imutável (DELETE só com a flag local `app.expurgo_autorizado` **e** 20 anos de idade) | triggers `tg_aceite_termo_imutavel`, `tg_versao_termo_imutavel`, `tg_prontuario_versao_imutavel` (**só PG**; SQLSTATE 23000 → `IntegrityError`) | 0046 / 0054 / 0077 |
| Ordenação pt-BR de nomes | collation ICU `pt_br` em `cliente/profissional/procedimento.nome`, no estado do Django (`db_collation`; o SQLite dos testes tem um equivalente em `db/collation.py`) | 0038 / 0054 |
| Um cashback por atendimento | `uq_movimento_carteira_cashback_por_atendimento` | 0025/0032 / 0055 |
| Padrão de nomes: PK `pk_<t>`, FK `fk_<t>_<coluna>` com `ON DELETE` igual ao `on_delete` do model, UNIQUE de OneToOne `uq_`, sequência `<t>_id_seq`, nenhum índice `_like`, `COMMENT` em toda tabela | `PadronizarNomes()` + `tests/test_nomes_padrao.py` (**só PG**) | 0055 |
| Grade sem janelas sobrepostas; folgas ativas do mesmo profissional sem sobreposição | `ex_agenda_horario_sobreposicao`, `ex_agenda_excecao_folga_sobreposta` — EXCLUDE gist (**só PG**) | 0048 |
| Exceção coerente com o tipo (FOLGA = datas, HORARIO_DIFERENTE = 1 dia + horas, BLOQUEIO = instantes); feriado/recesso só como FOLGA da clínica; origem MANUAL/FERIADO_NACIONAL/FERIADO_LOCAL/RECESSO | `ck_agenda_excecao_*` (tipo, origem, folga, horario_diferente, bloqueio, recorrencia, feriado_global) | 0048 / 0071 |
| RRULE só no subconjunto que o motor expande | `ck_agenda_excecao_regra_recorrencia_suportada` (regex, **só PG**; `clean()` normaliza) | 0048 |
| 1 horário diferente ativo por profissional/dia; 1 feriado por origem/data | `uq_agenda_excecao_horario_diferente_dia`, `uq_agenda_excecao_feriado` | 0048 |
| Link público: alvo coerente com a finalidade (1 FK: notificação, ficha, cliente ou profissional); 1 link por hash; hash = sha256 hex; depois de criado só `usado_em`/`revogado_em` mudam (uma vez cada) | `ck_link_acesso_finalidade`, `ck_link_acesso_alvo_por_finalidade`, `uq_link_acesso_token_hash`; `ck_link_acesso_token_hash_formato` e trigger `tg_link_acesso_imutavel` (**só PG**) | 0049 / 0072 |
| Consentimento: só os 3 pares finalidade × canal que existem, origem no domínio (inclui WHATSAPP e PORTAL); histórico append-only (só a pseudonimização pode apagar IP/user-agent) | `ck_consentimento_finalidade`, `_canal`, `_finalidade_canal`, `_origem`; trigger `tg_consentimento_imutavel` (**só PG**) | 0050 / 0069 / 0075 |
| Questionário: tipo/escopo no domínio e coerentes (escopo PROCEDIMENTO/CATEGORIA com o alvo); PRONTUARIO sempre GLOBAL e não obrigatório; no máximo 1 PRONTUARIO ativo | `ck_formulario_tipo`, `ck_formulario_escopo`, `ck_formulario_escopo_coerente`, `ck_formulario_prontuario_global`, `uq_formulario_prontuario_ativo` | 0051 / 0073 |
| Papel da equipe só ADMIN/PROFISSIONAL; tipo de notificação só os 6 com produtor | `ck_usuario_papel`, `ck_notificacao_tipo` | 0072 / 0073 |
| Pacote vendido: 1 item por procedimento na compra, sessões > 0, valor ≥ 0; item imutável; o consumo é de um item **da mesma compra**, do **mesmo procedimento**, nunca de retorno e só com saldo | `uq_compra_pacote_item_compra_procedimento`, `ck_compra_pacote_item_*`; FK composta `fk_consumo_sessao_compra_pacote_item_compra`, triggers `tg_consumo_sessao_validar` e `tg_compra_pacote_item_imutavel` (**só PG**; o Python espelha no `save()`) | 0052 |
| Comissão: no máximo 1 CREDITO ativo (não estornado) por atendimento — estornado, pode nascer outro (valor corrigido, realização refeita) —, ≤ 1 PAGAMENTO e ≤ 1 ESTORNO por crédito, valor > 0, origem só fora do CREDITO, regra e snapshot (`valor_base_calculo`, `percentual_aplicado` 0–100) só no CREDITO, comissão nunca acima da base; PAGAMENTO/ESTORNO repetem profissional/atendimento/valor do crédito; estornado não se paga; ledger append-only | `…_estorno_por_origem`, `…_pagamento_por_origem`, `ck_movimento_comissao_*` (inclui `_snapshot_so_credito`, `_valor_ate_base_calculo`, `_percentual_aplicado_0_100`); triggers `tg_movimento_comissao_validar` (o crédito ativo único, 0063) e `tg_movimento_comissao_imutavel` (**só PG**; o Python espelha no `save()`) | 0053 / 0063 / 0066 |
| Regra de comissão já usada não muda profissional, procedimento, percentual nem valor fixo (só desativa) | trigger `tg_regra_comissao_validar` (**só PG**; `RegraComissao.save()` espelha) | 0066 |
| Compra de pacote: `cancelado_em` preenchido se e só se CANCELADO; só a cancelada tem reembolso | `ck_compra_pacote_cancelado_em_coerente`, `ck_compra_pacote_reembolso_so_cancelado` | 0065 |
| Movimento da carteira só com origem que tem fluxo, sempre explícita | `ck_movimento_carteira_origem` (sem VALE_PRESENTE/OUTRO, sem default) | 0067 |
| Job de mensagem roda no máximo 1 vez por dia (local) | `uq_execucao_job_job_dia` | 0070 |
| `atualizado_em` sempre atualizado, inclusive em `QuerySet.update()`, procedure e SQL manual (o valor que o ORM manda é respeitado) | `fn_definir_atualizado_em` + `tg_<t>_atualizado_em` nas 22 tabelas com a coluna (**só PG**; o teste de conformidade reprova tabela com a coluna e sem o trigger) | 0056 |
| Nota de sessão é append-only (correção = nova nota); DELETE só no expurgo de saúde (flag local **e** 20 anos) | `tg_anotacao_sessao_imutavel` → `fn_anotacao_sessao_imutavel` (**só PG**; o Python recusa save/delete/update) | 0056 / 0077 |
| Toda escrita nas 11 tabelas sensíveis fica na trilha `historico_alteracao` (autor, IP, origem); a trilha é append-only — só o `SET NULL` do autor e a purga de retenção (flag local `app.expurgo_autorizado` **e** 180+ dias; a do prontuário sai com o expurgo de saúde aos 20 anos, e o prontuário expurgado não deixa cópia na trilha) passam | `fn_registrar_historico` + `tg_<t>_historico`; `fn_historico_alteracao_imutavel` + `tg_historico_alteracao_imutavel`; `ck_historico_alteracao_*` (**só PG**) | 0056 / 0059 / 0077 |
| `log_auditoria` append-only: UPDATE só da pseudonimização (`acao` com `[ANONIMIZADO-<id>]`, `detalhes` NULL) e do `SET NULL` do autor; DELETE só com 180+ dias | `fn_log_auditoria_imutavel` + `tg_log_auditoria_imutavel` (**só PG**) | 0059 |

Além disso: FKs `PROTECT` em tudo que é histórico clínico/financeiro (prontuário, anotação,
ficha, compra de pacote, carteira, aceite), índices só onde há query real ou FK (spec §2.11; o
GIN de `prontuario` saiu na 0055 — nenhuma query de contenção), `COMMENT ON` em todas as tabelas
e nas colunas não óbvias. Detalhe por migration:
[`specs/remodelagem-banco-v2.md`](specs/remodelagem-banco-v2.md) (0027–0047) e
[`specs/padrao-sql.md`](specs/padrao-sql.md) (0048+).

### 5.4 Lógica no banco (0056–0060, só Postgres)

O SQLite dos testes não tem nada disto: o Python cobre (auto_now, `save()`/`delete()` que recusam,
fallback das procedures). Texto SQL versionado em `aranha_estetica/migrations/sql/vNNNN_*.py`
(mudar = módulo + migration nova com `CREATE OR REPLACE`). Detalhe e desvios: spec §5 e §5.9.

| Objeto | O que é | Quem usa |
|---|---|---|
| `tg_<t>_atualizado_em` (22) | `fn_definir_atualizado_em`: preenche `atualizado_em` quando o UPDATE não mexeu nela | todo UPDATE |
| `tg_<t>_historico` (11) | `fn_registrar_historico` (AFTER, por linha): grava `historico_alteracao` — só colunas alteradas; UPDATE só de `atualizado_em` não gera linha | prontuario, atendimento, compra_pacote, carteira, regra_comissao, preco, promocao, pacote, versao_termo, usuario (`senha_hash` redigida), configuracao |
| `tg_*_imutavel` / `tg_*_validar` (13) | append-only (`movimento_*`, `anotacao_sessao` e `prontuario_versao` — DELETE só no expurgo de saúde aos 20 anos, 0077 —, `compra_pacote_item`, `aceite_termo` — só a minimização de IP/user-agent —, `versao_termo` aceita, `consentimento`, `link_acesso`, `historico_alteracao`) e validação (`consumo_sessao`, `movimento_comissao` — crédito ativo único, 0063 —, `regra_comissao` usada, 0066) — SQLSTATE 23 → `IntegrityError` | — |
| `fn_fuso_clinica()`, `fn_data_local(ts)`, `fn_sem_acento(txt)` | fuso explícito (`America/Sao_Paulo` = `TIME_ZONE`), data local, `lower(unaccent())` IMMUTABLE | views/funções; `ix_cliente_nome_trgm` (GIN trigram) e a busca de cliente (`utils/busca.filtrar_clientes`: "joao" acha "João") |
| `fn_horarios_livres`, `fn_ocorrencias_recorrencia`, `fn_recorrencia_casa_dia`, `fn_saldo_pacote`, `fn_nps_periodo`, `fn_saldo_comissao`, `fn_tem_consentimento`, `fn_clientes_para_anonimizar` | **espelho de leitura** do Python (SlotService, `AgendaExcecao.ocorrencias`, `saldo_por_procedimento`, `utils/nps`, `ComissaoService`, `services/consentimentos`); `fn_clientes_para_anonimizar` é a regra da retenção (sem espelho: `LgpdService.candidatos_retencao` lê dela; desde a 0077 retém só pela janela de saúde de 20 anos e até 5 anos depois do fim do pacote), travado por `tests/test_pg_paridade.py` | relatório/integração e as procedures — **o app continua no Python** (slots nunca vêm do SQL) |
| `vw_agenda_dia`, `vw_faturamento_mensal`, `vw_nps_mensal`, `vw_comissao`, `vw_comissao_saldo`, `vw_cliente_resumo`, `vw_consentimento_atual`, `vw_pacote_saldo`, `vw_atendimento_sem_desfecho` | views de relatório (mesmas regras de `services/financeiro`, NPS e comissão). `vw_faturamento_mensal` é por **competência** (0065): avulsos no mês do atendimento, pacotes = vendas no mês da venda − reembolsos no mês do cancelamento (pode ser negativo; colunas `valor_vendas_pacotes`/`valor_reembolsos_pacotes` no fim); `vw_comissao` traz o snapshot do crédito (`valor_base_calculo`, `percentual_aplicado`; 0066) | BI/SQL ad hoc (`railway connect` → `SELECT * FROM vw_faturamento_mensal`); o app não lê views |
| `sp_anonimizar_cliente(cliente, autor, ip)` | direito ao esquecimento = **pseudonimização** (efeito completo testado em `test_pg_paridade`/`test_lgpd_esquecimento`); versão vigente da 0077: também zera código de indicação e quem indicou, tira IP/user-agent do aceite LGPD (e dos de SAÚDE/PROCEDIMENTO quando não há mais registro de saúde), apaga a inscrição anônima da lista de espera pelo celular e os detalhes das trilhas `LGPD: ...` da titular | `LgpdService.esquecer_cliente` (ação "Pseudonimizar" do admin) |
| `sp_aplicar_retencao_lgpd(...)` (9 argumentos) | pseudonimiza os candidatos (`fn_clientes_para_anonimizar`; COMMIT por cliente), apaga fichas de pedido não realizado e espera vencida (inclusive anônima) | `job_lgpd_purgar_inativos` (`LgpdService.aplicar_retencao`) |
| `sp_purgar_dados_expirados(...)` (12 argumentos) | OTP, texto de notificação, auditoria, `link_acesso` vencido/revogado, trilha antiga e, desde a 0077, a etapa **`saude`**: expurgo do registro de saúde (prontuário, versões, anotações, fichas e a trilha do prontuário) de quem teve o último evento de saúde há mais de `RETENCAO_ATENDIMENTO_SAUDE_DIAS`, com a minimização dos aceites SAÚDE/PROCEDIMENTO e o registro na `log_auditoria` (cada etapa num sub-bloco) | `job_housekeeping` (etapa `banco`) |
| `sp_expirar_pacotes(hoje)` | compras ATIVO vencidas → EXPIRADO | `job_expirar_pacotes` |

**Contexto da sessão (GUCs `app.*`)** lido pela trilha: `app.usuario_id`, `app.ip_origem`,
`app.origem`. Quem define (`utils/banco.definir_contexto_banco`, `set_config(..., false)` = nível de
sessão) e **sempre limpa**: o `Ator` (`ator.atuando`) aberto pelo `ContextoBancoMiddleware` (`web`
+ usuário + IP; o `/healthz/` não toca o banco) e por `views/infra/cron.py` (`cron:<job>`; aninhado, volta
ao do request na saída) — o mesmo autor/IP vai à `LogAuditoria` —, `rodar_job` e
`rodar_jobs_devidos` (`comando:<job>`),
`migrate_atomico` (`SET LOCAL app.origem = 'migracao'`); conexão nova nasce limpa
(`connection_created`). SQL manual sai com autor `NULL` e origem `''`. **PgBouncer em modo
transação quebraria isso** (o Railway não usa): nesse dia, trocar para `ATOMIC_REQUESTS` +
`SET LOCAL`.

**Fonte única no Postgres** (decisão de 2026-10-27, ARCHITECTURE D23): `utils/banco.chamar('sp_…',
...)` (allowlist com a aridade) é o único caminho; não há fallback Python. Fora do Postgres ele
recusa com `ImproperlyConfigured` (dev: `docker compose up -d db`), e os testes dessas rotinas são
só-Postgres.

---

## 6. Rotas

Namespace `aranha` (`aranha_estetica/urls/`) + `clinica/urls.py`. Rotas removidas nesta
versão: `/account/*` (django-two-factor), `/api/schema/redoc/`, `/servicos/produtos/`,
`/ajax/verificar-telefone/`, `/ajax/buscar-procedimentos/`, `/ajax/buscar-horarios/`,
`/painel/cancelar-agendamento/`, `/reagendar/<token>/` (link legado; a cliente reagenda só pelo
portal), `/lgpd/aceitar-cookies/` (o aviso de cookies não grava nada no servidor) e
`/painel/integrations/google/*` (Google Agenda).

### 6.1 Site público

| Rota | Função |
|---|---|
| `/`, `/quem-somos/`, `/equipe/`, `/especialidades/`, `/galeria/`, `/depoimentos/`, `/promocoes/` | Páginas institucionais; números do site só com dado real (`services/prova_social`); com uma profissional ativa, `/equipe/` vira "Sobre a profissional" (foto ou monograma, especialidade, bio e os tratamentos que ela realiza, do catálogo); depoimentos só com opt-in + aprovação; promoções com o mesmo preço do agendamento |
| `/duvidas/` | Perguntas frequentes por categoria (`conteudo/faq.py`, respostas geradas das constantes que o sistema aplica; JSON-LD `FAQPage`); a home mostra só as 5 em destaque e leva para cá. No menu, no rodapé, no sitemap e no cache do SW |
| `/servicos/faciais/`, `/servicos/corporais/`, `/servicos/detalhe/<slug>/` | Catálogo (cartões dos procedimentos ativos por categoria, com o "Agendar" já no procedimento; "Cuidados em destaque" saem do catálogo — até 4, só ativos com descrição, um por família) |
| `/contato/` | Formulário de contato (`views/publico/contato`, `forms/publico.ContatoForm`): vai ao e-mail de contato da Identidade visual (`CLINIC_EMAIL`; sem ele, o `DEFAULT_FROM_EMAIL` se não for "noreply"), com o e-mail da pessoa no reply-to; aceite da política obrigatório e **sem caixa de marketing**; captcha (Turnstile, quando configurado), honeypot, 5/min e 20/dia por IP e teto de 50 mensagens/dia no site todo (acima dele, ou com o e-mail fora do ar, a página mantém o texto e oferece o WhatsApp). Mapa = ilustração local + link "Abrir no Google Maps" (nada do Google carrega na página) |
| `/termos-de-uso/`, `/politica-de-privacidade/` | Textos legais; a política é o texto da **VersaoTermo LGPD vigente** (filtro `texto_termo`, sanitizado), com versão e vigência, emoldurado pela identificação da clínica e o encarregado (Identidade visual); sem versão ativa, o texto padrão da v1.0 |
| `/lista-espera/` (+ `/sucesso/`) | Pedido de aviso de vaga **sem criar cadastro** (`forms/publico.ListaEsperaForm` + `services/lista_espera.inscrever`): `?procedimento=&profissional=&data=` preenche (o "Avise-me se vagar" do wizard); captcha, honeypot e 20/dia por IP; sucesso igual em todos os casos |
| `/sitemap.xml`, `/robots.txt`, `/manifest.json`, `/sw.js`, `/favicon.ico` | SEO/PWA |

### 6.2 Agendamento e auto-atendimento da cliente

| Rota | Função |
|---|---|
| `/agendamento/` · `/agendar/<slug>/` · `/embed/agendar/` | Wizard (geral, direto por profissional, widget para iframe — `EMBED_FRAME_ANCESTORS`) |
| `/ajax/dias-disponiveis/` · `/ajax/horarios-disponiveis/` | Dias do mês e horários livres (SlotService) |
| `POST /agendamento/otp/solicitar/` · `POST /agendamento/otp/verificar/` | OTP do celular (Turnstile quando configurado) |
| `POST /agendamento/confirmar/` · `/agendamento/sucesso/` | Grava o atendimento (PENDENTE) |
| `/meus-agendamentos/` (+ `otp/enviar/`, `otp/verificar/`, `POST sair/`) | Área da cliente (login por celular ou e-mail; código sempre ao celular; a sessão também abre no fim do wizard). Sair só por POST com CSRF |
| `POST /meus-agendamentos/preferencias/` · `POST saude/revogar/` · `POST pedido/` · `POST meus-dados/` | Minhas preferências (3 pares, origem PORTAL; 20/h por IP) · revogar o consentimento de saúde (10/h) · pedido de correção ou exclusão (`tipo` + descrição; trilha + e-mail à clínica; 5/h) · baixar meus dados **sem a ficha de saúde** (exige código de até 10 min; 10/h) |
| `/meus-agendamentos/<id>/reagendar/` (+ `<id>/horarios/`) | Reagendar pelo portal: id + sessão do OTP (só agendamento do telefone da sessão; até 24h antes) — o único caminho da cliente |
| `POST /ajax/cancelar-agendamento/` | Cancelar pelo portal: `{"atendimento_id"}` + sessão do OTP (`agenda.cancelar_pela_cliente`); resposta `{ok, mensagem, tardio}` ou `{ok: false, codigo}` (`dados_invalidos`, `sem_atendimento`, `nao_encontrado` 404, `passado`, `nao_cancelavel`) — com menos de 24 h de horário aprovado é cancelamento tardio e conta falta |
| `/confirmar/<token>/` | Confirmar presença / cancelar a partir do lembrete D-1 (WhatsApp ou e-mail); cancelar com menos de 24 h é cancelamento tardio (a página avisa antes) |
| `/termo/<token>/` | Aceitar termo(s) do atendimento (vale até o fim do atendimento) |
| `/nps/<token>/` (+ `obrigado/`) | NPS (link de 7 dias) + opt-in de depoimento; a página de obrigado oferece a pesquisa pós-atendimento, se houver |
| `/anamnese/<token>/` · `/pesquisa/<token>/` (+ `/anamnese/obrigado/`, `/pesquisa/obrigado/`) | Ficha/pesquisa por link (60 dias; ficha de saúde exige consentimento art. 11). Produtores: "Link da ficha" na agenda do painel e convite da pesquisa no fim do NPS (D18) |
| `/lgpd/meus-dados/` · `/lgpd/unsubscribe/<token>/` | "Meus dados": telefone → código (propósito DSAR, captcha, 10/h por IP e 5/h por telefone, latência mínima) → JSON **sem a ficha de saúde**; descadastro |
| `/agenda/<slug>/feed.ics?token=` | Feed iCal do profissional (`views/infra/feed_ics`; link gerado no painel ou no portal do profissional; gerar = revogar o anterior; a URL só aparece na geração) |

### 6.3 Equipe

| Rota | Função |
|---|---|
| `/admin-login/` · `/admin-logout/` (logout só via POST; GET mostra confirmação) · `/admin-login/recuperar/...` | Login da equipe e reset de senha |
| `/painel/seguranca/2fa/` (+ `verificar/`, `challenge/`) | Cadastro e desafio TOTP |
| `/profissional/` · `/profissional/atendimento/<pk>/{realizado,anotar,aprovar,rejeitar}/` · `POST /profissional/calendario/novo-link/` · `/profissional/comissoes/` | Portal do profissional (+ gerar o próprio link do feed ICS; "Minhas comissões" do mês, só leitura) |
| `/painel/`, `/painel/overview/` | Dashboard |
| `/painel/agendamentos/` (+ `novo/`, `bulk/`, `<pk>/aprovar/`, `<pk>/rejeitar/`, `<pk>/termo-link/`, `<pk>/ficha-link/`, `<pk>/valor/`, `<pk>/desfazer-realizacao/`), `/painel/atualizar-status/` | Agenda em lista (marcadores em lote, "Agendar retorno" quando devido), agendamento interno (`novo/`: uma tela, POST com `acao` buscar/horarios/agendar → `agenda.agendar_pela_recepcao`; 60/min), aprovação um a um e em lote (item a item), links do termo e da ficha, valor cobrado, desfazer realização indevida. As escritas usam `@escrita_painel` (POST + 30/min por usuário) |
| `POST /painel/atualizar-status/` | Seletor de status (JSON `{atendimento_id, status, sem_termo?}`, contrato `utils/respostas`): `200 {ok, status_anterior, status_novo}`; `400` `dados_invalidos`/`atendimento_invalido`/`status_invalido`, transição recusada (`transicao_invalida`, `status_nao_suportado`...); `404 nao_encontrado`; **`409 termo_pendente`** (com `termos`; o front pede a confirmação e reenvia com `sem_termo: true`, que fica na trilha); **`409 desfecho_antes_do_inicio`** (Realizado/Faltou antes do início); `500 erro_interno`. PENDENTE vai para o `AgendamentoService` (aprovar/recusar + e-mail) |
| `/painel/retornos/`, `/painel/agendamentos/<pk>/retorno/` | Retornos a marcar (só horários livres dentro da janela) |
| `/painel/calendario/` (+ `eventos/`, `mover/`) | Calendário FullCalendar com arrastar-e-soltar. `eventos/` = lista crua do FullCalendar (range inválido → `[]`). `POST mover/` (`{id, start, end}` ISO, `agenda.mover`): `200 {ok, nova_data}`; `400 dados_invalidos`/`fim_antes_do_inicio` e status que não se move (`transicao_invalida`); `404 nao_encontrado`; `409` recusa de horário com o motivo (conflito, bloqueio, folga, feriado, expediente — ex.: `horario_ocupado`) |
| `/painel/clientes/`, `/painel/clientes/<pk>/` | Clientes e ficha do cliente (pacotes, alertas, histórico) |
| `/painel/prontuario/` (+ `<cliente_id>/`, `<cliente_id>/salvar/`), `/painel/anotacao/<id>/salvar/` | Prontuário com histórico de versões e anotações |
| `/painel/profissionais/` (+ `<pk>/ics/novo-token/`, `<prof_id>/excecoes/...`), `/painel/cadastrar-profissional/`, `/painel/editar-profissional/<pk>/` | Profissionais, grade semanal, exceções, gerar link do ICS (a lista mostra só "ativo desde"; a URL aparece uma vez, na geração). Desligamento — profissional desativada, login dela desativado ou vínculo do login trocado — revoga o feed ICS (auditado); reativar exige gerar um link novo |
| `/painel/procedimentos/...`, `/painel/pacotes/...` (+ `vender/`, `compras/<pk>/cancelar/`, `compras/<pk>/prorrogar/`), `/painel/promocoes/...` (+ `<pk>/disparar/`) | Catálogo, pacotes (venda, cancelamento com reembolso, prorrogação da validade), promoções (disparo em lotes; reenvio só confirmado) |
| `/painel/bloqueios/...`, `/painel/lista-espera/` (+ `<pk>/notificar/`) | Bloqueios de agenda e lista de espera (inscrições anônimas com nome e celular de contato; "Avisar" manda e-mail quando há destino e registra o aviso mesmo sem e-mail, auditado) |
| `/painel/financeiro/?mes=AAAA-MM`, `/painel/exportar-relatorio/?mes=AAAA-MM` | Financeiro do mês (competência; padrão o mês corrente) e o Excel gerencial do mesmo mês (exportação auditada com o mês) |
| `/painel/comissoes/` (+ `<pk>/pagar/`, `repasse/`, `repasse/<pk>/`, `regras/`, `regras/criar/`, `regras/<pk>/ativo/`, `regras/<pk>/excluir/`) | Comissões: baixa de um crédito, repasse em lote ("pagar pendentes até") e o recibo para imprimir (sem nome de cliente), regras de comissão (usada só desativa) |
| `/painel/nps/` (+ `<pk>/publicacao/`) | NPS e moderação de depoimentos |
| `/painel/anamneses/...`, `/painel/termos/` (+ `criar/`, `compliance/`) | Formulários e termos |
| `/painel/notificacoes/`, `/painel/auditoria/`, `/painel/usuarios/...`, `/painel/configuracoes/` (+ `criar/`, `<pk>/editar/`, `<pk>/excluir/` = restaurar padrão), `/painel/branding/`, `/painel/email-preview/[<nome>/]` | Sistema: notificações, auditoria, usuários (ninguém muda o próprio papel; o último ADMIN ativo não é rebaixado nem desativado), **Regras operacionais** (só o registro tipado), **Identidade visual** (marca e contatos). As escritas de usuários, profissionais, procedimentos e regras usam `@escrita_painel` (POST + 30/min por usuário) |
| `/webpush/{public-key,subscribe,unsubscribe}/` | Web Push da equipe |
| `/django-admin-sv/` | Django admin (só ADMIN com sessão 2FA verificada) |

### 6.4 Webhooks e infraestrutura

| Rota | Função |
|---|---|
| `GET/POST /api/whatsapp/webhook/` | Meta: handshake `hub.challenge` (`WHATSAPP_VERIFY_TOKEN`, tempo constante) e eventos assinados (`X-Hub-Signature-256` com `WHATSAPP_APP_SECRET`; sem o segredo recusa tudo); captura a nota de NPS 0–10 e o pedido de parar ("PARAR", "SAIR", "STOP", "cancelar mensagens", "descadastrar" — a mensagem inteira) |
| `POST /api/zenvia/webhook/` | Status de entrega de SMS: `ZENVIA_ALLOWED_IPS` (IP/CIDR) e/ou segredo compartilhado no header `X-Zenvia-Signature` (`ZENVIA_WEBHOOK_SECRET`, o valor configurado na assinatura do webhook na Zenvia; o corpo não é assinado), comparado em tempo constante; prod sem nenhum dos dois responde 503 |
| `POST /cron/run/<job>/` | Jobs periódicos (§10), header `X-Cron-Token`, 30 chamadas/h por IP |
| `/healthz/` | Liveness (processo vivo + manifest do Vite); healthcheck do Railway |
| `/health/` | Readiness: banco + cache + manifest (503 se algo falha) |

---

## 7. Integrações externas

| Serviço | Uso | Configuração | Sem configuração |
|---|---|---|---|
| **Zenvia SMS** | OTP do agendamento, do "Meus agendamentos" e do DSAR. Só para **celular**. Quotas: 3/h por telefone, 10/h por IP, 60/h global (alerta no log a 80%) | `ZENVIA_API_TOKEN`, `ZENVIA_FROM` (+ `SMS_MAX_*`, `ZENVIA_API_URL`, webhook) | Falha fechada: sem agendamento online; CTAs "Agendar" viram WhatsApp (`AGENDAR_WHATSAPP`) |
| **WhatsApp Cloud API (Meta)** | Templates aprovados: `confirmacao_d1` (7 parâmetros: nome, data, hora, procedimento, profissional, link confirmar, link cancelar), `nps_pos_atendimento` (nome, procedimento, link), `lista_espera_vaga` (nome, procedimento, data/hora, link). Categoria UTILITY; links no corpo. Recebe a nota do NPS e o pedido de parar (revoga lembrete e pesquisa por WhatsApp, origem WHATSAPP); a confirmação do pedido é texto livre, dentro da janela de 24 h que a resposta da cliente abriu | `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN` (32+ caracteres, E003), `WHATSAPP_APP_SECRET`, `WHATSAPP_TEMPLATE_*`, `WHATSAPP_API_VERSION` (padrão v23.0) | Nada é enviado nem marcado como enviado; o lembrete D-1 vai por e-mail |
| **E-mail** (backend Django) | Transacional (aprovação/rejeição, cancelamento, lembrete D-1 de quem não autorizou o WhatsApp, termos, fila de espera, pacote expirando, aprovação ao profissional, reset/convite de senha, contato) e marketing (aniversário, promoções) com `List-Unsubscribe` (RFC 8058) e HTML de promoção sanitizado (bleach) | `EMAIL_BACKEND` + credenciais, `DEFAULT_FROM_EMAIL` | Prod usa backend *dummy*: nada sai; reset de senha/convites recusam (aviso W003) |
| **Botão WhatsApp** (`wa.me`) | Contato, fallback do agendamento, link do termo pela recepção | `WHATSAPP_NUMERO` (tela Branding > env) | Botões escondidos (aviso W004) |
| **Web Push (VAPID)** | À profissional: novo agendamento, cliente cancelou/remarcou e o resumo diário de atendimentos sem desfecho | `WEBPUSH_VAPID_*` (contato do claim: `WEBPUSH_VAPID_CLAIMS_EMAIL` > `ADMIN_EMAIL` > e-mail da marca) | Botão de ativar escondido |
| **Feed ICS** (sem integração) | A profissional assina a própria agenda (Google, Apple, Outlook) pelo link `link_acesso` FEED_ICS — único caminho da agenda para fora (o Google Agenda por OAuth saiu na 0071) | — | — |
| **Google Maps** (só link) | "Abrir no Google Maps" na página de contato, com o endereço da Identidade visual; a página mostra uma ilustração local e nada do Google carrega até a pessoa clicar (a CSP não tem `frame-src` do Google) | `CLINIC_ADDRESS` (Identidade visual > env) | Sem endereço, o bloco do mapa some |
| **Cloudflare Turnstile** | Captcha no pedido de OTP (wizard, portal e "Meus dados"), no contato e na lista de espera (`utils/captcha.captcha_invalido`) | `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | Sem captcha (aviso W009 se o SMS for real); os formulários públicos ficam com honeypot e limites |
| **Sentry** | Erros e transações amostradas; `send_default_pii=False`, sem variáveis locais nem corpo do request, `EventScrubber` recursivo (contato e dado de saúde) e filtros em erro, transação e breadcrumb que redigem tokens de link, e-mail/CPF/telefone e o DETAIL do Postgres (`utils/pii.opcoes_sentry`) | `SENTRY_DSN`, `SENTRY_TRACES_SAMPLE_RATE` (ambiente = `SENTRY_ENVIRONMENT` > `RAILWAY_ENVIRONMENT_NAME` > `DJANGO_ENV`) | Desligado |

Mídia: o sistema não recebe upload (nenhum `FileField`/`ImageField`; sem S3/`AWS_*`). Upload futuro
= decisão antes (bucket privado, URL assinada).

---

## 8. Segurança

Detalhe de login, 2FA, OTP e tokens em [`AUTENTICACAO-E-ACESSO.md`](AUTENTICACAO-E-ACESSO.md).

- **Equipe:** e-mail + senha (mín. 10, validadores do Django), django-axes (5 falhas → bloqueio de
  1h por IP+usuário e por IP — nunca por usuário sozinho, que trancaria a dona fora com o e-mail
  público; página pt-BR 429), rate-limit no login (10/min por IP, 5/min por e-mail), **2FA TOTP
  obrigatório para ADMIN e PROFISSIONAL em prod**, conferido a cada request e falhando fechado (erro
  de banco na consulta do 2FA leva ao desafio, nunca deixa passar só com a senha).
- **Cliente:** sem senha; OTP de 6 dígitos por SMS preso ao telefone (HMAC-SHA256, 10 min, 5
  tentativas, reenvio ≥ 60 s), propósitos isolados. Anti-enumeração: o pedido de código do portal
  e do "Meus dados" responde igual exista ou não o cadastro, em no mínimo
  `OTP_LATENCIA_MINIMA_SEGUNDOS` (0,8 s, fixo no settings). Sessão do portal de 1 h presa ao
  telefone; baixar os dados exige verificação de até 10 min; sair só por POST.
- **Formulários públicos sem login** (contato, lista de espera): captcha quando configurado,
  honeypot `website` (robô descartado em silêncio) e limite por IP; o contato tem ainda o teto
  diário global (protege a cota de e-mail do reset de senha e dos avisos).
- **Headers:** CSP com nonce por request (sem `unsafe-inline` em `script-src`/`style-src`, sem
  handlers inline; `style-src-attr 'unsafe-inline'` ainda permitido), nenhum CDN (o único host
  externo de script é o Turnstile; FullCalendar e Chart.js vêm do npm no bundle do Vite),
  `frame-ancestors 'none'` (exceto o widget), HSTS 1 ano com preload,
  X-Frame-Options DENY, nosniff, COOP/CORP, Permissions-Policy.
- **Privacidade no navegador:** `/painel/`, `/profissional/`, `/meus-agendamentos/`, `/lgpd/`,
  `/api/`, `/django-admin-sv/` saem com `Cache-Control: no-store` (e views sensíveis com
  `@never_cache`); rotas com token no path usam `Referrer-Policy: no-referrer`; privadas e com token saem com `X-Robots-Tag: noindex, nofollow, noarchive` (o robots.txt só pede para não buscar); logout envia
  `Clear-Site-Data: "cache"`; o access log do gunicorn redige tokens (`LoggerSemTokens`) e o log da
  aplicação passa pelo filtro `clinica/logging_filtros.RedigirLog` (token de link, DETAIL do
  Postgres, e-mail, CPF e telefone na mensagem, no traceback e nos extras — a mesma redação do
  Sentry, `utils/pii.redigir_texto`).
- **Cron e webhooks:** segredo só em header, comparado em tempo constante
  (`utils/seguranca.segredo_confere`); `CRON_TOKEN` e `WHATSAPP_VERIFY_TOKEN` com 32+ caracteres
  (E003); cron com 30 chamadas/h por IP e corpo de erro só com o tipo da exceção e uma referência
  do log.
- **Imagem:** usuário `app` sem shell, código e estáticos do root (só leitura para o processo —
  nada em runtime escreve em `/app`), sem `curl` (o `HEALTHCHECK` usa o Python da imagem); o
  `.dockerignore` deixa fora testes, scripts de backup, docs, bancos e dumps.
- **Cookies:** HttpOnly, SameSite=Lax, Secure em prod; sessão da equipe 8h deslizantes que
  expira ao fechar o navegador.
- **IP do cliente:** `utils/seguranca.client_ip` lê `CLIENT_IP_HEADER` (prod: `HTTP_X_REAL_IP`
  do Railway; nunca X-Forwarded-For). Rate-limit, axes e auditoria usam a mesma função.
- **Entradas:** uploads ≤ 5 MB, ≤ 200 campos, IDs só com dígitos ASCII (`utils/parse.id_int`),
  células de Excel com texto forçado (anti-fórmula), JSON injetado via `json_script`.
- **Segredos e configuração:** só em env, lida e validada só em `clinica/settings` (o código lê
  `settings.X` na hora da chamada; ruff `TID251` barra `os.environ` fora dali); `DJANGO_SECRET_KEY`
  obrigatória fora de DEBUG/no Railway e, publicada, com 50+ caracteres aleatórios (E003);
  `DJANGO_ENV` inválido derruba o boot. Commit com segredo: gancho e job do CI com gitleaks (§12).

---

## 9. LGPD

### 9.1 Bases legais (política de privacidade e termo LGPD v1.0)

Fonte única: `conteudo/lgpd.TRATAMENTOS` (a seção 3 da política e a declaração do "baixar meus
dados" saem dali). Resumo:

| Tratamento | Base legal |
|---|---|
| Agendar, confirmar, remarcar, cancelar e realizar o atendimento, com os e-mails de confirmação, cancelamento e lembrete; nascimento (18 anos ou mais e segurança do procedimento); código por SMS; lista de espera e contato; programa de indicação | Execução de contrato e procedimentos preliminares (art. 7º, V); o SMS também prevenção a fraudes (art. 7º, IX) |
| CPF, CEP e endereço só para nota fiscal e contrato de pacote | Obrigação legal e execução de contrato (art. 7º, II e V) |
| Ficha de avaliação, prontuário e anotações (dado de saúde) | **Só consentimento específico e destacado (art. 11, I), revogável**; o registro feito fica pelo prazo de guarda (art. 7º VI, art. 11 II "d", art. 16) |
| Lembrete e pesquisa de satisfação por WhatsApp; promoções, novidades e aniversário por e-mail; depoimento no site | Consentimento (art. 7º, I), revogável |
| Nota e comentário da pesquisa (responder é opcional); segurança do site e prevenção de abusos | Legítimo interesse (art. 7º, IX) |
| Registros dos atendimentos, dos termos aceitos e dos consentimentos | Obrigação legal e exercício regular de direitos (art. 7º, II e VI; art. 11, II, "d") |

**Revisão jurídica antes do go-live:** o texto da política, as bases, os operadores e os prazos
(`conteudo/lgpd.py`) foram escritos a partir do que o sistema faz; a dona (de preferência com um
advogado) revisa antes do primeiro deploy de produção (§14 a, [`LGPD.md`](LGPD.md)).

### 9.2 Consentimentos e provas

- **Política de Privacidade = termo LGPD:** o texto de `conteudo/lgpd.TERMO_LGPD_CONTEUDO` é a
  v1.0 (gravada pela `0045` e pelo `seed`; a `0078` reescreveu a v1.0 sem aceite) e a página
  `/politica-de-privacidade/` mostra o texto da versão LGPD **vigente** (`VersaoTermo.lgpd_vigente`,
  filtro `texto_termo`) — o mesmo cujo SHA-256 vai na prova. Aceite obrigatório no wizard →
  `AceiteTermo` da versão vigente com IP, user-agent e SHA-256 do texto. Sem termo LGPD ativo o
  pedido online é recusado (`sem_termo_lgpd`; garanta um — §14.e). Os prazos da tabela da política
  têm de bater com `settings.RETENCAO_*` (`aranha.W016`).
- **Dado de saúde (art. 11 I):** ficha do wizard e ficha pública (`/anamnese/<token>/`) só são
  gravadas com a caixa de consentimento destacada marcada; o aceite do termo SAÚDE é a prova e o
  texto (`TEXTO_CONSENTIMENTO_SAUDE`) vai para a `LogAuditoria` com IP. **Revogável**
  (`services/consentimentos.revogar_saude`): pelo portal (origem PORTAL) ou pela ação do Django
  admin "Registrar revogação do consentimento de saúde" (motivo obrigatório, origem ADMIN) —
  grava `cliente.saude_revogada_em` e a trilha `LGPD: consentimento de saude revogado`; os alertas
  passam a mostrar a revogação; o registro feito fica pelo prazo (§9.4). Aceite SAÚDE novo
  reativa (`AceiteTermo.registrar` limpa a data). A ficha ainda em dia (12 meses) continua valendo
  no wizard depois da revogação (pendência no [`ARCHITECTURE.md`](ARCHITECTURE.md) §6.2).
- **Comunicação:** três opt-ins no wizard — promoções por e-mail (MARKETING/EMAIL), lembrete
  D-1 e aviso de vaga por WhatsApp (LEMBRETE_AGENDAMENTO/WHATSAPP) e pesquisa de satisfação por
  WhatsApp (PESQUISA_SATISFACAO/WHATSAPP) — nunca pré-marcados. Cada concessão/revogação é uma
  linha nova em `consentimento` (append-only) com o texto exibido, IP, user-agent e data; o
  estado vale pela linha mais recente (`services/consentimentos`). Desmarcar (com o estado do
  cadastro exibido) revoga; marcar de novo depois de um descadastro **volta a valer**
  (reconsentimento — o antigo `aceita_comunicacao` vetava para sempre e saiu na 0050). O WhatsApp
  de lembrete é opt-in (política da Meta): sem ele o lembrete D-1 vai por e-mail.
- **Área da cliente** ("Minhas preferências", `consentimentos.registrar_do_portal`): os mesmos
  três pares, com o estado vigente no checkbox; marcar concede (texto do agendamento), desmarcar
  revoga; origem PORTAL, com IP e user-agent; par sem mudança não grava.
- **Descadastro** (`/lgpd/unsubscribe/<token>/`): revoga marketing e pesquisa (origem
  DESCADASTRO, com IP/UA de quem confirmou), **mantém o lembrete D-1** (transacional). Cada
  e-mail de marketing leva o seu link (`link_acesso` DESCADASTRO; descartado se o envio falha).
- **"PARAR" no WhatsApp** (mensagem inteira "parar", "sair", "stop", "cancelar mensagens" ou
  "descadastrar", sem acento/caixa): revoga lembrete e pesquisa por WhatsApp do titular daquele
  telefone (origem WHATSAPP; revogar tão fácil quanto consentir — art. 8º §5), auditado, e a
  cliente recebe a confirmação com o caminho de volta (texto livre, dentro da janela de 24 h).
- **Contato e lista de espera** não pedem opt-in de marketing (o do contato saiu): o
  consentimento de promoções só existe no agendamento, onde há prova.
- **Cookies:** só essenciais (sessão, CSRF, tema). O aviso é informativo: "Entendi" grava
  `cookie_consent=essenciais` no próprio navegador; nada é registrado no servidor.
- **Painel** (ficha do cliente, "Comunicação autorizada"): a equipe vê o estado por canal e
  **revoga** a pedido da cliente; registrar uma autorização exige a justificativa (o que foi
  autorizado e como), gravada como `texto` com o autor (origem PAINEL). Django admin: histórico só
  leitura + ações "Revogar consentimentos de comunicação" (origem ADMIN) e "Registrar revogação do
  consentimento de saúde".
- **Depoimento:** só aparece no site com `autoriza_publicacao` (cliente) **e**
  `aprovado_publicacao` (equipe), nota ≥ 9 e comentário.
- **Imutabilidade:** aceite e termo já aceito não mudam (model + trigger 0046); editar o texto
  = publicar nova versão. O aceite só perde IP/user-agent na minimização LGPD (0077). Aceites
  anteriores à 0044 ficam com `conteudo_sha256` vazio.

### 9.3 Direitos do titular

| Direito | Como |
|---|---|
| Confirmação, acesso, portabilidade | **"Baixar meus dados"** no portal (código de até 10 min) ou `/lgpd/meus-dados/` (telefone → OTP DSAR): JSON por seção (`services/lgpd.SECOES_DSAR`) — cadastro, preferências (bloqueio, faltas, revogação de saúde), consentimentos (estado e histórico), atendimentos, NPS, aceites, pacotes, carteira, lista de espera (inclusive a anônima pelo mesmo celular), notificações — e `sobre_o_tratamento` (art. 19 II: finalidades e bases, operadores e prazos efetivos, da mesma fonte da política). **Sem a ficha de saúde** (prontuário, versões, anotações, fichas): o arquivo diz que ela sai por e-mail ou na clínica, a pedido; o export completo é `LgpdService.exportar_dados_cliente` (sem tela no painel). Resposta idêntica exista ou não o cadastro; auditado (`DSAR: exportacao de dados`) |
| Correção / exclusão | Pedido no portal (`POST /meus-agendamentos/pedido/`): trilha `LGPD: pedido de correcao` ou `de exclusao` com o prazo (15 dias) + e-mail à clínica (encarregado > `email_admin` > `ADMIN_EMAIL` > e-mail da marca) com a descrição. A equipe corrige na ficha (`/painel/clientes/<pk>/`) ou pseudonimiza no Django admin |
| Revogação de consentimento | Portal (preferências e saúde); link de descadastro; "PARAR" no WhatsApp; desmarcar no wizard; equipe na ficha do cliente (comunicação) ou no Django admin (comunicação e saúde) |
| Pseudonimização ("esquecimento") | Ação "Pseudonimizar" no Django admin (`LgpdService.esquecer_cliente`, lote tudo ou nada) e job semanal (§9.4) |
| Revisão de decisão automatizada (art. 20) | Pausa do online por faltas (o cancelamento tardio conta): o wizard dá o motivo e o caminho (WhatsApp); a recepção revisa |

**Pseudonimização** (`esquecer_cliente` → `sp_anonimizar_cliente`, art. 13 §4): troca o nome por
`[ANONIMIZADO-<pk>]` e zera CPF, e-mail, telefone, CEP, endereço, nascimento, **código de indicação
e quem indicou**; revoga os consentimentos vigentes (origem ESQUECIMENTO — o histórico de
datas/finalidades fica como prova de base legal, sem IP e user-agent); tira IP e user-agent do
aceite LGPD (e dos de SAÚDE/PROCEDIMENTO quando não há mais registro de saúde); soft delete;
apaga OTPs, a lista de espera (inclusive a inscrição anônima com o mesmo celular) e o texto das
notificações; apaga fichas que não são de atendimento REALIZADO; tira o depoimento do site e apaga o
comentário do NPS; **revoga todos os links públicos** do titular (descadastro, confirmação, NPS,
termo, ficha); pseudonimiza o nome nos textos da `LogAuditoria` e do histórico do Django admin e
apaga os detalhes das trilhas `LGPD: ...` da titular. Atendimentos, aceites, pacotes e o registro
de saúde (até o expurgo aos 20 anos) ficam ligados a esse id — pseudonimização, não anonimização.

### 9.4 Retenção

Inventário coluna a coluna em `inventario_lgpd.py`, conferido por `tests/test_inventario_lgpd.py`
contra os models, as procedures (`sp_anonimizar_cliente`, `sp_aplicar_retencao_lgpd`,
`sp_purgar_dados_expirados`) e o registro de operações do `docs/LGPD.md`: tabela ou coluna pessoal
nova sem tratamento declarado reprova o CI.

Os prazos **LGPD** (`RETENCAO_CLIENTE_INATIVO_DIAS`, `RETENCAO_CARENCIA_SOFT_DELETE_DIAS`,
`RETENCAO_ATENDIMENTO_SAUDE_DIAS`, `RETENCAO_FICHA_SEM_ATENDIMENTO_DIAS`,
`RETENCAO_PACOTE_APOS_EXPIRAR_DIAS`) são **fixos** em `settings/base.py`, sem variável de
ambiente: são os publicados na política (`conteudo/lgpd.PRAZOS_RETENCAO`), e mudar exige versão
nova do termo LGPD — o check `aranha.W016` acusa o settings diferente do texto da versão vigente.
`services/lgpd.prazos_retencao` os passa às procedures. OTP, texto das notificações e auditoria
também estão publicados na política, mas seguem por env (`RETENCAO_OTP_HORAS`,
`RETENCAO_NOTIFICACAO_DIAS`, `RETENCAO_LOG_AUDITORIA_DIAS` — mudar sem versão nova do termo gera o
W016); links e axes não aparecem no texto.

| Dado | Prazo | Mecanismo |
|---|---|---|
| Código OTP | 24 h (`RETENCAO_OTP_HORAS`) | `housekeeping` apaga |
| Sessões expiradas | — | `housekeeping` (`clearsessions`) |
| Texto das notificações | 12 meses (`RETENCAO_NOTIFICACAO_DIAS`) | `housekeeping` limpa `mensagem` |
| Log de auditoria (com o IP) | 5 anos (`RETENCAO_LOG_AUDITORIA_DIAS`, mínimo 180 dias — W013) | `housekeeping` apaga (o trigger só aceita apagar linha com 180+ dias) |
| Trilha de dados (`historico_alteracao`) | 5 anos (`RETENCAO_LOG_AUDITORIA_DIAS`); a do **prontuário** sai com o expurgo de saúde | `housekeeping` apaga (flag local `app.expurgo_autorizado` + 180 dias; fora disso o trigger barra DELETE) |
| Link público vencido ou revogado | 90 dias depois de vencer/ser revogado (`RETENCAO_LINK_ACESSO_DIAS`); feed ICS sem validade fica | `housekeeping` apaga |
| Logs do django-axes | 90 dias (`RETENCAO_AXES_LOG_DIAS`) | `housekeeping` apaga |
| **Registro de saúde** (prontuário, versões, anotações, fichas e a trilha do prontuário) | **20 anos depois do último evento de saúde** da cliente: último REALIZADO, edição/versão do prontuário, anotação ou ficha (`RETENCAO_ATENDIMENTO_SAUDE_DIAS`) | `housekeeping`, etapa **`saude`** do `sp_purgar_dados_expirados` (0077): apaga e minimiza IP/user-agent dos aceites SAÚDE/PROCEDIMENTO, registra a quantidade na `log_auditoria`; os triggers só aceitam com a flag **e** 20 anos (prazo menor no settings = etapa falha, nada sai antes) |
| Cliente sem atendimento | 5 anos (`RETENCAO_CLIENTE_INATIVO_DIAS`) | `lgpd_purgar` **pseudonimiza** — **exceto** enquanto houver registro de saúde dentro da janela de 20 anos (REALIZADO, versão do prontuário ou prontuário com conteúdo editado no período) ou pacote a menos de 5 anos do fim (`RETENCAO_PACOTE_APOS_EXPIRAR_DIAS`: cancelamento, validade ou, sem validade, a compra). Aceite de termo não retém |
| Cliente excluído (soft delete) | 30 dias (`RETENCAO_CARENCIA_SOFT_DELETE_DIAS`) | `lgpd_purgar` pseudonimiza (mesmas exceções) |
| Ficha de pedido não realizado (CANCELADO, PENDENTE vencido, convite não respondido ou vazio de horário passado) | 90 dias (`RETENCAO_FICHA_SEM_ATENDIMENTO_DIAS`) | `lgpd_purgar` apaga (a respondida de FALTOU/REAGENDADO fica) |
| Lista de espera | até a data desejada passar ou a cliente agendar o procedimento | `lgpd_purgar` apaga a vencida (inclusive anônima); `services/atendimento.ao_criar` apaga ao agendar |
| Aceites de termo | nunca apagados (prova) | IP/user-agent minimizados na pseudonimização (LGPD) e no expurgo de saúde (SAÚDE/PROCEDIMENTO) |

**Cifragem em repouso** (0060, `utils/cifragem` + `db/campos.TextoCifrado`/`JsonCifrado`): os 5
campos clínicos e as respostas do prontuário, as versões do prontuário, as anotações de sessão e as
fichas de anamnese ficam gravados cifrados (Fernet, chave de `CHAVES_CIFRAGEM` com subchave
`saude`; a subchave `segredos` fica para segredo de integração e hoje não tem campo — era o token
do Google Agenda, que saiu na 0071); a trilha `historico_alteracao` do prontuário guarda o mesmo texto cifrado. A aplicação
lê em claro; vazio continua vazio no banco. Um dump/backup sem a chave não mostra a ficha (e um
incidente com dado ininteligível pesa menos — art. 48 §3). Consequência: o banco não busca nem
filtra pelo conteúdo desses campos. Não protege contra quem tem a chave (ADMIN no painel, acesso ao
Railway).

No Postgres a escrita da retenção é das procedures (`sp_purgar_dados_expirados`,
`sp_aplicar_retencao_lgpd`, `sp_anonimizar_cliente` — §5.4), **fonte única**: não há equivalente
em Python, então no SQLite (dev sem Postgres) esquecimento, retenção e limpeza não rodam
(`docker compose up -d db` — §12).

### 9.5 Incidente (art. 48)

Passo a passo, prazos (3 dias úteis; registro por 5 anos), registro das operações (art. 37),
operadores e o relatório de impacto do dado de saúde: [`LGPD.md`](LGPD.md).

Conter (revogar tokens/sessões, `setup_2fa --force`, rotacionar `DJANGO_SECRET_KEY` — invalida
sessões —, `OTP_HMAC_KEY` e, se a chave de cifragem vazou, `CHAVES_CIFRAGEM` com
`rotacionar_chave_cifragem`), avaliar pela `LogAuditoria`, comunicar a ANPD e os titulares
afetados quando houver risco relevante.

---

## 10. Jobs periódicos

Os jobs são funções (`jobs.py`, `jobs_manutencao.py`; sem Celery). **Fonte única dos horários:
`jobs.JOB_AGENDA`** (horário de Brasília). Três portas, todas por `jobs.executar` (o job roda uma
vez, sem retry na hora, dentro do Ator dele):

- **Serviço Cron do Railway** (o caminho de produção, §13.2): `railway.cron.json` agenda
  `*/15 * * * *` e roda `python manage.py rodar_jobs_devidos`, que executa os jobs cujo horário da
  `JOB_AGENDA` caiu nos últimos 15 min — um serviço só, no lugar de um por horário. Um job que falha
  não impede os outros; no fim, exit 1 se algum falhou (o Railway marca a execução como falha).
- **Cron HTTP**: `POST /cron/run/<job>/` com o header `X-Cron-Token: $CRON_TOKEN` (nunca
  `?token=`), comparado em tempo constante, 30 chamadas/h por IP. Respostas no contrato
  `utils/respostas`: `200 {"ok": true, "job", "resultado"}`; `403` token; `404` job desconhecido
  (lista os disponíveis); `409 job_ja_executado` (job de mensagem que já rodou hoje); `429` acima
  do limite; `500 job_falhou` com **só o tipo da exceção e um `ref`** (o mesmo do log — o agendador
  guarda o corpo, e a mensagem poderia ter dado pessoal). O job roda **dentro do request** e o
  gunicorn corta em 120 s.
- **Linha de comando**: `python manage.py rodar_job <job>` (`--lista`; erro = exit 1;
  `--forcar` roda de novo no mesmo dia, depois de uma falha — pode reenviar).

**Nunca mandar duas vezes:** lembrete e NPS têm marca por destinatário (`Notificacao`); os jobs de
mensagem sem essa marca (`aniversario`, `pacote_expirando`, `nps_24h` — `jobs.JOBS_UMA_VEZ_POR_DIA`)
rodam no máximo uma vez por dia local: a linha `execucao_job` (UNIQUE job + dia) nasce antes do job,
e a 2ª chamada recebe 409 (no comando: aviso, sem rodar). Cada execução avisa o monitor
(`CRON_MONITOR_URL`: `<url>/<job>` ou `<url>/<job>/fail`).

```bash
curl -fsS -X POST -H "X-Cron-Token: $CRON_TOKEN" https://<dominio>/cron/run/<job>/
```

| Job (`<job>`) | Horário BRT (`JOB_AGENDA`) | Cron (America/Sao_Paulo) | O que faz |
|---|---|---|---|
| `emails` | a cada 15 min (:00, :15, :30, :45) | `*/15 * * * *` | Reenvia a fila de e-mail (`Notificacao` EMAIL PENDENTE vencida; até 5 tentativas / 48 h) |
| `limpeza_status` | de hora em hora (:00) | `0 * * * *` | PENDENTE a menos de 1 h do início → CANCELADO (FSM) + e-mail à cliente ("não conseguimos confirmar a tempo"), vaga para a lista de espera. Não toca nos sem desfecho (ver `sem_desfecho`) |
| `pacote_expirar` | 00:30 | `30 0 * * *` | Compras ATIVO com `data_expiracao` < hoje → EXPIRADO (`sp_expirar_pacotes`) |
| `pacote_expirando` | 07:00 | `0 7 * * *` | E-mail a quem tem pacote vencendo em 7 ou 1 dia com saldo (1×/dia) |
| `lembrete_diario` | 08:00 e 18:00 | `0 8,18 * * *` | Lembrete D-1 dos atendimentos **AGENDADO** de amanhã: WhatsApp (template `confirmacao_d1`) a quem tem LEMBRETE_AGENDAMENTO/WHATSAPP vigente, telefone e o canal configurado; senão **e-mail** (fila, com os mesmos links de confirmar/cancelar). A rodada das 18:00 pega quem marcou depois das 8h; idempotente por atendimento (WhatsApp que falhou é tentado de novo) |
| `sem_desfecho` | 08:00 | `0 8 * * *` | Push a cada profissional com "N atendimentos aguardam desfecho" (AGENDADO/CONFIRMADO que já terminaram, sem Realizado/Faltou/Cancelado); o sistema nunca marca falta sozinho |
| `aniversario` | 09:00 | `0 9 * * *` | E-mail de felicitação (sem desconto) com consentimento de marketing; 29/02 comemora em 28/02 (1×/dia) |
| `nps_24h` | 10:00 | `0 10 * * *` | WhatsApp de NPS para REALIZADO entre 24 h e 7 dias, **nunca retorno**, no máximo 1 pesquisa por cliente a cada 90 dias (`NPS_INTERVALO_DIAS`), com consentimento; até 3 tentativas (1×/dia) |
| `detrator_alerta` | 10:30 | `30 10 * * *` | E-mail de NPS ≤ 6 para `email_admin` > `ADMIN_EMAIL` > `CLINIC_EMAIL` |
| `lgpd_purgar` | dom 03:00 | `0 3 * * 0` | Pseudonimização por retenção (confirmada cliente a cliente; saúde na janela de 20 anos e pacote a menos de 5 anos do fim retêm), fichas de pedidos não realizados, lista de espera vencida (`sp_aplicar_retencao_lgpd`) |
| `housekeeping` | 04:00 | `0 4 * * *` | Sessões, OTP > 24h, texto de notificação > 12 meses, auditoria e trilha `historico_alteracao` > 5 anos (a do prontuário sai com a saúde), link público vencido/revogado > 90 dias, **registro de saúde 20 anos depois do último evento** (etapa `saude`) — `sp_purgar_dados_expirados` —, axes > 90 dias (etapas independentes; falha de uma → 500 no fim) |
| `feriados` | dia 1, 04:30 | `30 4 1 * *` | Feriados nacionais do ano atual e do seguinte (`services/feriados.carregar`) |

O que um job muda nas tabelas com trilha sai em `historico_alteracao` com origem `cron:<job>`
(HTTP) ou `comando:<job>` (serviço Cron e `rodar_job`).

O Brasil está sem horário de verão desde 2019: BRT = UTC−3 o ano todo. O disparo de promoção não é
job: sai pelo painel (Promoções > Disparar), em lotes, uma vez
([`REGRAS-DE-NEGOCIO.md`](REGRAS-DE-NEGOCIO.md) §19).

---

## 11. Variáveis de ambiente

Lista completa e comentada em [`.env.example`](../.env.example) — conferida contra o código por
`tests/infra/test_deploy.EnvExampleTests` (toda variável lida está lá e nenhuma sobra). Só os
settings (`clinica/settings`) leem a env. Em produção:

### 11.1 Obrigatórias / pré-requisito do go-live

| Variável | Propósito |
|---|---|
| `DJANGO_SECRET_KEY` | Chave do Django (sessões, reset; HMAC do OTP se não houver `OTP_HMAC_KEY`). Sem ela o boot falha fora de DEBUG; publicada, 50+ caracteres aleatórios, nunca valor de exemplo nem `django-insecure-` (E003) |
| `CHAVES_CIFRAGEM` | Chave(s) Fernet da cifragem em repouso do dado de saúde (`manage.py gerar_chave_cifragem`). **Sem ela o deploy para** (check `aranha.E001` no pre-deploy). Várias, por vírgula: a 1ª cifra, todas decifram. **Cópia offline com a dona**: sem a chave o prontuário não abre |
| `DJANGO_ENV=prod` | Carrega `settings/prod.py` (a imagem já define; vazio no Railway = `prod`; `staging` = ambiente de testes, §13.1; qualquer valor fora de `dev`/`prod`/`staging`/`test` derruba o boot, e `dev` no Railway também; `manage.py test` sempre usa `settings/test.py`) |
| `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | Domínio próprio (o `RAILWAY_PUBLIC_DOMAIN` e o host do healthcheck entram sozinhos; em prod só origem `https://` vale no CSRF) |
| `SITE_URL` | URL pública https sem barra final — links de e-mail/WhatsApp/termos/JSON-LD (W001) |
| `DATABASE_URL` | **Não vem sozinha**: variável de referência `DATABASE_URL=${{Postgres.DATABASE_URL}}` no serviço web e no serviço Cron. Sem ela o check `aranha.E002` barra o pre-deploy (antes caía em SQLite em silêncio) |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `ADMIN_NOME` | **Pré-requisito do 1º deploy**: a `0042` desativa as contas demo; o `bootstrap_admin` cria o ADMIN real. Apagar `ADMIN_PASSWORD` depois. `ADMIN_EMAIL` também é o destino do alerta de detrator (sem `email_admin` nas Configurações) e o contato do claim VAPID (sem `WEBPUSH_VAPID_CLAIMS_EMAIL`) |
| `CRON_TOKEN` | Autentica o cron HTTP `/cron/run/<job>/` (W005); 32+ caracteres (E003). O serviço Cron do Railway não precisa dele (roda o comando direto) |
| `ZENVIA_API_TOKEN`, `ZENVIA_FROM` | SMS/OTP. **Sem SMS não há agendamento online** (CTA vira WhatsApp) (W002) |
| `EMAIL_BACKEND` + chave do provedor + `DEFAULT_FROM_EMAIL` | Sem ele nada é entregue (W003). **O Railway Hobby bloqueia SMTP**: use o `django-anymail` (já no requirements), ex.: `EMAIL_BACKEND=anymail.backends.resend.EmailBackend` + `RESEND_API_KEY` (ou Brevo/SendGrid/Mailgun/Postmark — §14 i). `DEFAULT_FROM_EMAIL` = remetente de um **domínio verificado** no provedor; sem a chave ou com o remetente de exemplo, W011 e nada sai. SMTP (`EMAIL_HOST*`) só no plano Pro |
| `WHATSAPP_NUMERO` (ou tela Branding) | Botões de WhatsApp e fallback do agendamento (W004) |
| `CLINIC_EMAIL` (ou tela Branding) | Canal do titular LGPD e fallback do alerta de detrator (W007) |
| `CLINIC_RAZAO_SOCIAL`, `CLINIC_CNPJ`, `ENCARREGADO_NOME`, `ENCARREGADO_EMAIL` (ou tela Branding) | Controlador e encarregado (art. 41) na política de privacidade; vazios somem do texto |

### 11.2 Recomendadas

| Variável | Propósito |
|---|---|
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN` (32+ caracteres, E003), `WHATSAPP_APP_SECRET`, `WHATSAPP_TEMPLATE_D1`/`_NPS`/`_LISTA_ESPERA` | Lembrete D-1, NPS, aviso de vaga e o webhook (nota do NPS, "PARAR"). Sem o canal o lembrete D-1 vai por e-mail |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | Captcha no pedido de OTP; com SMS real sem captcha poucos IPs esgotam a cota global (W009) |
| `SENTRY_DSN` (+ `SENTRY_TRACES_SAMPLE_RATE`, `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE`) | Monitoramento de erros. Ambiente padrão = nome do ambiente no Railway (`RAILWAY_ENVIRONMENT_NAME`: `production`, `dev`) e só depois `DJANGO_ENV` (que a imagem fixa em `prod`); release padrão = `RAILWAY_GIT_COMMIT_SHA` |
| `WEBPUSH_VAPID_PUBLIC_KEY`, `WEBPUSH_VAPID_PRIVATE_KEY`, `WEBPUSH_VAPID_CLAIMS_EMAIL` | Push ao profissional |
| `OTP_HMAC_KEY` | Chave própria do HMAC dos códigos OTP (vazia = `DJANGO_SECRET_KEY`): rotacionar uma não mexe na outra |
| `ADMIN_2FA_OBRIGATORIO` | Sem a env: ligado em prod. `false` = válvula de emergência (W008) |
| `PROFISSIONAL_2FA_OBRIGATORIO` | Sem a env: ligado em prod (o PROFISSIONAL lê prontuário e alertas de saúde). `false` = válvula de emergência (W012) |
| `EMBED_FRAME_ANCESTORS` | Origens que podem embutir `/embed/agendar/` (vazio = qualquer `https:`) |
| `REDIS_URL` | Opcional: cache/sessão compartilhados (obrigatório se `WEB_CONCURRENCY` > 1) |
| `CRON_MONITOR_URL` | Monitor dos jobs (healthchecks.io: `https://hc-ping.com/<ping key>`): cada job avisa `<url>/<job>` ou `<url>/<job>/fail` |

### 11.3 Opcionais / ajuste fino

`CLINIC_NAME`, `CLINIC_SUBTITLE`, `CLINIC_PHONE`, `CLINIC_ADDRESS`, `CLINIC_HOURS`,
`INSTAGRAM_URL`, `THEME_COLOR` (a tela Branding vence a env) · `USE_HTTPS` ·
`USE_X_FORWARDED_HOST` (prod sempre liga) · `CLIENT_IP_HEADER` (prod: `HTTP_X_REAL_IP`) ·
`WEB_CONCURRENCY` (1), `WEB_THREADS` (4), `WEB_TIMEOUT` (120) · `SESSION_COOKIE_AGE` (28800) ·
`ADMIN_PASSWORD_RESET` · `SMS_MAX_POR_HORA` (3), `SMS_MAX_POR_IP_HORA` (10),
`SMS_MAX_GLOBAL_HORA` (60), `ZENVIA_API_URL`, `ZENVIA_WEBHOOK_SECRET` (segredo do header
`X-Zenvia-Signature`), `ZENVIA_ALLOWED_IPS` (IP/CIDR) · `OTP_TTL_SEGUNDOS` (600),
`OTP_MAX_TENTATIVAS` (5; fora de 1..10 é o erro E004 — o banco limita a 10),
`OTP_REENVIO_MINIMO_SEG` (60) · `WHATSAPP_API_URL`, `WHATSAPP_API_VERSION`,
`WHATSAPP_PHONE_NUMBER_ID` (alias) · `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`,
`EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` (SMTP, só no plano Pro) ·
`AXES_FAILURE_LIMIT` (5), `AXES_COOLOFF_TIME_HOURS` (1), `PASSWORD_RESET_TIMEOUT_SECONDS`
(3600) · `RETENCAO_OTP_HORAS`, `RETENCAO_NOTIFICACAO_DIAS`,
`RETENCAO_LOG_AUDITORIA_DIAS` (também a trilha `historico_alteracao`; os três estão publicados na
política — mudar sem versão nova do termo gera o W016), `RETENCAO_LINK_ACESSO_DIAS`
(90), `RETENCAO_AXES_LOG_DIAS` (os prazos LGPD — cliente inativa, carência, saúde, ficha, pacote —
**não** são env: ficam fixos no settings, §9.4) · `DJANGO_LOG_LEVEL` ·
`EMAIL_TIMEOUT` (10; vale também para as chamadas HTTP do anymail) · `MAILGUN_SENDER_DOMAIN`
(Mailgun; padrão = domínio do remetente) · `DB_STATEMENT_TIMEOUT_MS` (30000; teto por statement na
conexão Postgres, abaixo do `WEB_TIMEOUT`; `0` desliga; o `migrate_atomico` roda sem teto),
`DB_APPLICATION_NAME` (`shivazen`; nome da conexão no `pg_stat_activity` e no log — ex.:
`shivazen-cron` no serviço do cron, `shivazen-staging`).

Só dev/testes: `DEBUG`, `SMS_DEV_LOG_ONLY` (em prod gera W002), `DB_ENGINE`/`DB_*`,
`TEST_DATABASE_URL`, `PG_EXIGE_ICU`, `TESTE_SQLITE_MEMORIA`, `TESTE_VITE_BUILD`, `EXIGE_NAVEGADOR`,
`VARREDURA_ROTAS` (filtro opcional da varredura visual: só as rotas cujo nome contém o texto).
Só no ambiente de testes publicado (`DJANGO_ENV=staging`, §13.1): `DESTINATARIOS_TESTE`
(e-mails/celulares separados por vírgula — os únicos que recebem mensagem)
([`DESENVOLVIMENTO.md`](DESENVOLVIMENTO.md)).

**Legada — remover do Railway:** `STATIC_ROOT` (era `/tmp`; `settings/prod.py` ignora e usa
`/app/staticfiles`, preenchido no build). O código não lê mais `GOOGLE_OAUTH_*` (Google Agenda
removido), `AWS_*` (sem upload), `TWO_FACTOR_REMEMBER_COOKIE_AGE`, `ZENVIA_API_KEY` (use
`ZENVIA_API_TOKEN` + `ZENVIA_FROM`), `SECRET_KEY` (use `DJANGO_SECRET_KEY`) nem
`LGPD_RETENCAO_CLIENTE_DIAS` (o prazo é o publicado na política: `RETENCAO_CLIENTE_INATIVO_DIAS`
fixo no settings).

Fixos no settings, sem env (mudar = código + versão nova do termo, quando for prazo LGPD): os
`RETENCAO_*` LGPD do §9.4 e `OTP_LATENCIA_MINIMA_SEGUNDOS` (0,8 s; 0 em `settings/test.py`).

---

## 12. Testes e CI

- `python manage.py test aranha_estetica` — suíte Django (SQLite `.cache/db_test.sqlite3`, ~2.020
  testes fora a tag `migracoes`, em 124 arquivos, alguns em pastas por área — `tests/conta/`,
  `infra/`, `jobs/`, `painel/`, `publico/`, `servicos/`, `utils/` — roda **tudo**, inclusive as data
  migrations). `manage.py test` sempre
  carrega `clinica/settings/test.py` (única detecção de teste, em `settings/__init__.py`; é o runner
  único — pytest/pytest-django/conftest saíram, e `test_deploy_config.RunnerUnicoTests` reprova a
  volta): hash de senha MD5, SMS em modo log, avisos `aranha.W001`–`W016` silenciados (testados à
  parte em `test_deploy_config.py` e `tests/infra/test_deploy.py`), django-vite em `dev_mode` (não
  precisa do build; `TESTE_VITE_BUILD=1` usa o bundle).
- **Atalho do dia a dia**: `python manage.py test aranha_estetica --exclude-tag migracoes` (~40 s)
  pula `tests/test_migrations_dados.py` (tag `migracoes`: desce e sobe a cadeia de migrations
  dezenas de vezes); `--tag migracoes` roda só ele. `--keepdb` reaproveita o banco de teste em
  arquivo (um módulo: ~15 s → ~2 s); `--parallel` no Windows/macOS exige
  `TESTE_SQLITE_MEMORIA=1`. Antes do merge, rode tudo (o CI roda as duas partes em jobs paralelos).
- **Navegador** (Playwright + Chromium; local com `npm run build` + `TESTE_VITE_BUILD=1`, no CI com
  `EXIGE_NAVEGADOR=1` — faltou navegador ou bundle = reprova, não pula):
  `tests/test_wizard_browser.py` cobre as regras que só existem no `wizard.js`;
  `tests/painel/test_status_navegador.py`, o seletor de status, a barra do lote e o FullCalendar do
  bundle; `tests/test_crawl_visual.py` (varredura visual) abre **toda** rota pública, da equipe e da
  profissional de `tests/test_rotas.ROTAS` no papel certo, em 1366px (tema claro) e 390px (tema
  escuro), e reprova em erro HTTP (da página ou de recurso do site), erro de JS/console (inclui
  violação de CSP), rolagem lateral no celular, sintaxe de template vazando no texto e violação
  serious/critical do axe-core (WCAG 2.0/2.1 A e AA; `axe-core` é devDependency, `npm ci`). Exceção de
  terceiro só pela lista `PERMITIDOS` do teste, com o motivo (hoje vazia); `VARREDURA_ROTAS=<trecho>`
  filtra as rotas para depurar uma tela.
- **Cobertura**: modo paralelo (um arquivo `.cache/coverage.*` por execução):
  `coverage run manage.py test ... && coverage combine && coverage report`; no CI o job `cobertura`
  junta SQLite + Postgres (o código só-Postgres — procedures, paridades, LGPD — conta como testado)
  e aplica a catraca `fail_under` do `pyproject.toml` (86,9 %, uma casa; só sobe) no total.
- **Lint**: `ruff check .` com lista fixa (F, E4/E7/E9, **DTZ** — `date.today()`/`datetime.now()`
  sem fuso erram o dia entre 21h e 24h; use `utils/datas` — e **TID251**: `os.environ`/`os.getenv`
  só em `clinica/settings`, nos pontos de entrada e nas exceções listadas no `pyproject.toml`);
  `pre-commit install` liga gitleaks (segredo no que está sendo commitado), ruff, `lint-imports` e
  `makemigrations --check` antes do commit.
- **Postgres local** (`docker-compose.yml`, PG 18 com ICU): `docker compose up -d db` e
  `TEST_DATABASE_URL=postgres://shivazen:shivazen@localhost:5432/shivazen PG_EXIGE_ICU=1 python
  manage.py test aranha_estetica`. No SQLite os testes do esquecimento, da retenção, do housekeeping
  e da expiração de pacotes são **pulados** (procedures sem fallback Python — §9.4); o CI roda
  tudo no Postgres.
- `TEST_DATABASE_URL=postgres://... python manage.py test aranha_estetica` — mesma suíte no
  Postgres; `tests/test_pg_ddl.py` confere os objetos só-Postgres (EXCLUDE, CHECK regex,
  triggers, funções, views, procedures, extensões, collation — com `PG_EXIGE_ICU=1` a collation
  tem de ser ICU), `tests/test_nomes_padrao.py` lê o `pg_catalog` e reprova objeto fora do padrão
  SQL, FK sem índice, `ON DELETE` divergente do model, tabela sem `COMMENT`, tabela com
  `atualizado_em` sem `tg_<t>_atualizado_em`, índice `_like` (migration nova que recria FK pelo
  `AlterField` precisa terminar com `PadronizarNomes()`), `tests/test_pg_historico.py` prova a
  trilha (autor/IP do request, contexto limpo entre requests na mesma conexão, cron, `rodar_job`,
  redação da senha, append-only) e `tests/test_pg_paridade.py` compara cada `fn_*`/`vw_*` com o
  Python (mudou a regra no Python → atualize a função numa migration nova) e confere o efeito de
  cada `sp_*`.
- Guardas de regressão relevantes: `test_csp.py` (CSP x templates; nenhum script ou CSS de outro
  host além do Turnstile; libs do painel com versão exata no `package.json`), `test_front_pipeline.py` (Vite/cotton/webpush), `test_componentes.py` (os
  componentes cotton: nome acessível, classe mesclada, abas, menu, diálogo, KPI, tabela),
  `test_a11y_front.py` (tokens, tema escuro sem cor fixa, site sem `style=`/`<style>`/hex),
  `test_site_tags.py` (`{% foto %}` e variantes em dia), `tests/servicos/test_prova_social.py`
  (mínimos, arredondamento e cache dos números do site), `test_deploy_config.py` (checks, migrate
  atômico, runner único, pins do CI), `tests/infra/test_deploy.py` (perfis prod/staging, E001–E004,
  `.env.example` × código, Dockerfile × `railway.json`), `tests/infra/test_logs.py` (redação de
  token/PII no log), `tests/jobs/` (agenda dos jobs, 409 do 2º disparo, lembretes, NPS),
  `test_slots_disponibilidade.py` (caracterização do SlotService).

**CI** (`.github/workflows/ci.yml`, push em `main` e `dev` ou manual; sem gatilho de PR — o PR
`dev` → `main` mostra os checks do push do mesmo commit). Commit novo na `dev` cancela a execução
anterior; na `main` toda execução termina. `permissions: contents: read` e `timeout-minutes` em
todos os jobs; actions fixadas por SHA e imagens (`postgres:18`, `python:3.12-slim`,
`node:22-slim`, também no `Dockerfile` e no `docker-compose.yml`) por digest — o Dependabot
(`.github/dependabot.yml`: pip, npm, actions, docker, compose; PRs semanais na `dev`) atualiza o
pin; `tests/test_deploy_config.CiFixadoTests` reprova pin solto:

| Job | Passos |
|---|---|
| `testes` | `check`, `makemigrations --check`, `ruff check` (erros, fuso e `TID251`), `lint-imports` (camadas e áreas de views), `mypy` (services), suíte Django (SQLite, `--exclude-tag migracoes`) sob `coverage` (o arquivo vai para o job `cobertura`), `check --deploy --fail-level ERROR` com settings de prod e de staging |
| `migracoes-sqlite` | `tests/test_migrations_dados.py` (`--tag migracoes --parallel auto`) no SQLite |
| `postgres` | Postgres 18: `migrate_atomico` do zero, `bootstrap_admin`, suíte Django no Postgres sem a tag `migracoes` sob `coverage` (bloqueante; inclui conformidade, trilha e paridade) |
| `cobertura` | Depois de `testes` e `postgres`: `coverage combine` dos dois e `coverage report` com a catraca (`fail_under`) no total |
| `postgres-migracoes` | `tests/test_migrations_dados.py` no Postgres 18 (reverso/ida de cada migration, inclusive triggers/funções/views/procedures), `--parallel auto` (um `test_ci_N` por worker) |
| `front` | `npm ci`, `npm run build`, confere o manifest |
| `navegador` | `npm ci` + `npm run build` + Playwright/Chromium, com o bundle (`TESTE_VITE_BUILD=1`, `EXIGE_NAVEGADOR=1`): `tests/test_wizard_browser.py` (wizard), `tests/painel/test_status_navegador.py` (seletor de status, lote e FullCalendar do bundle) e `tests/test_crawl_visual.py` (varredura visual de toda rota pública/equipe/profissional em 1366px claro e 390px escuro: HTTP, JS/console/CSP, rolagem lateral, template vazando e axe-core serious/critical) |
| `docker` | `docker build` (buildx, camadas no cache do Actions) e o smoke do 1º deploy: Postgres 18 exigindo SSL, o `preDeployCommand` e o `startCommand` **lidos do `railway.json`**, `/healthz/` com o Host do healthcheck do Railway, `/health/` e gunicorn fora do root |
| `auditoria-deps` | `pip-audit` no `requirements.lock`, `npm audit --package-lock-only --audit-level=high` e gitleaks no histórico do push (allowlist dos dados sintéticos em `.gitleaks.toml`) — bloqueante |

---

## 13. Deploy (Railway)

- **Build** (`Dockerfile`, builder `DOCKERFILE` no `railway.json`; o Procfile/Nixpacks foi
  removido):
  1. stage `front` (`node:22-slim`): `npm ci` + `npm run build`; falha se não houver
     `static/dist/manifest.json`;
  2. stage `builder` (`python:3.12-slim`): wheels de `requirements.txt` com `-c requirements.lock`;
  3. stage `runtime`: só `libpq5` (sem `curl`), usuário de runtime `app` sem shell, código e
     estáticos copiados como root (só leitura para o processo), `DJANGO_ENV=prod`, smoke do QR SVG
     do 2FA (a imagem não tem Pillow), `collectstatic` (WhiteNoise `CompressedManifest`) +
     `check --tag staticfiles --fail-level WARNING` no build, `HEALTHCHECK` em `/healthz/` pelo
     Python da imagem. O `.dockerignore` deixa fora testes, `scripts/`, `docs/`, bancos, dumps e
     `.env`.
- **Pre-deploy** (`railway.json`): `python manage.py migrate_atomico --noinput && python manage.py
  bootstrap_admin`. Se uma migration falha, a transação inteira é desfeita e o deploy anterior
  continua no ar com o banco intacto.
- **Start**: `gunicorn clinica.wsgi` com `WEB_CONCURRENCY=1` worker × `WEB_THREADS=4`,
  timeout 120 s, access log com tokens redigidos. O `startCommand` do `railway.json` sobrescreve
  o `CMD` do Dockerfile (mudar os dois juntos). 1 réplica; restart ON_FAILURE (5×).
- **Healthcheck**: `/healthz/` (timeout 120 s) — 503 se o bundle do Vite faltar.
- **Produção**: `DEBUG` sempre falso, HTTPS + HSTS, e-mail *dummy* sem `EMAIL_BACKEND`, SMS falha
  fechado sem Zenvia, logs JSON redigidos. Sem worker de fila: jobs pelo serviço Cron (§13.2).
- O deploy de produção sai do branch `main` (configuração do serviço no Railway). O trabalho
  acontece na `dev`; a `main` é protegida no GitHub (os 6 jobs bloqueantes do CI obrigatórios,
  inclusive para admin; sem force-push) e recebe a `dev` por PR ou fast-forward.

### 13.1 Ambiente **dev** publicado (testes, sai da branch `dev`)

Primeiro deploy: um ambiente de testes na internet, igual à produção, mas sem risco de mandar
mensagem a cliente real. Perfil `DJANGO_ENV=staging` (`clinica/settings/staging.py` = `prod.py` +
travas): faixa "Ambiente de testes" no site e no painel, `robots.txt` fechado e `X-Robots-Tag:
noindex` em toda resposta, código do SMS só no log (`sms_dev_log`, com a prévia), e-mail e WhatsApp
só para `DESTINATARIOS_TESTE` (o `BackendAmbienteTeste` filtra todo e-mail, inclusive o de
recuperação de senha) e `seed --demo` liberado. `DJANGO_ENV=dev` no Railway derruba o boot.

Passo a passo (projeto do Railway reativado):

1. **Environment** `dev` no projeto (Settings › Environments) com o serviço web ligado ao repositório,
   **branch `dev`** (produção segue da `main`). O `railway.json` já traz o pre-deploy deste
   environment: `migrate_atomico`, `bootstrap_admin` e `seed --demo` (idempotente).
2. **Postgres 18** no environment: imagem `ghcr.io/railwayapp-templates/postgres-ssl:18` (nunca
   `:latest`; o check `aranha.W015` avisa outro major).
3. **Variáveis** do serviço web:
   - `DJANGO_ENV=staging`, `DATABASE_URL=${{Postgres.DATABASE_URL}}` (sem ela o check `aranha.E002`
     barra o pre-deploy — antes caía em SQLite em silêncio);
   - `DJANGO_SECRET_KEY` e `CHAVES_CIFRAGEM` **próprias** do dev (`python manage.py
     gerar_chave_cifragem`; nunca as de produção);
   - `SITE_URL=https://<app>.up.railway.app`, `CSRF_TRUSTED_ORIGINS` igual (o domínio do Railway
     entra sozinho em `ALLOWED_HOSTS`);
   - `ADMIN_EMAIL`, `ADMIN_PASSWORD` (a dona define; trocar a senha no 1º acesso), `CRON_TOKEN`;
   - `DESTINATARIOS_TESTE=<e-mail da dona>,<celular da dona>` (só eles recebem mensagem);
   - e-mail opcional: `EMAIL_BACKEND=anymail.backends.resend.EmailBackend` + `RESEND_API_KEY` com
     `DEFAULT_FROM_EMAIL=onboarding@resend.dev` (sem domínio verificado o Resend só entrega na
     caixa do dono da conta);
   - `SENTRY_DSN` opcional (o *environment* sai com o nome do ambiente no Railway, `dev`);
   - `DB_APPLICATION_NAME=shivazen-dev`.
4. **Deploy**: push na `dev` → CI verde → o Railway constrói a imagem (o `watchPatterns` ignora
   commit só de documentação), roda o pre-deploy e troca o container quando `/healthz/` responde.
   O `seed --demo` só existe no pre-deploy do environment `dev` (`railway.json` › `environments`);
   o de produção nunca semeia demonstração.
5. **Conferir**: `/healthz/` 200; a faixa amarela no topo; login da equipe (`/admin-login/`) com o
   2FA; agendar pelo site com o celular da lista e pegar o código no log (`sms_dev_log` → `preview`);
   `python manage.py check --deploy --database default` no shell do Railway.
6. **Cron** (opcional no dev): o serviço Cron do §13.2 no environment `dev`, com as variáveis do
   web deste environment (`DESTINATARIOS_TESTE` inclusive).

### 13.2 Serviço Cron (jobs)

Um segundo serviço no mesmo projeto, ligado ao mesmo repositório e branch, com o config-as-code
**`railway.cron.json`** (Settings › Config-as-code › caminho do arquivo): mesma imagem do
`Dockerfile`, *Cron Schedule* `*/15 * * * *`, comando `python manage.py rodar_jobs_devidos`,
1 réplica, sem restart (cada execução termina sozinha). A agenda do Railway é em UTC, mas o comando
compara a `JOB_AGENDA` no fuso da clínica: de 15 em 15 min ele roda o que venceu na janela (§10).

- **Variáveis**: as mesmas do serviço web (o job roda o código direto, não pelo HTTP) —
  `DATABASE_URL=${{Postgres.DATABASE_URL}}`, `DJANGO_SECRET_KEY`, `CHAVES_CIFRAGEM`, e-mail,
  WhatsApp, `SITE_URL` (links das mensagens), `CRON_MONITOR_URL`; `DB_APPLICATION_NAME=shivazen-cron`.
  Não precisa de `CRON_TOKEN` nem de domínio público.
- **Sem pre-deploy**: as migrations são só do serviço web (o `railway.cron.json` não tem
  `preDeployCommand`).
- **Falha**: exit 1 se algum job falhou (o Railway mostra a execução vermelha); cada job avisa o
  monitor (`CRON_MONITOR_URL`) com o nome dele.
- **Alternativa sem o serviço**: um agendador externo chamando o cron HTTP (§14 d).

---

## 14. Go-live / Operação (runbook)

> **Estado em 2026-10-27:** ainda não há produção. O projeto do Railway que existia (web + Postgres
> na `0026`) era só **ambiente de desenvolvimento**; está em **stand-by** (trial expirado) e não
> precisa de upgrade nem de backup. O primeiro deploy de produção cria um **banco novo**: o
> `migrate_atomico` aplica `0001`→`0078` numa transação e o `seed --force` carrega o catálogo. O
> roteiro de atualização de um banco já existente (`0026` → `0078`) fica no §14 a.1, para o caso
> de esse banco um dia ser reaproveitado.

### a) Pré-deploy

1. **Contas e acessos** — quem entra no GitHub, no Railway ou no DNS passa por cima de todo
   controle da aplicação:
   - 2FA (app autenticador ou passkey, nunca SMS) e o mínimo de membros em GitHub, Railway,
     registrador/DNS, Meta Business, Zenvia, provedor de e-mail, Sentry, cron e monitor. Faça o
     inventário (conta, dono, quem tem acesso) e revise a cada trimestre ([`LGPD.md`](LGPD.md) §6).
   - GitHub: a `main` já está protegida (checks do CI obrigatórios, inclusive para admin; sem
     force-push nem exclusão). Ligue também *Secret scanning* + *Push protection*
     (Settings > Code security).
   - Railway: *Wait for CI* no serviço `web` (deploy só depois do CI verde) e variáveis sensíveis
     como *sealed*. Postgres **sem TCP proxy público**: ligue só durante uma manutenção, desligue
     em seguida e troque a senha se ele ficou aberto.
2. **Chave de cifragem**: `python manage.py gerar_chave_cifragem` → `CHAVES_CIFRAGEM` no Railway e
   **uma cópia offline com a dona** (gerenciador de senhas + papel). Sem a chave o pre-deploy para
   (`aranha.E001`); com a chave perdida, o prontuário não abre.
3. **Variáveis no Railway** (§11): `DATABASE_URL=${{Postgres.DATABASE_URL}}` (referência — não
   vem sozinha; sem ela E002), `DJANGO_SECRET_KEY` (50+ caracteres, E003), `CHAVES_CIFRAGEM`
   (passo 2), `CRON_MONITOR_URL` (§14 d), `ADMIN_EMAIL`/`ADMIN_PASSWORD`/`ADMIN_NOME` (senha ≥ 10,
   não comum), `SITE_URL`, `ZENVIA_*`, `EMAIL_BACKEND` + chave do provedor + `DEFAULT_FROM_EMAIL`
   (domínio verificado — §14 i), `WHATSAPP_*` (`WHATSAPP_VERIFY_TOKEN` com 32+), `TURNSTILE_*`,
   `SENTRY_DSN`, `CLINIC_EMAIL`, `WHATSAPP_NUMERO` e, se for usar o cron HTTP, `CRON_TOKEN` (32+);
   **remova `STATIC_ROOT`** e as legadas do §11 (`GOOGLE_OAUTH_*`, `AWS_*`...). Postgres com a
   imagem `ghcr.io/railwayapp-templates/postgres-ssl:18` (W015 avisa outro major). Confira no
   painel do Railway que não há *start command* manual antigo (`gunicorn shivazen.wsgi`)
   competindo com o `railway.json` (config-as-code: builder DOCKERFILE, pre-deploy, healthcheck
   `/healthz/`) e crie o serviço Cron (§13.2) com as mesmas variáveis.
4. **Backup** configurado antes do primeiro cliente real (§14 h).
5. ⚠️ **Revisão jurídica da Política de Privacidade — obrigatória antes do go-live.** O texto v1.0
   em `aranha_estetica/conteudo/lgpd.py` (seções, `TRATAMENTOS`, `OPERADORES`, `PRAZOS_RETENCAO`)
   foi escrito a partir do que o sistema faz, não por advogado: **a dona da clínica (de preferência
   com um advogado) revisa e aprova**, e as correções entram no arquivo **antes** deste deploy — é
   o texto que a `0045` grava como termo LGPD no banco novo, que a página
   `/politica-de-privacidade/` mostra e cujo SHA-256 vai na prova de cada aceite. Depois do primeiro
   aceite a versão fica imutável (correção = versão nova em Painel > Termos). Prazo alterado na
   tabela tem de mudar junto em `settings.RETENCAO_*` (senão `aranha.W016`). Registre quem revisou e
   quando no [`LGPD.md`](LGPD.md).
6. PR ou fast-forward `dev` → `main` (dispara o deploy; a proteção exige o CI verde no commit).

### a.1) Só para atualizar um banco existente (`0026` → `0078`)

1. **Backup** (é o único rollback): `scripts/backup/backup_postgres.sh` (§14 h). Nunca grave o dump
   em claro dentro do repositório (o `.gitignore`/`.dockerignore` ignoram `*.dump`, mas um
   `git add -f` ou outra extensão passa).
2. **Ensaio**: restaure o backup num Postgres 18 local e rode a atualização:
   ```bash
   createdb shivazen_ensaio
   age -d -i chave-privada.txt -o /tmp/ensaio.dump <backup>.dump.age    # apague depois
   pg_restore --no-owner -d shivazen_ensaio /tmp/ensaio.dump
   DATABASE_URL=postgres://.../shivazen_ensaio DEBUG=True python manage.py migrate_atomico --noinput
   ```
   As migrations que podem **abortar** (tudo é desfeito) e o que fazer:
   - `0035` — pares de atendimentos ativos sobrepostos do mesmo profissional. Ela mesma cancela
     (com log) o PENDENTE já vencido ou o retorno PENDENTE sugerido; o resto aborta listando os
     pares → cancelar/reagendar um de cada par. Pré-checagem no schema 0026:
     ```sql
     SELECT a.id, b.id, a.profissional_id, a.status, b.status, a.data_hora_inicio, b.data_hora_inicio
     FROM atendimento a JOIN atendimento b
       ON a.profissional_id = b.profissional_id AND a.id < b.id
      AND a.status IN ('PENDENTE','AGENDADO','CONFIRMADO')
      AND b.status IN ('PENDENTE','AGENDADO','CONFIRMADO')
      AND a.data_hora_inicio < b.data_hora_fim AND b.data_hora_inicio < a.data_hora_fim;
     ```
   - `0043` — dois retornos já aprovados/realizados para a mesma origem (duplicata PENDENTE é
     cancelada sozinha). Pré-checagem no schema 0026 (coluna ainda `is_retorno`):
     ```sql
     SELECT atendimento_origem_id, array_agg(id ORDER BY id), array_agg(status ORDER BY id)
     FROM atendimento
     WHERE is_retorno AND atendimento_origem_id IS NOT NULL
       AND status IN ('PENDENTE','AGENDADO','CONFIRMADO','REALIZADO')
     GROUP BY atendimento_origem_id HAVING count(*) > 1;
     ```
   - `0048` (agenda unificada) — (1) duas janelas do expediente semanal do mesmo
     profissional/dia que se sobrepõem; (2) regra de recorrência de bloqueio fora do subconjunto
     (DAILY/WEEKLY/MONTHLY, INTERVAL, BYDAY — ordinal só em MONTHLY, -1/1..4 — ou 1 BYMONTHDAY;
     COUNT/UNTIL viram data de fim sozinhos). A mensagem lista os ids. Pré-checagem no schema 0026:
     ```sql
     SELECT a.id, b.id, a.profissional_id, a.dia_semana
       FROM disponibilidade_profissional a JOIN disponibilidade_profissional b
         ON a.profissional_id = b.profissional_id AND a.dia_semana = b.dia_semana AND a.id < b.id
        AND a.hora_inicio < b.hora_fim AND b.hora_inicio < a.hora_fim;
     SELECT id, regra_recorrencia FROM bloqueio_agenda WHERE coalesce(regra_recorrencia, '') <> '';
     ```
     A 0048 não aborta (e registra na `LogAuditoria` `migration 0048: agenda unificada`, junto
     com o mapa id antigo → id novo de exceções e feriados; bloqueios mantêm o id) por: horário
     diferente com fim ≤ início (vira FOLGA — era o efeito real), folga com horas (horas
     ignoradas), fim de recorrência sem regra (descartado), COUNT/UNTIL (viram
     `data_fim_recorrencia`).
   - A `0049` (tokens em hash) não aborta: grava `sha256` de cada token em `link_acesso` — os
     links já enviados (lembrete, termo, NPS, ficha, descadastro, feed ICS) continuam abrindo; os
     de reagendar (GERENCIAR_AGENDAMENTO) migram aqui e a `0072` os apaga — e registra
     `migration 0049: links publicos em hash` com a contagem por finalidade,
     as notificações cujo token nenhuma rota aceitava (não migram) e token repetido entre tabelas
     (fica o 1º). Depois dela a URL do feed ICS não é mais exibida: quem precisar do link gera um
     novo (o antigo continua valendo até lá).
   - A `0050` (consentimentos) não aborta: cada `consent_*` vira linha em `consentimento`
     (concedido com a data/IP do aceite; `False` com data = revogação; sem data = nada) e
     `aceita_comunicacao=False` vira revogação de marketing e pesquisa **por último** (mesmo efeito
     de hoje: veta). Registra `migration 0050: consentimentos com historico` com as contagens.
   - `0051` (questionário do prontuário) — aborta se algum `formulario_anamnese` tem tipo/escopo
     fora do domínio ou escopo sem o alvo (PROCEDIMENTO sem procedimento, CATEGORIA sem categoria,
     MODALIDADE sem modalidade) → corrigir no painel. Pré-checagem no schema 0026:
     ```sql
     SELECT id, tipo, escopo, categoria, modalidade, procedimento_id FROM formulario_anamnese
      WHERE tipo NOT IN ('ANAMNESE', 'PESQUISA')
         OR escopo NOT IN ('GLOBAL', 'CATEGORIA', 'PROCEDIMENTO', 'MODALIDADE')
         OR (escopo = 'PROCEDIMENTO' AND procedimento_id IS NULL)
         OR (escopo = 'CATEGORIA' AND categoria = '')
         OR (escopo = 'MODALIDADE' AND modalidade = '');
     ```
     A configuração `prontuario_perguntas` vira o formulário "Questionário do prontuário" (tipo
     PRONTUARIO; as respostas não mudam). JSON inválido não aborta: fica na Configuração e vai
     para a `LogAuditoria` (`migration 0051: ... JSON invalido`) — recrie as perguntas em
     Painel > Anamneses. (O escopo MODALIDADE sai depois, na `0073`.)
   - A `0052` (pacote congelado) não aborta: cada compra ganha `pacote_nome` e os itens do
     catálogo **atual** do pacote, com o valor de 1 sessão = valor pago ÷ sessões; cada consumo
     aponta para o item do mesmo procedimento. Sessão consumida cujo procedimento saiu do
     catálogo vira **item reconstruído** (sessões = consumos, valor 0) e consumo acima da
     quantidade fica só registrado — `migration 0052: item reconstruido` / `consumo acima da
     quantidade` / `pacote congelado na venda` na `LogAuditoria`. Depois dela o catálogo volta a
     ser editável (só vale para vendas futuras).
   - `0053` (comissão como livro-razão) — aborta se alguma comissão tem valor ≤ 0, status fora de
     PENDENTE/PAGA/ESTORNADA ou se 2+ comissões são do mesmo atendimento (lista os ids) → ajuste
     no Django admin/SQL antes. Pré-checagem no schema 0026:
     ```sql
     SELECT id, atendimento_id, valor, status FROM movimento_comissao
      WHERE valor <= 0 OR status NOT IN ('PENDENTE', 'PAGA', 'ESTORNADA');
     SELECT atendimento_id, array_agg(id ORDER BY id) FROM movimento_comissao
      GROUP BY atendimento_id HAVING count(*) > 1;
     ```
     Cada linha vira CREDITO; PAGA ganha um PAGAMENTO na data `pago_em`; ESTORNADA ganha um
     ESTORNO com **data estimada** (a última alteração do atendimento — o sistema nunca gravou
     quando estornou). Paga e depois estornada deixa o saldo do profissional negativo (a
     descontar do próximo repasse; o painel de comissões mostra). A mesma migration tira o nome da
     indicada das observações de cashback (`Indicacao (cli #id)`), registrado em `migration 0053:
     nome da indicada retirado do ledger da carteira (LGPD)`.
   - A `0054` (nomes de tabelas e colunas) não aborta por dado: normaliza `NULL`↔`''` (texto
     `NOT NULL DEFAULT ''`; e-mail/CPF/telefone/hash vazios viram `NULL`), preenche
     `atualizado_em` nulo com `criado_em`, troca o nome das 3 tabelas renomeadas em
     `log_auditoria.tabela` e registra `migration 0054: nulos e vazios normalizados (padrao SQL)`.
     **Exige ICU** (collation `pt_br` no estado): sem ICU a `0038` só avisa, mas a `0054` aborta
     com mensagem — o Postgres do Railway tem.
   - `0055` (constraints e índices) — a 1ª operação é uma pré-checagem: aborta listando regra e
     ids se alguma linha viola um CHECK/UNIQUE novo ou os 3 CHECK jsonb da 0043 (agora
     validados). O SQL de pré-checagem (nomes de coluna novos, rodar depois da 0054 no ensaio)
     está no topo de `aranha_estetica/migrations/0055_padrao_nomes_constraints_indices.py`;
     corrigir no Django admin/SQL e refazer o ensaio.
   - As `0056`–`0058` (lógica no banco) não mexem em dado: criam a tabela `historico_alteracao`,
     os triggers (`atualizado_em`, imutabilidade, trilha), as extensões `pg_trgm`/`unaccent`, as
     funções, as 9 views e as 4 procedures. Só a **`0057` aborta**, e só se o Postgres não tiver
     `pg_trgm`/`unaccent` (ou o usuário não puder criá-las — no Railway ele é dono do banco e as
     duas são *trusted*). Pré-checagem:
     ```sql
     SELECT name, default_version, installed_version FROM pg_available_extensions
      WHERE name IN ('btree_gist', 'pg_trgm', 'unaccent');      -- 3 linhas
     ```
     Os triggers de trilha nascem depois de todo DML das migrations anteriores: logo depois do
     upgrade `SELECT count(*) FROM historico_alteracao` = 0 (no ensaio também).
   - A `0059` (trilhas append-only) não mexe em dado. A `0060` cifra o dado de saúde existente
     (inclusive na trilha) e **aborta sem `CHAVES_CIFRAGEM`** em prod: defina a chave antes (§14 a, passo 2).
   - A `0061` só acrescenta a fila de e-mail na `notificacao` (2 colunas + índice parcial); sem DML.
   - A `0062` (habilitação obrigatória) não aborta: procedimento ativo sem nenhuma habilitação
     ganha a de todos os profissionais ativos (o mesmo conjunto que o atendia até ali). A `0063`
     troca o UNIQUE de crédito de comissão pela validação no trigger (1 crédito **ativo** por
     atendimento) e a `0064` só ajusta rótulos; sem DML.
   - `0065` (financeiro por competência) — preenche `cancelado_em` = `atualizado_em` das compras
     já CANCELADAS e **aborta** se houver reembolso em compra não cancelada (lista os ids).
     Pré-checagem: `SELECT id, status, valor_reembolsado FROM compra_pacote WHERE valor_reembolsado
     > 0 AND status <> 'CANCELADO';`. A `0066` (snapshot da comissão, `tg_regra_comissao_validar`,
     `vw_comissao`) não mexe em dado: créditos antigos ficam com `valor_base_calculo` NULL.
   - `0067` (origens da carteira) — **aborta** se algum `movimento_carteira` tem origem
     VALE_PRESENTE ou OUTRO (ledger imutável, sem conversão). Pré-checagem: `SELECT id, origem FROM
     movimento_carteira WHERE origem IN ('VALE_PRESENTE', 'OUTRO');`.
   - A `0068` apaga as linhas `promocao_<pk>_envio_cursor` da `configuracao` (o cursor passa para
     a `promocao`; disparo em andamento recomeça só se alguém disparar de novo, com a confirmação
     de reenvio); a `0069` acrescenta a origem WHATSAPP ao CHECK do consentimento; a `0070` cria a
     `execucao_job`. Nenhuma aborta.
   - A `0071` (Google Agenda removido) não aborta: exceção de origem GCAL vira MANUAL (motivo
     mantido; continua bloqueando a agenda; ids na `LogAuditoria`) e saem `origem_ref` e as 4
     colunas `gcal_*` do profissional (o token cifrado some junto).
   - `0072` (papéis e links) — **aborta** se existir usuário RECEPCAO (defina o papel de cada um
     ou desative). Apaga os links legados GERENCIAR_AGENDAMENTO (ids na `LogAuditoria`) e a coluna
     `link_acesso.atendimento_id`; recria `fn_link_acesso_imutavel` e `sp_anonimizar_cliente` sem
     ela. Pré-checagem: `SELECT id, email FROM usuario WHERE papel = 'RECEPCAO';`.
   - `0073` (modalidade e notificações sem produtor) — **aborta** se alguma notificação
     LEMBRETE_2H/PESQUISA tem resposta da cliente; as demais desses tipos são apagadas. Ficha
     MODALIDADE presencial/híbrida vira GLOBAL; online vira GLOBAL inativa; procedimento online/
     híbrido passa a presencial (ids na `LogAuditoria`). Pré-checagem (no topo da migration):
     ```sql
     SELECT id, tipo, resposta FROM notificacao WHERE tipo IN ('LEMBRETE_2H', 'PESQUISA');
     SELECT id, modalidade, eh_ativo FROM formulario WHERE escopo = 'MODALIDADE';
     SELECT id, modalidade, url_videoconferencia FROM procedimento
      WHERE modalidade <> 'PRESENCIAL' OR url_videoconferencia <> '';
     ```
   - A `0074` não aborta: tira `rg`, `profissao` e `url_foto` da `cliente` (o valor some com a
     coluna — exporte antes se a dona quiser guardar algo) e recria `sp_anonimizar_cliente` sem elas.
   - A `0075` não aborta: acrescenta a origem PORTAL ao CHECK do consentimento e a coluna
     `cliente.saude_revogada_em` (vazia); sem DML.
   - `0076` (lista de espera sem cadastro) — converte `eh_notificado` em `ultimo_aviso_em` (avisada =
     `atualizado_em`), troca o UNIQUE "pendente" por um por pessoa + procedimento + dia e **aborta**
     se a mesma cliente tem duas inscrições no mesmo procedimento e dia (o UNIQUE novo não tem mais
     a condição "não notificado"; a mensagem lista os ids) → apague as duplicadas (fica a mais
     recente). Pré-checagem (nomes atuais, no ensaio antes da 0076):
     ```sql
     SELECT cliente_id, procedimento_id, data_desejada, array_agg(id ORDER BY id)
       FROM lista_espera GROUP BY 1, 2, 3 HAVING count(*) > 1;
     ```
   - A `0077` (retenção de saúde e pacote, pseudonimização completa) não mexe em dado: só Postgres,
     recria `fn_clientes_para_anonimizar`, `sp_aplicar_retencao_lgpd` e `sp_purgar_dados_expirados`
     com assinatura nova (DROP + CREATE), `sp_anonimizar_cliente`, os triggers de imutabilidade do
     aceite, da versão do prontuário e da anotação (`fn_anotacao_sessao_imutavel` nova) e
     `fn_registrar_historico`. O primeiro `housekeeping` depois dela já roda a etapa `saude` (num
     banco com registro de 20+ anos, expurga — confira o resumo do job).
   - `0078` (política v1.0 completa) — reescreve o texto da v1.0 LGPD com a política de
     `conteudo/lgpd.py` (num banco novo a `0045` já gravou esse texto e ela não muda nada) e
     **aborta** se essa v1.0 já tem aceite com texto diferente (o texto aceito é prova; a mensagem
     lista os ids). Nesse caso a política nova precisa entrar como versão nova — o roteiro não
     prevê isso (ver o resultado do ensaio antes de seguir). Pré-checagem:
     ```sql
     SELECT t.id, count(a.id) AS aceites FROM versao_termo t
       JOIN aceite_termo a ON a.versao_termo_id = t.id
      WHERE t.tipo = 'LGPD' AND t.procedimento_id IS NULL AND t.versao = '1.0' GROUP BY t.id;
     ```
   - A `0034` não aborta: normaliza telefone/CPF/e-mail, tira duplicatas (o cliente com mais
     atendimentos fica com o valor; os outros ficam ativos com o campo nulo) e registra cada caso
     na `LogAuditoria` (`migration 0034: ...`, com `telefone_original`) para mesclagem manual;
     promoção fora do CHECK é **desativada**.
### b) O que o deploy faz

1. Build da imagem (§13) — falha de front, collectstatic ou check derruba o build, nada muda.
2. Pre-deploy: `migrate_atomico` (`0001`→`0078` no banco novo, ou `0027`→`0078` no §14 a.1, numa
   transação; `lock_timeout` 5 s — se o deploy antigo segurar lock, falha e é só refazer) e `bootstrap_admin` (cria/reativa o ADMIN).
   Leia o log do pre-deploy: avisos `aranha.W0xx`, `0042: ATENCAO ...` e
   `bootstrap_admin: ERRO — painel sem administrador ativo` indicam env faltando (os checks
   rodam **antes** das migrations: W006/W010 ali podem ser o banco antigo — confira no passo c).
   Se uma migration falhar, o log termina com `migrate_atomico: FALHOU - transacao desfeita;
   banco continua em aranha_estetica.<ultima aplicada>` (ex.: `0026_otpcode_canal_default_sms`):
   nada foi alterado e o deploy antigo segue no ar — corrija (ensaio do §14 a.1) e refaça.
3. Container novo sobe; só recebe tráfego depois de `/healthz/` responder 200.
4. Até a troca, o código antigo roda contra o schema novo por alguns segundos (erros pontuais
   esperados — por isso o horário sem movimento).

### c) Depois do deploy — verificação

```bash
curl -fsS https://<dominio>/healthz/                  # {"status": "alive"}
curl -fsS https://<dominio>/health/                   # db/cache/front = true
railway ssh   # shell no container:
python manage.py check --database default             # lista W001–W016 / E001–E004 pendentes
python manage.py sendtestemail voce@exemplo.com       # e-mail real pelo provedor (§14 i)
python manage.py showmigrations aranha_estetica | tail -3   # [X] 0078_termo_lgpd_v1_completo
python manage.py rodar_jobs_devidos --somente emails  # o mesmo comando do serviço Cron
```

Trilha funcionando: edite um prontuário de teste pelo painel e confira (`railway connect` no
Postgres):

```sql
SELECT criado_em, tabela, registro_pk, operacao, colunas_alteradas, usuario_autor_id, ip_origem, origem
  FROM historico_alteracao ORDER BY id DESC LIMIT 5;       -- autor = você, origem = 'web', IP real
SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE 'tg%';   -- 47
```

### d) Jobs periódicos

- **Serviço Cron do Railway** (o caminho padrão): um serviço só, com o `railway.cron.json`
  (`*/15 * * * *` → `python manage.py rodar_jobs_devidos`) e as mesmas variáveis do web — passo a
  passo no §13.2. Os horários de cada job ficam na `jobs.JOB_AGENDA` (§10); mudar horário = mudar
  o código, não o agendador. Confira na aba de execuções do serviço (exit 1 = algum job falhou;
  o log diz qual).
- **Alternativa: cron externo pelo HTTP** (ex.: cron-job.org) — um agendamento por job da
  tabela do §10: `POST /cron/run/<job>/`, header customizado `X-Cron-Token`, fuso
  `America/Sao_Paulo` (coluna "Cron"), timeout alto e alerta de falha por e-mail (500 = job com
  erro; 409 = job de mensagem que já rodou hoje, não é falha). Escolha um dos dois caminhos: com
  os dois, cada job roda duas vezes (os de mensagem sem marca recebem 409 na 2ª; os demais repetem
  o trabalho).

Com `CRON_MONITOR_URL`, crie no monitor um check por job com o **mesmo nome do job** e o horário
da `JOB_AGENDA`: ele alerta quando o job não roda (serviço parado, token trocado, site fora) ou
falha. Para rodar um job à mão no shell: `python manage.py rodar_job <job>` (`--forcar` depois de
uma falha no dia).

### e) Primeiro acesso

1. Entre em `/admin-login/` com `ADMIN_EMAIL`/`ADMIN_PASSWORD`. O painel **obriga o cadastro do
   2FA**: escaneie o QR em `/painel/seguranca/2fa/` e confirme o código. Na mesma tela, gere os
   **códigos de backup** (10, uso único, mostrados uma vez) e guarde-os fora do celular. Cada
   profissional faz o mesmo no primeiro login (o 2FA dele também é obrigatório em prod).
2. **Remova `ADMIN_PASSWORD` do Railway** (fica em texto puro; o comando não faz nada sem ela).
3. Rode `python manage.py axes_reset` (limpa bloqueios de login herdados do deploy antigo).
4. **Painel > Identidade visual** (Branding): telefone, WhatsApp (DDI+DDD+número), e-mail de contato, endereço e
   horário reais — a tela vence a env; campo vazio esconde o item no site.
5. **Painel > Termos**: confira que o **termo LGPD v1.0** ativo (gravado pela `0045`: a
   Política de Privacidade completa de `conteudo/lgpd.py`) é o texto **já revisado** no passo a.5
   e que `/politica-de-privacidade/` mostra a versão e a vigência com a razão social, o CNPJ e o
   encarregado da Identidade visual (passo 4). Depois do primeiro aceite ele fica imutável —
   correções viram nova versão. Crie os termos de PROCEDIMENTO necessários.
6. Confira Regras operacionais (`email_admin` do alerta de detrator, faltas para pausar o online,
   anos de experiência do site — em branco, o site não mostra), profissionais (expediente,
   `max_advance_dias`), procedimentos/preços e a `LogAuditoria` com `migration 0034:` (duplicatas
   a mesclar e telefones inválidos a corrigir).
7. Faça um agendamento real de ponta a ponta (SMS chega, e-mail sai, aprovação no painel).

Sem acesso ao painel (2FA perdido, lockout): `railway ssh` → `python manage.py setup_2fa <email>
--force` (novo QR ASCII + códigos) e/ou `python manage.py axes_reset`. Emergência extrema:
`ADMIN_2FA_OBRIGATORIO=false` + redeploy (gera W008; volte assim que possível).

### f) System checks (`aranha.W0xx` / `aranha.E00x`)

Aparecem no log de todo `manage.py` fora de DEBUG (inclusive no pre-deploy). Os que consultam o
banco (W004/W007 via Branding, W006, W010, W015, W016) só rodam com `check --database default` ou no
`migrate` (antes das migrations). Os **erros** E001–E003 só valem nos perfis publicados
(prod/staging) e **barram o `migrate_atomico` do pre-deploy** (o deploy anterior segue no ar); o
E004 vale em qualquer perfil.

| Check | Significa | Como resolver |
|---|---|---|
| `W001` | `SITE_URL` não é https público | `SITE_URL=https://<dominio>` |
| `W002` | SMS sem provedor (ou `SMS_DEV_LOG_ONLY=true` em prod) — OTP falha | `ZENVIA_API_TOKEN` + `ZENVIA_FROM`; remover `SMS_DEV_LOG_ONLY` |
| `W003` | Backend de e-mail que não entrega (dummy/console/arquivo/locmem, `anymail.backends.test`) | `EMAIL_BACKEND` do anymail + chave do provedor (§14 i); SMTP só no plano Pro |
| `W004` | WhatsApp vazio/inválido — botões e fallback ocultos | Branding ou `WHATSAPP_NUMERO` |
| `W005` | `CRON_TOKEN` ausente — o cron HTTP `/cron/run/<job>/` recusa tudo (403). O serviço Cron (§13.2) roda os jobs sem ele (o texto do aviso ainda diz que nenhum job roda) | Definir se usar o cron HTTP; com o serviço Cron, o aviso pode ficar |
| `W006` | Nenhum ADMIN ativo com senha — ninguém entra no painel | `ADMIN_EMAIL`/`ADMIN_PASSWORD` + redeploy |
| `W007` | `CLINIC_EMAIL` vazio — sem canal do titular LGPD | Branding ou env |
| `W008` | `ADMIN_2FA_OBRIGATORIO=false` em prod | Remover a env |
| `W009` | SMS real sem Turnstile — cota global de SMS vulnerável | `TURNSTILE_SITE_KEY` + `TURNSTILE_SECRET_KEY` |
| `W010` | Sem versão ativa do termo LGPD e/ou SAÚDE — sem LGPD o agendamento online recusa confirmar; sem SAÚDE o consentimento art. 11 fica sem prova | Painel > Termos: publicar/reativar a versão (a `0045`/`0047` criam as v1.0 se não houver ativa) |
| `W011` | Backend anymail sem a chave do provedor (ou de provedor não suportado pelos settings), ou `DEFAULT_FROM_EMAIL` de exemplo — nada é entregue | Chave do provedor no Railway; `DEFAULT_FROM_EMAIL` do domínio verificado (§14 i) |
| `W012` | `PROFISSIONAL_2FA_OBRIGATORIO=false` em prod — profissional lê dado de saúde só com a senha | Remover a env; profissional sem celular usa códigos de backup impressos |
| `W013` | `RETENCAO_LOG_AUDITORIA_DIAS` abaixo de 180 — o Postgres recusa a purga da trilha | 180 ou mais (padrão 1825; Marco Civil: 6 meses) |
| `W014` | Só no dev (`DEBUG=True`): Python ou pacote local diferente do CI/imagem (3.12 + `requirements.lock`) | venv 3.12 + `pip install -r requirements-dev.txt -c requirements.lock` ([`DESENVOLVIMENTO.md`](DESENVOLVIMENTO.md)) |
| `W015` | Postgres fora do major 18 (CI, paridades e procedures só são validados na 18) | Serviço com a imagem `ghcr.io/railwayapp-templates/postgres-ssl:18` (nunca `:latest`) |
| `W016` | Prazo de `settings.RETENCAO_*` (LGPD) diferente do publicado no termo LGPD **vigente** — a cliente aceitou o prazo do texto, a procedure aplicaria o do settings | Volte o settings ao prazo publicado ou publique versão nova do termo LGPD (Painel > Termos) com a tabela de prazos de `conteudo/lgpd.py` atualizada |
| `E001` (**erro**) | Perfil publicado sem `CHAVES_CIFRAGEM` válida | `CHAVES_CIFRAGEM=<manage.py gerar_chave_cifragem>` + cópia offline |
| `E002` (**erro**) | Perfil publicado sem Postgres (`DATABASE_URL` ausente: cairia no SQLite, sem procedures/triggers e apagado a cada deploy) | `DATABASE_URL=${{Postgres.DATABASE_URL}}` no serviço |
| `E003` (**erro**) | Segredo fraco ou de exemplo: `DJANGO_SECRET_KEY` com menos de 50 caracteres (ou < 5 distintos, `django-insecure-`, valor de exemplo); `CRON_TOKEN`/`WHATSAPP_VERIFY_TOKEN` definidos com menos de 32 | Gerar com `get_random_secret_key()` / `secrets.token_urlsafe(32)` e atualizar também quem envia o header |
| `E004` (**erro**) | `OTP_MAX_TENTATIVAS` fora de 1..10 (o CHECK do banco limita a 10: o 11º erro viraria 500) | Padrão 5 ou até 10 |

Build: `django_vite.W001` (manifest ausente) reprova o `check --tag staticfiles` do Dockerfile.

### g) Rollback

**Rollback = redeploy do anterior + restaurar o backup mais recente.** Os *reverse* das migrations
não recuperam dados (renomeações, dedup da 0034, contas demo desativadas, termo criado, triggers).
Não use `migrate aranha_estetica <anterior>`.

1. Railway → Deployments → *Redeploy* do último deployment bom.
2. No banco atual (se ele ainda abre), liste quem foi pseudonimizado depois do backup:
   `python manage.py reaplicar_esquecimentos --listar --desde <data do backup> > esquecidos.json`.
3. Teste o backup num banco descartável (`scripts/backup/testar_restauracao.sh`) e restaure:
   decifre com a chave privada num arquivo temporário (`age -d -i chave.txt -o /tmp/r.dump
   <backup>.dump.age`), `pg_restore --clean --if-exists --no-owner --dbname="$DATABASE_URL"
   /tmp/r.dump` e apague o `/tmp/r.dump`.
4. `python manage.py reaplicar_esquecimentos esquecidos.json` — quem pediu o esquecimento depois do
   backup não pode voltar identificado.
5. Confira `/health/` e um login. O que foi gravado entre o backup e o rollback se perde.

### h) Operação contínua

- **SQL manual / BI**: use os nomes do banco (`eh_ativo`, `pacote_item`, `resposta_formulario`...;
  `\d+ <tabela>` mostra o `COMMENT` de tabela e colunas). Os atributos Python seguem os antigos.
- **Migration nova que mexe numa FK** (`AlterField` de tipo/`null`/destino): o Django recria a FK
  com nome de hash e sem `ON DELETE` — termine a migration com `PadronizarNomes()`
  (`aranha_estetica/db/operacoes.py`); o `tests/test_nomes_padrao.py` reprova se esquecer.
- **Tabela nova com `atualizado_em`**: crie o `tg_<t>_atualizado_em` (`EXECUTE FUNCTION
  fn_definir_atualizado_em()`) na mesma migration — o teste de conformidade reprova sem ele. Tabela
  sensível nova entra na trilha com `tg_<t>_historico` (`fn_registrar_historico('atualizado_em',
  '<colunas a redigir>')`).
- **Regra mudou no Python** (slots, saldo de pacote, NPS, comissão, consentimento): o
  `tests/test_pg_paridade.py` reprova no CI Postgres até a função de leitura ser atualizada —
  módulo novo em `migrations/sql/` + migration com `CREATE OR REPLACE`. **Retenção, pseudonimização,
  limpeza e expiração de pacotes** só existem nas procedures: mudar a regra = migration nova com a
  procedure + ajuste do teste de efeito (`test_lgpd_esquecimento`, `test_pg_paridade`).
- **Quem mudou o quê**: `historico_alteracao` (Django admin, só leitura e auditado, ou SQL) —
  `transacao_ref` agrupa o que saiu do mesmo request; SQL manual sai sem autor (origem `''`). Para
  identificar uma correção manual, rode antes `SELECT set_config('app.origem', 'manual:<seu nome>',
  false);` na sessão do `psql`.
- **BI / relatório**: leia as views `vw_*` (§5.4); se um dia houver acesso de BI direto ao banco,
  crie uma role só-leitura com `GRANT SELECT` nas views. **PgBouncer em modo transação** quebra o
  contexto da trilha (GUC de sessão) — o Railway não usa; se entrar, trocar para
  `ATOMIC_REQUESTS` + `SET LOCAL`.
- **Link do ICS vazado/perdido**: Painel > Profissionais > "Novo link"/"Gerar link" (ou o
  próprio profissional em Minha agenda > "Link do calendário"): mostra a URL uma única vez (o
  banco guarda só o hash); o antigo para de funcionar; auditado.
- **Escalar**: mais de 1 worker gunicorn exige `REDIS_URL` (rate-limit, axes, lock de slot e quota
  de SMS são por processo no LocMem).
- **Consulta lenta / índice novo**: meça antes. Uma vez no Postgres do Railway (superusuário):
  `ALTER SYSTEM SET shared_preload_libraries = 'pg_stat_statements';` + reiniciar o serviço +
  `CREATE EXTENSION IF NOT EXISTS pg_stat_statements;` (opcional: `ALTER SYSTEM SET
  log_min_duration_statement = '500ms'` e `log_line_prefix` com `%a` — o `application_name` diz se
  foi web ou cron). Depois `python manage.py consultas_lentas` (top por tempo total; `--ordem
  media`), crie o índice numa migration e `--zerar` para comparar. Toda conexão já sai com
  `statement_timeout` de 30 s (`DB_STATEMENT_TIMEOUT_MS`): query fora de controle cai sozinha e
  aparece no Sentry/log em vez de segurar worker e locks. Filtro por dia em `DateTimeField` é
  sempre intervalo (`utils/datas.dias_locais`), nunca `campo__date` (que não usa o índice); lista
  com alerta de saúde por linha usa `utils/saude.alertas_em_lote` (3 consultas para a página toda;
  `tests/test_consultas_por_tela.py` reprova N+1 no painel e nos prontuários).
- **Ambiente de testes (dev publicado)**: roteiro no §13.1. **Nunca** dados reais de cliente
  (`seed --demo` ou dump anonimizado). Serve para ensaiar o go-live (§14 a) e cada migration nova num
  banco que já tem dados antes da `main`.
- **Backups** (fora do Railway e cifrados): `scripts/backup/backup_postgres.sh` todo dia (serviço
  Cron do Railway com `postgresql-client`, `age` e `rclone`, ou outra máquina) para um bucket com
  versionamento/*object lock*. Lá fica só a chave **pública** do age; a privada fica offline com a
  dona. Retenção sugerida: 30 diários + 12 mensais (declarar em [`LGPD.md`](LGPD.md) §3). **Todo
  mês**, `scripts/backup/testar_restauracao.sh` num banco descartável. Rotacionou a
  `CHAVES_CIFRAGEM`? Guarde a antiga enquanto existir backup feito com ela.
- **Monitoramento**: `SENTRY_DSN` (ambiente e release preenchidos sozinhos; filtros de PII em
  `utils/pii.opcoes_sentry`), `CRON_MONITOR_URL` (§14 d) e um monitor de uptime externo
  (healthchecks.io, UptimeRobot...) em `/healthz/` a cada 5 min.

### i) E-mail em produção (provedor HTTP)

O plano Hobby do Railway bloqueia SMTP de saída: sem um provedor HTTP **nenhum** e-mail sai
(reset de senha, convites, confirmações, termos, contato, marketing). O `django-anymail` já vem
no requirements; `settings/base.py` liga o app e monta `ANYMAIL` quando `EMAIL_BACKEND` é
`anymail.backends.*`, lendo da env só as chaves presentes:

| Provedor | `EMAIL_BACKEND` | Chave |
|---|---|---|
| Resend (sugerido) | `anymail.backends.resend.EmailBackend` | `RESEND_API_KEY` |
| Brevo | `anymail.backends.brevo.EmailBackend` | `BREVO_API_KEY` |
| SendGrid | `anymail.backends.sendgrid.EmailBackend` | `SENDGRID_API_KEY` |
| Mailgun | `anymail.backends.mailgun.EmailBackend` | `MAILGUN_API_KEY` (+ `MAILGUN_SENDER_DOMAIN`) |
| Postmark | `anymail.backends.postmark.EmailBackend` | `POSTMARK_SERVER_TOKEN` |

1. Crie a conta no provedor e **verifique o domínio** da clínica no DNS: SPF terminando em
   `-all`, DKIM e **DMARC** (comece com `p=quarantine; rua=mailto:<e-mail da clínica>` e passe para
   `p=reject` quando os relatórios estiverem limpos — os e-mails levam links de termo, descadastro e
   senha, alvo fácil de phishing). Se o registrador permitir, registro CAA e DNSSEC. Sem domínio
   verificado o provedor recusa o envio.
2. Gere a chave de API (permissão só de envio).
3. No Railway: `EMAIL_BACKEND=anymail.backends.resend.EmailBackend`, `RESEND_API_KEY=re_...` e
   `DEFAULT_FROM_EMAIL=Jaqueline Aranha Estética <contato@<dominio verificado>>`. Redeploy.
4. Confira: `python manage.py check --database default` sem W003/W011 e
   `python manage.py sendtestemail voce@exemplo.com` (erro do provedor aparece no traceback).
5. Em operação, falhas ficam no log como `email_falha_envio` (erro do provedor: o envio conta
   como não feito e o fluxo segue) ou `email_nao_configurado`. Timeout da API = `EMAIL_TIMEOUT`.

---

## 15. Convenções de código

- Regra de negócio em `services/`/`utils/`, não em view; mudança de status só pela FSM.
- Camadas `views > services > models` (para baixo pode, para cima não) e nada importa `views`
  fora dela; `utils` não importa `services`; as áreas de views (`publico`, `painel`,
  `profissional`, `conta`, `infra`) não se importam — helper de view compartilhado vai para
  `views/comum.py`, regra para `services/`: `lint-imports` no CI (`pyproject.toml
  [tool.importlinter]`; exceção = inversão consciente listada lá).
- Configuração por ambiente só em `clinica/settings` (lida e validada no boot); o código lê
  `settings.X` na hora da chamada (`override_settings` funciona nos testes). O ruff `TID251` barra
  `os.environ`/`os.getenv` fora dali (exceções no `pyproject.toml`); variável nova entra também no
  `.env.example` (`EnvExampleTests`).
- Resposta JSON de view = `utils/respostas` (`json_ok`/`json_erro`/`json_recusa`); telefone =
  `utils/telefone` (normalizar, validar, E.164, máscara), fonte única da forma canônica.
- Entrada de formulário do painel = Django Form (`forms/`), não `request.POST.get` campo a campo;
  erro vira `messages.error` por `forms.painel.avisar_erros`.
- Recusa de regra = `excecoes.RegraDeNegocio` com mensagem pronta para a tela (nunca `None`/
  `False`/exceção privada da view); horário de atendimento só por `services/agenda`.
- Toda escrita multi-tabela em `transaction.atomic`; I/O externo (e-mail, WhatsApp, push) só no
  `on_commit`, best-effort.
- Datas "hoje/amanhã" sempre no fuso da clínica (`utils/datas.hoje`), nunca `date.today()`; data/hora
  de formulário por `utils/datas.ler_data`/`ler_hora`. O ruff (`DTZ`) barra no CI e no pre-commit.
  Filtro "do dia" em `DateTimeField` = `filter(**dias_locais('campo', dia))` (intervalo que usa o
  índice), nunca `campo__date=`.
- Lista com dado por linha (alerta de saúde, contagem...): carregue em lote (`alertas_em_lote`,
  `select_related`/`annotate`), nunca uma consulta por linha — `tests/test_consultas_por_tela.py`.
- Ação sensível → `utils/auditoria.registrar_log(usuario, acao, tabela, pk, detalhes)` — registrador
  único; autor e IP vêm do `Ator` da borda (`ator.py`: middleware `web`, cron `cron:<job>`),
  `usuario`/`request` explícitos só quando o Ator não basta (sem nome de cliente
  no texto — use pk em `detalhes`).
- Templates: CSP estrita (sem `onclick`, `<script>`/`<style>` com `nonce`), lógica Alpine em
  `Alpine.data()` no `app.js` (nas telas da equipe, comportamento de componente no `painel.js`,
  vanilla), tokens de cor do `tokens.css` (nunca cor crua), sem `style="..."` (classe do
  `componentes.css`/`site.css` ou utilitário). Tela da equipe usa os componentes cotton do design
  system antes de classe solta. Lib de terceiro entra pelo `package.json` e sai no bundle do Vite —
  nunca por CDN (`test_csp` reprova).
- Copy do site em tom de estética/bem-estar (cliente, atendimento, avaliação — nunca
  "paciente/consulta médica"). Texto que também é regra (política de privacidade, FAQ) mora em
  `conteudo/` e sai das mesmas constantes/settings que o sistema aplica — nunca número escrito à mão
  no template.
- E-mail (`templates/email/`): layout em tabela; faixa com gradiente leva `bgcolor` + `background-color`
  de reserva (o Outlook ignora gradiente) e botão vai num `<td bgcolor>` com `mso-padding-alt` (o Outlook
  ignora padding em `<a>`); texto do botão escuro (`#23200f`). `test_email_texto.EmailNoOutlookTests` cobra.
- Títulos com só a 1ª maiúscula ("Lista de espera", "Perguntas frequentes"); nome de tratamento do
  catálogo e sigla ficam como são. Dinheiro sempre `{{ valor|brl }}` (R$ 1.350,00; filtro builtin,
  `utils/formatos.formatar_brl`), nunca `R$ {{ x|floatformat }}`.
- Migrations: nunca editar migration já aplicada em produção; DDL só-Postgres via `RunPython`
  com checagem de vendor; teste no Postgres (CI) antes do merge.
- Commits: Conventional Commits (`feat(scope)`, `fix(scope)`, `docs(scope)`...).
- Docs: este arquivo + requisitos (`docs/_dados_requisitos.json`, lido por
  `python docs/gerar_documentacao.py`) no mesmo PR da mudança.

---

## 16. Histórico

| Quando | Marco |
|---|---|
| 2026-05 | Auditoria inicial, CSP com nonce, split do booking |
| 2026-06 | Remodelagem do banco v2.1 (0027–0038: 50 → 33 tabelas, invariantes no banco); fundação do front (Vite/Tailwind/cotton/Alpine CSP) e migração de 100% das telas; auditoria SWE de backend (0039) |
| 2026-09-23 | Padrão SQL, fase (a): agenda 4→2 (`agenda_horario` + `agenda_excecao`, migration 0048; [`specs/padrao-sql.md`](specs/padrao-sql.md) §4.a) |
| 2026-10-02 | Padrão SQL, fase (b): tokens de link público em hash (`link_acesso`, migration 0049; portal reagenda/cancela por sessão; ICS exibido só na geração; [`specs/padrao-sql.md`](specs/padrao-sql.md) §4.b) |
| 2026-10-26 | Padrão SQL, fases (c) e (d): consentimentos com histórico append-only (`consentimento`, 0050 — sai `aceita_comunicacao`, reconsentimento vale, painel revoga) e questionário único (perguntas do prontuário = formulário PRONTUARIO, 0051; `alerta_saude` por pergunta) ([`specs/padrao-sql.md`](specs/padrao-sql.md) §4.c/§4.d) |
| 2026-10-26 | Padrão SQL, fases (e) e (f): pacote congelado na venda (`compra_pacote_item`, 0052 — catálogo volta a ser editável) e comissão como livro-razão (CREDITO/PAGAMENTO/ESTORNO, 0053 — saldo negativo visível, regra usada só desativa); nome da indicada fora do ledger da carteira; ticket médio e LTV pela mesma regra (`services/financeiro`) ([`specs/padrao-sql.md`](specs/padrao-sql.md) §4.e/§4.f) |
| 2026-10-26 | Padrão SQL, fase de nomes (N1/N2): 3 tabelas e 64 colunas renomeadas só no banco (0054 — tipos `bigint`/`char(64)`, `NULL`↔`''`, `criado_em`/`atualizado_em` completados, `db_default`, collation no estado, triggers `tg_`/`fn_`) e todas as constraints/índices no padrão (0055 — 15 índices e 39 constraints renomeados, unique de campo no `Meta`, índices das queries reais, CHECKs novos com pré-checagem, PK/FK com `ON DELETE` real, `COMMENT` em todas as tabelas); `tests/test_nomes_padrao.py` ([`specs/padrao-sql.md`](specs/padrao-sql.md) §3) |
| 2026-10-26 | Padrão SQL, fase de lógica (L1–L3): trilha de dados `historico_alteracao` por trigger com autor/IP/origem da sessão (`ContextoBancoMiddleware`, cron, Celery, `migrate_atomico`) e `atualizado_em` por trigger (0056); `pg_trgm`/`unaccent`, busca de cliente sem acento, 8 funções de leitura e 9 views com paridade testada contra o Python, NPS "meio p/ cima" (0057); 4 procedures como fonte única da retenção/LGPD (0058); data migrations em jobs paralelos do CI (tag `migracoes`) ([`specs/padrao-sql.md`](specs/padrao-sql.md) §5) |
| 2026-10-27 | Revisão, fases 3 e 4: regras de agenda num lugar só (`services/agenda`), efeitos de status explícitos (`services/atendimento`, sem signals), auditoria pelo `Ator` da borda, e-mail como fila na própria `notificacao` (0061) e saída do Celery (`jobs.py`/`rodar_job`), Django Forms no painel, inventário LGPD testado, camadas (import-linter) e mypy nos services; front com tokens/foco/tema automático, wizard com botão Continuar, entradas do Vite por tela, fontes locais, `<dialog>`, formulários acessíveis, e-mails para o Outlook, componentes cotton e PWA |
| 2026-10-28 | Revisão, fases 2 e 5: `settings/test.py` único (hash MD5: suíte de ~4 min para ~40 s; `--keepdb`), ruff com regra de fuso + pre-commit, catraca de cobertura (87 %), wizard no Chromium no CI (achou o link pré-selecionado parado na etapa 1), data migrations em paralelo (25 → 10 min), CI com pins por SHA/digest, Dependabot e `pip-audit` bloqueante, `aranha.W014` (ambiente fora do lock), [`DESENVOLVIMENTO.md`](DESENVOLVIMENTO.md); banco: alertas de saúde em lote (painel 38 → 20 consultas, prontuários 28 → 8 com 10 clientes), filtros por dia como intervalo (14 `__date`), `statement_timeout`/`application_name` na conexão, `consultas_lentas` (pg_stat_statements) e roteiro de staging |
| 2026-10-28 | Pacotes da revisão funcional (0062–0074): financeiro por competência mensal, rateio ponderado do pacote, prorrogar/cancelar compra, regras de comissão no painel, snapshot e repasse em lote, "Minhas comissões" (0065–0067); jobs com agenda única (`JOB_AGENDA`) e serviço Cron do Railway, lembrete D-1 por e-mail, push de sem desfecho, NPS 1×/90 dias, promoção de disparo único, "PARAR" no WhatsApp, trava diária `execucao_job` (0068–0070); remoções — Google Agenda, papel RECEPCAO, link legado de reagendar, modalidade/videoconferência, tipos de notificação sem produtor, RG/profissão/foto, gravação do aviso de cookies, pytest (0071–0074); borda — checks E002–E004/W015, segredos fortes, `.env.example` testado, configuração só nos settings (TID251), `utils/telefone` e `utils/respostas`, redação de logs, imagem endurecida, axes por IP, 2FA falha fechado, gitleaks/npm audit e cobertura combinada no CI |
| 2026-10-29 | Pedido online como caso de uso (`services/agendamento_online` + `forms/agendamento`, view fina; teto de 3 horários pelo site, ficha válida por 12 meses, pedido sempre da titular, vitrine em `services/precos`, "Avise-me se vagar"); equipe, catálogo e configurações com Django Forms (`forms/cadastros`), `services/catalogo`, regras operacionais em registro tipado (`models/configuracao`) e feed ICS revogado no desligamento |
| 2026-10-29 | LGPD e área da cliente (0075–0078): Política de Privacidade como fonte única (`conteudo/lgpd.py` = termo LGPD = página = declaração do DSAR, prazos fixos e `aranha.W016`), consentimento de saúde revogável, expurgo do registro de saúde aos 20 anos do último evento e pacote retido 5 anos, pseudonimização completa, export do site sem a ficha de saúde, "Meus agendamentos" com preferências, revogação, pedidos LGPD e sessão aberta no fim do wizard, cancelamento tardio conta falta, lista de espera sem cadastro (turno, aviso que mantém na fila), contato com captcha/honeypot/teto diário e mapa sem Google, FAQ das constantes; agenda do painel por casos de uso (`agendar_pela_recepcao`, `marcar_realizado`, `registrar_valor_cobrado`, lote item a item), Forms em `forms/agenda`, seletor de status sem PENDENTE nem finais e Realizado/Faltou só após o início, JS em módulos do Vite e leitura de alerta de saúde auditada |
| 2026-10-29 | Front: design system das telas da equipe (tokens do cromo café/ouro e do painel, `componentes.css` em `@layer components`, componentes cotton — botão, campo, cartão, selo, nota, KPI, vazio, ícone, menu "⋯", diálogo, tabela responsiva, abas — e `painel.js` vanilla), painel e portal da profissional sem estilo inline e em caixa de frase, agenda em tabela com o "⋯" e modais, ficha da cliente em abas, visão geral com Hoje/Amanhã e KPIs comparados, FullCalendar 6.1.21 e Chart.js 4.5.1 do npm (jsDelivr fora da CSP), varredura visual com axe-core no CI; site com números reais (`services/prova_social`, regra `anos_experiencia`; saíram o "5.0/5 · 32 avaliações" e os "5+ / 500+ / 20+" fixos), fotos responsivas (`scripts/imagens.py`, `{% foto %}`) e arte da marca, `/duvidas/` (SW v10), "Sobre a profissional", destaques do catálogo, CSS das páginas no `site.css` e ajustes de celular (etapas do wizard, "Continuar" fixo, "Seu celular", aviso de cookies em faixa) |
| 2026-09-23 | Auditoria pré-produção completa (~450 correções em ~30 commits, `b5fd681..182e2e9`): OTP preso ao telefone em todo agendamento, consentimentos LGPD/art. 11 com prova, 2FA obrigatório do ADMIN, histórico do prontuário, prova de aceite imutável, deploy por Dockerfile + `migrate_atomico`, retenção LGPD efetiva, migrations 0040–0046. Detalhe em [`ARCHITECTURE.md`](ARCHITECTURE.md#6-progresso-checkpoint) |

**Mantenedor:** Rafael Maciel · **Última atualização:** 2026-10-29
