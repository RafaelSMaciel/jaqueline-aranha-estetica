# Fundação do Front-end — Design System

> Spec única (sobrescrever a cada revisão). Status: **IMPLEMENTADA** (Ondas 1–4 concluídas;
> 100% do site e do painel na fundação nova; design system das telas da equipe e site com números
> reais em 2026-10-29). Direção visual **D2 "Clínico-premium"**.
> Stack efetiva: **Vite 6 + Tailwind CSS v4 + Alpine.js (`@alpinejs/csp`) + django-cotton**
> — HTMX saiu (ver F2). Cross-ref: [`../ARCHITECTURE.md`](../ARCHITECTURE.md) (checkpoint, D50–D51),
> [`../PROJECT.md`](../PROJECT.md) §3.

## 1. Objetivo & escopo

Um design system único para o **site público**, o **painel da equipe (PWA)** e o **portal da
profissional**, substituindo o template comprado (Bootstrap + jQuery + tema). Resultado: nenhum
template usa mais `estrutura/base.html`, `painel/base.html`, Bootstrap, jQuery, AOS, Swiper ou fontes
de ícone; nenhuma lib de terceiro vem de CDN; painel, portal e site sem `style="..."`.

## 2. Decisões fixadas

| # | Decisão | Estado |
|---|---|---|
| F1 | Tailwind v4, tokens em CSS vars ligados ao `@theme` | ✅ |
| F2 | Interatividade local com Alpine; **sem HTMX** | ✅ HTMX foi incluído na Fatia 1 e **removido** do bundle (nenhum `hx-*` no projeto; injetava `<style>` sem nonce, incompatível com a CSP). Bundle JS caiu de ~137 KB para ~65 KB |
| F3 | django-cotton para componentes `<c-*>` | ✅ (seção 5) |
| F4 | Vite + django-vite (HMR em dev, manifest com hash em prod) | ✅ `static/dist/` gerado no Docker/CI; `static/src/` fora do collectstatic |
| F5 | Tema claro padrão + escuro opcional (toggle), nos dois lados | ✅ |
| F6 | Tema por tokens semânticos + `data-theme` + cookie, sem FOUC | ✅ cookie `tema` = `claro`/`escuro`; o servidor escreve `<html data-theme="{{ tema }}">`; sem cookie segue o sistema (`prefers-color-scheme`) |
| F7 | Coexistência Bootstrap × Tailwind por página | ✅ encerrada — Bootstrap removido quando a última tela migrou |
| F8 | Alpine via `@alpinejs/csp`: lógica só em `Alpine.data()`, atributos `x-data`/`x-on:`/`x-bind:` sem expressão JS, nenhum `onclick` | ✅ garantido por `tests/test_csp.py` |
| F9 | Fontes servidas pelo próprio site | ✅ `@fontsource` no `app.css` (Playfair Display 500/600/700 e Lato 400/700, só o subconjunto latin), arquivo com hash no build; sem Google Fonts (nenhum IP de visitante para terceiro, nada a mais na CSP) |
| F10 | Lib de terceiro só pelo npm, nunca CDN | ✅ FullCalendar 6.1.21 e Chart.js 4.5.1 com versão exata no `package.json`, empacotados nas entradas `painel-calendario` e `painel-visao-geral`; jsDelivr fora da CSP (`test_csp.SemScriptExternoTests`) |
| F11 | Duas camadas de CSS sobre os tokens: `componentes.css` (telas da equipe) e `site.css` (páginas do site) | ✅ seção 3 |
| F12 | Comportamento dos componentes da equipe em `painel.js` **vanilla**, por delegação — não Alpine | ✅ sem JS as abas são âncoras para seções visíveis e o "⋯" é `<details>` nativo |
| F13 | Varredura visual de toda rota no CI | ✅ `tests/test_crawl_visual.py` (seção 8) |

## 3. Tokens e camadas de CSS

Três camadas, sentido único — componente nunca usa cor crua:

```
PALETA crua                 →  TOKEN semântico                    →  UTILITÁRIO / COMPONENTE
--ouro-500 #C9A84C (marca)     --cor-marca (texto, AA: ouro-700)      text-marca, bg-marca-forte,
--ouro-600/700                 --cor-marca-forte (preenchimento)      bg-superficie, text-texto,
--neutro-0/50/100/500/900      --cor-fundo / --cor-superficie         border-borda, text-erro...
                               --cor-texto / --cor-texto-suave        .btn-primario, .cartao, .selo-erro
                               --cor-borda / --cor-borda-campo        (componentes.css)
                               --cor-erro / --cor-sucesso / --cor-aviso / --cor-info / --cor-destaque
                               (+ -fundo), --cor-sobre-marca, --st-<status>-fundo/-texto
```

