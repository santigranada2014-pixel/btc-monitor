// BTC Monitor Service Worker
// Corre en segundo plano y envía notificaciones cuando hay señal

const CACHE_NAME = 'btc-monitor-v1';
const ASSETS = ['/', '/index.html', '/manifest.json'];

// ── Install ───────────────────────────────────────────
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS))
  );
  self.skipWaiting();
});

// ── Activate ──────────────────────────────────────────
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ── Fetch (serve from cache when offline) ────────────
self.addEventListener('fetch', e => {
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});

// ── Background sync — check signals every 2 min ──────
let checkInterval = null;

self.addEventListener('message', e => {
  if (e.data && e.data.type === 'START_MONITORING') {
    const { tf, srLevels } = e.data;
    startMonitoring(tf, srLevels);
  }
  if (e.data && e.data.type === 'STOP_MONITORING') {
    if (checkInterval) { clearInterval(checkInterval); checkInterval = null; }
  }
  if (e.data && e.data.type === 'UPDATE_SR') {
    currentSR = e.data.srLevels;
  }
});

let currentTF = '1h';
let currentSR = [];
let lastAlertKey = '';

function startMonitoring(tf, srLevels) {
  currentTF = tf || '1h';
  currentSR = srLevels || [];
  if (checkInterval) clearInterval(checkInterval);
  checkInterval = setInterval(() => runCheck(), 2 * 60 * 1000);
}

async function runCheck() {
  try {
    const interval = currentTF;
    const url = `https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=${interval}&limit=100`;
    const r = await fetch(url);
    if (!r.ok) return;
    const raw = await r.json();
    const candles = raw.map(k => ({ t: k[0], o: +k[1], h: +k[2], l: +k[3], c: +k[4], v: +k[5] }));

    // Need 4H data too for trend
    const r4 = await fetch('https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=4h&limit=20');
    const raw4 = await r4.json();
    const candles4h = raw4.map(k => ({ t: k[0], o: +k[1], h: +k[2], l: +k[3], c: +k[4], v: +k[5] }));

    const result = analyzeSignal(candles, candles4h, currentSR);

    if (result.signal && result.key !== lastAlertKey) {
      lastAlertKey = result.key;
      await sendNotification(result);
    }
  } catch (err) {
    // silently fail — no connection
  }
}

// ── Indicators ────────────────────────────────────────
function ema(arr, p) {
  if (arr.length < p) return arr.map(() => null);
  const k = 2 / (p + 1);
  let v = arr.slice(0, p).reduce((a, b) => a + b, 0) / p;
  const r = new Array(p - 1).fill(null); r.push(v);
  for (let i = p; i < arr.length; i++) { v = arr[i] * k + v * (1 - k); r.push(v); }
  return r;
}

function calcMACD(closes) {
  const e12 = ema(closes, 12), e26 = ema(closes, 26);
  const ml = closes.map((_, i) => (e12[i] != null && e26[i] != null) ? +(e12[i] - e26[i]).toFixed(2) : null);
  const valid = ml.filter(v => v != null);
  if (valid.length < 9) return { ml, sig: ml.map(() => null), hist: ml.map(() => null) };
  const sr = ema(valid, 9);
  const sig = new Array(ml.length).fill(null); let si = 0;
  for (let i = 0; i < ml.length; i++) if (ml[i] != null && si < sr.length) { sig[i] = sr[si] != null ? +sr[si].toFixed(2) : null; si++; }
  const hist = ml.map((v, i) => (v != null && sig[i] != null) ? +(v - sig[i]).toFixed(2) : null);
  return { ml, sig, hist };
}

