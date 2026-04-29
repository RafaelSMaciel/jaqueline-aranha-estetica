# Regras de Negócio — Jaqueline Aranha Estética

Documento descritivo do domínio de produto. Linguagem de negócio, sem código.

Data: 2026-04-27 · Branch: `main` (HEAD `e8892e2`)

---

## 1. Atores

### Cliente (paciente final)
Pessoa física que busca tratamentos estéticos. Acessa o site público, agenda consultas, consulta histórico próprio, recebe lembretes e pode dar nota NPS após atendimento. **Não tem login tradicional** — verifica identidade por OTP (código por SMS) toda vez que precisa acessar área restrita pessoal.

### Profissional (Jaqueline Aranha — modelo single-professional atual)
Atende clientes, registra prontuário (anamnese, evolução, fotos), confirma realização de atendimento. Tem login no painel. O sistema foi modelado para **N profissionais**, mas está configurado para 1 (Jaqueline) — abertura para multi-profissional já existe no schema.

### Administrador (clínica)
Pode ser a própria Jaqueline ou recepção. Gerencia agenda completa, clientes, procedimentos, preços, promoções, pacotes, relatórios. Acessa `/painel/`. Acesso protegido por login + (após esta sessão) 2FA TOTP.

### Sistema externo
Cron-job.org dispara endpoints HTTP autenticados (`X-Cron-Token`) para tarefas agendadas (lembrete D-1, NPS, expiração de pacotes, anonimização LGPD).

---

## 2. Entidades Centrais

### Procedimento
Catálogo de tratamentos oferecidos. Atributos: nome, slug, descrição curta + longa, duração em minutos, categoria (Facial / Corporal / Capilar / Outro), imagem destaque, status ativo. Cada procedimento tem N preços vigentes (por profissional + data) e pode estar em promoções.

### Profissional
Pessoa que atende. Vinculada a um usuário do sistema. Define M2M com procedimentos (quem faz o quê). Tem agenda de horários disponíveis e bloqueios.

### Atendimento (Agendamento)
Núcleo do sistema. Liga **Cliente × Profissional × Procedimento × Janela horária**. Status: Pendente → Agendado → Confirmado → Realizado / Cancelado / Faltou / Reagendado. Cada atendimento tem token único de cancelamento (link mágico via email/whatsapp). Pode ter promoção aplicada e valor cobrado diferente do original.

### Cliente
Cadastro com nome, email, telefone, CPF, data nascimento. Consentimento LGPD granular por canal (transacional, marketing, NPS). Token único de unsubscribe. Soft-delete + anonimização.

### Pacote / PacoteCliente / SessaoPacote
Pacotes pré-vendidos: ex. "10 sessões de drenagem". `Pacote` é a oferta. `PacoteCliente` é a compra (status Ativo / Expirado / Concluído + data expiração). `SessaoPacote` registra cada uso (debita 1 sessão a cada Atendimento realizado de procedimento contemplado).

### Promoção
Desconto vinculado a procedimento (ou geral). Pode ser percentual ou preço fixo promocional. Janela de validade (data início/fim). Usado em tela `/promocoes/` e disparado por email para clientes opt-in.

### Prontuário / ProntuarioResposta
Anamnese estruturada (perguntas customizadas). Cliente preenche antes da consulta via link mágico. Profissional acessa, complementa, evolui ao longo do atendimento. Suporta upload de imagens "antes/depois".

### AvaliacaoNPS
Nota 0-10 + comentário pós-atendimento. Disparada via WhatsApp ~24h após realização. Notas ≤6 viram alertas internos.

### Termo / TermoAceite
Termos de consentimento LGPD (privacidade, uso de imagem, fotos antes/depois). Cliente assina digitalmente uma vez por versão; sistema audita aceites.

### Notificação
Histórico de envios (OTP/email/WhatsApp/SMS): tipo, canal, status (Pendente/Enviado/Falhou), timestamp. Permite reprocessar falhas e auditar comunicação.

### ConfiguracaoSistema
Key-value editável via painel. Atualmente armazena overrides de branding (nome clínica, cores tema, logo) que se sobrepõem aos defaults do código.

---

## 3. Fluxos Principais

