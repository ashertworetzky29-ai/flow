self.addEventListener('install', (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open('flow-v1').then(cache => cache.addAll(['/', '/index.html', '/manifest.json'])));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== 'flow-v1').map(k => caches.delete(k))))
  );
  self.clients.claim();
});
self.addEventListener('fetch', (e) => {
  // Network-first for API, cache-first for static
  if (e.request.url.includes('/api/')) {
    e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
    return;
  }
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request).then(resp => {
    // cache new static assets
    if (resp.ok && e.request.method === 'GET') {
      const clone = resp.clone();
      caches.open('flow-v1').then(cache => cache.put(e.request, clone));
    }
    return resp;
  })));
});
