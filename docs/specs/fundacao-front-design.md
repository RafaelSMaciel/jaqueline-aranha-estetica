# Fundação do Front-end — Design System

> Spec única (sobrescrever a cada revisão). Status: **IMPLEMENTADA** (Ondas 1–4 concluídas;
> 100% do site e do painel na fundação nova). Direção visual **D2 "Clínico-premium"**.
> Stack efetiva: **Vite 6 + Tailwind CSS v4 + Alpine.js (`@alpinejs/csp`) + django-cotton**
> — HTMX saiu (ver F2). Cross-ref: [`../ARCHITECTURE.md`](../ARCHITECTURE.md) (checkpoint),
> [`../PROJECT.md`](../PROJECT.md) §3.

## 1. Objetivo & escopo

Um design system único para o **site público** e o **painel da equipe (PWA)**, substituindo o
template comprado (Bootstrap + jQuery + tema). Resultado: nenhum template usa mais
`estrutura/base.html`, `painel/base.html`, Bootstrap, jQuery, AOS, Swiper ou fontes de ícone.

## 2. Decisões fixadas

| # | Decisão | Estado |
|---|---|---|
| F1 | Tailwind v4, tokens em CSS vars ligados ao `@theme` | ✅ |
| F2 | Interatividade local com Alpine; **sem HTMX** | ✅ HTMX foi incluído na Fatia 1 e **removido** do bundle (nenhum `hx-*` no projeto; injetava `<style>` sem nonce, incompatível com a CSP). Bundle JS caiu de ~137 KB para ~65 KB |
| F3 | django-cotton para componentes `<c-*>` | ✅ (lib enxuta — seção 5) |
| F4 | Vite + django-vite (HMR em dev, manifest com hash em prod) | ✅ `static/dist/` gerado no Docker/CI; `static/src/` fora do collectstatic |
| F5 | Tema claro padrão + escuro opcional (toggle), nos dois lados | ✅ |
| F6 | Tema por tokens semânticos + `data-theme` + cookie, sem FOUC | ✅ cookie `tema` = `claro`/`escuro`; o servidor escreve `<html data-theme="{{ tema }}">` |
| F7 | Coexistência Bootstrap × Tailwind por página | ✅ encerrada — Bootstrap removido quando a última tela migrou |
| F8 | Alpine via `@alpinejs/csp`: lógica só em `Alpine.data()`, atributos `x-data`/`x-on:`/`x-bind:` sem expressão JS, nenhum `onclick` | ✅ garantido por `tests/test_csp.py` |

## 3. Tokens

Três camadas, sentido único — componente nunca usa cor crua:

```
PALETA crua                 →  TOKEN semântico                    →  UTILITÁRIO / COMPONENTE
--ouro-500 #C9A84C (marca)     --cor-marca (texto, AA: ouro-700)      text-marca, bg-marca-forte,
--ouro-600/700                 --cor-marca-forte (preenchimento)      bg-superficie, text-texto,
--neutro-0/50/100/500/900      --cor-fundo / --cor-superficie         border-borda, text-erro...
                               --cor-texto / --cor-texto-suave
                               --cor-borda / --cor-erro / --cor-sucesso / --cor-sobre-marca
```

- Arquivos: `static/src/css/tokens.css` (paleta + semânticos; `[data-theme="escuro"]` redefine os
  semânticos) e `static/src/css/app.css` (`@import "tailwindcss"`, `@theme`, `@source`, camadas
  base do site `.site-publico` e do painel `.painel-admin`).
