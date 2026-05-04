# DB Audit & Padronização — Jaqueline Aranha Estética

Análise comparativa do schema atual (47 tabelas de negócio) vs best practices de:
- **Cal.com** (Prisma, scheduling — 80+ campos em EventType)
- **Easy!Appointments** (PHP/MySQL, working plan exceptions)
- **OpenEMR** (MariaDB, EHR clínico)
- **Pabau** (SaaS aesthetic clinic)
- **Django best practices** (BaseModel mixins, soft delete, timestamps)

Data: 2026-05-04 · Branch: `dev`

---

## 1. Naming conventions

### ✅ Boas práticas seguidas

- Tabelas: `lower_snake_case` ✓ (ex: `atendimento`, `disponibilidade_profissional`, `excecao_disponibilidade`)
- Singular (não plural) ✓ — `cliente`, não `clientes`
- FKs como `<entidade>_id` ✓ — `cliente_id`, `procedimento_id`
- M2M intermediárias com nome composto ✓ — `cliente_tag`, `profissional_procedimento`
- Idioma português consistente ✓ (modelo single-tenant BR)

### 🟡 Inconsistências detectadas

| # | Tabela | Problema | Sugestão |
|---|---|---|---|
| 1 | `web_push_subscription` | Inglês — quebra padrão PT | Renomear `assinatura_push_web` ou aceitar termo técnico |
| 2 | `aceite_privacidade` vs `assinatura_termo_procedimento` | Padrões diferentes p/ termos LGPD | Padronizar `aceite_termo_*` |
| 3 | `codigo_verificacao` vs `otp_code` | 2 tabelas similares | Mergear ou esclarecer separação semântica |
| 4 | `prontuario_pergunta` + `prontuario_resposta` legacy vs `formulario_anamnese` + `resposta_anamnese` novo | Duplicação conceitual | Migrar legado p/ formulario_anamnese, deprecar prontuario_pergunta |

---

## 2. Timestamps (criado_em / atualizado_em)

### Status atual

- **TimestampedMixin** existe em `models/mixins.py` ✓
- **Aplicado apenas nos extras novos** (PatchTest, FotoAntesDepois, Produto, etc.)
- Models legacy: inconsistentes

### 🔴 Tables SEM `criado_em` (32 de 47)

Muitas tabelas relacionais simples (ok), MAS principais faltando:
- `bloqueio_agenda` (auditável — quem bloqueou quando)
- `disponibilidade_profissional` (idem)
- `excecao_disponibilidade`
- `lista_espera`
- `notificacao`
- `pacote_cliente` (compra de pacote SEM data???)
- `procedimento`
- `profissional`
- `usuario`

### 🔴 Tables SEM `atualizado_em` (33 de 47)

- Críticas: `cliente`, `procedimento`, `profissional`, `usuario`, `prontuario` — sem rastreamento de última edição

### Recomendação

Aplicar `TimestampedMixin` em **todos** modelos de negócio. Migration consolidada.

```python
class Procedimento(TimestampedMixin, models.Model):
    ...
```

Ganho: rastreamento auditoria + queries `WHERE atualizado_em > X` para sync.

**Esforço**: 1 migration, ~30 min. Risco baixo (apenas adiciona colunas nullable).

---

## 3. Soft delete

### Status atual

- **SoftDeleteMixin** existe em `models/mixins.py` ✓
- **Aplicado apenas em**: `Cliente` (manualmente, não via mixin)
- 46 de 47 tabelas SEM `deletado_em`

### Cal.com pattern
Cal.com não usa soft delete universal — apenas em `User` e `EventType`. Hard delete em bookings antigos.

### OpenEMR pattern
Usa flag `activated` (boolean) — semelhante ao nosso `ativo` em `Procedimento`/`Profissional`/`Usuario`.

### Recomendação seletiva

| Tabela | Soft delete | Por quê |
|---|---|---|
| `cliente` | ✅ JÁ TEM | LGPD anonimização |
| `profissional` | adicionar | Histórico atendimentos não pode quebrar |
| `procedimento` | adicionar | Idem |
| `pacote` | adicionar | Pacotes vendidos referenciam |
| `usuario` | adicionar | Audit log referencia |
| `formulario_anamnese` | adicionar | Respostas referencia |
| `tag` | adicionar | ClienteTag referencia |
| `produto` | adicionar | MovimentoEstoque referencia |
| Demais | hard delete OK | Dependências cascade |

