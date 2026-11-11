/* COOPEX PWA – camada segura: somente recursos estáticos públicos.
 * Nunca interceptar POST, autenticação, dados financeiros ou APIs.
 */
const CACHE = 'coopex-pwa-estaticos-v1';
const FALLBACK = '/static/coopex-offline.html';
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.add(FALLBACK)));
  self.skipWaiting();
});
self.addEventListener('activate', event => {
  event.waitUntil(Promise.all([
    caches.keys().then(keys => Promise.all(keys.filter(key =>
      key.startsWith('coopex-pwa-estaticos-') && key !== CACHE
    ).map(key => caches.delete(key)))),
    self.clients.claim()
  ]));
});
self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;
  let url;
  try { url = new URL(req.url); } catch (_) { return; }
  if (url.origin !== self.location.origin) return;
  if (req.mode === 'navigate') {
    event.respondWith(fetch(req).catch(async () => {
      const cache = await caches.open(CACHE);
      return (await cache.match(FALLBACK)) || new Response('COOPEX offline', {
        status:503,headers:{'Content-Type':'text/plain; charset=UTF-8'}
      });
    }));
    return;
  }
  // Apenas JS/CSS/fontes públicos da pasta static. Evita guardar informações de sessões.
  if (!url.pathname.startsWith('/static/') ||
      !/\.(?:js|css|woff2?|ttf|svg)$/.test(url.pathname)) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const stored = await cache.match(req);
    if (stored) {
      event.waitUntil(fetch(req).then(r => {
        if (r.ok && r.type === 'basic') return cache.put(req,r.clone());
      }).catch(() => {}));
      return stored;
    }
    const response = await fetch(req);
    if (response.ok && response.type === 'basic') {
      event.waitUntil(cache.put(req,response.clone()).catch(() => {}));
    }
    return response;
  })());
});