- **Arquivos:** `static/src/css/tokens.css` (paleta + semânticos; `[data-theme="escuro"]` e
  `prefers-color-scheme: dark` sem escolha redefinem os semânticos — o mesmo bloco duas vezes) e
  `static/src/css/app.css` (`@import "tailwindcss"`, fontes, `@theme`, `@source`, a camada base e o
  foco). O `app.css` importa as duas camadas de página:
  - **`componentes.css`** — design system das telas da equipe (painel e portal): `.btn` e variantes,
    `.campo`/`.rotulo`/`.ajuda`/`.erro-campo`, `.cartao`, `.selo`, `.nota`, `.kpi`, `.tabela`
    (+ `.tabela-responsiva`), `.aba`, `.menu-acoes`, `.dialogo-*`, `.vazio`, `.barra-filtros`,
    `.item-lista`, `.avatar`, `.linha-tempo`, `.paginacao`, `.barra-lote`, o cabeçalho de página e o
    menu lateral. Tudo em **`@layer components`**: o utilitário do template (camada `utilities`)
    sempre vence, então `<c-botao class="w-full">` ou `class="cartao mb-6"` ajustam sem
    `!important`. Fora de camada, de propósito, só o tema do FullCalendar (ele injeta CSS sem camada).
  - **`site.css`** — o CSS das páginas do site público que antes vinha em `<style>` por template:
    wizard (`.pagina-agendamento`), "Meus agendamentos" e reagendar (`.pagina-portal`), aviso de
    cookies em faixa fina no pé, arte da marca, faixa de números, monograma da profissional, `.foto`.
    Só `var(--cor-*)` (nenhum hex — `test_a11y_front` confere); cor nova vira um `--site-*` que
    referencia um token. Regra de página fica sob a classe da página (o arquivo vai para todas as telas).
- **Tokens do design system da equipe** (claro e escuro): cromo do menu lateral em café/ouro quente
  derivado da marca, escuro nos dois temas (`--cor-cromo-fundo`, `-elevado`, `-borda`, `-texto`,
  `-suave`, `-ativo-fundo`, `-ativo-texto`; texto 13:1, suave 7,3:1, ativo 10:1); realce neutro
  (`--cor-fundo-realce`), borda forte de botão secundário (`--cor-borda-forte`), ouro suave de fundo
  (`--cor-marca-suave`), hover do primário (`--cor-marca-forte-hover`), perigo preenchido
  (`--cor-perigo-forte`, `-hover`, `--cor-sobre-perigo`, 7:1 nos dois temas) e sombras
  (`--sombra-cartao`, `--sombra-flutuante` → `shadow-cartao`/`shadow-flutuante`).
- **Tokens só do site** (`site.css`): `--site-whatsapp` = o verde de ação do sistema
  (`--cor-sucesso-forte`, 5,3:1 com o texto; o `#25D366` da marca do WhatsApp dava 2:1) →
  `bg-whatsapp`/`text-sobre-whatsapp`; `--site-dourado-vela`/`-fio` (misturas da marca).
