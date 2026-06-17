# Autenticação & Acesso — Shiva Zen

> Como o login/identificação funciona **hoje**, avaliação de segurança e **melhorias
> recomendadas**. Inclui a **decisão em aberto** sobre o modelo de login do cliente
> (cadastro com senha vs. telefone + SMS). Cross-ref: [`REGRAS-DE-NEGOCIO.md`](REGRAS-DE-NEGOCIO.md),
> [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## 0. Visão geral — dois mundos de acesso

| | **Cliente** (público) | **Staff** (admin/PWA) |
|---|---|---|
| Tem conta/senha? | **Não** — passwordless | **Sim** — email + senha |
| Identidade | Telefone (digits-only) | Email |
| Login | **SMS OTP** (6 dígitos) | Senha + **2FA TOTP** (opcional) |
| Acesso sem login | Links mágicos (token) | — |
| Papéis | — | ADMIN / PROFISSIONAL / RECEPÇÃO |

São sistemas **separados**. O cliente nunca vira `Usuario`; o staff nunca usa OTP de SMS.

---

## 1. Cliente — como funciona HOJE

### 1.1 Não há cadastro tradicional
O cliente **não cria conta nem senha**. É criado por **upsert** durante o agendamento:
`Cliente.objects.get_or_create(telefone=…)` (`views/booking_public.py:213`). Identidade
canônica = **telefone digits-only** (`uniq_cliente_telefone_ativo`). Email/CPF opcionais.
Quando não há email, o sistema usa pseudo-email `sms+<telefone>@shivazen.local`
(`models/sistema.py:192`).

### 1.2 Agendamento público (com OTP para cliente existente)
1. Escolhe procedimento → profissional → data/horário (sem identificação).
2. Ao confirmar: **cliente novo** → criado direto; **cliente existente** (telefone/email já no sistema) → **bloqueia e exige OTP SMS** (`booking_public.py:144-154`).
3. Envia OTP → cliente digita → valida → grava `session['otp_agendamento_email']` (TTL 30 min) → confirma.

> Protege contra alguém agendar em nome de cliente já cadastrado; novo cadastro não pede OTP.

### 1.3 "Meus agendamentos" (auto-serviço)
Login por **email → OTP SMS → sessão** `meus_agendamentos_email` (TTL 1h)
(`views/booking_otp.py:97-141`). Sem senha. Logout limpa a sessão.

### 1.4 Links mágicos (acesso sem login, por token)
Todos gerados com `secrets.token_urlsafe(...)` (cripto-seguro), **não assinados** (HMAC):

| Token | TTL | Single-use | O que dá acesso | Risco |
|---|---|---|---|---|
| `token_cancelamento` (atendimento) | **∞** | não | cancelar/reagendar agendamento | baixo |
| `token_descadastro` (cliente) | **∞** | idempotente | opt-out de comunicação (LGPD) | médio |
| Reagendamento (reusa `token_cancelamento`) | até a data do atendimento | não | form de reagendar | baixo-médio |
| `RespostaAnamnese.token` | 60 d | sim | preencher anamnese/pesquisa | baixo |
| `Notificacao.token` (NPS) | 7 d | sim | responder NPS | baixo-médio |
| `ics_token` (profissional) | **∞** | não | feed .ics com **nomes de clientes** | médio |
| `token_reserva` (lista espera) | 30 min | sim | reservar vaga | ⚠️ verificar (view de consumo não localizada) |
| **DSAR** (`/lgpd/meus-dados`) | OTP 10 min | sim | **exporta TODO o PII** (CPF, endereço, histórico) | médio |

### 1.5 Pontos fracos do lado cliente
- ⚠️ **Enumeração no agendamento:** a resposta do OTP de agendamento devolve `cliente_existente: true/false` (`booking_otp.py:67`) — vaza se o telefone está cadastrado. (Os fluxos **LOGIN** e **DSAR** já são anti-enumeração — resposta uniforme.)
- ⚠️ **DSAR só com SMS OTP:** exporta CPF/endereço/histórico inteiro com 1 fator (SMS), sem IP-binding. SIM-swap/SMS interceptado → vazamento de PII.
- ⚠️ **Tokens ∞:** `token_cancelamento`, `token_descadastro`, `ics_token` não expiram; se vazarem (link encaminhado, log de proxy), valem para sempre. `ics_token` ainda vai na **URL** (logável) e expõe nomes de clientes.
- ⚠️ **`token_reserva` sem consumo rastreado** — confirmar se a view existe.

---

## 2. OTP de SMS — como funciona HOJE

`models/sistema.py` (CodigoOtp) + `utils/sms.py` + `services/otp.py`.

- **Geração:** `secrets.randbelow(1_000_000)` → 6 dígitos, **cripto-seguro** ✅ (~20 bits entropia).
- **Armazenamento:** SHA-256 hex em `codigo_hash`; **nunca em texto plano**, nunca logado ✅.
- **Validade:** 10 min (`OTP_TTL_SEGUNDOS`).
- **Tentativas:** lockout após o máx → marca `usado_em`. ⚠️ **Discrepância:** `constants.py` diz `MAX_TENTATIVAS_OTP=3`, mas o model usa `OTP_MAX_TENTATIVAS` (default **5**). Alinhar.
- **Rate-limit em camadas:** reenvio ≥60s; SMS 3/h por telefone, 10/h por IP, 60/h global; HTTP 5–10/min por IP.
- **Propósitos isolados:** AGENDAMENTO / LOGIN_CLIENTE / DSAR — código de um não vale no outro ✅.
- **Envio:** Zenvia API v2 (real); em DEBUG/sem token, só loga (stub) ✅.

### Fraquezas do OTP
| Sev | Achado | Onde |
|---|---|---|
| média | **Hash SHA-256 sem salt** — 6 dígitos = 1M hashes, rainbow-table viável se o DB vazar | `sistema.py:232` |
| média | **Comparação `!=` direta**, não `hmac.compare_digest()` — timing-attack teórico (existe `safe_str_compare()` mas não é usada) | `sistema.py:277` |
| baixa | Enumeração no fluxo de agendamento | `booking_otp.py:67` |
| baixa | Discrepância tentativas (3 doc vs 5 código) | `constants.py` vs `sistema.py` |

---

## 3. Staff/Admin — como funciona HOJE

`models/acesso.py` (Usuario, AbstractBaseUser) + `views/auth.py` + `middleware.py`.

- **Login:** email + senha. Hash **PBKDF2** (Django default) ✅.
- **Política de senha:** mínimo **10 chars**, anti-similaridade, anti-wordlist, não-numérica ✅.
- **Lockout:** **django-axes** — 5 tentativas / cooloff 1h, por IP+email; reset no sucesso ✅. + rate-limit `@ratelimit` (10/min IP, 5/min email).
- **2FA:** **TOTP** (django-two-factor-auth) — QR + app autenticador, 10 backup codes. Enforçado via `Enforce2FAMiddleware` **se o usuário tiver device confirmado**. ⚠️ **Opcional** — não é obrigatório para todos os papéis.
- **Reset de senha:** Django CBV, token TTL **1h** (vs 3 dias default) ✅, rate-limit 3/15min, audit log ✅.
- **Sessão:** 8h, `HTTPONLY`, `SAMESITE=Lax`, `SECURE` em prod, expira ao fechar browser. `cycle_key()` após login ✅.
- **Papéis:** `is_staff = (papel == ADMIN)`; decorators `@staff_required`, `@profissional_required`, `@staff_otp_required`.

### Fraquezas do staff
| Sev | Achado | Onde |
|---|---|---|
| média | **2FA não-obrigatório** — staff sem 2FA acessa só com senha. Para ADMIN/dados clínicos, deveria ser mandatório | `middleware.py:14` |
| baixa | **`cycle_key()` ausente após verificar 2FA** — rotaciona na senha mas não no 2FA | `admin_2fa.py:89` |

---

## 4. Sessão & transporte — como funciona HOJE

Bom, no geral ✅:
- Cookies: `HTTPONLY` + `SAMESITE=Lax` sempre; `SECURE` em prod.
- CSRF: token em forms + `X-CSRFToken` nos fetch; `CSRF_TRUSTED_ORIGINS` com domínio Railway.
- HTTPS: `SSL_REDIRECT` + **HSTS 1 ano** + `INCLUDE_SUBDOMAINS` + `PRELOAD` em prod; proxy headers Railway ok; healthcheck isento.
- Headers: `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, COOP/CORP, Permissions-Policy.
- **CSP com nonce por request**, **sem `unsafe-inline`** em `script-src`/`style-src` ✅.

Falta:
- ⚠️ `script-src-attr`/`style-src-attr` ainda com `unsafe-inline` por causa dos ~33 handlers `onclick` + style attrs inline (mesmo achado da auditoria de front — Onda 3 mata).

---

## 5. Decisão em aberto — modelo de login do CLIENTE

Você está definindo entre **cadastro com senha** vs **telefone + SMS**. Hoje é, na prática,
o **passwordless por OTP** (sem conta). Comparação:

### A) Passwordless — telefone + SMS OTP (atual) ⭐ recomendado
- ✅ **Sem senha** para vazar/reusar/esquecer; menor superfície de ataque.
- ✅ Fricção baixa, alinhado ao público (WhatsApp/celular é o canal natural da clínica).
- ✅ Já implementado e funcional.
- ❌ Depende de SMS: **custo por mensagem**, deliverability, atraso.
- ❌ **SIM-swap** / interceptação de SMS = vetor (mitigar: limitar o que OTP-só libera).
- ❌ Recuperação ruim se o cliente **troca de número**.

### B) Cadastro tradicional — email + senha
- ✅ Recuperação por email; sem custo de SMS.
- ✅ Independe de operadora.
- ❌ **Senha** para o cliente gerenciar (fricção, reuso, vazamento) — público de estética raramente quer criar conta.
- ❌ Mais telas (cadastro, login, reset, verificação de email).

### C) Híbrido (recomendação de evolução)
- **Base passwordless** (telefone + OTP) **+ e-mail magic-link** como canal alternativo de OTP (sem custo SMS, e cobre quem trocou de número).
- **"Lembrar este dispositivo" 30 dias** (cookie assinado) para não pedir OTP toda vez — reduz custo de SMS e fricção.
- **Step-up** para ações sensíveis: DSAR/export de PII exige OTP **fresco** (não basta a sessão), idealmente 2 canais.

**Recomendação:** ficar no **passwordless (A)**, evoluindo para **(C)**. Para clínica de estética solo, criar senha é fricção sem ganho. O foco deve ser **endurecer o OTP** e **limitar o que 1 fator (SMS) libera** — principalmente o DSAR.

---

## 6. Melhorias recomendadas (priorizadas)

### Quick wins (baixo esforço, alto valor)
1. **`hmac.compare_digest()`** na verificação do OTP (usar a `safe_str_compare()` já existente) — mata timing-attack. `sistema.py:277`
2. **Salt/pepper no hash do OTP** — incluir um segredo do servidor + identificador no hash (`sha256(pepper + telefone + codigo)`), inviabiliza rainbow-table mesmo com DB vazado. `sistema.py:232`
3. **Anti-enumeração no agendamento** — não devolver `cliente_existente`; resposta uniforme como já é em LOGIN/DSAR. `booking_otp.py:67`
4. **Alinhar tentativas OTP** (3 vs 5) entre `constants.py`, doc e código.
5. **`cycle_key()` após verificar 2FA** do staff. `admin_2fa.py:89`

### Médio (endurecer)
6. **2FA obrigatório para ADMIN** (e idealmente todo staff que vê PII clínico) — hoje é opcional.
7. **DSAR com 2 fatores ou step-up:** export de PII completo não deveria sair com só 1 SMS. Opções: OTP por SMS **e** confirmação por email; ou entregar o export por **link enviado ao email/telefone cadastrado** em vez de exibir na hora.
8. **TTL + rotação nos tokens ∞:** dar `expira_em` a `token_cancelamento`/`token_descadastro`; `ics_token` → mover do query-param para header e permitir **regenerar** (revogação). 
9. **Confirmar/implementar consumo de `token_reserva`** (lista de espera) com validação de `expira_em` + idempotência.
10. **Assinar tokens de contexto** com `django.core.signing.TimestampSigner` (TTL embutido + à prova de adulteração) nos fluxos novos.

### Boa prática contínua
11. Auditar todo acesso a PII (DSAR, ICS) com IP/temporização no `log_auditoria`.
12. Remover `unsafe-inline` de `script-src-attr` (depende da Onda 3 do front — migrar handlers inline).
13. Mensageria: avisar o cliente por outro canal quando o número for usado em DSAR/login (detecção de SIM-swap).

---

## 7. Resumo — está bom o quê, falta o quê

**Forte hoje:** OTP cripto-seguro + hasheado + rate-limit em camadas + propósitos isolados;
staff com PBKDF2 + axes + 2FA TOTP + reset curto; sessão/HSTS/CSP-nonce sólidos; anti-enumeração
em login/DSAR; tokens com `secrets`.

**A endurecer:** `compare_digest` + salt no OTP; enumeração no agendamento; 2FA obrigatório p/ admin;
DSAR com 2º fator; TTL/revogação nos tokens ∞; `cycle_key` pós-2FA.

**Decisão sua:** manter passwordless (recomendado) e evoluir p/ híbrido (e-mail magic-link +
"lembrar dispositivo" + step-up no DSAR).

---

_Última atualização: 2026-06-14 — criação do documento._