- **Cor da marca confirmada pelo dono: dourado `#C9A84C`** (preenchimentos). Texto dourado no tema
  claro usa `--ouro-700` (#7A5F1F, contraste AA 5,8:1); `#C9A84C` como texto no claro daria 2,2:1.
- **Tipografia:** `--heading-font` Playfair Display (serif) e `--body-font` Lato (sans), via
  Google Fonts no `<head>` das duas bases (declarado na política de privacidade). Self-host é
  opcional (pendência T9).
- Bordas de campos de formulário no token (`@layer base`), corrigindo `currentColor` no escuro.

## 4. Cascas

| Base | Uso | Pontos-chave |
|---|---|---|
| `estrutura/base_v2.html` | Site público, portal do profissional, páginas legais/erro | Vite tags, nonce da CSP, tema sem FOUC, chrome cotton (cabeçalho com menu mobile acessível e toggle de tema, rodapé com coluna "Para você", mensagens, aviso de cookies), botão flutuante de WhatsApp, JSON-LD/SEO, manifest/SW |
| `estrutura/base_auth.html` | Login e reset de senha da equipe | Mínima, sem chrome |
| `painel/base_v2.html` | Painel ADMIN (app-shell PWA) | Drawer lateral Alpine (`adminShell`), seções Operação/Cadastros/Relatórios/Sistema, busca instantânea, `manifest_admin.json`, botão de push só com VAPID, "Sair" como form POST |
| `email/base_email.html` | 10 e-mails | Tabelas email-safe, CSS inline, dark mode por media query, fallback Outlook (CSP não se aplica) |

Páginas de erro próprias: `400.html`, `403.html`, `403_csrf.html`, `404.html`, `429.html`
(rate-limit, `views.public.limite_excedido`), `500.html` (estáticas, sem contexto) e
`axes_bloqueio.html` (bloqueio de login, 429).

## 5. Biblioteca de componentes

**django-cotton** (`templates/cotton/`), nomes PT-BR, estilo 100% por tokens. O atributo `class`
é declarado em `<c-vars>` e **mesclado** à classe base (sem gerar um 2º atributo `class`):

| Componente | Variantes / nota |
|---|---|
| `<c-botao>` | `variante` = solido (padrão) / ghost / link; `tipo`; `loading` (spinner + disabled) |
| `<c-card>` | Superfície + borda do token |
| `<c-campo>` | `<label for>` sempre + input + mensagem de erro com `aria-invalid`/`aria-describedby` |
| `chrome/cabecalho`, `chrome/rodape`, `chrome/mensagens`, `chrome/cookie_consent` | Casca do site |

Removidos na auditoria (sem uso real): `<c-badge>` (status usa `partials/_status_badge.html`,
mapa canônico com SVG inline) e `<c-toast>`/`<c-modal>` (mensagens do Django via
`chrome/mensagens` com `role`/`aria-live`; modais via `x-data="modal"` do `app.js`).

**Componentes Alpine** (`static/src/js/app.js`, CSP-safe): `temaToggle`, `modal`, `navMenu`,
`cookieConsent`, `adminShell`, `alertaDismiss`, `anotacaoModal` (fetch da anotação),
`pacoteCriar` (clonar item do pacote). Também no bundle: `data-confirm` global delegado
(substitui `window.confirm` inline) e `admin-search.js`.

**JS estático fora do Vite** (`static/js/`): `wizard.js` (wizard de agendamento, vanilla,
estado em `sessionStorage`), `webpush.js` (inscrição de push; lê o CSRF da página),
`admin-search.js`.

**Parciais** (`templates/partials/`): `_status_badge`, `_paginacao`, `_alerta_saude` (alertas de
saúde em texto visível), `_ficha_pendente`, `faq_accordion`.

## 6. Regras de CSP para templates

- `<script>` e `<style>` inline só com `nonce="{{ csp_nonce }}"`; JSON via `json_script`.
- Nenhum handler `on*=` (a CSP não tem `script-src-attr`); eventos por `addEventListener` ou Alpine.
- `style="..."` ainda é aceito (`style-src-attr 'unsafe-inline'`) — ~160 ocorrências fora dos
  e-mails, a migrar para classes.
- Libs externas só do jsdelivr, por caminho com versão fixa (`JSDELIVR_PATHS` no middleware) e
  com SRI (`integrity` + `crossorigin`): FullCalendar 6.1.11, Chart.js 4.4.0. Trocar a versão =
  atualizar template, allowlist e hash (o `test_csp` confere).
- Em DEBUG a CSP libera o dev server do Vite (`localhost:5173`, `ws:`) e `unsafe-inline` em
  estilo (HMR); produção fica estrita.

## 7. PWA

- `/manifest.json` (site) e `/painel/manifest.json` (equipe); ícones em `static/assets/`.
- `/sw.js` **v7**: precache de estáticos e cache só de **páginas públicas** (lista fixa +
  `/servicos/detalhe/`); nunca HTML do painel/portal/LGPD (a v7 apaga o que a v6 guardou);
  página offline própria.

## 8. Pendências

- **T9 (parcial):** cor final confirmada; fontes definidas (Playfair Display + Lato). Falta só,
  se desejado, servir as fontes localmente (woff2) em vez do Google Fonts.
- Migrar os `style=""` restantes e então remover `style-src-attr 'unsafe-inline'`.
- `agenda/embed.html` segue mínimo (sem chrome), por design.

---

_Última atualização: 2026-09-23 — HTMX e `<c-badge>/<c-toast>/<c-modal>` removidos; SW v7;
páginas de erro; cor/fontes definidas._
