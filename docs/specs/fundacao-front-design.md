# Fundação do Front-end — Design System (Onda 1)

> Spec única (sobrescrever a cada revisão). Status: **APROVADA em brainstorm 2026-06-14**.
> Direção visual: **D2 "Clínico-premium"**. Stack alvo aprovada: **Tailwind v4 + HTMX + Alpine + django-cotton + Vite**.
> Cross-ref: [`../ARCHITECTURE.md`](../ARCHITECTURE.md) (escopo geral / 67 telas / ondas).

## 1. Objetivo & escopo

Construir a **fundação** que padroniza site público **+** admin PWA com um design system único,
substituindo o template comprado (Bootstrap + jQuery + tema). Abordagem **lean incremental**:
só o que Home/Wizard precisam primeiro; a lib cresce conforme as telas pedem.

**Fatia 1 (esta spec):** tokens + casca do site + 6 componentes base.
**Fora da Fatia 1:** componentes do wizard (stepper/slot/calendário) → Onda 2; app-shell do admin PWA → Onda 3.

## 2. Decisões fixadas

| # | Decisão | Razão |
|---|---|---|
| F1 | Tailwind v4 (tokens via CSS vars) | 1 config p/ site+admin; mata Bootstrap/2 dark modes/26 `<style>` inline |
| F2 | HTMX + Alpine (sem SPA) | parcial sem reload + estado local; mata jQuery |
| F3 | django-cotton p/ componentes `<c-*>` | mata monolitos/duplicação |
| F4 | Vite + django-vite (build) | HMR dev + bundla CSS/Alpine/HTMX + hash; fallback: Tailwind CLI standalone (zero-Node) |
| F5 | Dark mode **opcional (toggle), padrão claro** nos dois | unifica os 2 dark modes atuais; marketing fica melhor claro |
| F6 | Tema via tokens semânticos + `data-theme` + cookie | sem FOUC (servidor pinta certo no 1º render) |
| F7 | Coexistência: 2 bundles que não dividem página | preflight Tailwind não briga com Bootstrap |
| F8 | Alpine via **`@alpinejs/csp`** (build CSP-safe) | mantém CSP sem `unsafe-eval`; **regra:** lógica em `Alpine.data()`, usar `x-data`/`x-on:`/`x-bind:`, SEM expressão JS inline em atributo nem `onclick` |

## 3. Arquitetura de tokens

Três camadas, sentido único — componente nunca usa cor crua:

```
PALETA crua            →   TOKEN semântico              →   COMPONENTE
--ouro-500 #C9A84C         --cor-marca / -marca-forte       usa só semântico
--neutro-50..900           --cor-fundo / -superficie
(placeholder; troca        --cor-texto / -texto-suave
 1 lugar c/ cor real)      --cor-borda / -sucesso/-erro/…
```

- **Light = `:root`**; **dark = `[data-theme="dark"]`** sobrescreve os mesmos tokens semânticos.
- **Tailwind v4 `@theme`** liga utilitários (`bg-superficie`, `text-marca`) aos vars → vale utilitário e CSS cru/cotton.
- **Tokens cobrem:** cor, tipografia (1 serif display + 1 sans; 2 pesos: 400/500), espaçamento, raio, sombra, z-index, breakpoints.
- **Cor da marca = 1 fonte** (placeholder dourado `#C9A84C`); cores reais entram trocando a paleta crua num lugar.
- **Toggle de tema:** `data-theme` no `<html>`; persiste em **cookie** (servidor lê → render correto) + localStorage; botão controlado por Alpine. Default light.

## 4. Casca do site

Rebuild no Tailwind, já corrigindo achados da auditoria de front:
- **`head`:** tokens + **fontes self-hosted** (1 serif display + 1 sans, subset woff2) + meta/SEO mantidos; **remove** o render-blocking (Bootstrap 232KB, 2 libs de ícone, 5 fontes Google, jQuery, swiper/glightbox globais). Lê cookie de tema (sem flash).
- **`cabecalho`:** logo/monograma + nav + menu mobile (Alpine, **focável** — corrige gap a11y) + toggle de tema + CTA Agendar.
- **`rodape`:** institucional + LGPD.
- **`estrutura/base.html`** nova (Tailwind). Ícones: **1 set SVG inline** (corrige as 2 libs de fonte-ícone).

## 5. Biblioteca de componentes (lean — Fatia 1)

Componentes django-cotton `<c-*>`, nomes PT-BR, estilo 100% via tokens:

| Componente | Variantes / nota | Corrige |
|---|---|---|
| `<c-botao>` | sólido / ghost / link; estado loading | botões inconsistentes |
| `<c-card>` | superfície + borda token | cards ad-hoc |
| `<c-campo>` | input + `<label for>` + mensagem de erro | **label sempre** (15 inputs sem label) |
| `<c-toast>` | conteúdo via **textContent** + `role`/`aria-live` | **mata DOM-XSS** + leitor de tela |
| `<c-modal>` | Alpine, foco preso, ESC fecha | substitui `window.confirm()` (21×) |
| `<c-badge>` | mapa de status canônico único | `.status-badge` forkado |

## 6. Estrutura de arquivos

```
static/src/css/tokens.css      paleta + semânticos + [data-theme=dark]
static/src/css/app.css         @import tailwind + tokens
static/src/js/app.js           Alpine + HTMX + toggle de tema
templates/cotton/              componentes <c-*>
templates/estrutura/base.html  casca nova (Tailwind)
vite.config / django-vite      build (HMR dev, bundle hash prod)
```

## 7. Coexistência & migração

- Página **migrada** usa **base nova (Tailwind puro, sem Bootstrap)**; página **antiga** segue Bootstrap.
- **2 bundles que nunca dividem a mesma página** → preflight do Tailwind não conflita com Bootstrap.
- Migração página-a-página; **Bootstrap sai quando a última página migrar**.
- **Ordem (pós-fundação):** Home → Wizard → resto do site → admin PWA → e-mails.
- Whitenoise serve os bundles hasheados (compressão/cache já existentes).

## 8. Pendências (não bloqueiam a spec)

- **Cores reais da marca** + **1-2 referências** → calibram os tokens (hoje placeholder dourado).
- **Fontes** definitivas (D2 usou Marcellus + Hanken Grotesk como mock) → confirmar com refs.
- Decisão final build: Vite (recomendado) vs Tailwind CLI standalone.

## 9. Próximo passo

Fatia 1 vira plano de implementação (`writing-plans`) quando o dono pedir. Critério de pronto da Fatia 1:
tokens+tema funcionando, casca do site no ar, 6 componentes prontos, 1 página piloto (Home) migrada como prova.

---

_Última atualização: 2026-06-14 — criação (brainstorm aprovado)._
