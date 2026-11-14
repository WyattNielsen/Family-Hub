// App shell cache. Versioned JS/CSS (?v=N) are served cache-first.
// HTML pages are pre-cached on install and served immediately from cache.
// API calls skip the worker completely.
const CACHE = 'familyhub-v5';
const SHELL_HTML = [
  '/',
  '/calendar.html',
  '/chores.html',
  '/meals.html',
  '/shopping.html',
  '/security.html',
  '/home.html',
  '/messages.html',
  '/settings.html',
];

self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(
    caches.open(CACHE).then(cache =>
      Promise.allSettled(SHELL_HTML.map(url => cache.add(url)))
    )
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function isImmutableAsset(url) {
  return (
    ((url.pathname.startsWith('/js/') || url.pathname.startsWith('/css/')) && url.search.includes('v=')) ||
    url.pathname.startsWith('/icons/') ||
    url.pathname === '/favicon.svg'
  );
}

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/')) return;

  if (isImmutableAsset(url)) {
    e.respondWith(
      caches.match(e.request).then(hit => {
        if (hit) return hit;
        return fetch(e.request).then(res => {
          if (res && res.ok) {
            const copy = res.clone();
            e.waitUntil(caches.open(CACHE).then(c => c.put(e.request, copy)));
          }
          return res;
        });
      })
    );
    return;
  }

  e.respondWith(
    caches.open(CACHE).then(cache => cache.match(e.request).then(hit => {
      if (hit) {
        // Defer background HTML revalidation slightly so initial API requests
        // never compete with SW background fetches for HTTP/1.1 connections.
        e.waitUntil(
          new Promise(r => setTimeout(r, 1500)).then(() =>
            fetch(e.request).then(res => {
              if (res && res.ok) return cache.put(e.request, res.clone());
            }).catch(() => {})
          )
        );
        return hit;
      }
      return fetch(e.request).then(res => {
        if (res && res.ok) {
          const copy = res.clone();
          e.waitUntil(cache.put(e.request, copy));
        }
        return res;
      });
    }))
  );
});
