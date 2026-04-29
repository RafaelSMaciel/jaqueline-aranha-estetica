# Roadmap Melhorias — Jaqueline Aranha Estética

Versão: 2026-04-29
Status: **Sprints 1-5 concluídas** ✅
Stack: Django 5.2 + Postgres + Redis + Celery + DRF + Bootstrap 5 + Cloudflare Turnstile
Escopo: clínica single-tenant (biomédica Jaqueline Aranha — atendimento exclusivo)

---

## Sumário

- [Sprint 1 — Fundação back-end + agenda visual](#sprint-1--fundacao-back-end--agenda-visual)
- [Sprint 2 — UX painel + PWA](#sprint-2--ux-painel--pwa)
- [Sprint 3 — UX cliente + ICS](#sprint-3--ux-cliente--ics)
- [Sprint 4 — Automação + FSM + push](#sprint-4--automacao--fsm--push)
- [Sprint 5 — Diferenciação](#sprint-5--diferenciacao)
- [Pós-deploy de cada sprint](#pos-deploy-de-cada-sprint)

---

## Sprint 1 — Fundação back-end + agenda visual

### #1 Working Plan c/ exceções por data

**Estado atual**: `DisponibilidadeProfissional` (semanal) + `BloqueioAgenda` (datetime range) + `Feriado`. Falta override por data específica c/ horário diferente (não só bloqueio).

**Steps**:
1. Criar model `ExcecaoDisponibilidade` em `aranha_estetica/models/profissionais.py`:
   - `profissional` FK
   - `data` DateField
   - `tipo` choices: `FOLGA` / `HORARIO_DIFERENTE`
   - `hora_inicio` Time nullable
   - `hora_fim` Time nullable
   - `motivo` Text nullable
2. Adicionar ao `__init__.py` exports.
3. Migration: `python manage.py makemigrations aranha_estetica`.
4. Atualizar `Profissional.get_horarios_disponiveis()` p/ consultar exceção antes da regra semanal.
5. Tela admin: `painel/disponibilidade.html` c/ tabela exceções + form add/edit.
6. View: `views/admin_disponibilidade.py`.
7. URL: `painel/profissionais/<id>/disponibilidade/`.
8. Teste: criar exceção HORARIO_DIFERENTE em data X, verificar slots refletem.

### #2 Buffer entre atendimentos

**Steps**:
1. Adicionar campo `buffer_minutos = SmallIntegerField(default=0)` em `Procedimento`.
2. Migration.
3. Atualizar gerador slots: bloquear slots dentro de `[fim_atendimento, fim_atendimento + buffer]`.
4. UI admin procedimento_form.html: input buffer (min 0, max 60).
5. Teste: procedimento c/ buffer 15min — slot adjacente fica indisponível.

### #3 Min-notice / max-advance

**Steps**:
1. Adicionar em `Profissional`:
   - `min_notice_horas = SmallIntegerField(default=2)`
   - `max_advance_dias = SmallIntegerField(default=60)`
2. Migration.
3. View pública booking: filtrar slots `< now() + min_notice` e datas `> now() + max_advance`.
4. UI: configurações_profissional inputs.
5. Teste: setar min_notice=24, tentar agendar daqui 1h, deve bloquear.

### #6 FullCalendar em painel/calendar.html

**Estado atual**: template `calendar.html` existe (criado mas vazio). View `admin_calendar.py` existe.

**Steps**:
1. Confirmar `calendar.html` atual + view atual.
2. Adicionar ao base.html FullCalendar CDN:
   - `https://cdn.jsdelivr.net/npm/fullcalendar@6.1.10/index.global.min.js`
   - locale PT-BR.
3. Endpoint JSON `painel/api/agenda/events.json?start=&end=&prof_id=`:
   - Retorna lista `[{id, title, start, end, color, extendedProps}]`
   - Filtra `Atendimento` no range
   - Adiciona `BloqueioAgenda` como `display: 'background'`
4. Template `calendar.html`:
   - `<div id="calendar"></div>`
   - JS init c/ `events: '/painel/api/agenda/events.json'`
   - Views: dayGridMonth, timeGridWeek, timeGridDay
   - Click event → modal detalhes
5. CSS: integrar paleta `--admin-accent`.
6. Teste: criar agendamentos, abrir calendar, verificar render mês/semana/dia.

### #8 Cards KPI overview

**Estado atual**: `overview.html` existe.

**Steps**:
1. View overview: agregar:
   - `agendamentos_hoje` count
   - `agendamentos_semana` count
   - `receita_mes` sum valor_cobrado WHERE status=REALIZADO
   - `taxa_ocupacao` = atendidos / slots disponíveis (semana)
   - `clientes_ativos` (atendimento últimos 90d)
   - `nps_medio` últimos 30d
2. Template: 6 cards Bootstrap 5 grid responsivo.
3. CSS: gradient `--admin-dark`, ícones Font Awesome (fa-calendar, fa-money, fa-percent, fa-users, fa-star).
4. Teste visual: dados mock + dados reais.

---

## Sprint 2 — UX painel + PWA

### #10 PWA drop-in (django-pwa)

**Estado atual**: meta tags PWA já em base.html. Manifest URL roteada (`jaqueline-aranha-estetica:manifest`) mas sem lib oficial.

**Steps**:
1. `pip install django-pwa==2.0.1` → adicionar requirements.txt.
2. settings/base.py:
   - `INSTALLED_APPS += ['pwa']`
   - Vars: `PWA_APP_NAME`, `PWA_APP_DESCRIPTION`, `PWA_APP_THEME_COLOR='#C9A84C'`, `PWA_APP_BACKGROUND_COLOR='#1a1a2e'`, `PWA_APP_DISPLAY='standalone'`, `PWA_APP_ORIENTATION='portrait'`, `PWA_APP_ICONS=[{...}]`, `PWA_APP_DIR='ltr'`, `PWA_APP_LANG='pt-BR'`.
3. urls.py: `path('', include('pwa.urls'))` (cuidar conflito c/ manifest custom — remover URL custom).
4. base.html: `{% load pwa %}`, adicionar `{% progressive_web_app_meta %}` no `<head>`.
5. Criar `serviceworker.js` em static/ (django-pwa expõe placeholder).
6. Teste: abrir Chrome DevTools → Application → Manifest. Lighthouse PWA score.
7. Mobile: Android Chrome → "Add to Home screen" funciona.

### #7 AdminLTE base.html (sidebar/topbar — opcional refactor)

**Decisão**: a aplicação já tem visual custom (admin-topbar dark gradient). **Pular AdminLTE full migration** — só roubar componentes específicos quando precisar.

**Steps reduzidos**:
1. Manter base.html atual.
2. Importar **só** componentes AdminLTE quando vier um PR específico (ex: timeline, datatables).

### #9 Drag-drop reagendamento via FullCalendar

**Steps** (depende #6):
1. FullCalendar config: `editable: true`, `eventDrop: handleDrop`.
2. Endpoint `PATCH painel/api/atendimento/<id>/reagendar`:
   - Body: `{ data_hora_inicio, data_hora_fim }`
   - Valida slot livre (regras working plan + buffer + min-notice)
   - Cria registro reagendado_de
   - Status → REAGENDADO
3. CSRF token via header.
4. UI: confirmação modal antes de salvar.
5. Teste: arrastar evento, ver persistência DB.

### #19 DataTables AdminLTE em listas

**Steps**:
1. Adicionar DataTables CDN no base.html:
   - `https://cdn.datatables.net/1.13.7/css/dataTables.bootstrap5.min.css`
   - `https://cdn.datatables.net/1.13.7/js/jquery.dataTables.min.js`
   - jQuery 3.7
2. Aplicar em `agendamentos.html`, `clientes.html` (já existe?), `usuarios.html`:
   - `<table class="table" id="tbl-agendamentos">`
   - JS init: `$('#tbl-agendamentos').DataTable({ language: { url: 'pt-BR.json' }})`
3. Server-side processing se >1000 rows (paginação backend).

### #21 Timeline cliente_detalhe.html

**Estado atual**: `cliente_detalhe.html` existe.

**Steps**:
1. View: query `Atendimento.objects.filter(cliente=cliente).order_by('-data_hora_inicio')`.
2. Calcular LTV: `sum(valor_cobrado WHERE status=REALIZADO)`.
3. Última visita, ticket médio, procedimento mais frequente.
4. Template: timeline vertical Bootstrap (CSS custom ou bootstrap-icons).
5. Cards: LTV / última visita / total atendimentos / ticket médio.

---

## Sprint 3 — UX cliente + ICS

### #4 URL pública por profissional (slug)

**Steps**:
1. Adicionar `slug = SlugField(unique=True, blank=True)` em `Profissional`.
2. Migration data: gerar slug via `slugify(nome)` p/ existentes.
3. Override `save()` p/ gerar slug automático.
4. URL: `path('agendar/<slug:prof_slug>/', views.booking.publico_por_profissional)`.
5. View: filtra `Profissional.objects.get(slug=prof_slug, ativo=True)`.
6. Teste: `/agendar/jaqueline-aranha/` carrega.

### #5 ICS feed por profissional

**Steps**:
1. `pip install django-ical==1.9.2`.
2. View `views/agenda_ics.py`:
```python
from django_ical.views import ICalFeed
class ProfissionalAgendaFeed(ICalFeed):
    product_id = '-//aranha-estetica//agenda//PT-BR'
    timezone = 'America/Sao_Paulo'
    def get_object(self, request, slug): ...
    def items(self, prof): return Atendimento.objects.filter(profissional=prof, status__in=['AGENDADO','CONFIRMADO'])
    def item_title(self, item): ...
    def item_start_datetime(self, item): return item.data_hora_inicio
```
3. URL: `agenda/<slug:prof_slug>/feed.ics`.
4. Auth: query param `?token=<hash>` p/ não vazar dados.
5. Teste: assinar URL no Google Calendar, ver eventos.

### #13 Reagendamento self-service link mágico

**Estado atual**: `Atendimento.token_cancelamento` já existe. Falta endpoint reagendamento.

**Steps**:
1. View `agendar/reagendar/<token>/`:
   - Busca atendimento por token.
   - Verifica TTL (ex: 7 dias antes do evento).
   - Renderiza form c/ slots disponíveis (working plan + buffer).
2. POST: cria novo atendimento c/ `reagendado_de=original`, marca original como REAGENDADO.
3. Email/WhatsApp confirmação contém link `/agendar/reagendar/<token>/`.
4. Rate limit 5/h por IP.
5. Teste E2E.

### #20 Booking step-by-step público

**Steps**:
1. Refatorar `publico/home.html` ou criar `publico/agendar.html`:
   - Step 1: escolher procedimento (cards c/ categoria filter)
   - Step 2: escolher profissional (se >1)
   - Step 3: data picker + slots
   - Step 4: dados cliente (nome, tel, email)
   - Step 5: OTP SMS + Turnstile
   - Step 6: confirmação
2. JS state machine vanilla (sem framework — manter Django templates).
3. Validação Turnstile no Step 5.
4. Inspiração visual: easyappointments.

### #22 Service categories

**Steps**:
1. Adicionar `categoria = CharField(max_length=30, choices=CATEGORIA_CHOICES, default='OUTROS')` em `Procedimento`.
2. Choices: FACIAL / CORPORAL / MASSAGEM / DEPILACAO / ESTETICA_AVANCADA / OUTROS.
3. Migration data: classificar 23 procedimentos seedados.
4. UI público: filtro tabs por categoria.
5. UI admin: select categoria no procedimento_form.

---

## Sprint 4 — Automação + FSM + push

### #12 FSM agendamento status (django-fsm-2)

**Estado atual**: `Atendimento.status` CharField c/ choices, transições manuais espalhadas em views.

**Steps**:
1. `pip install django-fsm-2==4.0.0` → requirements.txt.
2. Refactor `Atendimento.status` p/ FSMField:
```python
from django_fsm import FSMField, transition
class Atendimento(models.Model):
    status = FSMField(default='PENDENTE', choices=STATUS_CHOICES, protected=True)

    @transition(field=status, source='PENDENTE', target='CONFIRMADO')
    def confirmar(self): ...

    @transition(field=status, source=['PENDENTE','CONFIRMADO'], target='CANCELADO')
    def cancelar(self, motivo): ...

    @transition(field=status, source='CONFIRMADO', target='REALIZADO')
    def marcar_realizado(self): ...

    @transition(field=status, source='CONFIRMADO', target='FALTOU')
    def marcar_falta(self): ...

    @transition(field=status, source=['PENDENTE','CONFIRMADO'], target='REAGENDADO')
    def reagendar(self, novo_atendimento): ...
```
3. Substituir todas atribuições `atendimento.status = X` por chamadas método.
4. Hooks pós-transição: signal `post_save` dispara workflow #14.
5. Migration (campo continua compatível).
6. Teste: tentar transição inválida → exception.

### #24 FSM auditoria (django-fsm-log)

**Steps**:
1. `pip install django-fsm-log==3.1.0`.
2. INSTALLED_APPS += `'django_fsm_log'`.
3. Migration `python manage.py migrate django_fsm_log`.
4. Tela admin: tab "Histórico" em `cliente_detalhe.html` c/ transições do atendimento.

### #14 Workflow regras dinâmicas

**Estado atual**: `Notificacao` é tabela registro, não regra. Tasks Celery hardcoded.

**Steps**:
1. Criar models em `aranha_estetica/models/workflow.py`:
```python
class WorkflowRegra(models.Model):
    nome = CharField(max_length=100)
    ativo = BooleanField(default=True)
    trigger = CharField(choices=[('ON_BOOK','...'),('BEFORE_EVENT','...'),('AFTER_EVENT','...'),('ON_CANCEL','...'),('ON_NO_SHOW','...')])
    offset_minutos = IntegerField(default=0)  # negativo = antes
    acao = CharField(choices=[('SEND_EMAIL','...'),('SEND_SMS','...'),('SEND_WHATSAPP','...'),('SEND_PUSH','...'),('WEBHOOK','...')])
    template = TextField(blank=True)
    config_json = JSONField(default=dict)
```
2. Migration.
3. Admin painel `painel/workflows.html` (CRUD).
4. Engine: task Celery `executar_workflows()` rodada por cron HTTP a cada 5min.
   - Para cada regra ativa, calcular candidatos (atendimentos elegíveis no offset).
   - Cria `Notificacao` pendente.
   - Existing dispatcher tasks/email/sms/whatsapp consome.
5. Migrar tasks atuais (D-1 WhatsApp, NPS) p/ regras seedadas.
6. Teste: criar regra, simular tempo, verificar disparo.

### #15 No-show tracking + auto-bloqueio

**Steps** (depende #12):
1. Adicionar `Cliente.no_show_count = IntegerField(default=0)`.
2. Hook FSM: `@transition target='FALTOU'` → `cliente.no_show_count += 1`.
3. Adicionar `Cliente.bloqueado = BooleanField(default=False)` + `bloqueado_em`.
4. Threshold config (`ConfiguracaoSistema`): `NO_SHOW_LIMIT=3`.
5. Booking público valida `cliente.bloqueado` antes de criar.
6. Painel: lista clientes bloqueados, botão desbloquear.

### #11 Web Push notifications (django-webpush)

**Steps**:
1. `pip install django-webpush==0.3.6`.
2. INSTALLED_APPS += `'webpush'`.
3. settings: `WEBPUSH_SETTINGS = {'VAPID_PUBLIC_KEY':..., 'VAPID_PRIVATE_KEY':..., 'VAPID_ADMIN_EMAIL':'rafelsebas@gmail.com'}`.
4. Gerar keys: `python -c "from py_vapid import Vapid; ..."` (ou online).
5. URLs: `path('webpush/', include('webpush.urls'))`.
6. JS: subscribe na primeira visita autenticada.
7. Backend send: `from webpush import send_user_notification; send_user_notification(user, payload)`.
8. Aplicar em workflow ações: `SEND_PUSH` adiciona ao Notificacao.canal choices.
9. UI: botão "Ativar notificações" no painel + público.
10. Teste: subscribe → trigger backend → push aparece OS notification.

### #26 Push profissional (novo agendamento)

**Steps** (depende #11):
1. Signal `post_save` Atendimento (novo): se profissional opt-in, dispara push.
2. Payload: "Novo agendamento — {cliente} em {data}".
3. Click → abre painel calendar.

---

## Sprint 5 — Diferenciação

### #16 Sync Google Calendar bidirecional

**Steps**:
1. `pip install google-auth google-auth-oauthlib google-api-python-client`.
2. Criar OAuth client no Google Cloud Console (Calendar API).
3. View `painel/integrations/google/connect/`:
   - Inicia fluxo OAuth.
   - Callback armazena `refresh_token` em `Profissional.google_calendar_token` (encriptado).
4. Sync outbound: signal Atendimento criado → push event Google Cal.
5. Sync inbound: cron 15min consulta `events().list()` → cria `BloqueioAgenda` p/ eventos externos.
6. Toggle on/off por profissional.

### #17 RRULE recorrência (bloqueios)

**Steps**:
1. Adicionar campo `regra_recorrencia = TextField(blank=True)` em `BloqueioAgenda` (formato iCal RRULE).
2. Lib: `pip install python-dateutil` (já tem).
3. Gerador slots usa `dateutil.rrule.rrulestr(regra)` p/ expandir bloqueios recorrentes no range.
4. UI form: campos amigáveis (toda quarta às 13-14h) → constrói RRULE.
5. Teste: bloqueio "toda quarta tarde" → todos os slots quarta-feira ficam bloqueados.

### #18 Anamnese pré-agendamento

**Estado atual**: `Prontuario` model existe.

**Steps**:
1. Criar model `FormularioAnamnese` (versão por procedimento) + `RespostaAnamnese`.
2. Schema JSON dinâmico: `[{tipo:'bool', label:'Gestante?', obrigatorio:true}, ...]`.
3. Booking flow Step 4.5: renderiza form se procedimento exige.
4. Renderer JS dinâmico baseado em schema.
5. Salva resposta vinculada ao Atendimento + Cliente (Prontuario).
6. Admin: criar templates anamnese por categoria procedimento.

### #23 Embed widget iframe

**Steps**:
1. View `embed/agendar/<slug>/` retorna HTML standalone (sem chrome admin/topbar).
2. Headers: `X-Frame-Options: ALLOW-FROM` (ou CSP frame-ancestors).
3. CSS minimal, paleta clinic.
4. Documentar p/ Jaqueline: tag iframe pra colar Linktree/Instagram bio.
5. Postman snippet: `<iframe src="https://jaquelineearanha.com.br/embed/agendar/jaqueline-aranha/" width="100%" height="700"></iframe>`.

### #25 Cache offline avançado (Workbox)

**Steps**:
1. Substituir serviceworker.js do django-pwa por bundle Workbox.
2. Estratégias:
   - **NetworkFirst** p/ HTML (sempre fresco se online)
   - **CacheFirst** p/ /static/ + fonts CDN
   - **StaleWhileRevalidate** p/ /api/ leitura
3. Pre-cache shell crítico: base.html, manifest, ícones.
4. Background sync p/ ações offline (criar agendamento sem rede → sincroniza ao voltar).
5. Teste: DevTools "Offline" → app ainda navega.

---

## Pós-deploy de cada sprint

Conforme `feedback_sempre_atualizar_docs.md`:

1. **Atualizar DOCX requisitos**: rodar `node tmp_req/create_docx.js`.
2. **Gerar PDF**: `docx2pdf` no DOCX gerado.
3. **Bump versão** no DOCX (header).
4. **Commit**: `docs: atualiza requisitos pos-sprint <N>`.
5. **Update Miro ERD** se model mudou (ver `reference_miro_board.md`).
6. **Smoke test**: golden path (criar agendamento) + edge case da feature.
7. **Browser cache purge** se template mudou (ver memória — service worker pode prender versão antiga).

---

## Checklist priorização sugerida

- [ ] Sprint 1 (#1, #2, #3, #6, #8) — base + agenda visual
- [ ] Sprint 2 (#10, #9, #19, #21) — PWA + UX painel
- [ ] Sprint 3 (#4, #5, #13, #20, #22) — UX cliente
- [ ] Sprint 4 (#12, #14, #15, #11, #24, #26) — automação + FSM + push
- [ ] Sprint 5 (#16, #17, #18, #23, #25) — diferenciação

## Pular (decisões já tomadas)

- Sinal MercadoPago — descartado pelo usuário
- Multi-tenant — single-clinic
- Multi-timezone — BR-only
- Mobile app nativo — PWA cobre
- AdminLTE full migration — visual custom já consolidado
- Viewflow BPMN — overkill p/ clínica single-prof
