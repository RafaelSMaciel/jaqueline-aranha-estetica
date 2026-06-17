# Fundação Front — Fatia 1 — Plano de Implementação

> **Para workers agênticos:** SUB-SKILL OBRIGATÓRIA: usar superpowers:subagent-driven-development (recomendado) ou superpowers:executing-plans para executar tarefa-a-tarefa. Passos usam checkbox (`- [ ]`).

**Goal:** Stand up o design system (Tailwind v4 + Vite + django-cotton + Alpine/HTMX) com tokens temáveis (light/dark), casca de site nova e 6 componentes base, provados por uma página de prova — coexistindo com o Bootstrap atual.

**Architecture:** Build via Vite (django-vite serve assets hasheados; Whitenoise em prod). Tema por tokens CSS semânticos + `data-theme` lido de cookie (sem FOUC). Componentes são templates django-cotton (`<c-*>`) — testáveis por render do Django, sem precisar do build. Páginas novas usam base nova (Tailwind puro); páginas antigas seguem Bootstrap — nunca na mesma página.

**Tech Stack:** Django 5.2 · Tailwind v4 · Vite · django-vite · django-cotton · Alpine.js · HTMX · Whitenoise · pytest/manage.py test.

Spec: [`../specs/fundacao-front-design.md`](../specs/fundacao-front-design.md).

**Pré-condição honesta:** cores/fontes reais da marca ainda não chegaram (spec §8). O plano usa **token placeholder dourado `#C9A84C`** num único lugar; a Task 9 é a calibração quando as cores chegarem — não bloqueia as Tasks 1-8.

---

### Task 1: Toolchain Node — Vite + Tailwind v4

**Files:**
- Create: `package.json`
- Create: `vite.config.js`
- Create: `.gitignore` (append `node_modules/`, `aranha_estetica/static/dist/`)

- [ ] **Step 1: Init npm e instalar deps de build**

Run:
```bash
npm init -y
npm i -D vite@^6 tailwindcss@^4 @tailwindcss/vite@^4
```
Expected: `package.json` criado, `node_modules/` populado, sem erro.

- [ ] **Step 2: Configurar Vite p/ Django (manifest + outDir em static/dist)**

Create `vite.config.js`:
```js
import { defineConfig } from 'vite'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [tailwindcss()],
  base: '/static/dist/',
  build: {
    manifest: 'manifest.json',
    outDir: 'aranha_estetica/static/dist',
    emptyOutDir: true,
    rollupOptions: {
      input: 'aranha_estetica/static/src/js/app.js',
    },
  },
  server: { origin: 'http://localhost:5173' },
})
```

- [ ] **Step 3: Adicionar scripts ao package.json**

Edit `package.json` `"scripts"`:
```json
"scripts": { "dev": "vite", "build": "vite build" }
```

- [ ] **Step 4: Ignorar artefatos no git**

Append a `.gitignore`:
```
node_modules/
aranha_estetica/static/dist/
```

- [ ] **Step 5: Commit**
```bash
git add package.json package-lock.json vite.config.js .gitignore
git commit -m "build(front): toolchain Vite + Tailwind v4"
```

---

### Task 2: Deps Python + wiring no settings (django-vite + django-cotton)

**Files:**
- Modify: `requirements.txt`
- Modify: `clinica/settings/base.py`

- [ ] **Step 1: Instalar deps Python**

Run:
```bash
pip install django-vite==3.0.5 django-cotton==1.5.1
```
(Se versões indisponíveis, usar a estável mais recente de cada e pinar.)

- [ ] **Step 2: Pinar em requirements.txt**

Append a `requirements.txt`:
```
django-vite==3.0.5
django-cotton==1.5.1
```

- [ ] **Step 3: INSTALLED_APPS + loader do cotton**

Edit `clinica/settings/base.py` — adicionar a INSTALLED_APPS:
```python
'django_vite',
'django_cotton',
```
E garantir o loader do cotton em TEMPLATES (django-cotton exige loader explícito; remover `'APP_DIRS': True` e declarar loaders):
```python
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [...],   # manter o que já existe
    'OPTIONS': {
        'context_processors': [...],   # manter
        'loaders': [(
            'django.template.loaders.cached.Loader', [
                'django_cotton.cotton_loader.Loader',
                'django.template.loaders.filesystem.Loader',
                'django.template.loaders.app_directories.Loader',
            ],
        )],
        'builtins': ['django_cotton.templatetags.cotton'],
    },
}]
```

