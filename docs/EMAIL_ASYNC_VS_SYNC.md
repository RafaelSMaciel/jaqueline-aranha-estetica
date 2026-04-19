# E-mail Síncrono vs Assíncrono — Shiva Zen

Documento de referência para decisão futura de migração do envio de e-mails.

## Estado atual: **síncrono**

O sistema envia e-mails dentro do request HTTP do Django. O usuário espera
o SMTP completar antes de ver a resposta da página.

**Motivo**: infraestrutura free (Railway Hobby gratuito). Sem Redis e sem
worker Celery 24/7, não há fila disponível para jobs em background.

## Fluxo síncrono (atual)

```
POST /confirmar-agendamento
  ↓
Django valida formulário
  ↓
Salva atendimento no Postgres
  ↓
Chama enviar_confirmacao(email)       ← 200-500ms conectar SMTP
  ↓
SMTP entrega e-mail                   ← 500-2000ms
  ↓
SMTP confirma recebimento             ← 100-300ms
  ↓
Retorna HTTP 200 + render sucesso
```

**Latência total**: 2-3 segundos.
**UX mitigada**: spinner + texto "Enviando confirmação por e-mail..." durante
o submit (implementado em `base.html` via `data-loading-msg`).

## Fluxo assíncrono (futuro, se escalar)

```
POST /confirmar-agendamento
  ↓
Django valida + salva no Postgres
  ↓
Enfileira task no Redis               ← 5ms
  ↓
Retorna HTTP 200 imediatamente        ← total ~200ms

[Worker Celery, processo separado]
  ↓
Pega task do Redis
  ↓
Monta e envia e-mail
  ↓
Retenta em caso de falha (3x, backoff exponencial)
```

**Latência total do cliente**: ~200ms.
**E-mail chega**: ~3s depois (paralelo).

## Comparação

| Aspecto | Síncrono (atual) | Assíncrono |
|---|---|---|
| UX | Cliente espera 2-3s | Resposta imediata |
| Falha SMTP | Request falha, cliente vê erro | Retry automático em background |
| Timeout | Se SMTP > 30s gunicorn mata request | Worker tem timeout próprio |
| Complexidade | 1 processo (web) | 3 processos (web + worker + Redis) |
| Infra | $0/mês | +$10-15/mês (Redis + worker Railway) |
| Throughput | Limitado ao tempo do SMTP | Alto (workers paralelos) |
| Envio em lote | Trava request inteiro | Enfileira N tasks |
| Observabilidade | Log inline | Flower / celery events |

## Quando migrar para assíncrono

Considerar migração quando ocorrer **qualquer** um destes:

1. Volume > 200 clientes/mês (> 20 e-mails/dia apenas de confirmação)
2. SMTP provedor com latência alta (> 3s, como SendGrid em horário pico)
3. Disparo em lote (campanhas promocionais, alertas NPS em batch)
4. Reclamação frequente de "site travou no agendamento"
5. Migração para plano Hobby ($5/mês já cobre Redis + worker básicos)

## Arquitetura alvo (quando migrar)

```
Railway project: shivazen-app
├── web        (Django + gunicorn)           $5
├── worker     (celery -A shivazen worker)   $5
├── Postgres                                  $5
└── Redis      (broker + result backend)      $5

Celery Beat: usar worker embarcado
  celery worker --beat --scheduler django
  (economiza 1 serviço, suficiente para ~40 clientes/mês)
```

**Custo total**: ~$20/mês.

## Código já preparado

O código em `app_shivazen/tasks.py` já usa `@shared_task`. A migração
envolve:

1. Provisionar Redis na Railway
2. Setar env vars `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`
3. Criar serviço `worker` com startCommand `celery -A shivazen worker --beat -l info`
4. Trocar chamadas síncronas `enviar_email(...)` por `enviar_email_task.delay(...)`
5. Remover endpoint `/cron/run/<job>/` (substituído por Celery Beat)

## Substituição atual de Celery Beat: cron externo

Como não há worker/beat rodando, os jobs agendados são disparados via HTTP
por um serviço externo (cron-job.org, gratuito):

**Endpoint**: `POST /cron/run/<job_name>/`
**Auth**: header `X-Cron-Token: <CRON_TOKEN>`

Jobs disponíveis:

| Nome | Descrição | Horário sugerido |
|---|---|---|
| `lembrete_diario` | Confirmação D-1 para agendamentos de amanhã | 08:00 |
| `nps_24h` | Pesquisa NPS 24h pós-atendimento | 10:00 |
| `detrator_alerta` | Alerta admin sobre nota baixa | 10:30 |
| `pacote_expirando` | Aviso clientes com pacotes prestes a expirar | 07:00 |
| `pacote_expirar` | Expirar pacotes vencidos | 00:30 |
| `aniversario` | E-mail aniversário | 09:00 |
| `limpeza_status` | Marca ausência em agendamentos não atualizados | 23:00 |
| `lgpd_purgar` | Purga clientes inativos (LGPD) | Domingo 03:00 |

**Exemplo de agendamento em cron-job.org**:
```
URL:     https://<app>.up.railway.app/cron/run/lembrete_diario/
Method:  POST
Headers: X-Cron-Token: <valor de CRON_TOKEN no Railway>
Agenda:  daily at 08:00 America/Sao_Paulo
```
