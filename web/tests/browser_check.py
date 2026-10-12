"""Loads the real app in headless Firefox, drives it (editing pouches, tapping, undo, overspending, pages), and screenshots it with a pass/fail report on top.
    python browser_check.py OUT_DIR [WIDTH] [demo|native]
Serves ../ over local http only; nothing leaves the machine."""
import http.server
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time

WEB = pathlib.Path(__file__).resolve().parents[1]
TYPES = {".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2", ".png": "image/png", ".html": "text/html", ".webmanifest": "application/manifest+json"}

COMMON = r"""
  var out = [];
  function say(ok, msg) { out.push((ok ? 'PASS ' : 'FAIL ') + msg); var p = document.getElementById('report'); if (p) p.textContent = out.join('\n'); }
  function $(s) { return document.querySelector(s); } function $$(s) { return Array.prototype.slice.call(document.querySelectorAll(s)); }
  function click(el) { el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true })); }
  function S() { return window.__alberich.S(); }
  function tile(name) { return $$('.tile').filter(function (t) { return $('.nm', t).textContent === name; })[0]; }
  function go(h) { location.hash = h; }
"""

TEST = COMMON + r"""
(function () {
  var demo = !!(window.ALBERICH_CONFIG && window.ALBERICH_CONFIG.demo);
  function run() {
    try {
      say(!!$('.hero svg.alberich') && $('h1').textContent.length > 0, 'the Hall shows Alberich and a mood (' + $('h1').textContent + ')');
      say($$('.tile').length === 7, 'seven starter pouches are on the Hall (' + $$('.tile').length + ')');
      say(demo ? !!$('.demobar') : /No allowances yet/.test(document.body.textContent), demo ? 'the demo banner is shown' : 'a fresh app says no allowances are set yet');
      var co = $('.callout'), tg = $('.infotoggle');
      say(getComputedStyle(co).display === 'none', 'explanations are hidden until asked for');
      tg.checked = true; tg.dispatchEvent(new Event('change', { bubbles: true }));
      say(getComputedStyle(co).display === 'block', 'the i button opens its callout'); tg.checked = false;
      go('#/purse');
      setTimeout(function () {
        say($$('.tab').length >= 9, 'the Purse has a tab for each pouch plus Plan and Add (' + $$('.tab').length + ')');
        click($$('.tab').filter(function (t) { return /Ordered in/.test(t.textContent); })[0]);
        var f = $('form.card'); f.querySelector('input[name=credits]').value = '3'; f.querySelector('input[name=estimate]').value = '40';
        f.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
        var a = S().acts.filter(function (x) { return x.name === 'Ordered in'; })[0];
        say(a.creditsPerMonth === 3 && a.estimateCents === 4000, 'saving the pouch form stores credits and cost (' + a.creditsPerMonth + ', ' + a.estimateCents + ')');
        say(/Saved\./.test(document.body.textContent), 'and says it was saved');
        click($$('.tab').filter(function (t) { return /Add/.test(t.textContent); })[0]);
        var nf = $('form.card'); nf.querySelector('input[name=name]').value = 'Concert'; nf.querySelector('input[name=credits]').value = '1'; nf.querySelector('input[name=estimate]').value = '50';
        nf.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
        say(S().acts.some(function (x) { return x.name === 'Concert' && x.creditsPerMonth === 1; }), 'a new pouch can be added');
        go('#/');
        setTimeout(function () {
          var t = tile('Ordered in'); var allotted = +$('.cnt', t).textContent.split('/')[1];
          say(allotted === 3, 'the Hall tile shows the new allowance (' + $('.cnt', t).textContent + ')');
          var n0 = S().logs.length; click(t);
          say(S().logs.length === n0 + 1 && !!$('.toast'), 'tapping a pouch logs one use and offers Undo');
          click($('.toast button'));
          say(S().logs.filter(function (l) { return !l.undoneAt; }).length === (demo ? n0 : 0), 'Undo strikes it out again');
          var st0 = $('h1').textContent;
          for (var i = 0; i < 6; i++) click(tile('Ordered in'));
          var t2 = tile('Ordered in');
          say(t2.dataset.status === 'overdrawn', 'six taps on a three-credit pouch is overdrawn (' + t2.dataset.status + ' ' + $('.lbl', t2).textContent + ')');
          say(/Cursed|Ailing/.test($('h1').textContent), 'and Alberich turns on you (' + st0 + ' -> ' + $('h1').textContent + ')');
          go('#/throne');
          setTimeout(function () {
            say($$('.gallery figure').length === 5, 'the Throne shows his five moods');
            say(/Zone this month/.test(document.body.textContent) && $$('table.t tbody tr').length >= 1, 'it lists the zone and recent mood changes');
            go('#/ravens');
            setTimeout(function () {
              var rf = $$('form.card')[0]; var checks = rf.querySelectorAll('input[type=checkbox]:not(.infotoggle)');
              say(checks.length === 4, 'the Ravens page lists four kinds of announcement');
              rf.querySelector('input[type=number]').value = '1'; rf.querySelector('select').value = 'plain';
              rf.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
              var pf = JSON.parse(S().settings.prefs); say(pf.daily_cap === 1 && pf.tone === 'plain', 'reminder preferences save (' + pf.daily_cap + ', ' + pf.tone + ')');
              say(JSON.parse(localStorage.getItem('alberich.v1')).acts.length >= 8, 'pouches are persisted to storage');
              go('#/settings');
              setTimeout(function () {
                say(/browser version/.test(document.body.textContent) && $$('.btn.warn').length === 1, 'settings explains the browser version and offers delete-all');
                say($$('.topnav a').length === 5, 'navigation has five tabs');
                say(true, 'done');
              }, 100);
            }, 100);
          }, 100);
        }, 100);
      }, 100);
    } catch (e) { say(false, 'exception: ' + e + ' ' + ((e.stack || '').split('\n')[0])); }
  }
  setTimeout(run, 300);
})();
"""