- [ ] **Step 4: Config django-vite**

Edit `clinica/settings/base.py` (após STATIC):
```python
DJANGO_VITE = {
    'default': {
        'dev_mode': DEBUG,
        'manifest_path': BASE_DIR / 'aranha_estetica/static/dist/manifest.json',
    },
}
```

- [ ] **Step 5: Verificar que o Django ainda sobe**

Run: `python manage.py check`
Expected: `System check identified no issues`.

- [ ] **Step 6: Commit**
```bash
git add requirements.txt clinica/settings/base.py
git commit -m "feat(front): wire django-vite + django-cotton"
```

---

### Task 3: Camada de tokens (paleta + semânticos + dark)

**Files:**
- Create: `aranha_estetica/static/src/css/tokens.css`
- Create: `aranha_estetica/static/src/css/app.css`

- [ ] **Step 1: tokens.css — paleta crua + semânticos light + dark**

Create `aranha_estetica/static/src/css/tokens.css`:
```css
:root {
  --ouro-500: #C9A84C;            /* PLACEHOLDER marca — trocar 1x quando vier cor real */
  --ouro-600: #9A7B2E;
  --neutro-0: #ffffff;
  --neutro-50: #FAFAF8;
  --neutro-100: #F1EFE8;
  --neutro-500: #6B6258;
  --neutro-900: #1B1F23;

  --cor-fundo: var(--neutro-50);
  --cor-superficie: var(--neutro-0);
  --cor-texto: var(--neutro-900);
  --cor-texto-suave: var(--neutro-500);
  --cor-marca: var(--ouro-600);     /* AA-safe p/ texto */
  --cor-marca-forte: var(--ouro-500);
  --cor-borda: #E7E8E3;
}
[data-theme="dark"] {
  --cor-fundo: #14171A;
  --cor-superficie: #1B2024;
  --cor-texto: #EDEFEC;
  --cor-texto-suave: #9AA0A2;
  --cor-marca: #C9A84C;
  --cor-marca-forte: #C9A84C;
  --cor-borda: #2A3034;
}
```

- [ ] **Step 2: app.css — Tailwind v4 + bind dos tokens ao @theme**

Create `aranha_estetica/static/src/css/app.css`:
```css
@import "tailwindcss";
@import "./tokens.css";

@theme {
  --color-fundo: var(--cor-fundo);
  --color-superficie: var(--cor-superficie);
  --color-texto: var(--cor-texto);
  --color-texto-suave: var(--cor-texto-suave);
  --color-marca: var(--cor-marca);
  --color-marca-forte: var(--cor-marca-forte);
  --color-borda: var(--cor-borda);
  --radius-md: 8px;
  --radius-lg: 12px;
}
```

- [ ] **Step 3: Build de prova**

Run: `npm run build`
Expected: gera `aranha_estetica/static/dist/manifest.json` + CSS hasheado, sem erro.

- [ ] **Step 4: Commit**
```bash
git add aranha_estetica/static/src/css/
git commit -m "feat(front): token layer (light/dark) + tailwind entry"
```

---

### Task 4: JS app (Alpine + HTMX) + toggle de tema sem FOUC

**Files:**
- Create: `aranha_estetica/static/src/js/app.js`
- Create: `aranha_estetica/context_processors.py` (ou modificar existente) — adicionar `tema`
- Modify: `clinica/settings/base.py` (registrar context processor se novo)

- [ ] **Step 1: Teste — context processor expõe `tema` do cookie**

Create `aranha_estetica/tests/test_tema.py`:
```python
from django.test import TestCase, RequestFactory
from aranha_estetica.context_processors import tema_atual

class TemaContextTests(TestCase):
    def test_default_claro(self):
        req = RequestFactory().get('/')
        self.assertEqual(tema_atual(req)['tema'], 'claro')

    def test_le_cookie(self):
        req = RequestFactory().get('/')
        req.COOKIES['tema'] = 'escuro'
        self.assertEqual(tema_atual(req)['tema'], 'escuro')

    def test_cookie_invalido_cai_no_claro(self):
        req = RequestFactory().get('/')
        req.COOKIES['tema'] = 'xpto'
        self.assertEqual(tema_atual(req)['tema'], 'claro')
```

- [ ] **Step 2: Rodar — deve falhar**

Run: `python manage.py test aranha_estetica.tests.test_tema -v 2`
Expected: FAIL (`tema_atual` não existe).

- [ ] **Step 3: Implementar o context processor**

