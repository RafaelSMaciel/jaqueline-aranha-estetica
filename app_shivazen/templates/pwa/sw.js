// Service Worker — estrategia hibrida (cache-first estaticos, network-first HTML)
const VERSION = 'v3';
const STATIC_CACHE = `shivazen-static-${VERSION}`;
const RUNTIME_CACHE = `shivazen-runtime-${VERSION}`;
const IMAGE_CACHE = `shivazen-img-${VERSION}`;

const PRECACHE_URLS = [
  '/static/assets/logo-completa.png',
  '/static/assets/logo-sem-fundo.png',
  '/static/assets/favicon.png',
];

const MAX_IMAGE_ENTRIES = 60;
const IMAGE_TTL_DAYS = 30;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE_URLS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  const allowed = new Set([STATIC_CACHE, RUNTIME_CACHE, IMAGE_CACHE]);
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

  if (url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/ajax/') ||
      url.pathname.startsWith('/admin/') ||
      url.pathname.startsWith('/painel/') ||
      url.pathname.startsWith('/lgpd/aceitar-cookies') ||
      url.pathname.startsWith('/health')) {
    return;
  }

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
          if (resp.ok) cache.put(req, resp.clone());
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
