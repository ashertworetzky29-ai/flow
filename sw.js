const CACHE_NAME = 'tickr-v9-1';
const ASSETS = [
  '/',
  '/index.html',
  '/manifest.json',
  '/tickr-192.png',
  '/tickr-512.png',
  '/tickr-192-maskable.png',
  '/tickr-512-maskable.png',
  'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap'
];

self.addEventListener('install', e => {
  console.log('[tickr sw] install v9.1');
  e.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  console.log('[tickr sw] activate v9.1');
  e.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  // Don't cache API calls - always go to network for /api/*
  if (e.request.url.includes('/api/')) {
    e.respondWith(fetch(e.request));
    return;
  }
  e.respondWith(
    caches.match(e.request).then(cached => {
      return cached || fetch(e.request).then(res => {
        // cache new assets on the fly
        if (res.ok && e.request.method === 'GET' && !e.request.url.includes('chrome-extension')) {
          const clone = res.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(e.request, clone));
        }
        return res;
      });
    }).catch(() => {
      // offline fallback to index
      if (e.request.destination === 'document') {
        return caches.match('/index.html');
      }
    })
  );
});