**Esforço**: 1 migration, ~20 min. Aplicar `SoftDeleteMixin`.

---

## 4. Tipos de PK

### Status atual
- `default_auto_field = 'django.db.models.BigAutoField'` no AppConfig ✓
- Todos models novos usam BigAutoField (8 bytes, ~9 quintilhões de IDs)
- Models pré-Django 3.2 podem ter `AutoField` (4 bytes, 2 bilhões) — verificar migration history

### Cal.com
Usa `String UUID` para Booking.uid (URL pública estável) + `Int Auto` para id interno. Padrão dual.

### Recomendação

Adicionar `uid` UUID em entidades que aparecem em URLs públicas:

| Tabela | Campo UUID atual | Sugestão |
|---|---|---|
| `atendimento` | `token_cancelamento` (64 chars) ✓ | OK |
| `profissional` | `slug` ✓ | OK |
| `cliente` | — | Adicionar `uid` UUID p/ DSAR LGPD |
| `pacote_cliente` | — | Adicionar p/ link de visualização cliente |

---

## 5. Indexes

### ✅ Bem indexado
- Atendimento: `idx_atendimento_status`, `idx_atendimento_data`, `idx_atendimento_cli_status`, `idx_atend_prof_data` ✓
- Cliente: `idx_cliente_telefone`, `idx_cliente_nome` ✓
- Notificacao: 4 indexes compostos ✓

### 🟡 Faltam (queries comuns)

| Tabela | Index sugerido | Motivo |
|---|---|---|
| `cliente` | `idx_cliente_email_ativo` | Busca cliente_existente em booking |
| `cliente` | `idx_cliente_cpf` (já tem unique) | Busca cliente por CPF |
| `procedimento` | `idx_procedimento_categoria_ativo` (composto) | Filtro `/agendamento/` por categoria |
| `pacote_cliente` | `idx_pacote_cli_status_exp` | Query pacotes expirando |
| `notificacao` | já bom | — |

---

## 6. Constraints / integridade referencial

### ✅ Bons
- `CheckConstraint` em status (Atendimento, Pacote, etc.) ✓
- `UniqueConstraint` em `cliente_tag` (cliente, tag) ✓
- `UniqueConstraint` em `excecao_disponibilidade` (profissional, data, tipo) ✓
- `on_delete=RESTRICT` em FKs críticas (Atendimento.cliente/profissional/procedimento) ✓
- `on_delete=CASCADE` apropriado em relacionamentos detalhe (ItemPacote, ItemPlanoTratamento) ✓

### 🟡 Faltam
| Constraint | Tabela | Motivo |
|---|---|---|
| `chk_atendimento_fim_apos_inicio` | atendimento | já existe ✓ |
| `chk_data_nascimento_passado` | cliente | data_nascimento <= today |
| `chk_promocao_desconto_range` | promocao | desconto_percentual entre 0 e 100 |
| `chk_pacote_validade_positiva` | pacote | validade_meses > 0 |
| `chk_estoque_atual_positivo` | produto | já tem MinValueValidator (mas DB-level seria melhor) |

---

## 7. Padrões de status (FSM)

### ✅ Atendimento
`PENDENTE → AGENDADO → CONFIRMADO → REALIZADO/CANCELADO/FALTOU/REAGENDADO`
- TRANSICOES dict + métodos (`confirmar`, `cancelar`, etc.) ✓
- LogAuditoria automático ✓

### 🟡 Falta FSM em
- `PacoteCliente`: status ATIVO/FINALIZADO/CANCELADO/EXPIRADO — sem TRANSICOES dict
- `PlanoTratamento`: status ATIVO/CONCLUIDO/PAUSADO/CANCELADO — sem TRANSICOES
- `MovimentoCredito`: idempotente, sem FSM (OK)

### Recomendação
Padronizar via mixin `FSMMixin` ou django-fsm-2 (já considerado em sprint passada).

---

## 8. Comparação com Cal.com

