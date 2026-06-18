# Onda 2 — Migração do Wizard de Booking — Plano

> Abordagem **pragmática** (aprovada): re-skin Tailwind/tokens + externalizar o JS que já funciona (CSP-safe) + corrigir os 2 bugs alta. SEM reescrita Alpine reativa (é o produto — minimizar risco de regressão). Cross-ref: [`../ARCHITECTURE.md`](../ARCHITECTURE.md), [`../specs/fundacao-front-design.md`](../specs/fundacao-front-design.md).

**Alvo:** `aranha_estetica/templates/agenda/agendamento_publico.html` (~1131 linhas, 3 steps, JS vanilla IIFE).

## Contrato a PRESERVAR (não quebrar o backend)
- Form field names: `procedimento`, `profissional`, `datetime`, `nome`, `telefone`, `data_nascimento`, `email`, `anamnese_respostas`, `website` (honeypot), `consent_*`, `aceita_cadastro`, `csrfmiddlewaretoken`.
- Hidden inputs por ID: `#form-procedimento`, `#form-profissional`, `#form-datetime`, `#anamneseRespostasJson`.
- Data-attrs: `data-proc-id/nome/preco/duracao`, `data-categoria`, `data-date`, `data-idx`.
- IDs de fluxo: `#step-1/2/3`, `#cal-days-grid`, `#btn-enviar-otp`, `#btn-verificar-otp`.
- Endpoints (URLs por `{% url %}`): `api_dias_disponiveis`, `api_horarios_disponiveis`, `solicitar_otp_agendamento`, `verificar_otp_agendamento`, `confirmar_agendamento`.
- JSON da anamnese (`FORMULARIOS_ANAMNESE` via `json_script`), session keys do OTP, CSRF.

## Ordem incremental (cada passo deixa a página funcionando)

### W1 — Externalizar o JS (sem mudar visual)
- Extrair o `<script>` inline (~linhas 407-429, 647-1128) p/ `aranha_estetica/static/js/wizard.js`.
- Carregar via `<script src="{% static 'js/wizard.js' %}" defer></script>` (external 'self' — CSP ok; funciona na base atual E na base_v2).
- Converter os 2 `onclick="goToStep(n)"` (467, 630) → `data-goto="1|2"` + `addEventListener` no wizard.js.
- **Sem mudar lógica.** Página segue Bootstrap, segue funcionando.
- Verificar fluxo de booking no browser.

### W2 — Re-skin do template p/ base_v2 + Tailwind
- Trocar `{% extends %}` p/ `estrutura/base_v2.html`.
- Converter markup Bootstrap → Tailwind/tokens; `<style>` inline (8-323) → utilitários Tailwind (bits complexos do calendário podem ficar num `<style nonce>` scoped no `{% block css %}` temporariamente).
- Preservar TODOS os IDs/names/data-attrs/hiddens do contrato.
- wizard.js (estático) continua carregando no `{% block js %}`.
- Verificar visual + fluxo.

### W3 — Bug A: a11y de teclado
- `.proc-card` (template) e `.cal-day` (criado em JS) → `<button type="button">` (Enter/Espaço nativos) + `:focus-visible`.
- Ajustar seletores no wizard.js se preciso (continuam `.proc-card`/`.cal-day` como classe).
- Verificar navegação 100% por teclado (Tab + Enter) nos cards e dias.

### W4 — Bug B: estado não se perde no reject
- wizard.js: salvar `{selectedProc, selectedDate, selectedSlot, selectedProf}` em `sessionStorage` a cada seleção; re-hidratar no load.
- `views/booking_public.py`: no conflito/erro, em vez de `redirect` cego, re-renderizar o wizard preservando contexto OU sinalizar erro p/ o JS re-hidratar e voltar ao step 3 (sem novo OTP se a sessão server ainda tiver OTP válido).
- Verificar: simular conflito → usuário volta ao step 3 com tudo preenchido.

### W5 — Verificação + regressão
- Fluxo completo no browser: procedimento → data → slot → profissional → form → OTP → confirmar (light + dark + teclado).
- Testes: rota 200, contrato de campos presente, 404 em prod se aplicável; suite verde; `makemigrations --check`.

## Notas
- wizard.js é estático puro (vanilla, sem imports) — NÃO precisa ser entry do Vite; `{% static %}` + whitenoise hash em prod basta.
- Cores via tokens (placeholder dourado); identidade plena (Direção 2) entra quando vierem as cores — mas o wizard é funcional, pouco dependente disso.
- Não tocar nas views de slot/OTP/confirmar além do fix do W4 (reject).

_Criado 2026-06-18._
