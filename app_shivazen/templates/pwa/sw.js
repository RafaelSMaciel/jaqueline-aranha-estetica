const CACHE_NAME = 'shivazen-admin-cache-v1';

// URLs to cache purely for offline UI shell (if any)
const URLS_TO_CACHE = [
  '/offline', // Optional fallback URL
  '/static/assets/logo-completa.png',
  '/static/assets/logo-sem-fundo.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(URLS_TO_CACHE))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
  self.clients.claim();
});

// Admin flows are dynamic, do network-first or network-only
self.addEventListener('fetch', (event) => {
  // Ignorar requisições não-GET
  if (event.request.method !== 'GET') {
    return;
  }
  
  // Ignorar rotas de API/AJAX para não causar inconsistência (ex: webhook, ajax)
  if (event.request.url.includes('/api/') || event.request.url.includes('/ajax/')) {
    return;
  }

  // Network First Strategy para as views do painel
  event.respondWith(
    fetch(event.request)
      .then((networkResponse) => {
        // Opcionalmente atualiza cache se o request for de estático (.css, .js)
        if (event.request.url.match(/\.(css|js|png|jpg|jpeg|svg|webp|gif|woff2?|ttf)$/i)) {
             const responseClone = networkResponse.clone();
             caches.open(CACHE_NAME).then((cache) => {
               cache.put(event.request, responseClone);
             });
        }
        return networkResponse;
      })
      .catch(() => {
        // Modo offline (tenta buscar do cache caso network caia)
        return caches.match(event.request).then((cachedResponse) => {
          if (cachedResponse) {
            return cachedResponse;
          }
          // Caso não tenha cache disponível
          return new Response(
              '<html><body><h1>Sem conexão</h1><p>Verifique sua internet.</p></body></html>',
              { headers: { 'Content-Type': 'text/html' } }
          );
        });
      })
  );
});
