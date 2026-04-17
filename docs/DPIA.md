# DPIA — Relatório de Impacto sobre Proteção de Dados

**Sistema:** shivazen-app (clinica de estetica)
**Versao:** 1.0
**Data:** 2026-04-17
**Responsavel:** Encarregado de Dados (DPO)

## 1. Descricao do tratamento

### 1.1 Finalidades
- Agendamento online de procedimentos esteticos
- Prontuario eletronico de clientes
- Notificacoes (lembrete, confirmacao, NPS)
- Faturamento e gestao de pacotes
- Auditoria interna

### 1.2 Categorias de dados tratados
| Categoria | Exemplos | Sensibilidade |
|-----------|----------|---------------|
| Identificacao | nome, CPF, RG | PII |
| Contato | email, telefone, endereco | PII |
| Saude | procedimentos realizados, observacoes prontuario | **DADO SENSIVEL** |
| Financeiro | valores cobrados, formas pagamento | PII |
| Tecnico | IP, user-agent, logs acesso | PII tecnica |

### 1.3 Titulares
- Clientes (publico geral, maiores 18 anos)
- Profissionais (vinculo trabalhista)
- Administradores (acesso interno)

## 2. Base legal (LGPD art. 7 e 11)

| Tratamento | Base legal | Justificativa |
|-----------|-----------|---------------|
| Agendamento | Execucao de contrato (art. 7-V) | Servico solicitado pelo cliente |
| Prontuario | Tutela da saude (art. 11-II-f) | Profissional de saude habilitado |
| Notificacao lembrete | Legitimo interesse (art. 7-IX) | Reduz no-show, beneficia ambas partes |
| Marketing | Consentimento expresso (art. 7-I) | Opt-in granular via cookie banner |
| Auditoria | Cumprimento obrigacao legal (art. 7-II) | Rastreabilidade fiscal/sanitaria |

## 3. Riscos identificados

### 3.1 Vazamento de dados sensiveis (saude)
- **Probabilidade:** baixa
- **Impacto:** alto
- **Mitigacao:**
  - HTTPS obrigatorio (HSTS 1 ano)
  - Acesso controlado por permissoes Django
  - Logs de auditoria com IP em LogAuditoria
  - PII masking em logs aplicacao

### 3.2 Acesso nao autorizado conta admin
- **Probabilidade:** media
- **Impacto:** alto
- **Mitigacao:**
  - django-axes lockout (5 tentativas/h)
  - Ratelimit django-ratelimit em login
  - Senha minima 10 chars
  - Sessao 30min em prod
  - Roadmap: 2FA obrigatorio

### 3.3 Injecao / XSS
- **Probabilidade:** baixa
- **Impacto:** alto
- **Mitigacao:**
  - CSP per-request nonce (script-src/style-src)
  - Django ORM (parametrized queries)
  - Templates Django (auto-escape)
  - X-Content-Type-Options: nosniff

### 3.4 Vazamento via backup
- **Probabilidade:** baixa
- **Impacto:** alto
- **Mitigacao:**
  - Roadmap: encryption at rest no DB (Railway/AWS)
  - Roadmap: snapshots criptografados em S3 com SSE

## 4. Direitos dos titulares (LGPD art. 18)

| Direito | Implementacao |
|---------|---------------|
| Confirmacao + acesso | `/lgpd/meus-dados/` (DSAR via OTP, JSON export) |
| Correcao | Via atendimento + atualizacao admin |
| Anonimizacao | `LgpdService.esquecer_cliente` (admin action + automatica 5+ anos) |
| Portabilidade | Export JSON em DSAR |
| Revogacao consentimento | `/lgpd/unsubscribe/<token>/` + cookie banner granular |
| Eliminacao | Soft-delete + purge job semanal |
| Informacao sobre uso | `politica_privacidade` view + DPIA publico |

## 5. Retencao

| Dado | Prazo | Apos prazo |
|------|-------|-----------|
| Cliente ativo | Indeterminado | — |
| Cliente inativo (sem agendamento) | 5 anos | Anonimizacao automatica |
| Atendimento (saude) | 20 anos | Conforme CFM Resolucao 1.821/2007 |
| LogAuditoria | 5 anos | Purga manual |
| Sessao web | 30min (prod) | Auto-expira |

## 6. Compartilhamento

- **WhatsApp Business API** (Meta) — apenas telefone + mensagem (lembrete/NPS)
- **SMTP provider** (configurado em `EMAIL_HOST`) — email + conteudo
- **Sentry** (se ativado) — stack traces (sem PII via `send_default_pii=False`)
- **Railway** (hosting) — DB + arquivos
- **Sem compartilhamento com 3rd-party para fins comerciais**

## 7. Responsaveis

- **Controlador:** Clinica (CNPJ a definir)
- **Operador:** Railway (hosting), Meta (WhatsApp)
- **Encarregado (DPO):** [Nome a definir]
- **Email contato:** dpo@<dominio>

## 8. Incidentes

Procedimento em caso de vazamento (LGPD art. 48):
1. Conter incidente (revogar tokens, isolar sistema)
2. Avaliar impacto via LogAuditoria
3. Comunicar ANPD em ate 72h se risco a titulares
4. Comunicar titulares afetados via email registrado

## 9. Revisao

Este DPIA deve ser revisado:
- Anualmente (data + 1 ano)
- Apos qualquer mudanca relevante no tratamento
- Apos qualquer incidente