### 3.1 Agendamento público (cliente)
1. Cliente acessa `/agendamento/` e escolhe procedimento (cards visuais por categoria)
2. Calendário mostra dias com vagas (consulta `/ajax/dias-disponiveis/`)
3. Seleciona dia → mostra horários livres da Dra.
4. Preenche cadastro (nome, email, telefone) — Cloudflare Turnstile bloqueia bot
5. Recebe OTP por **SMS** (Zenvia) — confirma identidade
6. Atendimento criado com status **Pendente**
7. Recebe email confirmando solicitação. Admin precisa **aprovar** (passa para Agendado)

### 3.2 Aprovação admin
Admin acessa `/painel/agendamentos/`. Pendentes destacados. Pode aprovar individualmente ou em lote (bulk action). Ao aprovar:
- Status vira **Agendado**
- Email sync de confirmação enviado ao cliente (~2-3s — UX com spinner)
- Token de cancelamento disponível em link mágico

### 3.3 Reagendamento / Cancelamento
Cliente clica link no email/WhatsApp → tela com calendar de novos horários. Cancelamento livre até X horas antes (regra parametrizável). Reagendamento mantém histórico (FK `reagendado_de`).

### 3.4 Lembrete D-1 (cron diário)
Cron HTTP bate `/cron/run/lembrete_diario/` → tasks.py varre Atendimentos do dia seguinte com status Agendado/Confirmado → envia template **WhatsApp** Meta aprovado (`lembrete_d1`). Cliente pode confirmar/cancelar pelo botão do WhatsApp.

### 3.5 Realização e prontuário
Profissional marca atendimento como **Realizado**. Sistema:
- Debita sessão de pacote ativo (se procedimento contemplado)
- Permite registrar evolução no prontuário
- Agenda envio de NPS para D+1

### 3.6 NPS (cron diário, D+1)
Cron varre atendimentos Realizados há ~24h. Envia template **WhatsApp** com escala 0-10. Resposta cai em `/whatsapp/webhook/` (futuro — atualmente captura via link). Notas ≤6 disparam email-alerta interno para a Dra. agir.

### 3.7 Pacotes e Promoções
**Pacote:** Cliente compra pacote (offline/PIX). Admin cadastra `PacoteCliente` no painel. Cada Atendimento Realizado debita automaticamente. Email "seu pacote expira em X dias" disparado quando próximo do vencimento.
**Promoção:** Admin cria promoção. Tela pública `/promocoes/` lista vigentes. Botão "Disparar" no painel envia email em massa para clientes opt-in (consent canal email).

---

## 4. Canais de Comunicação

Configuração atual (commit `a655f85`, alinhada com schema):

| Disparo | Canal | Por que |
|---------|-------|---------|
| OTP (verificação cliente) | **SMS** (Zenvia) | Confiável, instantâneo, não precisa app |
| Confirmação de agendamento | Email | Formal, com link cancelamento |
| Cancelamento | Email | Idem |
| Promoções e pacote expirando | **Email** | Marketing transacional, opt-in granular |
| Aniversário | Email | Mesmo motivo |
| Lembrete D-1 | **WhatsApp** | Maior taxa de abertura, template Meta |
| NPS pós-atendimento (D+1) | **WhatsApp** | Idem |
| Alertas internos (NPS baixo, agendamento pendente) | Email para staff | Dashboard + notificação |

**Consentimento granular** ([clientes.py:38](aranha_estetica/models/clientes.py:38)): cliente opta in/out por canal/tipo independentemente. OTP não exige consent explícito (é necessário para entrar). Marketing pede opt-in explícito (LGPD compliant).

**Bloqueador atual (Sprint 2):** templates WhatsApp Meta `lembrete_d1`, `nps`, `confirmacao` precisam ser submetidos no Business Manager. Sem aprovação, mensagens fora da janela 24h falham.

---

## 5. LGPD / Compliance