Em `aranha_estetica/context_processors.py` adicionar:
```python
def tema_atual(request):
    valor = request.COOKIES.get('tema', 'claro')
    if valor not in ('claro', 'escuro'):
        valor = 'claro'
    return {'tema': valor}
```
Registrar em `clinica/settings/base.py` TEMPLATES → `context_processors`:
```python
'aranha_estetica.context_processors.tema_atual',
```

- [ ] **Step 4: Rodar — deve passar**

Run: `python manage.py test aranha_estetica.tests.test_tema -v 2`
Expected: PASS (3 testes).

- [ ] **Step 5: app.js — Alpine + HTMX + toggle**

Create `aranha_estetica/static/src/js/app.js`:
```js
import Alpine from 'alpinejs'
import 'htmx.org'
import '../css/app.css'

window.alternarTema = function () {
  const atual = document.documentElement.dataset.theme === 'escuro' ? 'escuro' : 'claro'
  const novo = atual === 'escuro' ? 'claro' : 'escuro'
  document.documentElement.dataset.theme = novo
  localStorage.setItem('tema', novo)
  document.cookie = `tema=${novo}; path=/; max-age=31536000; samesite=lax`
}

window.Alpine = Alpine
Alpine.start()
```
Run: `npm i alpinejs htmx.org`
Then: `npm run build` → Expected: sem erro.

- [ ] **Step 6: Commit**
```bash
git add aranha_estetica/static/src/js/app.js aranha_estetica/context_processors.py clinica/settings/base.py aranha_estetica/tests/test_tema.py package.json package-lock.json
git commit -m "feat(front): tema via cookie (sem FOUC) + Alpine/HTMX bootstrap"
```

---

### Task 5: Casca nova (estrutura/base_v2.html)

**Files:**
- Create: `aranha_estetica/templates/estrutura/base_v2.html`
- Test: `aranha_estetica/tests/test_base_v2.py`

- [ ] **Step 1: Teste — base_v2 renderiza com data-theme e vite tags**

Create `aranha_estetica/tests/test_base_v2.py`:
```python
from django.test import TestCase
from django.template import engines

class BaseV2Tests(TestCase):
    def render(self, cookies=None):
        tmpl = engines['django'].from_string(
            "{% extends 'estrutura/base_v2.html' %}{% block conteudo %}OI{% endblock %}"
        )
        ctx = {'tema': (cookies or {}).get('tema', 'claro')}
        return tmpl.render(ctx)

    def test_aplica_tema_claro(self):
        html = self.render()
        self.assertIn('data-theme="claro"', html)
        self.assertIn('OI', html)

    def test_aplica_tema_escuro(self):
        html = self.render({'tema': 'escuro'})
        self.assertIn('data-theme="escuro"', html)
```

- [ ] **Step 2: Rodar — deve falhar**

Run: `python manage.py test aranha_estetica.tests.test_base_v2 -v 2`
Expected: FAIL (template não existe).

- [ ] **Step 3: Implementar base_v2.html**

Create `aranha_estetica/templates/estrutura/base_v2.html`:
```django
{% load static django_vite %}
<!doctype html>
<html lang="pt-br" data-theme="{{ tema }}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block titulo %}{{ CLINIC_NAME }}{% endblock %}</title>
  {% vite_hmr_client %}
  {% vite_asset 'aranha_estetica/static/src/js/app.js' %}
  {% block css %}{% endblock %}
</head>
<body class="bg-fundo text-texto antialiased">
  <a href="#conteudo" class="sr-only focus:not-sr-only">Pular para o conteúdo</a>
  {% include 'cotton/chrome/cabecalho.html' %}
  <main id="conteudo">{% block conteudo %}{% endblock %}</main>
  {% include 'cotton/chrome/rodape.html' %}
</body>
</html>
```
(Nesta task, criar `cabecalho.html`/`rodape.html` mínimos como stubs — logo + toggle de tema + CTA; refino visual entra na migração da Home.)

Create `aranha_estetica/templates/cotton/chrome/cabecalho.html`:
```django
<header class="border-b border-borda bg-superficie">
  <div class="mx-auto max-w-6xl flex items-center justify-between px-6 py-4">
    <a href="/" class="font-medium text-texto">{{ CLINIC_NAME }}</a>
    <div class="flex items-center gap-4">
      <button type="button" onclick="alternarTema()" aria-label="Alternar tema"
              class="text-texto-suave hover:text-texto">tema</button>
      <a href="{% url 'aranha:agendamento_publico' %}"
         class="rounded-md bg-marca-forte px-4 py-2 text-sm font-medium text-white">Agendar</a>
    </div>
  </div>
</header>
```
Create `aranha_estetica/templates/cotton/chrome/rodape.html`:
```django
<footer class="border-t border-borda bg-superficie mt-16">
  <div class="mx-auto max-w-6xl px-6 py-8 text-sm text-texto-suave">
    © {{ CLINIC_NAME }}
  </div>
</footer>
```

