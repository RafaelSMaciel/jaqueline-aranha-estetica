// Service Worker — cache so de assets estaticos e de paginas PUBLICAS.
// Registrado pelo painel com escopo '/': HTML privado (painel, portal do
// profissional, django-admin, meus agendamentos, links com token) NUNCA vai
// para o Cache Storage — ele sobrevive ao logout em computador compartilhado.
// Subir VERSION a cada mudanca: o activate apaga os caches antigos.
const VERSION = 'v7';
const STATIC_CACHE = `aranha-static-${VERSION}`;
const RUNTIME_CACHE = `aranha-runtime-${VERSION}`;
const IMAGE_CACHE = `aranha-img-${VERSION}`;
const PAGES_CACHE = `aranha-pages-${VERSION}`;

const PRECACHE_URLS = [
  '/static/assets/logo-completa.png',
  '/static/assets/favicon.png',
];

// Allowlist de HTML publico que pode ficar offline (sem dado pessoal).
const PUBLIC_PAGES = new Set([
  '/',
  '/quem-somos/',
  '/servicos/faciais/',
  '/servicos/corporais/',
  '/equipe/',
  '/especialidades/',
  '/depoimentos/',
  '/galeria/',
  '/contato/',
  '/promocoes/',
  '/termos-de-uso/',
  '/politica-de-privacidade/',
]);
const PUBLIC_PREFIXES = ['/servicos/detalhe/'];

const MAX_RUNTIME_ENTRIES = 80;
const MAX_PAGES_ENTRIES = 30;
const MAX_IMAGE_ENTRIES = 60;

const OFFLINE_HTML =
  '<!doctype html><html lang="pt-br"><meta charset="utf-8"><title>Sem conexão</title>' +
  '<body style="font-family:sans-serif;padding:2rem;text-align:center">' +
  '<h1>Sem conexão</h1><p>Verifique sua internet e recarregue a página.</p></body></html>';

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE_URLS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  // Apaga caches de versoes antigas (inclui HTML privado gravado pela v6)
  const allowed = new Set([STATIC_CACHE, RUNTIME_CACHE, IMAGE_CACHE, PAGES_CACHE]);
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => !allowed.has(n)).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

async function trimCache(cacheName, maxItems) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length > maxItems) {
    await Promise.all(keys.slice(0, keys.length - maxItems).map((k) => cache.delete(k)));
  }
}

function isPublicPage(url) {
  if (url.search) return false; // querystring pode carregar token/estado
  return PUBLIC_PAGES.has(url.pathname) || PUBLIC_PREFIXES.some((p) => url.pathname.startsWith(p));
}

function podeGuardar(resp) {
  if (!resp || !resp.ok || resp.type !== 'basic' || resp.redirected) return false;
  const cc = (resp.headers.get('Cache-Control') || '').toLowerCase();
  return !cc.includes('no-store') && !cc.includes('private');
}

function offline() {
  return new Response(OFFLINE_HTML, {
    status: 503,
    headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' },
  });
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return; // POST e afins: sempre rede, nunca cache

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  const ehHtml = req.mode === 'navigate' || (req.headers.get('accept') || '').includes('text/html');

  // HTML: network-first so p/ paginas publicas; o resto e so rede (fallback offline generico)
  if (ehHtml) {
    if (!isPublicPage(url)) {
      event.respondWith(fetch(req).catch(() => offline()));
      return;
    }
    event.respondWith(
      fetch(req)
        .then((resp) => {
          if (podeGuardar(resp)) {
            const clone = resp.clone();
            caches.open(PAGES_CACHE).then((c) => c.put(req, clone)).then(() => trimCache(PAGES_CACHE, MAX_PAGES_ENTRIES));
          }
          return resp;
        })
        .catch(() => caches.match(req, { cacheName: PAGES_CACHE }).then((cached) => cached || offline()))
    );
    return;
  }

  // Tudo que nao e /static/ (ajax, api, sw.js, manifest, uploads...) vai direto p/ rede
  if (!url.pathname.startsWith('/static/')) return;

  // Imagens estaticas: cache-first com limite
  if (req.destination === 'image' || /\.(png|jpg|jpeg|webp|gif|svg|ico)$/i.test(url.pathname)) {
    event.respondWith(
      caches.open(IMAGE_CACHE).then(async (cache) => {
        const cached = await cache.match(req);
        if (cached) return cached;
        try {
          const resp = await fetch(req);
          if (podeGuardar(resp)) {
            cache.put(req, resp.clone());
            trimCache(IMAGE_CACHE, MAX_IMAGE_ENTRIES);
          }
          return resp;
        } catch {
          return new Response('', { status: 504 });
        }
      })
    );
    return;
  }

  // Demais estaticos (css/js/fontes): stale-while-revalidate
  event.respondWith(
    caches.open(RUNTIME_CACHE).then(async (cache) => {
      const cached = await cache.match(req);
      const network = fetch(req).then((resp) => {
        if (podeGuardar(resp)) {
          cache.put(req, resp.clone());
          trimCache(RUNTIME_CACHE, MAX_RUNTIME_ENTRIES);
        }
        return resp;
      }).catch(() => cached);
      return cached || network;
    })
  );
});

// Permite skipWaiting via mensagem do front
self.addEventListener('message', (event) => {
  if (event.data?.type === 'SKIP_WAITING') self.skipWaiting();
});

// Web Push: recebe payload e exibe notification
self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { head: 'Notificação', body: event.data ? event.data.text() : '' };
  }
  const title = data.head || 'Jaqueline Aranha Estética';
  const options = {
    body: data.body || '',
    icon: data.icon || '/static/assets/logo-completa.png',
    badge: data.badge || '/static/assets/favicon.png',
    data: { url: data.url || '/painel/' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

// Click na notification: abre/foca janela
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = event.notification.data?.url || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if (w.url.includes(targetUrl) && 'focus' in w) return w.focus();
      }
      if (clients.openWindow) return clients.openWindow(targetUrl);
    })
  );
});
