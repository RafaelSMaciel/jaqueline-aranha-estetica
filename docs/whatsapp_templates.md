# WhatsApp Templates — Meta Business API

Apenas dois templates WhatsApp sao usados no projeto Shiva Zen (estrategia aprovada 2026-04-18).
Todos os outros fluxos de comunicacao usam email (ou SMS para OTP).

## 1. `confirmacao_d1` — Confirmacao D-1

| Campo | Valor |
|-------|-------|
| Categoria Meta | UTILITY |
| Idioma | `pt_BR` |
| Nome | `confirmacao_d1` (configuravel via env `WHATSAPP_TEMPLATE_D1`) |

### Corpo do template (submeter no Meta Business Manager)

```
Oi {{1}}! Passando para lembrar do seu agendamento amanha ({{2}} as {{3}}) no Shiva Zen.

- Procedimento: {{4}}
- Profissional: {{5}}

Confirmar presenca: {{6}}
Preciso remarcar: {{7}}

Qualquer duvida, responda esta mensagem.
```

### Parametros (ordem)

1. `{{1}}` nome completo do cliente
2. `{{2}}` data `dd/mm/aaaa`
3. `{{3}}` horario `HH:MM`
4. `{{4}}` nome do procedimento
5. `{{5}}` nome do profissional
6. `{{6}}` link confirmar (URL absoluta com token)
7. `{{7}}` link cancelar/remarcar (URL absoluta com token)

### Observacoes para aprovacao

- Nao usa variaveis emoji — evita rejeicao.
- URLs com token de 32 chars entram em `BODY`, nao em `BUTTONS` (templates com botao URL exigem domain allowlist).
- Categoria UTILITY porque mensagem e diretamente vinculada a transacao ativa (agendamento existente).

---

## 2. `nps_pos_atendimento` — Pesquisa NPS 24h

| Campo | Valor |
|-------|-------|
| Categoria Meta | UTILITY |
| Idioma | `pt_BR` |
| Nome | `nps_pos_atendimento` (configuravel via env `WHATSAPP_TEMPLATE_NPS`) |

### Corpo do template

```
Oi {{1}}, como foi o seu atendimento de {{2}} no Shiva Zen?

Sua avaliacao e o que guia nosso cuidado. Leva menos de 1 minuto:

{{3}}

Obrigado por escolher a gente.
```

### Parametros (ordem)

1. `{{1}}` nome completo do cliente
2. `{{2}}` nome do procedimento realizado
3. `{{3}}` link NPS (URL absoluta com token)

### Observacoes para aprovacao

- Submeter como UTILITY (pesquisa pos-transacao). Se Meta reclassificar como MARKETING, precisa opt-in `consent_whatsapp_nps=True` do cliente (ja implementado no model Cliente).
- Enviado apenas para clientes com `consent_whatsapp_nps=True` — ver `job_pesquisa_satisfacao_24h` em `app_shivazen/tasks.py`.

---

## Passos para submeter

1. Meta Business Manager -> WhatsApp Manager -> Message Templates -> Create Template
2. Name: exatamente `confirmacao_d1` / `nps_pos_atendimento` (deve bater com env)
3. Copiar o corpo acima, marcar `Body Variables: 7` (D1) ou `3` (NPS)
4. Adicionar exemplos para cada variavel (a Meta exige para aprovacao — use dados genericos)
5. Submit for review (aprovacao em 1-24h normalmente)

## Variaveis de ambiente

```
WHATSAPP_TOKEN=...              # Bearer token Meta WhatsApp Cloud API
WHATSAPP_PHONE_ID=...           # Phone number ID
WHATSAPP_TEMPLATE_D1=confirmacao_d1
WHATSAPP_TEMPLATE_NPS=nps_pos_atendimento
```