- **DSAR** (`/lgpd/meus-dados/`): cliente baixa JSON com tudo que sistema sabe sobre ele (atendimentos, NPS, prontuário). Ação manual via formulário público.
- **Direito ao esquecimento** (`LgpdService.anonimizar_dados`): substitui PII (nome, email, telefone, CPF) por hashes/placeholders. Mantém histórico contábil/agendamento como anônimo. Soft-delete.
- **Anonimização agendada**: cron semanal anonimiza clientes inativos há 2+ anos (RETENCAO_CLIENTE_INATIVO_DIAS=730). Logs auditoria mantidos 1 ano (RETENCAO_LOG_AUDITORIA_DIAS=365).
- **Cookie banner granular**: cliente escolhe quais cookies aceita (essenciais, analytics, marketing). Decisão registrada com IP + timestamp.
- **Termos de consentimento**: versionados. Exibidos no primeiro agendamento. Sistema rastreia versão aceita por cliente.
- **DPIA documentado** ([docs/DPIA.md](docs/DPIA.md)).

---

## 6. Estado Atual vs. Roadmap

### ✅ Implementado e funcional
- Agendamento público completo (calendar + OTP SMS + confirmação)
- Painel admin: dashboard KPIs, aprovação atendimentos, gestão clientes/profissionais/procedimentos/preços
- Promoções (criar, listar, disparar email)
- Pacotes (modelo + débito automático + alerta expiração)
- Prontuário básico (anamnese estruturada)
- LGPD: DSAR, anonimização, consent granular, cookie banner
- Notificações: email sync, WhatsApp + SMS via API, registro/auditoria
- Multi-canal por preferência cliente
- 2FA admin TOTP (Django Admin) — após esta sessão

### 🟡 Esqueleto / parcialmente pronto
- Webhook WhatsApp → reposta cliente (modelo pronto, parsing básico, falta processar respostas livres)
- Estoque/produtos (modelo `Produto` existe mas UI rasa)
- Auditoria UI (logs registrados, tela de busca limitada)

### 🔴 Não implementado
- Pagamento online (PIX automático, cartão) — fluxo é offline/manual hoje
- Multi-profissional ativo (modelo suporta, dados + UI configurados pra single)
- Mobile app nativo (existe PWA, sem app store)
- Push notifications VAPID
- Calendário FullCalendar drag-drop (lista hoje)
- Chat interno (Channels + WS)
- Integração TISS (convênios)

---

## 7. Sugestões de Melhoria (top 8 produto)

| # | Ideia | Impacto | Esforço |
|---|-------|---------|---------|
| 1 | **Pagamento PIX online no agendamento** — cliente paga sinal/total na hora, reduz no-show | 🔴 Alto (cash flow + conversão) | M |
| 2 | **Lista de espera com auto-fill** — quando vaga libera, sistema oferece para próximo da fila por WhatsApp com link de 1-clique | 🟡 Médio (max ocupação) | S |
| 3 | **Reagendamento self-service via WhatsApp button** — "Reagendar" no template Meta abre tela com novos horários sem login | 🟡 Médio (UX, menos trabalho admin) | M |
| 4 | **Programa de fidelidade / cashback automático** — cada R$X vira ponto trocável por desconto | 🟡 Médio (LTV) | M |
| 5 | **Galeria antes/depois com slider interativo** + autorização explícita por foto (LGPD) | 🟡 Médio (conversão site público) | M |
| 6 | **Cohort dashboard** — retenção por mês de primeira visita, valor médio, frequência | 🟢 Médio-baixo (insight gestão) | S |
| 7 | **Indicação por link** — cliente compartilha link único, ambos ganham desconto na próxima | 🟡 Médio (CAC zero) | S |
| 8 | **Bot WhatsApp respondendo dúvidas frequentes** + transferência para humano se complexo | 🟢 Baixo-médio (reduz volume admin) | L |

Bonus de polimento:
- Conversão imagens públicas para WebP/AVIF + srcset (perf SEO mobile)
- Critical CSS inline (LCP)
- A/B testing CTAs hero
- Calculadora "monte seu pacote" interativa

---

## Glossário rápido

- **OTP**: One-Time Password, código de 6 dígitos por SMS para verificação cliente
- **NPS**: Net Promoter Score, nota 0-10 medindo lealdade
- **DSAR**: Data Subject Access Request, direito do titular acessar seus dados (LGPD art. 18)
- **TOTP**: Time-based One-Time Password, código 2FA gerado por app authenticator
- **D-1**: dia anterior ao atendimento (lembrete)
- **D+1**: dia seguinte ao atendimento (NPS)
- **Cron-job.org**: serviço externo gratuito que dispara HTTP periodicamente — substitui Celery Beat no plano free Railway