- **Cor da marca confirmada pelo dono: dourado `#C9A84C`** (preenchimentos). Texto dourado no tema
  claro usa `--ouro-700` (#7A5F1F, contraste AA 5,8:1); `#C9A84C` como texto no claro daria 2,2:1.
- **Tipografia:** `--heading-font` Playfair Display (serif) e `--body-font` Lato (sans), locais (F9).
  Escala das telas da equipe: **texto base 15px** (`text-corpo`), **rótulo/meta 13px**
  (`text-legenda`), **nada abaixo de 12px**; espaçamento em múltiplos de 4px; raio 8px (controle),
  12px (menu/aviso), 16px (cartão/modal).
- **Caixa de frase** em títulos, botões, rótulos e abas ("Pacotes e crédito", "Perguntas
  frequentes"); nome de tratamento do catálogo e sigla ficam como são.
- Bordas de campos de formulário no token de campo (`@layer base`, 3:1), corrigindo `currentColor`
  no escuro; foco do teclado (`:focus-visible`) fora de camada, para nenhum `outline-none` apagar.

## 4. Cascas

| Base | Uso | Pontos-chave |
|---|---|---|
| `estrutura/base_v2.html` | Site público, páginas legais e de erro com contexto (403/404/429) | Vite tags, nonce da CSP, tema sem FOUC, chrome cotton (cabeçalho com menu mobile acessível e toggle de tema, rodapé com coluna "Para você", mensagens, aviso de cookies), botão flutuante de WhatsApp, JSON-LD/SEO, manifest/SW |
| `estrutura/base_auth.html` | Login, reset de senha, desafio 2FA e bloqueio do axes | Mínima, sem chrome |
| `painel/base_v2.html` | Painel ADMIN (app-shell PWA) | Menu lateral no cromo escuro quente (drawer Alpine `adminShell` no celular), seções Operação/Cadastros/Relatórios/Sistema (`painel_tags.MENU_PAINEL`), barra do topo clara com a busca global (Ctrl+K), cabeçalho de página igual em toda tela (blocos `page_voltar`, `page_title`, `page_subtitle`, `page_actions`), `manifest_admin.json`, botão de push só com VAPID, "Sair" como form POST; carrega `app.js`, `painel.js`, `painel-busca.js` e `webpush.js` |
| `profissional/base.html` | Portal da profissional (e `painel/prontuario_portal_base.html` por cima) | Mesmo design system do painel (componentes.css + cotton, mesmo cabeçalho de página), celular primeiro, abas de navegação na barra escura do topo; sem rodapé, CTA, WhatsApp, SEO nem aviso de cookies do site |
| `email/base_email.html` | 10 e-mails | Tabelas email-safe, CSS inline, dark mode por media query, fallback Outlook (CSP não se aplica) |

Páginas de erro próprias: `400.html`, `403.html`, `403_csrf.html`, `404.html`, `429.html`
(rate-limit, `views.public.limite_excedido`), `500.html` (400/403_csrf/500 estáticas,
autossuficientes: sem contexto, sem Vite, só `style=""`) e `axes_bloqueio.html` (bloqueio de login, 429).

## 5. Biblioteca de componentes

**django-cotton** (`templates/cotton/`), nomes PT-BR, estilo 100% por tokens. O atributo `class`
é declarado em `<c-vars>` e **mesclado** à classe base (sem gerar um 2º atributo `class`); o resto
(`name`, `value`, `data-*`, `aria-*`) vai em `attrs` para o elemento.

**Design system das telas da equipe** (emitem as classes do `componentes.css`):

| Componente | Variantes / nota |
|---|---|
| `<c-botao>` | `variante` = primario (padrão) / secundario / perigo / perigo-contorno / sucesso / fantasma / link (legado: solido, ghost); `tamanho` sm / lg; `href` vira `<a>`; `icone`; `so_icone` (com `aria-label`); `tipo`; `loading` (giro + disabled) |
| `<c-campo>` | `<label for>` sempre + input / `select` / `textarea`; `ajuda` e `erro` ligados por `aria-describedby`, `aria-invalid`; `opcional` mostra "(opcional)"; 16px no celular (sem zoom do iOS) |
| `<c-card>` | Sem `titulo`: um bloco. Com `titulo`: cabeçalho (h2 — `nivel` troca —, `descricao`, `icone`, slot `acoes`) + corpo; `espaco="nenhum"` encosta tabela/lista na borda; slot `rodape` |
| `<c-selo>` | `tom` neutro / info / sucesso / aviso / erro / marca / destaque; `tamanho` md; `icone`. Status do atendimento segue em `partials/_status_badge.html` |
| `<c-nota>` | Aviso em bloco: `tom` info / aviso / erro / sucesso / marca; `href` vira um link inteiro (atalhos "sem desfecho" e "retornos a marcar" da visão geral); sem `href` é `role="note"` (`papel` status/alert para mensagem viva) |
| `<c-kpi>` | Indicador: `variacao` + `tendencia` sobe / desce / igual + `comparacao` ("vs. semana passada") — seta, sinal e texto, nunca só a cor; `href` torna o cartão um link |
| `<c-vazio>` | Estado vazio: ícone, título, texto e a próxima ação no slot |
| `<c-icone>` | Ícone de contorno 24×24 em `currentColor`, sempre `aria-hidden` (o nome da ação fica no texto ou no `aria-label`); nomes = os do menu do painel + ação/estado |
| `<c-menu-acoes>` | Menu "⋯" de ações secundárias: `<details>` nativo (abre sem JS), nomeado por `rotulo`; itens `.menu-item` (link, form POST ou `data-abrir-dialogo`) |
| `<c-dialogo>` | `<dialog>` nativo (`showModal`: foco preso, Esc fecha, fundo inerte), nomeado pelo título (h2) + botão Fechar; `tamanho` sm / lg; abre por `data-abrir-dialogo="(id)"` ou pelo Alpine `modal` |
| `<c-tabela>` | `legenda` (caption só para leitor de tela); `responsiva`: tabela no desktop e cartões empilhados < 768px (cada `td` com `data-rotulo`, `td.acoes` no pé do cartão); sem ela, a tabela rola dentro do próprio bloco (nunca a página); `compacta` |
| `<c-abas>` + `<c-abas.aba>` / `<c-abas.painel>` | Sem JS: links de âncora para seções visíveis. Com `painel.js`: WAI-ARIA Tabs (`tablist`/`tab`/`tabpanel`, `aria-selected`, setas/Home/End, `#hash` na URL, aba lembrada por página, aba com erro do servidor abre sozinha); `contador` na aba |

**Site:** `<c-cta-agendar>` (o "Agendar": wizard ou, sem SMS, o WhatsApp — tag `{% agendar_link %}`),
`<c-servico.passo>`, `<c-servico.cartao>`/`<c-servico.item>` e o chrome `chrome/cabecalho`,
`chrome/rodape`, `chrome/mensagens`, `chrome/cookie_consent` (faixa fina no pé, acima da barra do
iPhone).

Removidos na auditoria (sem uso real): `<c-badge>` (status usa `partials/_status_badge.html`,
mapa canônico com SVG inline), `<c-toast>` (mensagens do Django via `chrome/mensagens` com
`role`/`aria-live`) e `<c-modal>` (hoje `<c-dialogo>`).

**Entradas do Vite** (`static/src/js/`, uma por tela, nome com hash no manifest):

| Entrada | Onde | O que faz |
|---|---|---|
| `app.js` | Todas as cascas | Alpine CSP-safe (`temaToggle`, `modal`, `navMenu`, `cookieConsent`, `adminShell`, `alertaDismiss`, `anotacaoModal`, `pacoteCriar`), `data-confirm` global delegado (substitui `window.confirm` inline); importa o `app.css` |
| `wizard.js` | `agendamento/agendamento_publico.html` | Wizard de agendamento (vanilla, estado em `sessionStorage`, reidratação após recusa do servidor) |
| `painel.js` | Painel e portal | Abas, menu "⋯", modais (`data-abrir-dialogo`/`data-fechar-dialogo`, clique no fundo fecha), Ctrl+K na busca, foco/nome na tabela que rola — vanilla, por delegação no `document` |
| `painel-busca.js` | Painel | Busca instantânea |
| `painel-status.js` | Agenda e visão geral | Seletor de status e lote de aprovar/recusar (`lib/requisicao.js`) |
| `painel-calendario.js` | `painel/calendar.html` | FullCalendar do npm (eventos por AJAX, arrasto para reagendar, modal); nomeia as setas, esconde os ícones e achata o ARIA de grade (`presentation`) que o FullCalendar 6 não fecha (axe `aria-required-children`) |
| `painel-visao-geral.js` | `painel/overview.html` | Chart.js do npm (só barra + rosca registradas), dados por `json_script`, cores dos tokens (a rosca usa `--st-<status>-texto`) |
| `webpush.js` | Painel e agenda do portal | Inscrição de push (lê o CSRF da página) |

**Parciais** (`templates/partials/`): `_status_badge`, `_select_status`, `_paginacao`, `_alerta_saude`
(alertas de saúde em texto visível), `_ficha_pendente`, `_ics_link`, `_antiabuso` (Turnstile),
`faq_accordion` + `_faq_item` (JSON-LD `FAQPage` só com as perguntas exibidas), `_numeros_site` e
`_estrelas` (números do site, `services/prova_social`), `_arte_marca` (moldura sem foto real: gradiente
nos tokens + ramo + selo; nunca foto inventada), `_servicos_catalogo`, `_servicos_destaque`
("Cuidados em destaque" do catálogo), `_icone_whatsapp`.

**Fotos do site:** `{% foto 'nome' alt=... sizes=... classe=... prioridade=... %}` (`site_tags`) —
`<picture>` com WebP no `srcset` e JPEG de reserva nas larguras 480/768/1024 (variantes de
`scripts/imagens.py` em `static/assets/clinica/variantes/`, versionadas), `width`/`height` reais
(sem CLS), `loading="lazy"` ou, na foto do topo, `fetchpriority="high"`. Foto sem variante cai na
original, sem `srcset`.

## 6. Regras de CSP para templates

- `<script>` e `<style>` inline só com `nonce="{{ csp_nonce }}"`; JSON via `json_script`. Blocos
  `<style nonce>` que restam: o `<noscript>` do `.reveal` no `base_v2`, o vazio que o FullCalendar
  reaproveita (`calendar.html`), as regras das abas geradas por categoria (`especialidades.html`) e
  o do widget `embed.html`.
- Nenhum handler `on*=` (a CSP não tem `script-src-attr`); eventos por `addEventListener` ou Alpine.
- **Sem CDN:** nenhum `<script src>`/CSS de outro host além do Turnstile
  (`https://challenges.cloudflare.com`); lib nova entra pelo `package.json` e é importada num módulo
  do Vite (`test_csp.SemScriptExternoTests` reprova o resto; as libs do painel ficam com versão exata). O CDN serviria
  qualquer pacote com o nonce da página.
- `style="..."` ainda é aceito pela CSP (`style-src-attr 'unsafe-inline'`), mas painel, portal e
  site não usam (no site, `test_a11y_front.SitePublicoSemEstiloInlineTests` barra atributo,
  `<style>` por página e cor crua em classe). Sobram só onde não há bundle nem nonce: o widget
  `agendamento/embed.html` (1 atributo), as páginas de erro 400/403_csrf/500, a página offline
  montada no `sw.js` e os e-mails.
- Em DEBUG a CSP libera o dev server do Vite (`localhost:5173` em script/fonte/conexão, `ws:`) e
  `unsafe-inline` em estilo (HMR); produção fica estrita.

## 7. PWA

- Três apps instaláveis, cada um no próprio escopo (Chrome sem `installabilityErrors`):
  `/manifest.json` (site, `short_name` = nome curto da clínica), `/painel/manifest.json` ("Painel",
  abre `/painel/overview/`, 4 atalhos) e `/profissional/manifest.json` ("Minha agenda"). Rótulo do
  ícone ≤ 12 caracteres, `lang` pt-BR, sem `orientation` (agenda no tablet deitado), nome com
  `escapejs` (JSON válido com aspas), splash `#1F1A14` (cromo) nos apps da equipe. Painel e portal:
  `apple-touch-icon`, `mobile-web-app-capable` e barra de status `default` no iPhone. Ícones em
  `static/assets/pwa/` (`test_public_views` confere os manifests da equipe).
- `/sw.js` **v10**: precache de estáticos e cache só de **páginas públicas** (lista fixa — inclusive
  `/duvidas/` — + `/servicos/detalhe/`); nunca HTML do painel/portal/LGPD; página offline própria.
  Subir `VERSION` a cada mudança do `sw.js` (o `activate` apaga os caches antigos).

## 8. Qualidade e pendências

- **Varredura visual no CI** (job `navegador`): `tests/test_crawl_visual.py` abre toda rota pública,
  da equipe e da profissional de `tests/test_rotas.ROTAS` em 1366px (claro) e 390px (escuro) e
  reprova em erro HTTP, erro de JS/console (inclui CSP), rolagem lateral no celular, template vazando
  e violação serious/critical do axe-core (devDependency). Exceção de terceiro só pela lista
  `PERMITIDOS` do teste, com o motivo (hoje vazia).
- Guardas estáticas: `test_componentes.py` (cotton), `test_a11y_front.py` (tokens, tema escuro,
  site sem estilo inline), `test_csp.py`, `test_front_pipeline.py`, `test_site_tags.py` (`{% foto %}`
  e variantes em dia).
- **Pendente:** remover `style-src-attr 'unsafe-inline'` exigiria tirar o `style=""` das páginas de
  erro autossuficientes (o 500 renderiza sem contexto e sem nonce) e do widget `embed.html`.
- **Pendente:** o jsDelivr ainda consta como operador na política (`conteudo/lgpd.OPERADORES`) e no
  [`../LGPD.md`](../LGPD.md) §3, embora nenhuma página carregue nada dele (ARCHITECTURE §6.2).
- `agendamento/embed.html` segue mínimo (sem chrome, CSS próprio), por design.

---

_Última atualização: 2026-10-29 — design system das telas da equipe (tokens do cromo,
`componentes.css`, componentes cotton, `painel.js`), CSS do site no `site.css`, fontes locais,
FullCalendar e Chart.js do npm (sem CDN), `{% foto %}`, SW v10 e varredura visual com axe no CI._
