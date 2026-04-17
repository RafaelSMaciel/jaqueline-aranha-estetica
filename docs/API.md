# Endpoints — shivazen-app

Não há REST API formal. Listagem dos endpoints HTTP organizados por área.

## Públicos

| URL | View | Método | Descrição |
|-----|------|--------|-----------|
| `/` | `inicio` | GET | Home institucional |
| `/equipe/` | `equipe` | GET | Lista profissionais |
| `/especialidades/` | `especialidades` | GET | Lista especialidades |
| `/servicos/` | `servicos` | GET | Catálogo de procedimentos |
| `/servicos/<slug>/` | `servico_detalhe` | GET | Detalhe procedimento |
| `/depoimentos/` | `depoimentos` | GET | Reviews |
| `/galeria/` | `galeria` | GET | Galeria |
| `/agendar/` | `booking` | GET, POST | Form de agendamento público |
| `/cancelar/<token>/` | `cancelar_atendimento` | GET, POST | Auto-cancelamento via token |

## Booking API (interna)

| URL | Descrição |
|-----|-----------|
| `/api/booking/horarios/` | GET: slots disponíveis (json) |
| `/api/booking/profissionais/` | GET: filtra por procedimento |

## LGPD

| URL | Descrição |
|-----|-----------|
| `/lgpd/meus-dados/` | DSAR: solicita export via OTP |
| `/lgpd/unsubscribe/<token>/` | Opt-out de comunicações |
| `/lgpd/aceitar-cookies/` | POST AJAX: registra consent |

## Auth

| URL | Descrição |
|-----|-----------|
| `/login/` | LoginForm |
| `/logout/` | Logout |

## Admin (Django Admin + custom)

| URL | Descrição |
|-----|-----------|
| `/admin/` | Django admin (otimizado) |
| `/dashboard/` | Dashboard interno |
| `/prontuario/<cliente_id>/` | Prontuário eletrônico |
| `/profissional/agenda/` | Agenda do profissional |

## Sistema

| URL | Descrição |
|-----|-----------|
| `/health/` | Healthcheck JSON (200 ok / 503 db down) |
| `/manifest.json` | PWA manifest |
| `/sw.js` | Service worker |

## WhatsApp Webhook

| URL | Descrição |
|-----|-----------|
| `/webhook/whatsapp/` | POST: recebe msgs (Meta Cloud API) |

## Tokens e segurança

- **token_cancelamento** — UUID único por Atendimento, expira por TTL
- **unsubscribe_token** — `secrets.token_urlsafe(32)`, gerado no save()
- **OTP DSAR** — comparação via `hmac.compare_digest`
- **CSRF** obrigatório em todos POSTs (Django default)