FAKE = r"""
(function () {
  var t = new Date(Date.now() - 86400000 * 2).toISOString().replace(/\.\d{3}Z$/, 'Z');
  var db = { acts: [{ id: 'aaaaaaaa-0000-4000-8000-000000000001', name: 'Ordered in', icon: 'takeout', phrase: 'order in', estimateCents: 4000, weight: 3, creditsPerMonth: 4, cadence: { mode: 'even', time: '09:00', dows: [], notify: true }, sort: 0, archived: false, autoCredits: false, updatedAt: t }],
             logs: [], settings: {} };
  var paired = false, running = false, log = [];
  window.__bridge = { db: db, log: log, snapshots: [], schedules: [] };
  window.AlberichNative = {
    loadAll: function () { return JSON.stringify(db); },
    putActivity: function (j) { var a = JSON.parse(j), i = db.acts.findIndex(function (x) { return x.id === a.id; }); if (i >= 0) db.acts[i] = a; else db.acts.push(a); },
    putLog: function (j) { var l = JSON.parse(j), i = db.logs.findIndex(function (x) { return x.id === l.id; }); if (i >= 0) db.logs[i] = l; else db.logs.push(l); },
    setSetting: function (k, v) { db.settings[k] = v; },
    saveSnapshot: function (j) { window.__bridge.snapshots.push(JSON.parse(j)); },
    saveSchedule: function (j) { window.__bridge.schedules.push(JSON.parse(j)); },
    notifyNow: function (a, b, m) { log.push('notify ' + b.slice(0, 18)); },
    requestNotifications: function () { log.push('askNotify'); return 'asked'; }, notificationsAllowed: function () { return false; },
    deleteAll: function () { db.acts = []; db.logs = []; db.settings = {}; log.push('deleteAll'); },
    saveFile: function (n, m, x) { log.push('file ' + n); return true; }, openComputer: function () { log.push('openComputer'); return true; },
    syncStatus: function () { return JSON.stringify({ paired: paired, name: paired ? 'Test PC' : '', message: '', running: running, last_sync: paired ? 'today' : '', web: paired }); },
    syncNow: function () { running = true; log.push('syncNow'); setTimeout(function () { running = false; }, 900); },
    unpair: function () { paired = false; log.push('unpair'); },
    pair: function (l) { if (l.indexOf('alberich://pair?') !== 0) return 'That is not an Alberich pairing link.'; paired = true; running = true; setTimeout(function () { running = false; }, 900); return ''; }
  };
})();
"""