function analyzeSignal(src, src4h, SR) {
  const n = src.length;
  if (n < 30) return { signal: null };
  const last = src[n-1], prev = src[n-2], p2 = src[n-3] || prev;
  const closes = src.map(c => c.c);
  const vols = src.map(c => c.v || 0);
  const { ml, sig, hist: ht } = calcMACD(closes);
  const i = n - 1;
  const e14 = ema(closes, 14), e21 = ema(closes, 21);

  const bull = last.c >= last.o, bear = last.c < last.o;
  const h4 = src4h.slice(-8);
  const tUp = h4[h4.length-1].c > h4[0].c && h4[h4.length-1].l > h4[0].l;
  const tDn = h4[h4.length-1].c < h4[0].c && h4[h4.length-1].h < h4[0].h;

  const isHL = last.l > p2.l, isHH = last.h > prev.h;
  const isLH = last.h < prev.h, isLL = last.l < prev.l;
  const nearSup = SR.find(s => s.t === 'sup' && Math.abs(s.p - last.c) / last.c < 0.018);
  const nearRes = SR.find(s => s.t === 'res' && Math.abs(s.p - last.c) / last.c < 0.018);
  const retestSup = SR.find(s => s.t === 'sup' && last.l <= s.p * 1.008 && last.c > s.p);
  const retestRes = SR.find(s => s.t === 'res' && last.h >= s.p * 0.992 && last.c < s.p);
  const body = Math.abs(last.c - last.o), rng = last.h - last.l || 1;
  const lwk = Math.min(last.c, last.o) - last.l, uwk = last.h - Math.max(last.c, last.o);
  const hammer = lwk > body * 1.5 && bull;
  const star = uwk > body * 1.5 && bear;
  const strongBull = bull && body / rng > 0.55;
  const strongBear = bear && body / rng > 0.55;
  const e14v = e14[i], e21v = e21[i];
  const aboveEMA = e14v && e21v && last.c > e14v && last.c > e21v;
  const belowEMA = e14v && e21v && last.c < e14v && last.c < e21v;
  const sl14u = e14[i] && e14[i-3] && e14[i] > e14[i-3];
  const sl14d = e14[i] && e14[i-3] && e14[i] < e14[i-3];
  const sl21u = e21[i] && e21[i-3] && e21[i] > e21[i-3];
  const sl21d = e21[i] && e21[i-3] && e21[i] < e21[i-3];
  const xUp = ml[i] != null && sig[i] != null && ml[i-1] != null && sig[i-1] != null && ml[i-1] < sig[i-1] && ml[i] > sig[i];
  const xDn = ml[i] != null && sig[i] != null && ml[i-1] != null && sig[i-1] != null && ml[i-1] > sig[i-1] && ml[i] < sig[i];
  const histGreen = ht[i] != null && ht[i] > 0;
  const histGrow = ht[i] != null && ht[i-1] != null && ht[i] > ht[i-1];
  const histRed = ht[i] != null && ht[i] < 0;
  const histRedGrow = ht[i] != null && ht[i-1] != null && ht[i] < ht[i-1];
  const avgVol = vols.slice(-20).reduce((a, b) => a + b, 0) / 20;
  const volSurge = vols[i] > avgVol * 1.2;

  const longItems = [isHL, isHH || tUp, !tDn, !!nearSup || !!retestSup, !!retestSup, SR.some(s=>s.t==='sup'), hammer, strongBull, !!(nearSup&&last.c>nearSup.p), !!aboveEMA, sl14u&&sl21u, !(sl14d&&sl21d), xUp, histGreen, histGrow, volSurge];
  const shortItems = [isLH, isLL || tDn, tDn, !!nearRes, !!retestRes, SR.some(s=>s.t==='res'), star, strongBear, !!(nearRes&&last.c<nearRes.p), !!belowEMA, sl14d&&sl21d, xDn, histRed&&histRedGrow, volSurge&&bear];

  const lOk = longItems.filter(Boolean).length;
  const sOk = shortItems.filter(Boolean).length;
  const lPct = Math.round(lOk / longItems.length * 100);
  const sPct = Math.round(sOk / shortItems.length * 100);
  const price = last.c;

  if (lPct >= 70 && lPct >= sPct) {
    return { signal: 'LONG', pct: lPct, ok: lOk, total: longItems.length, price, key: 'LONG' + Math.round(price / 200) };
  }
  if (sPct >= 70) {
    return { signal: 'SHORT', pct: sPct, ok: sOk, total: shortItems.length, price, key: 'SHORT' + Math.round(price / 200) };
  }
  return { signal: null };
}

async function sendNotification(result) {
  const price = Math.round(result.price).toLocaleString('en-US');
  const isLong = result.signal === 'LONG';
  const title = isLong ? '🟢 Señal LONG — BTC/USDT' : '🔴 Señal SHORT — BTC/USDT';
  const body = `Precio: $${price} · ${result.ok}/${result.total} condiciones (${result.pct}%) · Verificá en Binance`;

  await self.registration.showNotification(title, {
    body,
    icon: 'icon-192.png',
    badge: 'icon-192.png',
    tag: 'btc-signal',
    renotify: true,
    requireInteraction: true,
    vibrate: [200, 100, 200],
    data: { url: '/index.html', signal: result.signal, price: result.price }
  });
}

// ── Notification click — open app ─────────────────────
self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      if (list.length > 0) return list[0].focus();
      return clients.openWindow(e.notification.data.url || '/index.html');
    })
  );
});
