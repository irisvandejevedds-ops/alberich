/* Alberich service worker: app shell cache, offline outbox sync, push, notification actions. */
importScripts('/static/outbox.js');
var CACHE = 'alberich-v15';
var SHELL = ['/offline', '/static/app.css?v=51', '/static/frames.css?v=23', '/static/app.js', '/static/busy.js', '/static/outbox.js',
  '/static/fonts/fraktur-bold.woff2', '/static/fonts/pixel-console.woff2', '/static/icons/icon-192.png?v=2'];

self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return Promise.all(SHELL.map(function (u) { return c.add(u).catch(function () {}); })); })
    .then(function () { return self.skipWaiting(); }));
});
self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (ks) { return Promise.all(ks.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); })); })
    .then(function () { return self.clients.claim(); }));
});

self.addEventListener('fetch', function (e) {
  var req = e.request, url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.indexOf('/static/') === 0) {
    e.respondWith(caches.match(req).then(function (hit) {
      var net = fetch(req).then(function (r) { if (r.ok) { var cp = r.clone(); caches.open(CACHE).then(function (c) { c.put(req, cp); }); } return r; });
      return hit || net;
    }));
    return;
  }
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).then(function (r) {
      if (r.ok && !r.redirected) { var cp = r.clone(); caches.open(CACHE).then(function (c) { c.put(req, cp); }); }
      return r;
    }).catch(function () { return caches.match(req).then(function (hit) { return hit || caches.match('/offline'); }); }));
  }
});

self.addEventListener('sync', function (e) { if (e.tag === 'outbox-flush') e.waitUntil(Outbox.flush()); });

self.addEventListener('push', function (e) {
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = { title: 'Alberich', body: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(d.title || 'Alberich', {
    body: d.body || '', icon: d.icon, badge: d.badge, tag: d.tag || undefined, data: d.data || {}, actions: d.actions || [], renotify: false }));
});

self.addEventListener('notificationclick', function (e) {
  var n = e.notification, data = n.data || {};
  n.close();
  if (e.action === 'later') return;
  if (e.action === 'log' && data.activity_id) {
    var item = { key: data.key || ('n-' + data.activity_id + '-' + Date.now()), type: 'log', url: '/api/log',
      body: { activity_id: data.activity_id, key: data.key || ('n-' + data.activity_id + '-' + Date.now()) } };
    e.waitUntil(Outbox.add(item).then(function () { return Outbox.flush(); }).then(function (sent) {
      return self.registration.showNotification('Alberich', {
        body: sent ? 'Noted in the ledger.' : 'Saved. It will be recorded when you are back in range.',
        icon: '/static/icons/mood-neutral.png', tag: 'log-result' });
    }));
    return;
  }
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (cs) {
    for (var i = 0; i < cs.length; i++) { if ('focus' in cs[i]) { cs[i].navigate(data.url || '/'); return cs[i].focus(); } }
    return self.clients.openWindow(data.url || '/');
  }));
});
