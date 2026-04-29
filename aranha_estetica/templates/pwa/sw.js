// Service Worker — estrategia hibrida (cache-first estaticos, network-first HTML)
const VERSION = 'v5';
const STATIC_CACHE = `aranha-static-${VERSION}`;
const RUNTIME_CACHE = `aranha-runtime-${VERSION}`;
const IMAGE_CACHE = `aranha-img-${VERSION}`;
const API_CACHE = `aranha-api-${VERSION}`;

const PRECACHE_URLS = [
  '/',
  '/static/assets/logo-completa.png',
  '/static/assets/logo-sem-fundo.png',
  '/static/assets/favicon.png',
  '/static/css/base.css',
  '/static/js/admin-search.js',
];

const MAX_API_ENTRIES = 30;
const MAX_RUNTIME_ENTRIES = 80;

const MAX_IMAGE_ENTRIES = 60;
const IMAGE_TTL_DAYS = 30;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE_URLS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  const allowed = new Set([STATIC_CACHE, RUNTIME_CACHE, IMAGE_CACHE, API_CACHE]);
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

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Bypass total: admin/painel/lgpd/health (privado, dinamico)
  if (url.pathname.startsWith('/ajax/') ||
      url.pathname.startsWith('/admin/') ||
      url.pathname.startsWith('/painel/') ||
      url.pathname.startsWith('/lgpd/aceitar-cookies') ||
      url.pathname.startsWith('/health')) {
    return;
  }

  // Stale-while-revalidate p/ leitura de API publica (procedimentos, dias, horarios)
  if (url.pathname.startsWith('/api/dias-disponiveis') ||
      url.pathname.startsWith('/api/horarios-disponiveis') ||
      url.pathname.startsWith('/api/buscar-procedimentos')) {
    event.respondWith(
      caches.open(API_CACHE).then(async (cache) => {
        const cached = await cache.match(req);
        const network = fetch(req).then((resp) => {
          if (resp.ok) {
            cache.put(req, resp.clone());
            trimCache(API_CACHE, MAX_API_ENTRIES);
          }
          return resp;
        }).catch(() => cached);
        return cached || network;
      })
    );
    return;
  }
  if (url.pathname.startsWith('/api/')) return;

  // Estrategia: imagens - cache-first com TTL e limite
  if (req.destination === 'image' || /\.(png|jpg|jpeg|webp|gif|svg|ico)$/i.test(url.pathname)) {
    event.respondWith(
      caches.open(IMAGE_CACHE).then(async (cache) => {
        const cached = await cache.match(req);
        if (cached) return cached;
        try {
          const resp = await fetch(req);
          if (resp.ok) {
            cache.put(req, resp.clone());
            trimCache(IMAGE_CACHE, MAX_IMAGE_ENTRIES);
          }
          return resp;
        } catch {
          return cached || new Response('', { status: 504 });
        }
      })
    );
    return;
  }

  // Estrategia: estaticos (css/js/font) - stale-while-revalidate
  if (/\.(css|js|woff2?|ttf|eot)$/i.test(url.pathname) || url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.open(RUNTIME_CACHE).then(async (cache) => {
        const cached = await cache.match(req);
        const network = fetch(req).then((resp) => {
          if (resp.ok) {
            cache.put(req, resp.clone());
            trimCache(RUNTIME_CACHE, MAX_RUNTIME_ENTRIES);
          }
          return resp;
        }).catch(() => cached);
        return cached || network;
      })
    );
    return;
  }

  // Estrategia: HTML - network-first com fallback offline
  event.respondWith(
    fetch(req)
      .then((resp) => {
        if (resp.ok && req.headers.get('accept')?.includes('text/html')) {
          const clone = resp.clone();
          caches.open(RUNTIME_CACHE).then((c) => c.put(req, clone));
        }
        return resp;
      })
      .catch(() =>
        caches.match(req).then((cached) =>
          cached ||
          new Response(
            '<!doctype html><meta charset="utf-8"><title>Offline</title>' +
            '<style>body{font-family:sans-serif;padding:2rem;text-align:center}</style>' +
            '<h1>Sem conexao</h1><p>Verifique sua internet e recarregue a pagina.</p>',
            { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
          )
        )
      )
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
    data = { head: 'Notificacao', body: event.data ? event.data.text() : '' };
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