- [ ] **Step 4: Rodar — deve passar**

Run: `python manage.py test aranha_estetica.tests.test_base_v2 -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add aranha_estetica/templates/estrutura/base_v2.html aranha_estetica/templates/cotton/chrome/ aranha_estetica/tests/test_base_v2.py
git commit -m "feat(front): casca nova base_v2 + header/footer (Tailwind)"
```

---

### Task 6: Componentes base — botão, card, campo, badge

**Files:**
- Create: `aranha_estetica/templates/cotton/botao.html`
- Create: `aranha_estetica/templates/cotton/card.html`
- Create: `aranha_estetica/templates/cotton/campo.html`
- Create: `aranha_estetica/templates/cotton/badge.html`
- Test: `aranha_estetica/tests/test_componentes.py`

- [ ] **Step 1: Teste — render dos componentes via cotton**

Create `aranha_estetica/tests/test_componentes.py`:
```python
from django.test import TestCase
from django.template import engines

def render(s, ctx=None):
    return engines['django'].from_string(s).render(ctx or {})

class ComponentesTests(TestCase):
    def test_botao_solido_texto(self):
        html = render("<c-botao variante='solido'>Agendar</c-botao>")
        self.assertIn('Agendar', html)
        self.assertIn('bg-marca-forte', html)

    def test_botao_ghost(self):
        html = render("<c-botao variante='ghost'>Ver</c-botao>")
        self.assertIn('border-borda', html)

    def test_campo_tem_label_associado(self):
        html = render("<c-campo nome='email' label='E-mail' />")
        self.assertIn('for="email"', html)
        self.assertIn('id="email"', html)

    def test_badge_status(self):
        html = render("<c-badge status='REALIZADO'>Realizado</c-badge>")
        self.assertIn('Realizado', html)
```

- [ ] **Step 2: Rodar — deve falhar**

Run: `python manage.py test aranha_estetica.tests.test_componentes -v 2`
Expected: FAIL (componentes não existem).

- [ ] **Step 3: Implementar os 4 componentes**

Create `aranha_estetica/templates/cotton/botao.html`:
```django
<c-vars variante="solido" tipo="button" />
{% if variante == "solido" %}
  {% cotton_verbatim %}{% endcotton_verbatim %}
{% endif %}
<button type="{{ tipo }}" {{ attrs }}
  class="inline-flex items-center justify-center rounded-md px-4 py-2 text-sm font-medium transition
  {% if variante == 'solido' %}bg-marca-forte text-white hover:opacity-90
  {% elif variante == 'ghost' %}border border-borda text-texto hover:bg-fundo
  {% else %}text-marca underline underline-offset-4{% endif %}">
  {{ slot }}
</button>
```
(Nota: se a versão do cotton não suportar `{% cotton_verbatim %}`, remover esse bloco — é só ilustrativo; o essencial é `<c-vars>` + classes condicionais.)

Create `aranha_estetica/templates/cotton/card.html`:
```django
<div {{ attrs }} class="rounded-lg border border-borda bg-superficie p-5">{{ slot }}</div>
```

Create `aranha_estetica/templates/cotton/campo.html`:
```django
<c-vars tipo="text" erro="" />
<div class="flex flex-col gap-1">
  <label for="{{ nome }}" class="text-sm text-texto-suave">{{ label }}</label>
  <input id="{{ nome }}" name="{{ nome }}" type="{{ tipo }}" {{ attrs }}
    class="rounded-md border border-borda bg-superficie px-3 py-2 text-texto">
  {% if erro %}<span class="text-sm" style="color:var(--color-danger,#A32D2D)">{{ erro }}</span>{% endif %}
</div>
```

Create `aranha_estetica/templates/cotton/badge.html`:
```django
<c-vars status="" />
<span {{ attrs }} class="inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium border border-borda text-texto-suave"
  data-status="{{ status }}">{{ slot }}</span>
```

- [ ] **Step 4: Rodar — deve passar**