TEST_NATIVE = COMMON + r"""
(function () {
  var B = window.__bridge;
  function run() {
    try {
      var sn = B.snapshots[B.snapshots.length - 1];
      say(!!sn && sn.tiles.length === 1 && sn.tiles[0].allotted === 4 && sn.tiles[0].unlocks.length === 4 && typeof sn.mood === 'string' && sn.ts > 0, 'the widget snapshot has the pouch, its four unlock times and the mood');
      var sc = B.schedules[B.schedules.length - 1];
      say(Array.isArray(sc) && sc.length > 0 && sc.every(function (n) { return typeof n.at === 'number' && n.title && n.body; }), 'the reminder schedule is handed to the phone (' + (sc && sc.length) + ' reminders)');
      say(sc.some(function (n) { return n.kind === 'neglect_nag'; }) && sc.some(function (n) { return n.kind === 'credit_unlocked'; }), 'it has both unlock announcements and neglect nags');
      click(tile('Ordered in'));
      say(B.db.logs.length === 1 && B.db.logs[0].id.length === 36 && B.db.logs[0].activityId === B.db.acts[0].id, 'a tap goes to the phone database with its own id');
      say(B.snapshots[B.snapshots.length - 1].tiles[0].used === 1, 'and the widget snapshot now shows it used');
      go('#/ravens');
      setTimeout(function () {
        click($$('button').filter(function (b) { return /Allow ravens/.test(b.textContent); })[0]);
        click($$('button').filter(function (b) { return /test raven/.test(b.textContent); })[0]);
        say(B.log.indexOf('askNotify') >= 0 && B.log.some(function (l) { return /^notify /.test(l); }), 'the Ravens page can ask for permission and send a test reminder');
        go('#/settings');
        setTimeout(function () {
          say(/Not paired/.test(document.body.textContent), 'settings says the phone is not paired');
          $('input[placeholder^="alberich://"]').value = 'nonsense';
          click($$('button').filter(function (b) { return b.textContent === 'Pair'; })[0]);
          say(/not an Alberich pairing link/.test(document.body.textContent), 'a bad pairing link is refused with a reason');
          $('input[placeholder^="alberich://"]').value = 'alberich://pair?v=1&key=tps_x&e=https%3A%2F%2F1.2.3.4%3A8120';
          click($$('button').filter(function (b) { return b.textContent === 'Pair'; })[0]);
          setTimeout(function () {
            say(/Paired and synced/.test(document.body.textContent) && /Paired with Test PC/.test(document.body.textContent), 'a good link pairs and the page reports the sync finishing');
            say($$('button').some(function (b) { return /Open Alberich on the computer/.test(b.textContent); }), 'once paired it offers to open the full Alberich on the computer');
            click($$('button').filter(function (b) { return /Open Alberich on the computer/.test(b.textContent); })[0]);
            say(B.log.indexOf('openComputer') >= 0, 'and does');
            click($$('button').filter(function (b) { return /Download the log/.test(b.textContent); })[0]);
            say(B.log.indexOf('file alberich-log.csv') >= 0, 'the log is saved through the phone to Downloads');
            window.confirm = function () { return true; };
            click($$('button').filter(function (b) { return b.textContent === 'Delete all'; })[0]);
            say(B.log.indexOf('deleteAll') >= 0 && /deleted/.test(document.body.textContent), 'Delete all clears the phone database');
            say(true, 'done');
          }, 1700);
        }, 100);
      }, 100);
    } catch (e) { say(false, 'exception: ' + e + ' ' + ((e.stack || '').split('\n')[0])); }
  }
  setTimeout(run, 300);
})();
"""


def main():
    out = pathlib.Path(sys.argv[1]).resolve()
    width = int(sys.argv[2]) if len(sys.argv) > 2 else 1100
    demo, native = "demo" in sys.argv[3:], "native" in sys.argv[3:]
    shot_hash = next((a[5:] for a in sys.argv[3:] if a.startswith("shot=")), None)
    if shot_hash:
        demo = True
    out.mkdir(parents=True, exist_ok=True)
    tmp = pathlib.Path(tempfile.mkdtemp())

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/wait":
                time.sleep(2 if shot_hash else 9)
                body, ctype = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x01D\x00;", "image/gif"
            elif path == "/config.js":
                body, ctype = ("window.ALBERICH_CONFIG={demo:%s,downloadUrl:'/index.html'};" % ("true" if demo else "false")).encode(), "text/javascript"
            elif path == "/__bridge.js":
                body, ctype = FAKE.encode(), "text/javascript"
            elif path == "/__test.js":
                body, ctype = (TEST_NATIVE if native else TEST).encode(), "text/javascript"
            elif path in ("/", "/index.html"):
                html = (WEB / "index.html").read_text()
                if native:
                    html = html.replace('<script src="config.js"></script>', '<script src="__bridge.js"></script><script src="config.js"></script>')
                if shot_hash:
                    body, ctype = html.replace('<div id="app"></div>', '<div id="app"></div><img src="/wait" width="1" height="1" alt="" style="position:absolute">').encode(), "text/html"
                    self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
                html = html.replace('<div id="app"></div>', '<div id="app"></div><img src="/wait" width="1" height="1" alt="" style="position:absolute"><pre id="report" style="position:fixed;top:0;left:0;right:0;z-index:99;margin:0;padding:8px;background:#000d;color:#fff;font:13px/1.3 monospace;white-space:pre-wrap"></pre>')
                html = html.replace("</body>", '<script src="__test.js"></script></body>')
                body, ctype = html.encode(), "text/html"
            else:
                f = (WEB / path.lstrip("/")).resolve()
                ok = f.is_file() and WEB in f.parents and "tests" not in f.parts
                body, ctype = (f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream")) if ok else (b"", "text/plain")
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    shot = out / f"alberich_{(shot_hash or "").replace("#/", "").replace("/", "") + "_" if shot_hash else ""}{'demo_' if demo else ''}{'native_' if native else ''}{width}.png"
    prof = tmp / "p"
    prof.mkdir()
    r = subprocess.run(["firefox", "--no-remote", "--profile", str(prof), "--headless", f"--window-size={width},1500", "--screenshot", str(shot), f"http://127.0.0.1:{srv.server_address[1]}/{shot_hash or ''}"],
                       capture_output=True, text=True, timeout=150, env={"MOZ_HEADLESS": "1", "PATH": "/usr/bin:/bin", "HOME": str(tmp), "TZ": "America/Toronto"})
    srv.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    print(shot if shot.exists() else r.stderr[-600:])


if __name__ == "__main__":
    main()
