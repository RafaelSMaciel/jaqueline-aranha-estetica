# Autenticação & Acesso — Shiva Zen

> Como o login e a identificação funcionam **hoje** (código do branch `front-fundacao`),
> o que isso protege e o que ainda falta. Cross-ref: [`PROJECT.md`](PROJECT.md) (§8 segurança,
> §14 runbook), [`REGRAS-DE-NEGOCIO.md`](REGRAS-DE-NEGOCIO.md), [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## 0. Visão geral — dois mundos de acesso

| | **Cliente** (site público) | **Equipe** (painel / portal) |
|---|---|---|
| Conta e senha | Não — sem senha | Sim — e-mail + senha |
| Identidade | **Celular** (só dígitos, 10–11; OTP só vai para celular) | E-mail (`Usuario`) |
| Prova de posse | Código OTP de 6 dígitos por **SMS** | Senha + **TOTP** (obrigatório para ADMIN) |
| Acesso sem login | Links por token (confirmar, reagendar, termo, NPS, descadastro) | — |
| Papéis | — | ADMIN, PROFISSIONAL (RECEPCAO existe, sem login) |

São sistemas separados: a cliente nunca vira `Usuario`; a equipe nunca usa o OTP por SMS.

---

## 1. Equipe

### 1.1 Papéis e onde cada um entra

`usuario.papel` (CHECK no banco; padrão PROFISSIONAL):

| Papel | Login | Área | Pode |
|---|---|---|---|
| **ADMIN** (`is_staff`) | `/admin-login/` | `/painel/` (+ `/django-admin-sv/`, `/api/`) | Tudo. `@staff_required` |
| **PROFISSIONAL** vinculado a um Profissional **ativo** | `/admin-login/` | `/profissional/` | Própria agenda (dia/semana), aprovar/rejeitar os próprios pedidos, marcar Realizado (com override auditado "Realizado sem termo aceito"), anotações de sessão, ficha/prontuário só das clientes com vínculo (§1.6). `@profissional_required`; ADMIN também passa |
| **RECEPCAO** | **sem login** | — | Papel reservado até existirem telas próprias; o painel não oferece mais criar RECEPCAO |

`usuario_login` recusa quem não é ADMIN nem PROFISSIONAL vinculado (mensagem genérica). O
`?next=` é validado antes do login (sem open redirect) e, para quem não é ADMIN, só vale dentro
de `/profissional/`. PROFISSIONAL que abre `/painel/` volta para o próprio portal.

### 1.2 Senha, bloqueio e rate-limit

- Hash PBKDF2 (padrão Django); política: mínimo 10 caracteres, não parecida com os dados do
  usuário, não comum, não só números.
- **django-axes**: 5 falhas (`AXES_FAILURE_LIMIT`) → bloqueio de 1 h (`AXES_COOLOFF_TIME_HOURS`)
  por IP + usuário; zera no sucesso; página pt-BR `axes_bloqueio.html` (status 429). Limpar:
  `python manage.py axes_reset`.
- Rate-limit do login: 10/min por IP, 5/min por e-mail, e 5 falhas/min por IP no cache.
- IP sempre por `utils/security.client_ip` (`CLIENT_IP_HEADER`; prod `X-Real-IP` do Railway).

### 1.3 2FA (TOTP)

- **Obrigatório para ADMIN** fora de DEBUG (`ADMIN_2FA_OBRIGATORIO`, padrão ligado em prod;
  `false` = válvula de emergência com aviso `aranha.W008`). **PROFISSIONAL** só com
  `PROFISSIONAL_2FA_OBRIGATORIO=true`.
- **Cadastro forçado:** ADMIN sem TOTP que faz login fica preso em `/painel/seguranca/2fa/` até
  escanear o QR (SVG, sem Pillow) e confirmar o primeiro código. O middleware confere a
  obrigatoriedade **a cada request** (sessão promovida a ADMIN depois do login, sessão antiga ou
  aberta com a válvula desligada também cai no cadastro).
- **Desafio:** quem tem TOTP confirmado passa por `/painel/seguranca/2fa/challenge/` em toda
  sessão nova (`verificar/`: 5/min por usuário). Aceita o TOTP de qualquer device confirmado e
  os **códigos de backup** gerados pelo `setup_2fa`. Sucesso → `django_otp.login()` + troca do
  id da sessão.
- **Trocar de aparelho / desativar:** exige a sessão já verificada **e** o código atual. ADMIN
  que remove o 2FA volta direto para o cadastro.
- **Onde vale:** `/painel/`, `/profissional/`, `/api/`, `/django-admin-sv/`, `/webpush/subscribe|unsubscribe/`
  (em `/api/` e `/webpush/` a resposta é 403 JSON). O Django admin usa o `AdminSiteOTPRequired`:
  só abre depois do desafio do painel.
- **Rotas do django-two-factor (`/account/*`) não são publicadas** — eram uma 2ª tela de login
  que deixava uma sessão só com senha cadastrar TOTP e entrar no admin.
- **Recuperação** (2FA perdido): `railway ssh` → `python manage.py setup_2fa <email> --force`
  (apaga todos os devices, cria TOTP novo, imprime QR ASCII e 10 códigos de backup; auditado).
- O Django admin nunca mostra semente/QR/códigos de outro usuário
  (`OTP_ADMIN_HIDE_SENSITIVE_DATA`).

### 1.4 Sessão e logout

- Produção: **8 h deslizantes** (renova a cada request; `SESSION_SAVE_EVERY_REQUEST`), expira ao
  fechar o navegador; cookies HttpOnly, SameSite=Lax, Secure. `cycle_key()` no login e ao
  verificar o 2FA.
- **Logout só via POST** com CSRF (`/admin-logout/`; GET mostra a tela de confirmação). A
  resposta envia `Clear-Site-Data: "cache"`.
- Áreas privadas saem com `Cache-Control: no-store` (middleware + `@never_cache` nas telas com
  dado de saúde): o "voltar" no computador da recepção não reexibe ficha depois do logout.

### 1.5 Criação de contas, reset de senha

- **ADMIN inicial:** `bootstrap_admin` no pre-deploy (`ADMIN_EMAIL`/`ADMIN_PASSWORD`/
  `ADMIN_NOME`); a migration `0042` desativa as contas demo antigas (`admin@shivazen.com`,
  `ana@shivazen.com`) se ainda usarem a senha padrão, apagando também o 2FA delas.
- **Novo usuário (Painel > Usuários):** com e-mail configurado, recebe **link** para definir a
  senha (nunca a senha por e-mail); sem backend de e-mail real a **senha inicial é obrigatória**
  na criação.
- **Reset de senha** (`/admin-login/recuperar/`): token de 1 h (`PASSWORD_RESET_TIMEOUT_SECONDS`),
  3 pedidos/15 min por IP, resposta sempre genérica, auditado com e-mail mascarado. Sem
  `EMAIL_BACKEND` real o link **não é enviado** (falha fechada — nunca vai para o log).
- Senha de ADMIN existente só é trocada pelo deploy se a conta estava desativada/sem senha ou com
  `ADMIN_PASSWORD_RESET=true`.

### 1.6 Acesso do profissional à ficha (prontuário)

Regra em `views/prontuario._vinculo`: o profissional lê a ficha de uma cliente só se tiver com
ela um atendimento **REALIZADO nos últimos 180 dias** ou **PENDENTE/AGENDADO/CONFIRMADO entre
ontem e +60 dias** (escrita: o mesmo, sem PENDENTE). CANCELADO/REAGENDADO/FALTOU nunca dão
vínculo. Toda leitura, gravação e negativa vai para a `LogAuditoria` (sem nome da cliente no
texto). ADMIN lê todas. O link "Ficha" no portal só aparece quando há vínculo.

---

## 2. Cliente

### 2.1 OTP por SMS (regra única)

- **Todo agendamento público exige OTP** — cliente nova ou recorrente. O desafio é **preso ao
  telefone** que recebe o SMS (chave `sms+<dígitos>@shivazen.local`); o e-mail digitado nunca é
  identidade. Só **celular** recebe SMS (`[DDD]9XXXXXXXX`); fixo é recusado sem gastar cota.
- Geração: `secrets.randbelow(10^6)` → 6 dígitos; armazenamento **HMAC-SHA256 com a
  `SECRET_KEY`** (um dump do banco não permite testar os códigos offline); comparação
  `hmac.compare_digest`. Nunca logado (no dev só a prévia no log de modo dev).
- Validade 10 min (`OTP_TTL_SEGUNDOS`), **5 tentativas** (`OTP_MAX_TENTATIVAS`) e o código é
  inutilizado, reenvio ≥ 60 s (`OTP_REENVIO_MINIMO_SEG`), gerar um novo invalida o anterior.
- Cotas de SMS (checadas **antes** de gerar o código — sem SMS saindo, o código anterior continua
  valendo): 3/h por telefone, 10/h por IP, 60/h global (`SMS_MAX_*`), alerta no log a 80%.
  Rate-limit HTTP nos endpoints (5–10/min por IP). **Turnstile** no pedido de código quando
  configurado.
- Propósitos isolados: `AGENDAMENTO`, `LOGIN_CLIENTE` (portal), `DSAR` — um código não vale em
  outro fluxo.
- Sem provedor de SMS (produção sem `ZENVIA_*`): falha fechada; o site avisa e os botões
  "Agendar" levam ao WhatsApp da clínica.

### 2.2 Sessões da cliente

| Fluxo | Chave de sessão | Duração | Observação |
|---|---|---|---|
| Wizard de agendamento | `otp_agendamento_telefone` | 30 min após o código | `confirmar_agendamento` exige que o telefone do formulário seja **o** verificado; troca o id da sessão; limpa ao concluir |
| "Meus agendamentos" | `meus_agendamentos_telefone` | 1 h | Login por **celular ou e-mail cadastrado** — com e-mail o código vai sempre ao celular do cadastro. Resposta idêntica exista ou não o cadastro. `sair/` limpa |
| DSAR (`/lgpd/meus-dados/`) | — (código consumido na hora) | 10 min | Telefone → código → download do JSON; auditado |

Telefone provado no wizard **substitui o e-mail do cadastro** pelo digitado (um e-mail plantado
por terceiro sai); e-mail de outro cadastro é recusado. O formulário anônimo da lista de espera
nunca grava e-mail no cadastro (só na inscrição, e para telefone já cadastrado só se o telefone
foi provado por OTP na sessão).

### 2.3 Links por token

Todos com `secrets.token_urlsafe(32)` (≈ 256 bits), comparação por igualdade no banco ou
`compare_digest`, e `Referrer-Policy: no-referrer` nas páginas; o access log do gunicorn e o
Sentry redigem os tokens.

| Token | Onde | Validade | Dá acesso a |
|---|---|---|---|
| `Atendimento.token_cancelamento` | `/reagendar/<token>/`, cancelar no portal | sem expiração; reagendar só até 24h antes, cancelar até o início | Reagendar/cancelar aquele atendimento |
| `Notificacao.token` (LEMBRETE WhatsApp) | `/confirmar/<token>/` | uma resposta; atendimento futuro e editável | Confirmar presença (AGENDADO → CONFIRMADO) ou cancelar |
| `Notificacao.token` (TERMO) | `/termo/<token>/` | até o **fim do atendimento** | Aceitar os termos pendentes daquele atendimento (nunca vale em `/confirmar/`) |
| `Notificacao.token` (NPS) | `/nps/<token>/` | 7 dias, uma resposta | Dar nota 0–10, comentário e autorizar depoimento |
| `RespostaAnamnese.token` | `/anamnese/<token>/`, `/pesquisa/<token>/` | 60 dias, uma resposta | Responder ficha/pesquisa (ficha de saúde com consentimento art. 11) |
| `Cliente.token_descadastro` | `/lgpd/unsubscribe/<token>/` | sem expiração (renovado na anonimização) | Descadastro de marketing e pesquisas |
| `Profissional.ics_token` | `/agenda/<slug>/feed.ics?token=` | até ser **rotacionado** no painel ("Novo link") | Feed iCal com nomes das clientes |
| Reset de senha (equipe) | `/admin-login/recuperar/<uid>/<token>/` | 1 h, uso único | Definir nova senha |

---

## 3. Transporte e headers

- HTTPS obrigatório em produção (`SECURE_SSL_REDIRECT`, exceto healthchecks), HSTS 1 ano com
  subdomínios e preload, proxy SSL header do Railway.
- CSP com nonce por request, sem `unsafe-inline` em scripts/estilos e sem handlers inline;
  jsdelivr só nos caminhos das libs usadas; `frame-ancestors 'none'` (exceto `/embed/agendar/`,
  que usa `EMBED_FRAME_ANCESTORS`).
- `X-Frame-Options: DENY`, `nosniff`, COOP/CORP `same-origin`, Permissions-Policy restritiva,
  `Referrer-Policy: strict-origin-when-cross-origin` (e `no-referrer` nas rotas com token).
- CSRF em todos os formulários; o `webpush.js` lê o token da página (cookie CSRF é HttpOnly).
- API DRF: sessão + permissão padrão `IsAdminUser` (inclusive a raiz `/api/v1/`, o schema e o
  Swagger).

---

## 4. Avaliação

**Forte hoje:** OTP com HMAC + cotas + propósito isolado + preso ao telefone; anti-enumeração no
portal, no DSAR e na lista de espera; 2FA obrigatório do ADMIN conferido a cada request, sem
tela de login paralela; sessão/HSTS/CSP estritos; no-store e no-referrer onde há dado sensível;
tokens longos e redigidos nos logs; trilha de auditoria com IP.

**Riscos remanescentes (aceitos ou pendentes):**

| Sev | Ponto | Situação |
|---|---|---|
| média | DSAR e portal liberados com 1 fator (SMS) — SIM-swap expõe o cadastro | Aceito por ora (público de estética, sem senha); evolução: 2º canal ou entrega do export por e-mail |
| média | 2FA do PROFISSIONAL é opt-in, mas ele vê alertas de saúde | Ligar `PROFISSIONAL_2FA_OBRIGATORIO=true` quando a equipe estiver pronta |
| baixa | `token_cancelamento` e `token_descadastro` não expiram | Escopo limitado (um atendimento / opt-out); reagendar exige 24h |
| baixa | `Enforce2FAMiddleware` depende do marcador de sessão do login da equipe | Em produção a única porta é `/admin-login/` (equivalente); hardening listado no ARCHITECTURE |
| baixa | Link do termo pode ser enviado por `mailto:` ao e-mail do cadastro | Pendência listada no ARCHITECTURE |

**Decisão sobre o login da cliente (fechada):** *passwordless* por celular + OTP (opção A da
versão anterior deste documento), com regra única de OTP para todo agendamento. Evoluções
possíveis (não implementadas): sessão única de "cliente verificada" para wizard + portal,
"lembrar este aparelho" e step-up no DSAR.

---

_Última atualização: 2026-09-23 — reescrito após a auditoria pré-produção (2FA obrigatório,
login do profissional, OTP preso ao telefone, rotas do two_factor removidas)._