Run: `python manage.py test aranha_estetica.tests.test_componentes -v 2`
Expected: PASS (4 testes). Ajustar sintaxe cotton conforme a versão até passar.

- [ ] **Step 5: Commit**
```bash
git add aranha_estetica/templates/cotton/botao.html aranha_estetica/templates/cotton/card.html aranha_estetica/templates/cotton/campo.html aranha_estetica/templates/cotton/badge.html aranha_estetica/tests/test_componentes.py
git commit -m "feat(front): componentes base botao/card/campo/badge (cotton)"
```

---

### Task 7: Componentes interativos — toast (anti-XSS) + modal (Alpine)

**Files:**
- Create: `aranha_estetica/templates/cotton/toast.html`
- Create: `aranha_estetica/templates/cotton/modal.html`
- Test: `aranha_estetica/tests/test_componentes_interativos.py`

- [ ] **Step 1: Teste — toast usa textContent e aria-live; modal tem foco/ESC**

Create `aranha_estetica/tests/test_componentes_interativos.py`:
```python
from django.test import TestCase
from django.template import engines

def render(s, ctx=None):
    return engines['django'].from_string(s).render(ctx or {})

class InterativosTests(TestCase):
    def test_toast_aria_live_e_sem_innerHTML(self):
        html = render("<c-toast>Olá</c-toast>")
        self.assertIn('aria-live="polite"', html)
        self.assertIn('role="status"', html)
        # mensagem renderizada como texto escapado, não via innerHTML
        self.assertNotIn('innerHTML', html)

    def test_modal_acessivel(self):
        html = render("<c-modal titulo='Confirmar'>corpo</c-modal>")
        self.assertIn('role="dialog"', html)
        self.assertIn('aria-modal="true"', html)
        self.assertIn('x-data', html)  # Alpine
```

- [ ] **Step 2: Rodar — deve falhar**

Run: `python manage.py test aranha_estetica.tests.test_componentes_interativos -v 2`
Expected: FAIL.

- [ ] **Step 3: Implementar toast e modal**

Create `aranha_estetica/templates/cotton/toast.html`:
```django
<c-vars tipo="info" />
<div role="status" aria-live="polite"
  class="rounded-md border border-borda bg-superficie px-4 py-3 text-texto shadow">
  {{ slot }}
</div>
```
(Mensagens dinâmicas de Django messages renderizam o texto via `{{ message }}` — escapado pelo Django, nunca `innerHTML`. Quando houver toast por JS, o app.js cria nó com `textContent`.)

Create `aranha_estetica/templates/cotton/modal.html`:
```django
<c-vars titulo="" />
<div x-data="{ aberto: false }" @keydown.escape.window="aberto = false">
  <div x-show="aberto" x-trap="aberto" role="dialog" aria-modal="true"
    aria-label="{{ titulo }}"
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/45">
    <div class="rounded-lg bg-superficie p-6 text-texto max-w-md w-full" @click.outside="aberto = false">
      {% if titulo %}<h2 class="text-lg font-medium mb-3">{{ titulo }}</h2>{% endif %}
      {{ slot }}
    </div>
  </div>
</div>
```
(`x-trap` exige o plugin `@alpinejs/focus`: `npm i @alpinejs/focus` e registrar em app.js: `import focus from '@alpinejs/focus'; Alpine.plugin(focus)`.)

- [ ] **Step 4: Rodar — deve passar**

Run: `python manage.py test aranha_estetica.tests.test_componentes_interativos -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add aranha_estetica/templates/cotton/toast.html aranha_estetica/templates/cotton/modal.html aranha_estetica/tests/test_componentes_interativos.py aranha_estetica/static/src/js/app.js package.json package-lock.json
git commit -m "feat(front): toast (anti-XSS) + modal acessivel (Alpine)"
```

---

### Task 8: Página de prova + verificação visual

**Files:**
- Create: `aranha_estetica/templates/publico/prova_v2.html`
- Modify: `aranha_estetica/views/public.py` (add view `prova_v2`)
- Modify: `aranha_estetica/urls.py` (rota `/v2-prova/`)
- Test: `aranha_estetica/tests/test_prova_v2.py`

- [ ] **Step 1: Teste — rota responde 200 e usa base nova + componentes**

Create `aranha_estetica/tests/test_prova_v2.py`:
```python
from django.test import TestCase
from django.urls import reverse

class ProvaV2Tests(TestCase):
    def test_200_e_componentes(self):
        resp = self.client.get(reverse('aranha:prova_v2'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-theme=')
        self.assertContains(resp, 'bg-marca-forte')  # botão sólido
```