| Conceito Cal.com | Tabela atual | Equivalente? |
|---|---|---|
| `User` (organizer) | `usuario` + `profissional` | ✓ (split em 2) |
| `EventType` (template bookable) | `procedimento` | ~ (procedimento é catálogo, eventType é mais rico c/ buffer/min-notice por type) |
| `Booking` | `atendimento` | ✓ |
| `BookingReference` (calendar sync IDs) | `atendimento.gcal_event_id`? | **Falta** — sem campo p/ ID externo de evento sync |
| `Schedule` (working plan) | `disponibilidade_profissional` | ✓ |
| `Availability` (slot day) | `disponibilidade_profissional` | ~ |
| `DateOverride` (exception) | `excecao_disponibilidade` | ✓ |
| `Webhook` | `workflow_regra` (acao=WEBHOOK) | ~ (Cal.com tem dedicado) |
| `App` (integração) | settings env vars | ~ |
| `Workflow` (automation) | `workflow_regra` ✓ | ✓ |
| `Reminder` | `notificacao` | ~ (Cal.com tem reminder dedicado) |
| `Credentials` (oauth tokens) | `profissional.gcal_refresh_token` | ✓ inline |
| `Membership` (team) | N/A (single-tenant) | — |
| `OutOfOfficeEntry` | `excecao_disponibilidade` (FOLGA) | ✓ |

### Ganhos potenciais a copiar
1. **`BookingReference`** dedicado: tabela separada com `(atendimento_id, type, externalId)` p/ suportar múltiplas integrações (GCal, Outlook, Zoom)
2. **`Reminder`** dedicado: separar de `notificacao` p/ regras `at_event-24h`, `at_event-2h` configuráveis por evento
3. **`Webhook`** dedicado: payload + signing secret + retries

---

## 9. Comparação com OpenEMR

| Conceito | OpenEMR | Atual |
|---|---|---|
| Calendar event | `openemr_postcalendar_events` | `atendimento` |
| Patient | `patient_data` | `cliente` |
| Form/Encounter | `form_encounter` | `atendimento` + `anotacao_sessao` |
| Allergy | `lists` (type=allergy) | `prontuario.alergias` (texto) + `patch_test` |
| Drug/Medication | `drugs` + `prescriptions` | **Falta** — sem prescrição estruturada |
| Insurance | `insurance_data` | N/A (particular) |
| Billing | `billing` | **Falta** (decisão sua: pular pagamento) |

### Ganho: prescrição estruturada
Biomédica pode prescrever ativos. Modelo `Prescricao` opcional futuro.

---

## 10. Padrões Pabau (aesthetic clinic specific)

### Já cobertos
- ✓ Patient CRM (Cliente)
- ✓ Treatment history (Atendimento + AnotacaoSessao)
- ✓ Allergies (Prontuario)
- ✓ Patch test (PatchTest novo)
- ✓ Foto antes/depois (FotoAntesDepois novo)
- ✓ Estoque (Produto + MovimentoEstoque novos)
- ✓ Tags clientes (Tag novo)
- ✓ Pacote / membership
- ✓ Plano tratamento (PlanoTratamento novo)
- ✓ Crédito cliente (CreditoCliente novo)

### 🟡 Não cobertos
- **Comunicação log unificado**: Pabau tem `Communication` (todas mensagens enviadas/recebidas em 1 timeline). Atual: dispersos em `Notificacao`, `OtpCode`, `whatsapp` webhook
- **Document attachment** genérico: Pabau permite anexar PDFs/imagens em qualquer entity. Atual: só `FotoAntesDepois` + `imagem_destaque` em Procedimento
- **eRx (e-prescription)**: prescrição digital
- **Insurance claim**: irrelevante (particular)
- **Online forms** customizáveis pelo paciente: parcialmente (FormularioAnamnese)

---

## 11. Sugestões consolidadas (priorizadas)

### 🔴 Alta prioridade

1. **Aplicar `TimestampedMixin` em models legacy** — `Cliente`, `Procedimento`, `Profissional`, `Usuario`, `Notificacao`, `Pacote`, `PacoteCliente` (audit trail)
2. **Aplicar `SoftDeleteMixin` em** — `Profissional`, `Procedimento`, `Pacote`, `Usuario`, `Tag`, `Produto`, `FormularioAnamnese`
3. **Adicionar `uid` UUID em `Cliente`** — DSAR LGPD link público estável
4. **Indexes faltando** — `cliente(email, ativo)`, `procedimento(categoria, ativo)`, `pacote_cliente(cliente, status, data_expiracao)`
5. **Cal.com `BookingReference`** — tabela `atendimento_referencia_externa(atendimento_id, tipo, external_id)` p/ múltiplas integrações sync

