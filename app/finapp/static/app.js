(function () {
  'use strict';
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(function () {});

  function uuid() { return (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : Date.now() + '-' + Math.random().toString(16).slice(2); }
  function toast(msg, action) {
    var ul = $('#toasts'); if (!ul) return;
    var li = document.createElement('li'); li.className = 'toast';
    var sp = document.createElement('span'); sp.textContent = msg; li.appendChild(sp);
    if (action) { var b = document.createElement('button'); b.type = 'button'; b.textContent = action.label;
      b.addEventListener('click', function () { action.fn(); li.remove(); }); li.appendChild(b); }
    ul.appendChild(li); setTimeout(function () { li.remove(); }, action ? 10000 : 4500);
  }
  function post(url, body) {
    return fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Finapp': '1' }, body: JSON.stringify(body || {}) });
  }
  function paint(t) {
    var el = $('.tile[data-id="' + t.id + '"]'); if (!el) return;
    el.dataset.status = t.status; $('.cnt', el).textContent = t.remaining + '/' + t.allotted; $('.lbl', el).textContent = t.label;
  }
  function react(kind) {
    var st = $('#stage'); if (!st) return;
    st.classList.remove('cheer', 'grumble'); void st.offsetWidth; st.classList.add(kind === 'bad' ? 'grumble' : 'cheer');
  }
  function swapSprite(mood) {
    var st = $('#stage'); if (!st || !mood || st.dataset.mood === mood) return;
    fetch('/api/sprite/' + mood, { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.text() : null; }).then(function (svg) {
      if (!svg) return; var old = $('svg', st); if (old) old.outerHTML = svg; st.dataset.mood = mood;
    });
  }
  function queueOffline(activityId, key) {
    var ts = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
    return Outbox.add({ key: key, type: 'log', url: '/api/log', body: { activity_id: activityId, key: key, ts: ts } }).then(function () {
      if ('serviceWorker' in navigator && 'SyncManager' in window) {
        navigator.serviceWorker.ready.then(function (reg) { return reg.sync.register('outbox-flush'); }).catch(function () {});
      }
    });
  }

  /* returns {ok, offline, data} */
  function logActivity(id, key) {
    return post('/api/log', { activity_id: id, key: key }).then(function (r) {
      if (r.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname); return { ok: false }; }
      if (!r.ok) return { ok: false };
      return r.json().then(function (d) { return { ok: true, data: d }; });
    }, function () { return queueOffline(id, key).then(function () { return { ok: true, offline: true }; }); });
  }

  function onTile(el) {
    var id = parseInt(el.dataset.id, 10), name = $('.nm', el).textContent, key = uuid();
    logActivity(id, key).then(function (res) {
      if (!res.ok) { toast('Alberich could not record that. Try again.'); return; }
      if (res.offline) {
        var n = parseInt($('.cnt', el).textContent, 10); el.dataset.status = 'waiting';
        $('.lbl', el).textContent = 'Saved on this phone, syncing later'; toast('Saved offline. It will reach the ledger when you are back in range.'); react('good'); return;
      }
      var d = res.data; paint(d.tile); react(d.status === 'on_pace' ? 'good' : 'bad'); swapSprite(d.mood);
      var msg = d.status === 'on_pace' ? name + ' noted. ' + d.tile.remaining + ' left.' :
        d.status === 'borrowed' ? name + ' noted, drawn early from the month.' : name + ' noted, but you are over the plan.';
      toast(msg, { label: 'Undo', fn: function () {
        post('/api/undo', { log_id: d.log_id }).then(function (r) { return r.json(); }).then(function (u) { paint(u.tile); swapSprite(u.mood); toast('Struck from the ledger.'); });
      } });
    });
  }

  document.addEventListener('click', function (e) {
    var tile = e.target.closest && e.target.closest('.tile[data-id]');
    if (tile) { onTile(tile); return; }
    var act = e.target.closest && e.target.closest('[data-act]');
    if (!act) return;
    if (act.dataset.act === 'enable-push') enablePush();
    if (act.dataset.act === 'test-push') testPush();
  });
  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (f.matches && f.matches('form[data-logout]')) {
      e.preventDefault();
      (window.caches ? caches.keys().then(function (ks) { return Promise.all(ks.map(function (k) { return caches.delete(k); })); }) : Promise.resolve())
        .then(function () { f.submit(); });
    }
  });

  /* monthly review */
  var rvSec = $('section[data-month]');
  if (rvSec) {
    var month = rvSec.dataset.month;
    var setProg = function (p) {
      $('#prog-text').textContent = p.done + ' of ' + p.total + ' decided';
      $('#prog-sub').textContent = p.ask + ' need you · ' + p.auto + ' Alberich is sure of';
      $('#prog-bar').style.width = Math.round(100 * p.done / Math.max(1, p.total)) + '%';
    };
    var PRESETS = [0, 5000, 10000];
    /* items from one receipt that sit next to each other are tied together by a ribbon */
    var relink = function () {
      var parents = [];
      $$('.rv', rvSec).forEach(function (c) { if (parents.indexOf(c.parentNode) === -1) parents.push(c.parentNode); });
      parents.forEach(function (par) {
        var cards = Array.prototype.filter.call(par.children, function (c) { return c.classList.contains('rv'); });
        cards.forEach(function (c) { c.classList.remove('rl', 'rl-first', 'rl-mid', 'rl-last'); });
        for (var i = 0; i < cards.length;) {
          var j = i, key = cards[i].dataset.rcpt ? cards[i].dataset.txn : '';
          while (key && j + 1 < cards.length && cards[j + 1].dataset.rcpt && cards[j + 1].dataset.txn === key) j++;
          if (j > i) for (var k = i; k <= j; k++) cards[k].classList.add('rl', k === i ? 'rl-first' : k === j ? 'rl-last' : 'rl-mid');
          i = j + 1;
        }
      });
    };
    relink();
    var moveToDecided = function (card) {
      var fold = $('#decided-fold');
      if (!fold) {
        fold = document.createElement('details'); fold.className = 'fold'; fold.id = 'decided-fold';
        fold.innerHTML = '<summary><span id="decided-count">0</span> decided</summary><div id="decided-list"></div>';
        rvSec.appendChild(fold);
      }
      $('#decided-list', fold).appendChild(card);
      card.classList.add('aged');
      $('#decided-count', fold).textContent = $$('#decided-list .rv', fold).length;
      relink();
    };
    var decide = function (card, bps, clicked) {
      var cat = $('.cat', card), rule = $('.rule', card), line = parseInt(card.dataset.line, 10);
      if (clicked) {      /* answer the tap at once: the chosen button goes green before the server has replied */
        $$('.opt', card).forEach(function (b) { b.classList.remove('on', 'hint'); });
        clicked.classList.add('on');
      }
      post('/api/review/decide', { line_id: line, bps: bps, category_id: cat ? parseInt(cat.value, 10) : null, rule: rule ? rule.checked : false })
        .then(function (r) { if (r.status === 401) { location.href = '/login?next=/review'; return null; } return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d) { if (clicked) clicked.classList.remove('on'); toast('Alberich could not record that. Try again.'); return; }
          setProg(d.progress); card.classList.add('settled'); card.dataset.bps = bps;
          $$('.opt[data-bps]', card).forEach(function (b) { b.classList.remove('on', 'hint'); if (parseInt(b.dataset.bps, 10) === bps) b.classList.add('on'); });
          var custom = PRESETS.indexOf(bps) === -1, setBtn = $('[data-custom]', card), inp = $('.custom input', card);
          if (setBtn) { setBtn.classList.remove('hint'); setBtn.classList.toggle('on', custom); }
          if (inp) inp.value = custom ? String(bps / 100) : '';
          var w = $('.why', card); if (w) w.textContent = 'You decided this.';
          var keep = $('[data-keep]', card); if (keep) keep.remove();
          if (!$('[data-reopen]', card)) {
            var undo = document.createElement('button'); undo.type = 'button'; undo.className = 'btn ghost'; undo.setAttribute('data-reopen', ''); undo.textContent = 'Undo';
            var row = $('.row', card); if (row) row.appendChild(undo);
          }
          if (clicked) clicked.classList.add('on');
          setTimeout(function () { moveToDecided(card); }, clicked ? 900 : 0);        /* let the green be seen before it files away */
          toast('Done. Moved to the decided pile.', { label: 'Undo', fn: function () {
            post('/api/review/reopen', { line_id: line }).then(function () { location.reload(); }); } });
        });
    };
    document.addEventListener('click', function (e) {
      var card = e.target.closest && e.target.closest('.rv'); var t = e.target;
      if (card && t.matches('.opt[data-bps]')) decide(card, parseInt(t.dataset.bps, 10), t);
      else if (card && t.matches('[data-keep]')) decide(card, parseInt(card.dataset.bps, 10), t);
      else if (card && t.matches('[data-custom]')) {
        var v = parseFloat($('.custom input', card).value);
        if (isNaN(v) || v < 0 || v > 100) { toast('Enter a share between 0 and 100.'); return; } decide(card, Math.round(v * 100), t);
      } else if (card && t.matches('[data-reopen]')) {
        post('/api/review/reopen', { line_id: parseInt(card.dataset.line, 10) }).then(function () { location.reload(); });
      } else if (t.matches('[data-confirm-group]')) {
        post('/api/review/confirm', { month: month, which: t.dataset.confirmGroup }).then(function (r) { return r.json(); })
          .then(function (d) { toast(d.confirmed + ' decided.'); setTimeout(function () { location.reload(); }, 600); });
      }
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && e.target.matches && e.target.matches('.custom input')) { e.preventDefault(); var b = $('[data-custom]', e.target.closest('.rv')); if (b) b.click(); }
    });
    document.addEventListener('change', function (e) {
      var card = e.target.closest && e.target.closest('.rv');
      if (card && e.target.matches('.cat')) {
        var sel = e.target;
        post('/api/review/category', { line_id: parseInt(card.dataset.line, 10), category_id: parseInt(sel.value, 10) })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (d) {
            if (!d) { toast('Alberich could not save that category. Try again.'); return; }
            toast('Filed as ' + d.category + (d.moved ? '. ' + d.moved + ' other purchase' + (d.moved === 1 ? '' : 's') + ' there follow.' : '.'));
          });
      }
    });
  }


  /* a result line in an upload list can be dismissed; the text node stays last so later updates keep working */
  function dismissible(li, onGone) {
    var x = document.createElement('button'); x.type = 'button'; x.className = 'dismiss'; x.textContent = '\u00d7'; x.setAttribute('aria-label', 'Dismiss');
    x.addEventListener('click', function () { li.remove(); if (onGone) onGone(); });
    li.insertBefore(x, li.firstChild);
  }

  /* receipt capture, with an offline queue for the photos */
  var rd = $('[data-rdrop]');
  if (rd) {
    var rlist = $('#r-results');
    var rline = function (name, text, cls) {
      var li = document.createElement('li'); li.className = cls || '';
      var b = document.createElement('strong'); b.textContent = name; li.appendChild(b);
      li.appendChild(document.createTextNode(' ' + text)); dismissible(li); rlist.appendChild(li); return li;
    };
    var rsend = function (file) {
      var li = rline(file.name || 'photo', 'sending...', 'wait');
      var fd = new FormData(); fd.append('files', file, file.name || 'receipt.jpg');
      return fetch('/api/receipts/upload', { method: 'POST', credentials: 'same-origin', body: fd }).then(function (r) {
        if (r.status === 401) { location.href = '/login?next=/receipts'; return null; }
        if (!r.ok) throw new Error('bad');
        return r.json();
      }).then(function (d) {
        if (!d) return; var res = d.results[0];
        li.className = res.status === 'queued' ? 'ok' : res.status === 'duplicate' ? 'wait' : 'bad';
        li.lastChild.textContent = res.status === 'queued' ? ' received. Alberich is reading it.' : res.status === 'duplicate' ? ' was already here.' :
          res.status === 'too_big' ? ' is over 15 MB.' : ' is not a picture or PDF.';
      }, function () {
        return Outbox.add({ key: 'rc-' + uuid(), type: 'upload', url: '/api/receipts/upload', file: file, name: file.name || 'receipt.jpg' }).then(function () {
          li.className = 'wait'; li.lastChild.textContent = ' saved on this phone. It will go through when you are back in range.';
          if ('serviceWorker' in navigator && 'SyncManager' in window) navigator.serviceWorker.ready.then(function (reg) { return reg.sync.register('outbox-flush'); }).catch(function () {});
        });
      });
    };
    var rrun = function (files) {
      var p = Promise.resolve();
      Array.prototype.slice.call(files).forEach(function (f) { p = p.then(function () { return rsend(f); }); });
      p.then(function () { var a = document.createElement('a'); a.className = 'btn'; a.href = '/receipts'; a.textContent = 'See the list';
        var li = document.createElement('li'); li.appendChild(a); rlist.appendChild(li); });
    };
    ['#r-cam', '#r-files'].forEach(function (s) { var el = $(s); el.addEventListener('change', function () { if (el.files.length) rrun(el.files); el.value = ''; }); });
    ['dragenter', 'dragover'].forEach(function (n) { rd.addEventListener(n, function (e) { e.preventDefault(); rd.classList.add('over'); }); });
    ['dragleave', 'drop'].forEach(function (n) { rd.addEventListener(n, function (e) { e.preventDefault(); rd.classList.remove('over'); }); });
    rd.addEventListener('drop', function (e) { if (e.dataTransfer && e.dataTransfer.files.length) rrun(e.dataTransfer.files); });
  }

  /* forms that update themselves when a choice changes (inline handlers are blocked by CSP) */
  document.addEventListener('change', function (e) {
    var f = e.target.closest && e.target.closest('form[data-autosubmit]');
    if (f && !e.target.matches('input[type=text],textarea')) { if (window.Busy) Busy.nav('', 1200); f.submit(); }
  });

  /* a page that is waiting on a background job checks in and reloads when it is done */
  var pollBox = $('[data-poll]');
  if (pollBox) {
    var pollTimer = setInterval(function () {
      fetch(pollBox.dataset.poll, { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
        if (d && d.status !== 'running') { clearInterval(pollTimer); location.reload(); }
      }).catch(function () {});
    }, 2500);
  }

  /* anything sent to the model waits for the consent box to be ticked */
  $$('form').forEach(function (f) {
    var box = $('input[name="consent"]', f), btn = $('[data-needs-consent]', f);
    if (!box || !btn) return;
    var sync = function () { btn.disabled = !box.checked; };
    box.addEventListener('change', sync); sync();
  });

  /* the reference point form shows only the fields that apply to what is being added */
  var anchorKind = $('#anchors select[name="kind"]');
  if (anchorKind) {
    var syncAnchor = function () {
      $$('#anchors .only-pay').forEach(function (el) { el.hidden = anchorKind.value !== 'salary'; });
      $$('#anchors .only-rent').forEach(function (el) { el.hidden = anchorKind.value !== 'rent'; });
    };
    anchorKind.addEventListener('change', syncAnchor); syncAnchor();
  }

  /* an account that already holds statements asks before its bank or card number is changed */
  document.addEventListener('submit', function (e) {
    var f = e.target, n = f.getAttribute && parseInt(f.getAttribute('data-guard'), 10);
    if (!n || (e.submitter && e.submitter.getAttribute('formaction'))) return;
    var changed = ['institution', 'last4'].some(function (k) { var i = f.elements[k]; return i && i.value !== i.defaultValue; });
    if (changed && !window.confirm('This account already holds ' + n + ' statement' + (n === 1 ? '' : 's') + ' and you are changing its bank or card number. Is it really the same account? To add a different one, use "Add an account" at the bottom instead.')) e.preventDefault();
  });

  /* confirm-before-submit for destructive forms (inline handlers are blocked by CSP) */
  document.addEventListener('submit', function (e) {
    var m = e.target.getAttribute && e.target.getAttribute('data-confirm');
    if (m && !window.confirm(m)) e.preventDefault();
  });

  /* the matrix opens scrolled to the newest months */
  $$('.tscroll').forEach(function (el) { if (el.querySelector('table.matrix')) el.scrollLeft = el.scrollWidth; });

  /* statement drop zone */
  var dz = $('[data-drop]');
  if (dz) {
    var input = $('#drop-input'), list = $('#drop-results'), acct = $('#drop-acct');
    var again = null;      /* the one "Refresh the ledgers" button, kept at the bottom of the list */
    var tidy = function () { if (again && !list.querySelector('li:not(.again)')) { again.remove(); again = null; } };
    var line = function (name, text, cls) {
      var li = document.createElement('li'); li.className = cls || '';
      var b = document.createElement('strong'); b.textContent = name; li.appendChild(b);
      li.appendChild(document.createTextNode(' ' + text)); dismissible(li, tidy); list.appendChild(li); return li;
    };
    var sendOne = function (file) {
      var li = line(file.name, 'being read...', 'wait');
      var fd = new FormData(); fd.append('files', file); if (acct && acct.value) fd.append('account_id', acct.value);
      return fetch('/api/ledgers/upload', { method: 'POST', credentials: 'same-origin', body: fd }).then(function (r) {
        if (r.status === 401) { location.href = '/login?next=/ledgers'; return null; }
        return r.json();
      }).then(function (d) {
        if (!d) return; var res = d.results[0];
        li.className = res.status === 'reconciled' ? 'ok' : (res.status === 'parsed' || res.status === 'duplicate') ? 'wait' : 'bad';
        li.lastChild.textContent = ' ' + res.message;
        if (res.id && res.status !== 'duplicate' && res.status !== 'unsupported') {
          var a = document.createElement('a'); a.href = '/statements/' + res.id; a.textContent = ' Open'; li.appendChild(a);
        }
      }, function () { li.className = 'bad'; li.lastChild.textContent = ' could not reach Alberich. Try again.'; });
    };
    var run = function (files) {
      var arr = Array.prototype.slice.call(files), p = Promise.resolve();
      arr.forEach(function (f) { p = p.then(function () { return sendOne(f); }); });
      p.then(function () {
        if (!again) {
          var b = document.createElement('a'); b.className = 'btn'; b.href = '/ledgers'; b.textContent = 'Refresh the ledgers';
          again = document.createElement('li'); again.className = 'again'; again.appendChild(b);
        }
        list.appendChild(again);      /* moves it to the end if it is already there */
      });
    };
    input.addEventListener('change', function () { if (input.files.length) run(input.files); input.value = ''; });
    ['dragenter', 'dragover'].forEach(function (n) { dz.addEventListener(n, function (e) { e.preventDefault(); dz.classList.add('over'); }); });
    ['dragleave', 'drop'].forEach(function (n) { dz.addEventListener(n, function (e) { e.preventDefault(); dz.classList.remove('over'); }); });
    dz.addEventListener('drop', function (e) { if (e.dataTransfer && e.dataTransfer.files.length) run(e.dataTransfer.files); });
  }

  /* flush anything queued while offline */
  function flush() {
    if (!window.Outbox) return;
    Outbox.flush().then(function (n) { if (n) { toast(n + ' offline entr' + (n === 1 ? 'y' : 'ies') + ' carried to the ledger.');
      fetch('/api/state', { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (s) {
        if (s) { s.tiles.forEach(paint); swapSprite(s.mood); } }); } });
  }
  window.addEventListener('online', flush); window.addEventListener('load', flush);

  /* push */
  function b64ToU8(s) { var p = '='.repeat((4 - s.length % 4) % 4), b = atob((s + p).replace(/-/g, '+').replace(/_/g, '/')), o = new Uint8Array(b.length);
    for (var i = 0; i < b.length; i++) o[i] = b.charCodeAt(i); return o; }
  function setStatus(t) { var el = $('#push-status'); if (el) el.textContent = t; }
  function refreshStatus() {
    if (!$('#push-status')) return;
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) { setStatus('This browser cannot receive pushed notifications.'); return; }
    var perm = Notification.permission;
    navigator.serviceWorker.ready.then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (sub) {
      setStatus(perm === 'denied' ? 'Notifications are blocked for this site in Chrome settings.' :
        sub ? 'This phone is subscribed. Alberich can reach you.' : 'This phone is not subscribed yet.');
    });
  }
  function enablePush() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) { setStatus('This browser cannot receive pushed notifications.'); return; }
    Notification.requestPermission().then(function (perm) {
      if (perm !== 'granted') { setStatus('Permission was not granted.'); return; }
      return navigator.serviceWorker.ready.then(function (reg) {
        return fetch('/api/push/key', { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (k) {
          return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToU8(k.key) });
        });
      }).then(function (sub) { return post('/api/push/subscribe', sub.toJSON()); })
        .then(function () { setStatus('Subscribed. Press "Send a test raven".'); toast('Ravens allowed.'); setTimeout(function () { location.reload(); }, 1200); });
    }).catch(function (err) { setStatus('Could not subscribe: ' + err.message); });
  }
  function testPush() {
    post('/api/push/test').then(function (r) { return r.json(); }).then(function (d) {
      toast(d.ok ? 'A raven is on its way.' : 'No device could be reached. Allow ravens first.'); });
  }
  refreshStatus();
})();