- [ ] **Step 2: Rodar — deve falhar**

Run: `python manage.py test aranha_estetica.tests.test_prova_v2 -v 2`
Expected: FAIL (rota inexistente).

- [ ] **Step 3: View + rota + template**

Em `aranha_estetica/views/public.py`:
```python
def prova_v2(request):
    return render(request, 'publico/prova_v2.html')
```
Em `aranha_estetica/urls.py` (dentro do app namespace):
```python
path('v2-prova/', public.prova_v2, name='prova_v2'),
```
Create `aranha_estetica/templates/publico/prova_v2.html`:
```django
{% extends 'estrutura/base_v2.html' %}
{% block conteudo %}
<section class="mx-auto max-w-3xl px-6 py-16 space-y-6">
  <h1 class="text-4xl font-medium text-texto">Prova da fundação</h1>
  <p class="text-texto-suave">Tokens, tema e componentes funcionando.</p>
  <div class="flex gap-3">
    <c-botao variante="solido">Agendar</c-botao>
    <c-botao variante="ghost">Ver procedimentos</c-botao>
  </div>
  <c-card>
    <c-campo nome="email" label="E-mail" tipo="email" />
  </c-card>
  <c-badge status="REALIZADO">Realizado</c-badge>
</section>
{% endblock %}
```

- [ ] **Step 4: Rodar — deve passar**

Run: `python manage.py test aranha_estetica.tests.test_prova_v2 -v 2`
Expected: PASS.

- [ ] **Step 5: Suite completa + check de drift**

Run: `python manage.py test aranha_estetica && python manage.py makemigrations --check --dry-run`
Expected: tudo PASS, `No changes detected`.

- [ ] **Step 6: Verificação visual (preview)**

Run dev: `npm run dev` (Vite) + `python manage.py runserver`. Abrir `/v2-prova/`, alternar tema (botão "tema"), confirmar light↔dark sem flash. Screenshot p/ prova.

- [ ] **Step 7: Commit**
```bash
git add aranha_estetica/templates/publico/prova_v2.html aranha_estetica/views/public.py aranha_estetica/urls.py aranha_estetica/tests/test_prova_v2.py
git commit -m "feat(front): pagina de prova da fundacao (/v2-prova) + verificacao"
```

---

### Task 9: Calibração de marca (gated — quando vierem cores/refs)

**Files:**
- Modify: `aranha_estetica/static/src/css/tokens.css` (só a paleta crua)
- Modify: `aranha_estetica/templates/partials/head.html` ou base_v2 (fontes self-hosted)

- [ ] **Step 1: Trocar a paleta crua pela cor real da marca**

Editar SOMENTE os `--ouro-*`/`--neutro-*` em `tokens.css` pelos valores reais. Os semânticos e componentes não mudam.

- [ ] **Step 2: Self-host das fontes definitivas (1 serif display + 1 sans, subset woff2)**

Baixar woff2 subsetados p/ `aranha_estetica/static/src/fonts/`, declarar `@font-face` em `tokens.css`, mapear `--font-display`/`--font-sans` no `@theme`.

- [ ] **Step 3: Rebuild + verificação visual**

Run: `npm run build` + abrir `/v2-prova/`. Confirmar identidade D2 com cores/fontes reais, light e dark.

- [ ] **Step 4: Commit**
```bash
git add aranha_estetica/static/src/css/tokens.css aranha_estetica/static/src/fonts/
git commit -m "feat(front): calibrar tokens com cores e fontes reais da marca"
```

---

## Critério de pronto (Fatia 1)
- `npm run build` gera manifest sem erro; `python manage.py test aranha_estetica` verde; `makemigrations --check` sem drift.
- `/v2-prova/` responde 200, renderiza via `base_v2` + componentes cotton, toggle de tema funciona sem FOUC.
- Bootstrap intacto nas páginas antigas (nenhuma página divide os 2 bundles).
- Cores/fontes reais: Task 9 quando o dono enviar (não bloqueia 1-8).

## Notas de coexistência
- `base_v2.html` é separada da `estrutura/base.html` (Bootstrap). Páginas migram opt-in trocando o `extends`.
- django-vite em `dev_mode=DEBUG`: usa o dev server (HMR) em dev; manifest hasheado em prod (Whitenoise serve `static/dist/`). Rodar `npm run build` no deploy (Railway, antes do `collectstatic`).
