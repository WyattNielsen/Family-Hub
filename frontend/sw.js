// App shell cache. Versioned JS/CSS (?v=N) can be served from cache.
// HTML is shown from cache immediately, then refreshed in the background.
// API calls skip the worker.
const CACHE = 'familyhub-v3';

self.addEventListener('install', e => {
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function isVersionedAsset(url) {
  return url.pathname.startsWith('/js/') || url.pathname.startsWith('/css/');
}

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/')) return;

  if (isVersionedAsset(url)) {
    e.respondWith(
      caches.match(e.request).then(hit => {
        if (hit) return hit;
        return fetch(e.request).then(res => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then(c => c.put(e.request, copy));
          }
          return res;
        });
      })
    );
    return;
  }

  e.respondWith(
    caches.open(CACHE).then(cache => cache.match(e.request).then(hit => {
      const fresh = fetch(e.request).then(res => {
        if (res && res.ok) cache.put(e.request, res.clone());
        return res;
      });
      if (hit) {
        fresh.catch(() => {});
        return hit;
      }
      return fresh;
    }))
  );
});