### 🟡 Média

6. **CheckConstraint adicionais**: `cliente.data_nascimento <= today`, `promocao.desconto_percentual BETWEEN 0 AND 100`, `pacote.validade_meses > 0`
7. **FSM em PacoteCliente + PlanoTratamento** — métodos transition + LogAuditoria
8. **Mergear `prontuario_pergunta`/`prontuario_resposta` em `formulario_anamnese`/`resposta_anamnese`** — eliminar duplicação
9. **Padronizar nome `web_push_subscription`** → `assinatura_push_web` (ou aceitar termo técnico)
10. **Renomear `aceite_privacidade` → `aceite_termo_privacidade`** (consistente c/ `assinatura_termo_procedimento`)

### 🟢 Baixa

11. **Tabela `prescricao`** futura (biomédica) — não urgente
12. **Tabela `comunicacao_log`** unificada — Pabau pattern
13. **Document attachment genérico** — generic FK p/ anexar arquivo a qualquer entity

---

## 12. Migration consolidada sugerida (próximo sprint)

```python
# 0023_padronizacao_db.py
operations = [
    # Soft delete em entities críticas
    migrations.AddField('Profissional', 'deletado_em', DateTimeField(null=True, db_index=True)),
    migrations.AddField('Procedimento', 'deletado_em', ...),
    migrations.AddField('Pacote', 'deletado_em', ...),
    migrations.AddField('Usuario', 'deletado_em', ...),
    migrations.AddField('Tag', 'deletado_em', ...),

    # Timestamps em legacy
    migrations.AddField('Procedimento', 'criado_em', DateTimeField(auto_now_add=True)),
    migrations.AddField('Procedimento', 'atualizado_em', DateTimeField(auto_now=True)),
    migrations.AddField('Profissional', 'criado_em', ...),
    migrations.AddField('Profissional', 'atualizado_em', ...),
    migrations.AddField('Pacote', 'criado_em', ...),
    migrations.AddField('PacoteCliente', 'atualizado_em', ...),
    # ... etc

    # UUID em Cliente
    migrations.AddField('Cliente', 'uid', UUIDField(default=uuid.uuid4, unique=True)),

    # Indexes
    migrations.AddIndex('Cliente', Index(fields=['email', 'ativo'], name='idx_cliente_email_ativo')),
    migrations.AddIndex('Procedimento', Index(fields=['categoria', 'ativo'], name='idx_procedimento_cat_ativo')),
    migrations.AddIndex('PacoteCliente', Index(fields=['cliente', 'status', 'data_expiracao'], name='idx_pkcli_status_exp')),

    # Check constraints
    migrations.AddConstraint('Cliente', CheckConstraint(
        check=Q(data_nascimento__lte=date.today()), name='chk_cli_nasc_passado'
    )),
    migrations.AddConstraint('Promocao', CheckConstraint(
        check=Q(desconto_percentual__gte=0) & Q(desconto_percentual__lte=100),
        name='chk_promo_desconto_range'
    )),

    # BookingReference nova tabela
    migrations.CreateModel('AtendimentoReferenciaExterna', ...),
]
```

**Esforço total**: ~3-4h (migration + adapters de queries que dependem de hard delete).

---

## Sources

- [Cal.com Prisma schema](https://github.com/calcom/cal.com/blob/main/packages/prisma/schema.prisma)
- [Cal.com EventType configuration](https://deepwiki.com/calcom/cal.com/2.1-event-type-configuration)
- [Easy!Appointments working plan exceptions](https://easyappointments.org/2021/01/26/working-plan-exceptions-explained/)
- [OpenEMR database structure](https://www.open-emr.org/wiki/index.php/Database_Structure)
- [OpenEMR appointment scheduling](https://deepwiki.com/openemr/openemr/3.2-appointment-scheduling)
- [Django timestamps + soft delete (xgeeks)](https://medium.com/xgeeks/timestamps-and-soft-delete-with-django-65f74d86e022)
- [django-soft-delete pattern](https://pypi.org/project/django-soft-delete/)
- [Django models best practices](https://mshaeri.com/blog/django-best-practices-part-1/)
