/* Offline outbox shared by the page and the service worker. Items are replayed in order. */
(function (G) {
  'use strict';
  var DB = 'alberich-outbox', STORE = 'items';
  function open() {
    return new Promise(function (res, rej) {
      var r = G.indexedDB.open(DB, 1);
      r.onupgradeneeded = function () { r.result.createObjectStore(STORE, { keyPath: 'key' }); };
      r.onsuccess = function () { res(r.result); };
      r.onerror = function () { rej(r.error); };
    });
  }
  function tx(mode, fn) {
    return open().then(function (db) {
      return new Promise(function (res, rej) {
        var t = db.transaction(STORE, mode), s = t.objectStore(STORE), out = fn(s);
        t.oncomplete = function () { res(out && out.result !== undefined ? out.result : undefined); };
        t.onerror = function () { rej(t.error); };
      });
    });
  }
  var Outbox = {
    add: function (item) { item.at = item.at || Date.now(); return tx('readwrite', function (s) { return s.put(item); }); },
    all: function () { return tx('readonly', function (s) { return s.getAll(); }).then(function (v) { return (v || []).sort(function (a, b) { return a.at - b.at; }); }); },
    del: function (key) { return tx('readwrite', function (s) { return s.delete(key); }); },
    /* Send everything; keep items that failed for network/auth reasons. Returns the number delivered. */
    flush: function () {
      return Outbox.all().then(function (items) {
        var sent = 0;
        return items.reduce(function (p, it) {
          return p.then(function (ok) {
            if (!ok) return false;
            var req = it.file
              ? (function () { var fd = new G.FormData(); fd.append('files', it.file, it.name || 'receipt.jpg');
                  return { method: 'POST', credentials: 'same-origin', headers: { 'X-Finapp': '1' }, body: fd }; })()
              : { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Finapp': '1' }, body: JSON.stringify(it.body) };
            return G.fetch(it.url, req)
              .then(function (r) {
                if (r.status === 401 || r.status >= 500) return false;
                return Outbox.del(it.key).then(function () { sent++; return true; });
              }, function () { return false; });
          });
        }, Promise.resolve(true)).then(function () { return sent; });
      });
    }
  };
  G.Outbox = Outbox;
})(typeof self !== 'undefined' ? self : window);
