// App shell cache. Versioned JS/CSS (?v=N) are cache-first.
// HTML is network-first so tablets always pick up shell/script updates.
// API calls skip the worker completely.
const CACHE = 'familyhub-v7';
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
      Promise.all(SHELL_HTML.map(url => cache.add(url).catch(function () { return null; })))
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
    ((url.pathname.indexOf('/js/') === 0 || url.pathname.indexOf('/css/') === 0) && url.search.indexOf('v=') !== -1) ||
    url.pathname.indexOf('/icons/') === 0 ||
    url.pathname === '/favicon.svg'
  );
}

function isHtmlRequest(request, url) {
  if (request.mode === 'navigate') return true;
  if (url.pathname === '/' || /\.html$/i.test(url.pathname)) return true;
  const accept = request.headers.get('accept') || '';
  return accept.indexOf('text/html') !== -1;
}

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.indexOf('/api/') === 0) return;

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

  // HTML: network-first so Android tablets never stick on an old shell.
  if (isHtmlRequest(e.request, url)) {
    e.respondWith(
      fetch(e.request).then(res => {
        if (res && res.ok) {
          const copy = res.clone();
          e.waitUntil(caches.open(CACHE).then(c => c.put(e.request, copy)));
        }
        return res;
      }).catch(() => caches.match(e.request).then(hit => hit || caches.match('/')))
    );
    return;
  }

  e.respondWith(
    caches.open(CACHE).then(cache => cache.match(e.request).then(hit => {
      if (hit) return hit;
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
