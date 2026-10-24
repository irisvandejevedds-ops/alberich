/* Alberich, the app. Runs the same in a browser (the demo) and inside the Android app, where storage, reminders and syncing go through AlberichNative.
 * All explanatory text lives in the "i" callouts; the pages themselves stay quiet. */
(function () {
  'use strict';
  var C = window.AlberichCore, A = window.AlberichAssets, store = window.AlberichStore.create();
  var cfg = window.ALBERICH_CONFIG || {};
  var app = document.getElementById('app');
  var S = store.load();
  var DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  var notice = '', purseTab = 'plan', reacting = null, cache = null;

  // ---------- small helpers ----------
  function el(tag, props) {
    var n = document.createElement(tag);
    if (props) Object.keys(props).forEach(function (k) {
      var v = props[k];
      if (v == null || v === false) return;
      if (k === 'class') n.className = v; else if (k === 'text') n.textContent = v; else if (k === 'html') n.innerHTML = v;
      else if (k === 'value') n.value = v; else if (k.indexOf('on') === 0) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v === true ? '' : v);
    });
    for (var i = 2; i < arguments.length; i++) add(n, arguments[i]);
    return n;
  }
  function add(n, c) { if (c == null || c === false) return; if (Array.isArray(c)) c.forEach(function (x) { add(n, x); }); else n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); }
  var uidN = 0;
  /** A heading with an "i" button that opens a callout. Returns a fragment: toggle, heading, callout. */
  function head(kind, title, info) {
    var f = document.createDocumentFragment(), id = 'i' + (++uidN);
    var h = kind === 'h1' ? el('h1', { class: 'hd' }, el('span', null, title)) : el('div', { class: 'cardhead' }, el('h3', null, title));
    if (info) {
      f.appendChild(el('input', { type: 'checkbox', id: id, class: 'infotoggle' }));
      (kind === 'h1' ? h : h).appendChild(el('label', { for: id, class: 'infobtn', 'aria-label': 'More about this', text: 'i' }));
      f.appendChild(h); f.appendChild(el('div', { class: 'callout', text: info }));
    } else f.appendChild(h);
    return f;
  }
  function card(title, info) { var c = el('div', { class: 'card' }); if (title) c.appendChild(head('h3', title, info)); return c; }
  function runs(rows, pal, scale, cls, label) {
    var parts = [], w = rows[0].length;
    rows.forEach(function (row, y) { var x = 0; while (x < row.length) { var ch = row[x]; if (ch === '.' || !pal[ch]) { x++; continue; } var e = x; while (e < row.length && row[e] === ch) e++; parts.push('<rect x="' + x + '" y="' + y + '" width="' + (e - x) + '" height="1" fill="' + pal[ch] + '"/>'); x = e; } });
    return '<svg xmlns="http://www.w3.org/2000/svg" class="' + cls + '" width="' + (w * scale) + '" height="' + (rows.length * scale) + '" viewBox="0 0 ' + w + ' ' + rows.length + '" shape-rendering="crispEdges" role="img" aria-label="' + label + '">' + parts.join('') + '</svg>';
  }
  function moodSvg(mood, scale, cls) { var m = A.moods[mood] || A.moods.neutral; return runs(m.rows, m.palette, scale, 'alberich ' + (cls || ''), 'Alberich, ' + mood); }
  function pxicon(name, size) { return runs(A.icons[name] || A.icons.star, A.iconPalette, size / 16, 'px', name); }
  function now() { return new Date(); }
  function getJSON(k, d) { try { var v = S.settings[k]; return v ? JSON.parse(v) : d; } catch (e) { return d; } }
  function putJSON(k, v) { var s = JSON.stringify(v); if (S.settings[k] === s) return; S.settings[k] = s; store.set(k, s); }
  function put(k, v) { S.settings[k] = String(v); store.set(k, String(v)); }
  function toast(msg, action) {
    var ul = document.getElementById('toasts'); if (!ul) return;
    var li = el('li', { class: 'toast' }, el('span', { text: msg }));
    if (action) li.appendChild(el('button', { type: 'button', onclick: function () { action.fn(); li.remove(); } }, action.label));
    ul.appendChild(li); setTimeout(function () { li.remove(); }, action ? 10000 : 4500);
  }

  // ---------- the model ----------
  var periods = getJSON('periods', {});
  function prefs() { return C.mergePrefs(getJSON('prefs', {})); }
  function ctx() { var st = getJSON('stmt_uses', {}); return { periods: periods, logs: S.logs, stmtUses: function (id, m) { return (st[id] || []).map(C.parseIso).filter(function (d) { return C.monthOf(d) === m; }); } }; }
  function touch() { put('last_interaction', C.iso(now())); }
  function lastInteraction() { return S.settings.last_interaction ? C.parseIso(S.settings.last_interaction) : null; }
  function idleDays(d) { var l = lastInteraction(); return l ? Math.max(0, (d - l) / C.DAY_MS) : 0; }

  /** Everything the pages show, worked out from the pouches and logs; the pet remembers its wellbeing between looks. */
  function compute() {
    var d = now(), month = C.monthOf(d);
    holdEqualized(month);
    var states = C.allStates(S.acts, month, d, ctx());
    var hist = getJSON('pet_history', []), prev = hist.length ? hist[hist.length - 1].mood : null;
    var r = C.petCompute(getJSON('pet', null), states, parseInt(S.settings.buffer_cents || '0', 10), d, idleDays(d), prev);
    putJSON('pet', r.pet); putJSON('periods', periods);
    var changed = prev !== r.mood;
    if (changed) { hist.push({ ts: C.iso(d), mood: r.mood }); putJSON('pet_history', hist.slice(-100)); }
    cache = { now: d, month: month, states: states, pet: r, changedFrom: changed ? prev : undefined };
    return cache;
  }
  /** Equalizing re-spreads one month's credits and is remembered per month: {month: {at, allot: {id: credits}, drained: {id: {name, short}}}}. The schedule is
   * rebuilt from it whenever something (a sync, a pouch edit) put the usual numbers back, so the month stays equalized. */
  function equalizedLog() { return getJSON('equalized', {}); }
  function holdEqualized(month) {
    var e = equalizedLog()[month]; if (!e) return;
    Object.keys(e.allot).forEach(function (id) {
      var a = actById(id), per = periods[C.periodKey(month, id)];
      if (a && (!per || per.allotted !== e.allot[id])) C.buildPeriod(periods, month, a, true, e.allot[id]);
    });
  }
  function equalizeMonth() {
    var c = cache, res = C.equalize(c.states);
    if (!res.moves.length) { notice = 'The pouches are already as even as the credits allow.'; render(); return; }
    var log = equalizedLog(), prev = log[c.month] || { drained: {} }, drained = prev.drained;
    res.moves.forEach(function (m) { if (m.drained && !drained[m.id]) drained[m.id] = { name: m.name, short: m.to - m.from }; });
    log[c.month] = { at: C.iso(now()), allot: Object.assign({}, prev.allot, res.allot), drained: drained };
    putJSON('equalized', log); touch(); afterChange();
    notice = 'Equalized: ' + res.moves.map(function (m) { return m.name + ' ' + m.from + ' → ' + m.to; }).join(', ') + '. Alberich will remember which pouches ran dry.'; render();
  }
  /** Pouches that ran dry in a month that was equalized: how many credits they had to be given. Counted as demand when judging the Plan. */
  function dryWhenEqualized(id, month) { var e = equalizedLog()[month], d = e && e.drained[id]; return d ? d.short : 0; }

  function afterChange() {
    var c = compute(), d = c.now;
    store.snapshot({ ts: d.getTime(), month: c.month, mood: C.moodKey(c.pet), tiles: c.states.map(function (s) {
      return { id: s.activity.id, name: s.activity.name, icon: s.activity.icon, allotted: s.allotted, used: s.used, estimate: s.estimate_cents, unlocks: s.unlocks.map(function (u) { return u.getTime(); }) };
    }) });
    store.schedule(C.buildSchedule(S.acts, periods, lastInteraction(), prefs(), d));
  }
  function actById(id) { return S.acts.find(function (a) { return a.id === id; }); }
  function saveAct(a) { a.updatedAt = C.iso(now()); store.putAct(a); }
  function saveLog(l) { l.updatedAt = C.iso(now()); store.putLog(l); }

  function logAct(id) {
    var a = actById(id); if (!a) return;
    var l = { id: C.uuid(), activityId: id, ts: C.iso(now()), source: 'live', note: null, undoneAt: null, updatedAt: C.iso(now()) };
    S.logs.push(l); store.putLog(l); touch();
    var before = cache && cache.pet.mood; afterChange();
    var st = cache.states.find(function (s) { return s.activity.id === id; }), idx = st.log_ids.indexOf(l.id), status = idx >= 0 ? st.statuses[idx] : 'on_pace';
    reacting = status === 'on_pace' ? 'cheer' : 'grumble'; render();
    var msg = status === 'on_pace' ? a.name + ' noted. ' + Math.max(0, st.allotted - st.used) + ' left.' : status === 'borrowed' ? a.name + ' noted, drawn early from the month.' : a.name + ' noted, but you are over the plan.';
    toast(msg, { label: 'Undo', fn: function () { undoLog(l.id, true); } });
    var p = prefs();
    if (status !== 'on_pace' && p.types.pace_warning && !C.inQuiet(p, now())) {
      store.notifyNow('Alberich', status === 'borrowed' ? "You draw on next week's coin already? Hmph. Moderation, wretch." : "You DARE overspend?! The Ring's curse stirs...", status === 'borrowed' ? 'neutral' : 'sick');
    }
  }
  function undoLog(id, quiet) {
    var l = S.logs.find(function (x) { return x.id === id; }); if (!l) return;
    l.undoneAt = C.iso(now()); saveLog(l); afterChange(); render(); if (!quiet) toast('Struck from the ledger.');
  }
  function restoreLog(id) { var l = S.logs.find(function (x) { return x.id === id; }); if (!l) return; l.undoneAt = null; saveLog(l); afterChange(); render(); }

  // ---------- pages ----------
  function heroBlock(c, large) {
    var k = C.moodKey(c.pet), t = C.MOOD_TEXT[k];
    var stage = el('div', { class: 'stage' + (reacting ? ' ' + reacting : ''), id: 'stage', 'data-mood': k, html: moodSvg(k, large ? 8 : 4, 'bob') });
    reacting = null;
    return el('section', { class: 'hero' + (large ? ' large' : '') }, stage, el('div', null,
      el('p', { class: 'label who' }, large ? 'Throne room' : el('a', { href: '#/throne', style: 'color:inherit' }, 'Alberich, lord of the Nibelungs')),
      el('h1', { text: t[0] }), large ? el('p', { text: t[1] }) : el('p', { class: 'muted small', text: t[1] }), large ? null : el('div', { class: 'speech', id: 'saying', text: C.sayingFor(c.pet, c.now) })));
  }

  function hallPage() {
    var c = cache, out = el('div'), tiles = c.states.map(function (s) { return C.tileView(s, c.now); });
    var shown = tiles.filter(function (t) { return t.status !== 'unset'; }); if (!shown.length) shown = tiles;
    out.appendChild(heroBlock(c, false));
    var sec = el('section', { class: 'card' });
    sec.appendChild(head('h3', 'Log spending', 'Tap a pouch each time you spend on it. Credits unlock through the month on the schedule you set in the Purse. A glowing green gem means one is ready; blue means it is waiting; red means you are over the plan. Tap Undo straight after if you tapped by mistake.'));
    var grid = el('div', { class: 'tiles' });
    shown.forEach(function (t) {
      grid.appendChild(el('button', { class: 'tile', type: 'button', 'data-id': t.id, 'data-status': t.status, onclick: function () { logAct(t.id); } },
        el('i', { class: 'gem' }), el('span', { class: 'ico', html: pxicon(t.icon, 32) }), el('span', { class: 'nm', text: t.name }), el('span', { class: 'cnt', text: t.remaining + '/' + t.allotted }), el('span', { class: 'lbl', text: t.label })));
    });
    sec.appendChild(grid);
    var ready = shown.filter(function (t) { return t.status === 'available'; }).reduce(function (n, t) { return n + t.available; }, 0);
    var nxt = c.states.filter(function (s) { return s.next_at && s.allotted > 0; }).sort(function (a, b) { return a.next_at - b.next_at; })[0];
    var summary = (ready ? ready + ' credit' + (ready !== 1 ? 's' : '') + ' ready now.' : 'Nothing unlocked right now.') + (nxt ? ' Next: ' + nxt.activity.name + ' unlocks ' + C.fmtWhen(nxt.next_at, c.now) + '.' : '');
    sec.appendChild(el('p', { class: 'small', style: 'margin:14px 0 0' }, el('strong', { text: summary })));
    out.appendChild(sec);
    if (tiles.every(function (t) { return t.allotted === 0; })) out.appendChild(el('div', { class: 'notice' }, el('strong', null, 'No allowances yet. '), el('a', { href: '#/purse' }, 'Open the Purse'), ' to set how often you may spend on each.'));
    if (store.native && store.sync && !store.sync.status().paired) out.appendChild(el('div', { class: 'notice' }, 'Ledgers, receipts and budgets live on your computer. ', el('a', { href: '#/settings' }, 'Pair this phone with it'), ' to bring them in.'));
    return out;
  }

  function overdrawHistory(act, months) {
    var out = [], d = cache.now;
    for (var k = 0; k < months; k++) {
      var m = C.shiftMonth(cache.month, -k);
      if (k && !periods[C.periodKey(m, act.id)]) continue;
      var s = C.activityState(act, m, d, ctx()), dry = dryWhenEqualized(act.id, m);
      out.push({ month: m, allotted: s.allotted, used: s.used, over: Math.max(0, s.used - s.allotted), dry: dry });
    }
    return out;
  }

  function usesCard(tab) {
    var c = card('Credits used', 'Logged one by one' + (tab === 'plan' ? ', across every pouch' : '') + '. Tap Undo to give a credit back if you tapped by mistake; Restore puts it back.');
    var since = new Date(cache.now.getTime() - 45 * C.DAY_MS), rows = S.logs.filter(function (l) { return C.parseIso(l.ts) >= since && (tab === 'plan' || l.activityId === tab); })
      .sort(function (a, b) { return a.ts < b.ts ? 1 : -1; }).slice(0, 40);
    if (!rows.length) c.appendChild(el('p', { class: 'small muted', text: 'Nothing logged in the last 45 days.' }));
    rows.forEach(function (l) {
      var a = actById(l.activityId); if (!a) return;
      c.appendChild(el('div', { class: 'use' + (l.undoneAt ? ' gone' : '') }, el('span', { class: 'uic', html: pxicon(a.icon, 24) }),
        el('span', { class: 'utx' }, el('strong', { text: a.name }), el('br'), el('span', { class: 'small muted', text: C.fmtWhen(C.parseIso(l.ts), cache.now) + (l.undoneAt ? ' · undone' : '') })),
        l.undoneAt ? el('button', { class: 'btn ghost', type: 'button', onclick: function () { restoreLog(l.id); } }, 'Restore') : el('button', { class: 'btn ghost', type: 'button', onclick: function () { undoLog(l.id); } }, 'Undo')));
    });
    return c;
  }

  function picker(cur) {
    var fs = el('fieldset', { class: 'picker' }, el('legend', { class: 'label' }, 'Picture'));
    A.picker.forEach(function (k, i) {
      fs.appendChild(el('label', { class: 'pick' }, el('input', { type: 'radio', name: 'icon', value: k, checked: cur === k || (!cur && i === 0) }), el('span', { html: pxicon(k, 32) })));
    });
    return fs;
  }

  function purseForm(act) {
    var cad = act ? C.cadenceOf(act) : C.cadenceOf({}), f = el('form', { class: 'card' + (act && act.archived ? ' aged' : ''), style: act && act.archived ? 'opacity:.65' : null });
    f.appendChild(head('h3', act ? act.name + (act.archived ? ' (archived)' : '') : 'Add a spending',
      act ? 'Changing credits re-spreads this month\'s unlock times. Logs already made are kept. The phrase finishes the sentence "Alberich says: you may ___ this week." The typical cost is your share of one use; it is what the pet counts spending in.' : 'A spending is something that drains your purse, as a count per month: takeout, drinks, a shopping trip. Set how many you can afford each month and how they spread through it.'));
    f.appendChild(picker(act ? act.icon : ''));
    var name = el('input', { type: 'text', name: 'name', required: true, maxlength: '60', value: act ? act.name : '', placeholder: 'Concert tickets' });
    var phrase = el('input', { type: 'text', name: 'phrase', maxlength: '80', value: act ? act.phrase : '', placeholder: 'go to a show' });
    var est = el('input', { type: 'number', step: '0.01', min: '0', name: 'estimate', value: act ? (act.estimateCents / 100).toFixed(2) : '0.00' });
    var weight = el('select', { name: 'weight' }); [1, 2, 3, 4, 5].forEach(function (w) { weight.appendChild(el('option', { value: String(w), selected: (act ? act.weight : 3) === w }, String(w))); });
    var cr = el('input', { type: 'number', min: '0', max: '60', name: 'credits', value: String(act ? act.creditsPerMonth : 0) });
    f.appendChild(el('div', { class: 'grid2' }, el('label', { class: 'f' }, 'Name', name), el('label', { class: 'f' }, 'Phrase for reminders', phrase), el('label', { class: 'f' }, 'Typical cost, your share ($)', est),
      el('label', { class: 'f' }, 'Weight (1 to 5)', weight), el('label', { class: 'f' }, 'Credits per month', cr)));
    var mode = el('select', { name: 'mode' }, el('option', { value: 'even', selected: cad.mode === 'even' }, 'Spread evenly'), el('option', { value: 'weekdays', selected: cad.mode === 'weekdays' }, 'Only on chosen days'));
    var time = el('input', { type: 'time', name: 'time', value: cad.time });
    f.appendChild(el('div', { class: 'grid2' }, el('label', { class: 'f' }, 'Rhythm', mode), el('label', { class: 'f' }, 'Unlocks at', time)));
    var dows = el('div', { class: 'dows' }); DAYS.forEach(function (d, i) { dows.appendChild(el('label', null, el('input', { type: 'checkbox', name: 'dow', value: String(i), checked: cad.dows.indexOf(i) >= 0 }), d)); });
    f.appendChild(el('div', { class: 'label' }, 'Days (for "only on chosen days")')); f.appendChild(dows);
    var notify = el('input', { type: 'checkbox', name: 'notify', checked: cad.notify });
    f.appendChild(el('label', { class: 'check' }, notify, 'Alberich may announce when a credit unlocks'));
    if (act && act.autoCredits) f.appendChild(el('p', { class: 'small muted', text: 'This pouch follows your spending on your computer: the credits shown are set from your statements, and a change made here is replaced the next time the computer works them out.' }));
    var row = el('div', { class: 'row' }, el('button', { class: 'btn', type: 'submit' }, act ? 'Save' : 'Add'));
    if (act) row.appendChild(el('button', { class: 'btn ghost', type: 'button', onclick: function () { act.archived = !act.archived; saveAct(act); afterChange(); render(); } }, act.archived ? 'Restore' : 'Archive'));
    f.appendChild(row);
    f.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var icon = (f.querySelector('input[name=icon]:checked') || {}).value || 'star', checked = Array.prototype.slice.call(f.querySelectorAll('input[name=dow]:checked')).map(function (x) { return +x.value; });
      var a = act || { id: C.uuid(), sort: S.acts.length, archived: false, autoCredits: false };
      a.name = name.value.trim() || 'Pouch'; a.icon = icon; a.phrase = phrase.value.trim(); a.estimateCents = C.toCents(est.value); a.weight = +weight.value;
      a.creditsPerMonth = Math.max(0, Math.min(60, parseInt(cr.value, 10) || 0)); a.cadence = { mode: mode.value, time: time.value || '09:00', dows: checked, notify: notify.checked };
      if (!act) S.acts.push(a);
      saveAct(a); C.buildPeriod(periods, C.monthOf(now()), a, true); touch(); afterChange();
      purseTab = a.id; notice = 'Saved.'; render();
    });
    return f;
  }

  function pursePage() {
    var out = el('div'), tab = purseTab;
    out.appendChild(head('h1', 'The Purse', 'The spendings that drain you, as counts per month. Pick one below to adjust it. Credits unlock through the month; tapping a pouch on the Hall uses one.'));
    var tabs = el('nav', { class: 'tabs', 'aria-label': 'Purse sections' });
    function tabLink(key, icon, label, cls) { return el('a', { class: 'tab' + (tab === key ? ' on' : '') + (cls || ''), href: '#/purse', onclick: function (ev) { ev.preventDefault(); purseTab = key; render(); } }, el('span', { html: pxicon(icon, 32) }), el('span', null, label)); }
    tabs.appendChild(tabLink('plan', 'coins', 'Plan'));
    S.acts.slice().sort(function (a, b) { return (a.archived - b.archived) || (a.sort - b.sort); }).forEach(function (a) { tabs.appendChild(tabLink(a.id, a.icon, a.name, a.archived ? ' dim' : '')); });
    tabs.appendChild(tabLink('new', 'plus', 'Add'));
    out.appendChild(tabs);
    if (notice) { out.appendChild(el('div', { class: 'notice', text: notice })); notice = ''; }
    if (tab === 'plan') {
      var c = card('The plan, ' + cache.month, 'Each pouch has a number of credits for the month and a cost for one use. Equalize moves this month's credit dollars from pouches with plenty left to ones that ran out; next month starts from the usual numbers, but Alberich remembers which pouches ran dry. Credits unlock at the pace you set, and the pet judges you by how closely your spending follows them. When this phone is paired with your computer, pouches that follow a budget category get their credits from your statements.');
      var rows = el('div', { class: 'prows' });
      cache.states.forEach(function (s) {
        var a = s.activity, hist = overdrawHistory(a, 4), runs = hist.filter(function (o) { return o.over; }), dries = hist.filter(function (o) { return o.dry && !o.over; });
        rows.appendChild(el('div', { class: 'prow' },
          el('div', { class: 'phead' }, el('span', { html: pxicon(a.icon, 22) }), el('a', { href: '#/purse', onclick: function (ev) { ev.preventDefault(); purseTab = a.id; render(); } }, el('strong', { text: a.name })),
            el('span', { class: 'pcr' }, el('strong', { text: String(a.creditsPerMonth) }), ' credit' + (a.creditsPerMonth !== 1 ? 's ' : ' '), el('span', { class: 'small muted', text: a.autoCredits ? 'from your computer' : 'by hand' }))),
          el('div', { class: 'small pline', text: (a.estimateCents ? C.money(a.estimateCents) : 'set a cost') + ' each · used ' + s.used + ' of ' + s.allotted + (s.from_statements ? ' (' + s.from_statements + ' from statements)' : '') }),
          runs.length ? el('div', { class: 'small pline' }, el('span', { class: 'chip red', text: 'ran over' }), ' ' + runs.map(function (o) { return o.over + ' over in ' + o.month; }).join('; ') + '.') : null,
          dries.length ? el('div', { class: 'small pline' }, el('span', { class: 'chip red', text: 'ran dry' }), ' ' + dries.map(function (o) { return o.month + ' (needed ' + o.dry + ' more when equalized)'; }).join('; ') + '.') : null));
      });
      if (!cache.states.length) rows.appendChild(el('p', { class: 'muted', text: 'No pouches yet. Add one with the plus tab.' }));
      c.appendChild(rows);
      if (cache.states.length > 1) c.appendChild(el('div', { class: 'row' }, el('button', { class: 'btn', type: 'button', onclick: equalizeMonth }, 'Equalize this month')));
      out.appendChild(c); out.appendChild(usesCard('plan'));
    } else if (tab === 'new') out.appendChild(purseForm(null));
    else { var act = actById(tab); if (act) { out.appendChild(purseForm(act)); out.appendChild(usesCard(act.id)); } else { purseTab = 'plan'; return pursePage(); } }
    return out;
  }

  function throne() {
    var c = cache, out = el('div'), pet = c.pet, k = C.moodKey(pet), spentN = c.states.reduce(function (n, s) { return n + s.used; }, 0), expN = c.states.reduce(function (n, s) { return n + Math.min(s.unlocked, s.allotted); }, 0);
    var odN = c.states.reduce(function (n, s) { return n + s.overdrawn; }, 0);
    var hero = heroBlock(c, true); out.appendChild(hero);
    hero.lastChild.appendChild(el('p', { class: 'muted small' }, 'Zone this month: ', el('strong', { text: C.ZONE_TEXT[pet.zone] }), '. ' + spentN + ' of ' + expN + ' unlocked credits used' + (odN ? ', ' + odN + ' beyond the plan' : '') + '.'));
    var grid = el('div', { class: 'grid2' });
    var mean = card('The golden mean', 'Alberich judges you by moderation, not abstinence. Brooding: you barely spend; nothing gained, nothing lost. Pleased: you spend within reason; this is the goal. Ailing: you overspend a little; the coin-sickness takes him at once. Cursed: you overspend past your buffer; recovery takes days of moderation.');
    mean.appendChild(el('p', { class: 'small', text: 'Moderation is the point.' })); grid.appendChild(mean);
    var who = card('Who is he?', 'Alberich means "elf-ruler", the root of Oberon. In the Nibelungenlied he guards the Nibelung hoard with the strength of twelve men until Siegfried takes the cloak of invisibility and pulls his beard. In Wagner\'s Ring he forswears love, steals the Rhinegold, forges the Ring, enslaves the Nibelungs, and curses the Ring when Wotan takes it. He is, in short, a hoarder with a temper. You are here to stop him becoming either extreme.');
    who.appendChild(el('p', { class: 'small', text: 'A hoarder with a temper.' })); grid.appendChild(who); out.appendChild(grid);
    var g = card('His moods'); var gal = el('div', { class: 'gallery' });
    ['neutral', 'annoyed', 'happy', 'sick', 'evil'].forEach(function (m) { gal.appendChild(el('figure', { html: moodSvg(m, 6, '') + '<figcaption>' + C.MOOD_TEXT[m][0] + '</figcaption>' })); });
    g.appendChild(gal); out.appendChild(g);
    var h = getJSON('pet_history', []).slice().reverse().slice(0, 8), t = card('Recent changes'), tb = el('tbody');
    h.forEach(function (r) { tb.appendChild(el('tr', null, el('td', { text: C.fmtWhen(C.parseIso(r.ts), c.now) }), el('td', { text: r.mood }))); });
    if (!h.length) tb.appendChild(el('tr', null, el('td', { colspan: '2', class: 'muted', text: 'Nothing yet.' })));
    t.appendChild(el('table', { class: 't' }, el('thead', null, el('tr', null, el('th', null, 'When'), el('th', null, 'Mood'))), tb)); out.appendChild(t);
    return out;
  }

  function ravensPage() {
    var out = el('div'), p = prefs();
    out.appendChild(head('h1', 'The Ravens', 'How and when Alberich sends word to this phone. Reminders are set up on the phone itself, so they arrive with no internet and no server. When you use the app, the plan for the next five weeks is worked out and handed to Android to deliver; using the app again re-plans it.'));
    var dev = card('This phone', 'Android asks permission before an app may show notifications. If reminders do not arrive with the app closed, open Android settings for Alberich and allow notifications, and set battery use to unrestricted.');
    var allowed = store.notificationsAllowed();
    dev.appendChild(el('p', { class: 'muted', text: store.native ? (allowed ? 'Notifications are allowed.' : 'Notifications are not allowed yet.') : 'Reminders work in the Android app.' }));
    if (store.native) dev.appendChild(el('div', { class: 'row' }, el('button', { class: 'btn', type: 'button', onclick: function () { var r = store.requestNotifications(); notice = r === 'granted' ? 'Ravens allowed.' : 'Android will ask you; if it does not, allow notifications in the phone settings.'; setTimeout(render, 1500); render(); } }, 'Allow ravens on this phone'),
      el('button', { class: 'btn ghost', type: 'button', onclick: function () { store.notifyNow('Alberich', p.tone === 'plain' ? 'This is a test reminder.' : 'A raven, as promised. Do not get used to it.', 'neutral'); toast('A raven is on its way.'); } }, 'Send a test raven')));
    out.appendChild(dev);
    if (notice) { out.appendChild(el('div', { class: 'notice', text: notice })); notice = ''; }
    var f = el('form', { class: 'card' }); f.appendChild(head('h3', 'What he may announce', 'Quiet hours hold reminders until they end. The daily limit drops any extra. Silencing him pauses everything for a while, which suits a holiday. After two days without opening the app he starts to nag, and he gets ruder the longer you stay away, up to the ceiling you pick here.'));
    var boxes = {}; Object.keys(C.TYPE_LABELS).forEach(function (k) { boxes[k] = el('input', { type: 'checkbox', checked: !!p.types[k] }); f.appendChild(el('label', { class: 'check' }, boxes[k], C.TYPE_LABELS[k])); });
    var qs = el('input', { type: 'time', value: p.quiet.start }), qe = el('input', { type: 'time', value: p.quiet.end }), cap = el('input', { type: 'number', min: '0', max: '20', value: String(p.daily_cap) });
    var tone = el('select', null, el('option', { value: 'pet', selected: p.tone === 'pet' }, 'In character'), el('option', { value: 'plain', selected: p.tone === 'plain' }, 'Plainly'));
    var ceil = el('select'); [[1, 'Grumbles'], [2, 'Sharp words'], [3, 'Scolding'], [4, 'Fury'], [5, 'Unrestrained']].forEach(function (x) { ceil.appendChild(el('option', { value: String(x[0]), selected: p.nag_ceiling === x[0] }, x[1])); });
    var snooze = el('select'); [[0, 'No'], [1, 'One day'], [3, 'Three days'], [7, 'A week'], [14, 'Two weeks (travelling)']].forEach(function (x) { snooze.appendChild(el('option', { value: String(x[0]) }, x[1])); });
    f.appendChild(el('div', { class: 'grid2', style: 'margin-top:12px' }, el('label', { class: 'f' }, 'Quiet from', qs), el('label', { class: 'f' }, 'Quiet until', qe), el('label', { class: 'f' }, 'Most ravens per day', cap),
      el('label', { class: 'f' }, 'How he speaks', tone), el('label', { class: 'f' }, 'How rude he may get when ignored', ceil),
      el('label', { class: 'f' }, 'Silence him for a while', snooze, p.snooze_until && C.parseIso(p.snooze_until) > now() ? el('span', { class: 'hint', text: 'Currently silenced until ' + C.fmtWhen(C.parseIso(p.snooze_until), now()) + '.' }) : null)));
    f.appendChild(el('button', { class: 'btn', type: 'submit' }, 'Save'));
    f.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var np = C.mergePrefs(getJSON('prefs', {})); Object.keys(boxes).forEach(function (k) { np.types[k] = boxes[k].checked; });
      np.quiet = { start: qs.value || '22:00', end: qe.value || '08:00' }; np.daily_cap = Math.max(0, Math.min(20, parseInt(cap.value, 10) || 0)); np.tone = tone.value; np.nag_ceiling = +ceil.value;
      if (+snooze.value > 0) np.snooze_until = C.iso(new Date(now().getTime() + +snooze.value * C.DAY_MS));
      putJSON('prefs', np); afterChange(); notice = 'Saved.'; render();
    });
    out.appendChild(f);
    var up = card('Coming up', 'The next reminders Alberich has arranged on this phone, in order. They are re-planned whenever you use the app.'), sch = C.buildSchedule(S.acts, periods, lastInteraction(), p, now()).slice(0, 8), tb = el('tbody');
    sch.forEach(function (n) { tb.appendChild(el('tr', null, el('td', { class: 'small', text: C.fmtWhen(new Date(n.at), now()) }), el('td', { text: n.body }))); });
    if (!sch.length) tb.appendChild(el('tr', null, el('td', { colspan: '2', class: 'muted', text: 'Nothing planned.' })));
    up.appendChild(el('div', { class: 'tscroll' }, el('table', { class: 't' }, tb))); out.appendChild(up);
    return out;
  }

  function csvCell(v) { v = v == null ? '' : String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }
  function exportCsv() {
    var lines = [['when', 'pouch', 'source', 'undone'].join(',')];
    S.logs.slice().sort(function (a, b) { return a.ts < b.ts ? -1 : 1; }).forEach(function (l) { var a = actById(l.activityId); lines.push([l.ts, a ? a.name : '', l.source, l.undoneAt || ''].map(csvCell).join(',')); });
    var ok = store.saveFile('alberich-log.csv', 'text/csv', lines.join('\n') + '\n');
    notice = ok ? (store.native ? 'Saved alberich-log.csv to your Downloads folder.' : '') : 'Could not save the file.'; render();
  }

  function syncWatch(start, done) {
    if (start) start();
    var tries = 0, t = setInterval(function () {
      var st = store.sync.status();
      if (st.running && ++tries < 60) return;
      clearInterval(t); S = store.load(); periods = getJSON('periods', {}); afterChange(); notice = done(st); render();
    }, 700);
  }
  function settingsPage() {
    var out = el('div'); out.appendChild(head('h1', 'Settings'));
    var dc = card('Computer', 'Ledgers, statements, receipts, budgets and the Warlock need a real computer: they read PDFs, scan photos and ask Claude for advice. Pair this phone with Alberich on your computer to keep your pouches and logs in step, bring in credits worked out from your statements, and open the full app there. On your computer, open Alberich and scan the pairing code with your phone\'s camera app. It works over your home Wi-Fi, and over Tailscale if you use it. The app works fine without a computer.');
    if (store.native && store.sync) {
      var s = store.sync.status();
      dc.appendChild(el('p', { text: s.paired ? 'Paired with ' + (s.name || 'your computer') + '.' : 'Not paired.' }));
      if (s.paired) {
        dc.appendChild(el('p', { class: 'small muted', text: (s.last_sync ? 'Last synced ' + s.last_sync + '. ' : 'Not synced yet. ') + (s.message || '') }));
        dc.appendChild(el('div', { class: 'row' }, el('button', { class: 'btn', type: 'button', onclick: function () { syncWatch(function () { store.sync.now(); notice = 'Syncing…'; render(); }, function (st) { return st.message ? 'Could not sync: ' + st.message : 'Synced.'; }); } }, s.running ? 'Syncing…' : 'Sync now'),
          s.web ? el('button', { class: 'btn ghost', type: 'button', onclick: function () { if (!store.openComputer()) { notice = 'Could not open it.'; render(); } } }, 'Open Alberich on the computer') : null,
          el('button', { class: 'btn ghost', type: 'button', onclick: function () { if (confirm('Stop syncing with this computer? Your pouches on the phone stay.')) { store.sync.unpair(); render(); } } }, 'Unpair')));
        if (!s.web) dc.appendChild(el('p', { class: 'small muted', text: 'To open the full Alberich from here, pair over Tailscale: the pairing code from a Tailscale address includes a link the phone can open.' }));
      } else {
        var link = el('input', { type: 'text', placeholder: 'alberich://pair?…', 'aria-label': 'Pairing link', autocomplete: 'off' });
        dc.appendChild(el('label', { class: 'f' }, 'Or paste the pairing link', link));
        dc.appendChild(el('button', { class: 'btn', type: 'button', onclick: function () { var why = store.sync.pair(link.value); if (why) { notice = why; render(); } else syncWatch(function () { notice = 'Paired. Syncing…'; render(); }, function (st) { return st.message ? 'Paired, but could not sync yet: ' + st.message : 'Paired and synced.'; }); } }, 'Pair'));
      }
    } else {
      dc.appendChild(el('p', { text: 'This is the browser version. Its pouches live in this browser only and cannot sync.' }));
      if (cfg.downloadUrl) dc.appendChild(el('a', { class: 'btn', href: cfg.downloadUrl }, 'Get the apps'));
    }
    out.appendChild(dc);
    var data = card('Your data', 'Your pouches and logs are stored on this ' + (store.native ? 'phone' : 'browser') + ' only (and on your computer, if you pair). Download the log any time as a spreadsheet file. Deleting removes every pouch and log from here and cannot be undone.');
    data.appendChild(el('div', { class: 'row' }, el('button', { class: 'btn ghost', type: 'button', onclick: exportCsv }, 'Download the log (CSV)'),
      el('button', { class: 'btn warn', type: 'button', onclick: function () { if (confirm('Delete every pouch and log on this ' + (store.native ? 'phone' : 'browser') + '? This cannot be undone.')) { store.deleteAll(); S = store.load(); periods = {}; cache = null; seed(); afterChange(); notice = 'Everything was deleted.'; render(); } } }, 'Delete all')));
    out.appendChild(data);
    if (notice) { out.appendChild(el('div', { class: 'notice', text: notice })); notice = ''; }
    return out;
  }

  // ---------- shell ----------
  var ROUTES = { '': ['Hall', 'castle', hallPage], purse: ['Purse', 'pouch', pursePage], throne: ['Throne', 'crown', throne], ravens: ['Ravens', 'raven', ravensPage], settings: ['Settings', 'chest', settingsPage] };
  function route() { var r = (location.hash || '#/').replace(/^#\/?/, ''); return ROUTES[r] ? r : ''; }
  function render() {
    compute();
    var r = route(), page = ROUTES[r][2]();
    var nav = function (cls) {
      var n = el('nav', { class: cls, 'aria-label': 'Main' });
      Object.keys(ROUTES).forEach(function (k) { n.appendChild(el('a', Object.assign({ href: '#/' + k }, k === r ? { 'aria-current': 'page' } : {}), el('span', { html: pxicon(ROUTES[k][1], 22) }), el('span', null, ROUTES[k][0]))); });
      return n;
    };
    app.textContent = '';
    if (cfg.demo) app.appendChild(el('div', { class: 'demobar' }, 'Demo: your pouches stay in this browser. ', el('a', { href: cfg.downloadUrl || 'index.html' }, 'Get the app')));
    app.appendChild(el('header', { class: 'top' }, el('a', { class: 'brand', href: '#/' }, 'Alberich'), nav('topnav')));
    app.appendChild(el('main', { class: 'wrap' }, page));
    app.appendChild(nav('tabbar'));
    app.appendChild(el('ul', { class: 'toasts', id: 'toasts', 'aria-live': 'polite' }));
    document.title = 'Alberich' + (r ? ': ' + ROUTES[r][0].toLowerCase() : ': the Hall');
  }

  // ---------- start ----------
  function seed() {
    if (S.acts.length || S.settings.starter_seeded) return;
    var t = C.iso(now());
    C.STARTERS.forEach(function (s, i) {
      var a = { id: C.uuid(), name: s[0], icon: s[1], phrase: s[2], estimateCents: s[3], weight: s[4], creditsPerMonth: cfg.demo ? [4, 3, 1, 2, 1, 4, 1][i] : 0, cadence: { mode: 'even', time: '09:00', dows: [], notify: true }, sort: i, archived: false, autoCredits: false, updatedAt: t };
      S.acts.push(a); store.putAct(a);
    });
    put('starter_seeded', '1');
    if (cfg.demo) {                                              // a little history so the Hall and the Throne have something to show
      var d = now(), month = C.monthOf(d);
      S.acts.slice(0, 4).forEach(function (a, i) { for (var k = 0; k <= i % 2; k++) { var ts = new Date(d.getTime() - (2 + i * 3 + k) * 36 * 3600000); if (C.monthOf(ts) === month) { var l = { id: C.uuid(), activityId: a.id, ts: C.iso(ts), source: 'demo', note: null, undoneAt: null, updatedAt: C.iso(ts) }; S.logs.push(l); store.putLog(l); } } });
    }
  }
  seed();
  if (!lastInteraction()) touch();
  touch(); afterChange();
  window.addEventListener('hashchange', function () { notice = ''; render(); window.scrollTo(0, 0); });
  document.addEventListener('visibilitychange', function () { if (!document.hidden) { if (store.native) S = store.load(); periods = getJSON('periods', periods); touch(); afterChange(); render(); } });
  setInterval(function () { var a = document.activeElement; if (!document.hidden && !(a && /INPUT|SELECT|TEXTAREA/.test(a.tagName))) render(); }, 60000);
  window.__alberich = { S: function () { return S; }, render: render, cache: function () { return cache; } };
  render();
})();
