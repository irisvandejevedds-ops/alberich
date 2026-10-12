/* Where Alberich's pouches and logs live. In the browser (and the demo) they are kept in localStorage; inside the Android app the same calls go to the phone's own
 * database through the AlberichNative bridge, which is also what the home-screen widget and the reminders read. */
(function (root) {
  'use strict';
  var KEY = 'alberich.v1';

  function LocalStore() {
    var data = { acts: [], logs: [], settings: {} };
    try { var raw = root.localStorage.getItem(KEY); if (raw) data = Object.assign(data, JSON.parse(raw)); } catch (e) { /* private mode or damaged: start empty */ }
    function save() { try { root.localStorage.setItem(KEY, JSON.stringify(data)); } catch (e) { /* storage full or blocked */ } }
    function upsert(list, row) { var i = list.findIndex(function (x) { return x.id === row.id; }); if (i >= 0) list[i] = row; else list.push(row); save(); }
    return {
      native: false,
      load: function () { return { acts: data.acts.slice(), logs: data.logs.slice(), settings: Object.assign({}, data.settings) }; },
      putAct: function (a) { upsert(data.acts, a); },
      putLog: function (l) { upsert(data.logs, l); },
      set: function (k, v) { data.settings[k] = v; save(); },
      snapshot: function () {}, schedule: function () {}, notifyNow: function () {}, requestNotifications: function () { return 'unsupported'; }, notificationsAllowed: function () { return false; },
      deleteAll: function () { data = { acts: [], logs: [], settings: {} }; save(); },
      saveFile: function (name, mime, text) {
        var a = root.document.createElement('a');
        a.href = root.URL.createObjectURL(new root.Blob([text], { type: mime })); a.download = name;
        root.document.body.appendChild(a); a.click(); a.remove();
        return true;
      },
      openComputer: function () { return false; },
      sync: null
    };
  }

  function NativeStore(n) {
    var ok = function (s) { try { return JSON.parse(s); } catch (e) { return null; } };
    return {
      native: true,
      load: function () { var d = ok(n.loadAll()) || {}; return { acts: d.acts || [], logs: d.logs || [], settings: d.settings || {} }; },
      putAct: function (a) { n.putActivity(JSON.stringify(a)); },
      putLog: function (l) { n.putLog(JSON.stringify(l)); },
      set: function (k, v) { n.setSetting(k, String(v)); },
      snapshot: function (s) { n.saveSnapshot(JSON.stringify(s)); },
      schedule: function (list) { n.saveSchedule(JSON.stringify(list)); },
      notifyNow: function (title, body, mood) { n.notifyNow(title, body, mood || 'neutral'); },
      requestNotifications: function () { return n.requestNotifications(); },
      notificationsAllowed: function () { return !!n.notificationsAllowed(); },
      deleteAll: function () { n.deleteAll(); },
      saveFile: function (name, mime, text) { return !!n.saveFile(name, mime, text); },
      openComputer: function () { return !!n.openComputer(); },
      sync: {
        status: function () { return ok(n.syncStatus()) || {}; },
        now: function () { return n.syncNow(); },
        unpair: function () { n.unpair(); },
        pair: function (link) { return n.pair(link); }
      }
    };
  }

  root.AlberichStore = { create: function () { return root.AlberichNative ? NativeStore(root.AlberichNative) : LocalStore(); } };
})(window);
