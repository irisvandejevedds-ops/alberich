/* Visible feedback whenever the app is working: a marching bar at the top, a hopping-dots button, and for slow things a waiting card. */
(function () {
  var body = document.body, work = document.getElementById('working'), msg = document.getElementById('workmsg');
  var timer = null, guard = null, pending = 0, navigating = false;

  function dots() {
    var s = document.createElement('span'); s.className = 'dots'; s.setAttribute('aria-hidden', 'true');
    s.innerHTML = '<i></i><i></i><i></i>'; return s;
  }
  function show(text) { if (work) { msg.textContent = text; work.hidden = false; } }
  function reset() {
    clearTimeout(timer); clearTimeout(guard); navigating = false;
    if (!pending) body.classList.remove('loading');
    if (work) work.hidden = true;
    Array.prototype.forEach.call(document.querySelectorAll('.busy'), function (b) { b.classList.remove('busy'); var d = b.querySelector(':scope > .dots'); if (d) d.remove(); });
  }
  function nav(text, delay, giveUp) {
    navigating = true; body.classList.add('loading');
    clearTimeout(timer); clearTimeout(guard);
    timer = setTimeout(function () { show(text || 'One moment. Alberich is working on it.'); }, text ? 250 : (delay || 2500));
    guard = setTimeout(reset, giveUp || 90000);
  }
  window.Busy = { nav: nav, reset: reset };

  var realFetch = window.fetch;
  window.fetch = function (url, opts) {
    var method = ((opts && opts.method) || 'GET').toUpperCase();
    if (method === 'GET') return realFetch.apply(this, arguments);
    pending++; body.classList.add('loading');
    var done = function () { pending = Math.max(0, pending - 1); if (!pending && !navigating) body.classList.remove('loading'); };
    var p = realFetch.apply(this, arguments);
    p.then(done, done);
    return p;
  };

  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (e.defaultPrevented || !f.matches || !f.matches('form') || f.hasAttribute('data-nobusy')) return;
    var btn = e.submitter || f.querySelector('button[type=submit], button:not([type])');
    var text = f.getAttribute('data-busy') || (btn && btn.getAttribute('data-busy')) || '';
    if (btn) { btn.classList.add('busy'); if (!btn.querySelector(':scope > .dots')) btn.appendChild(dots()); }
    nav(text, 2000);
  });

  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a || e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || a.target || a.hasAttribute('download')) return;
    var u;
    try { u = new URL(a.href, location.href); } catch (x) { return; }
    if (u.origin !== location.origin || (u.pathname === location.pathname && u.search === location.search)) return;
    if (a.hasAttribute('data-nobusy') || /^\/(download|static|api)\//.test(u.pathname) || /\.(apk|pdf|zip|csv|json|png|jpe?g|webp|ofx|qfx)$/i.test(u.pathname)) return;
    nav(a.getAttribute('data-busy') || '', 2500, 15000);
  });

  window.addEventListener('pageshow', reset);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) reset(); });

  var box = document.querySelector('[data-poll]');
  if (box) {
    var m = box.querySelector('.meter.working'), host = m ? m.parentNode : box;
    var h = host.querySelector('h3, .label, p');
    if (h) h.insertBefore(dots(), h.firstChild);
  }
})();
