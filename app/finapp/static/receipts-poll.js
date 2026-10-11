(function () {
  'use strict';
  var t = setInterval(function () {
    fetch('/api/receipts/busy', { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      if (d && d.busy === 0) { clearInterval(t); location.reload(); }
    }).catch(function () {});
  }, 3000);
})();
