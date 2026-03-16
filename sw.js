// BTC Monitor Service Worker v3 — cache busted
const CACHE_NAME = 'btc-monitor-v3';
const ASSETS = ['/btc-monitor/', '/btc-monitor/index.html', '/btc-monitor/manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Network first — always try network, fall back to cache
self.addEventListener('fetch', e => {
  // Don't intercept Binance API calls — let them go direct
  if (e.request.url.includes('binance.com')) return;
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});

let currentTF = '1h', currentSR = [], lastAlertKey = '';

self.addEventListener('message', e => {
  if (e.data?.type === 'START_MONITORING') { currentTF = e.data.tf || '1h'; currentSR = e.data.srLevels || []; }
  if (e.data?.type === 'STOP_MONITORING') { currentTF = null; }
  if (e.data?.type === 'UPDATE_SR') { currentSR = e.data.srLevels || []; }
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      if (list.length > 0) return list[0].focus();
      return clients.openWindow('/btc-monitor/index.html');
    })
  );
});
