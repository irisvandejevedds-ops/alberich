/* Alberich core: pouch credits, the golden-mean pet, and the reminder rules. One copy of the logic, used by the phone app, the browser demo and the tests.
 * A port of finapp/credits.py, pet.py and notify.py (tests/parity.test.js checks the two agree). Times are the device's own timezone. No DOM, no storage. */
(function (root) {
  'use strict';

  // ---------------- time ----------------
  var pad2 = function (n) { return (n < 10 ? '0' : '') + n; };
  function iso(d) { return d.toISOString().replace(/\.\d{3}Z$/, 'Z'); }
  function parseIso(s) { return new Date(s); }
  /** A wall-clock moment as a number: fields laid out as if the clock were UTC, so adding seconds is the same as adding them to the clock face (what Python does for one timezone). */
  function wall(y, mo, d, h, mi, s) { return Date.UTC(y, mo, d, h || 0, mi || 0, s || 0); }
  function wallToDate(ms) { var u = new Date(ms); return new Date(u.getUTCFullYear(), u.getUTCMonth(), u.getUTCDate(), u.getUTCHours(), u.getUTCMinutes(), u.getUTCSeconds()); }
  function monthOf(d) { return d.getFullYear() + '-' + pad2(d.getMonth() + 1); }
  function monthParts(month) { return [parseInt(month.slice(0, 4), 10), parseInt(month.slice(5, 7), 10)]; }
  function shiftMonth(month, k) { var p = monthParts(month), t = p[0] * 12 + (p[1] - 1) + k; return Math.floor(t / 12) + '-' + pad2((t % 12) + 1); }
  function monthBounds(month) { var p = monthParts(month); return [new Date(p[0], p[1] - 1, 1), new Date(p[0], p[1], 1)]; }
  function parseHHMM(s) { var m = /^(\d{1,2}):(\d{2})$/.exec(String(s || '')); return m && +m[1] < 24 && +m[2] < 60 ? [+m[1], +m[2]] : [9, 0]; }
  var DAY_MS = 86400000;

  // ---------------- credits ----------------
  var DEFAULT_CADENCE = { mode: 'even', time: '09:00', dows: [], notify: true };
  var BORROW_WEIGHT = 0.25, MATCH_DAYS = 1.5;

  function cadenceOf(act) {
    var c = act && act.cadence;
    if (typeof c === 'string') { try { c = JSON.parse(c || '{}'); } catch (e) { c = {}; } }
    return Object.assign({}, DEFAULT_CADENCE, c || {});
  }

  /** The ideal unlock for credit k is the month's start + k * length / n, snapped to the preferred time (and weekdays). -> array of Dates. */
  function computeUnlockTimes(month, n, cadence) {
    if (n <= 0) return [];
    var p = monthParts(month), startW = wall(p[0], p[1] - 1, 1), endW = wall(p[0], p[1], 1), total = (endW - startW) / 1000;
    var pref = parseHHMM(cadence.time), dows = cadence.mode === 'weekdays' && (cadence.dows || []).length ? cadence.dows : null;
    function dayOf(ms) { var u = new Date(ms); return [u.getUTCFullYear(), u.getUTCMonth(), u.getUTCDate()]; }
    function snap(dayMs) {
      var d = dayOf(dayMs), c = wall(d[0], d[1], d[2], pref[0], pref[1]);
      if (dows) for (var i = 0; i < 8; i++) {
        if (dows.indexOf((new Date(c).getUTCDay() + 6) % 7) >= 0) break;
        d = dayOf(wall(d[0], d[1], d[2] + 1)); c = wall(d[0], d[1], d[2], pref[0], pref[1]);
      }
      return c;
    }
    var out = [], prev = null;
    for (var k = 0; k < n; k++) {
      var ideal = startW + Math.round(total * k / n * 1000) , c = snap(ideal);
      if (prev !== null && c <= prev) c = snap(prev + DAY_MS);
      if (c >= endW) { c = endW - (n - k) * 60000; if (prev !== null && c <= prev) c = prev + 60000; }
      out.push(c); prev = c;
    }
    return out.map(wallToDate);
  }

  /** Each use takes the earliest credit: on_pace if one had unlocked, borrowed if allotment remains, overdrawn otherwise. */
  function classifyLogs(times, unlocks, allotted) {
    return times.slice().sort(function (a, b) { return a - b; }).map(function (ts, i) {
      var n = i + 1, unlocked = unlocks.filter(function (u) { return u <= ts; }).length;
      return n <= unlocked ? 'on_pace' : n <= allotted ? 'borrowed' : 'overdrawn';
    });
  }

  /** The frozen schedule for one pouch and month. periods is {key: {allotted, estimateCents, unlocks: [iso]}}; an existing one is kept unless replace. */
  function periodKey(month, id) { return month + '|' + id; }
  function buildPeriod(periods, month, act, replace, count) {
    var k = periodKey(month, act.id), n = count == null ? act.creditsPerMonth : count;
    if (!replace && periods[k]) return periods[k];
    var unlocks = computeUnlockTimes(month, n, cadenceOf(act));
    periods[k] = { allotted: n, estimateCents: act.estimateCents, unlocks: unlocks.map(iso) };
    return periods[k];
  }

  /** Re-spread this month's credit dollars so every pouch is equally full. The month's total (credits x cost, summed) stays put and what was already used stays used;
   * what is left is shared out so each pouch gets the same fraction of its own original allotment back (the full pouches give, the empty ones receive).
   * states: this month's pouch standings. -> {allot: {id: new credits}, moves: [{id, name, from, to}]}; empty when there is nothing to move. */
  function equalize(states) {
    var live = states.filter(function (s) { return s.allotted > 0 && s.estimate_cents > 0; });
    var pool = 0, used = 0;
    live.forEach(function (s) { pool += s.allotted * s.estimate_cents; used += s.used * s.estimate_cents; });
    var out = { allot: {}, moves: [] };
    if (live.length < 2 || pool <= used) return out;
    var f = (pool - used) / pool, left = pool, rows = live.map(function (s) {
      var ideal = s.used + f * s.allotted, n = Math.max(s.used, Math.min(60, Math.floor(ideal)));
      left -= n * s.estimate_cents;
      return { s: s, n: n, frac: ideal - n };
    });
    rows.sort(function (a, b) { return (b.frac - a.frac) || (a.s.activity.sort - b.s.activity.sort); });
    for (var i = 0, moved = true; moved && left > 0; i++) {   // hand the dollars rounding left over to the pouches closest to the next credit, while one still fits
      moved = false;
      rows.forEach(function (r) { if (r.n < 60 && r.s.estimate_cents <= left && r.frac > 0.0001) { r.n++; r.frac--; left -= r.s.estimate_cents; moved = true; } });
      if (i > 60) break;
    }
    rows.forEach(function (r) { out.allot[r.s.activity.id] = r.n; if (r.n !== r.s.allotted) out.moves.push({ id: r.s.activity.id, name: r.s.activity.name, from: r.s.allotted, to: r.n, drained: r.s.remaining <= 0 }); });
    if (!out.moves.length) out.allot = {};
    return out;
  }

  /** ctx: {periods, logs (all logs), stmtUses(actId, month) -> [Date]}. -> the pouch's standing this month. */
  function activityState(act, month, now, ctx) {
    var per = buildPeriod(ctx.periods, month, act), unlocks = per.unlocks.map(parseIso), b = monthBounds(month);
    var taps = ctx.logs.filter(function (l) { return l.activityId === act.id && !l.undoneAt && parseIso(l.ts) >= b[0] && parseIso(l.ts) < b[1]; })
      .sort(function (x, y) { return x.ts < y.ts ? -1 : x.ts > y.ts ? 1 : x.id < y.id ? -1 : x.id > y.id ? 1 : 0; });
    var stmt = (ctx.stmtUses ? ctx.stmtUses(act.id, month) : []).slice(), free = stmt.slice(), kept = [];
    taps.forEach(function (l) {
      var t = parseIso(l.ts), j = free.findIndex(function (s) { return Math.abs(s - t) <= MATCH_DAYS * DAY_MS; });
      if (j < 0) kept.push(l); else free.splice(j, 1);
    });
    var uses = stmt.map(function (s) { return [s, null]; }).concat(kept.map(function (l) { return [parseIso(l.ts), l.id]; }));
    uses.sort(function (x, y) { return (x[0] - y[0]) || ((x[1] ? 1 : 0) - (y[1] ? 1 : 0)); });
    var statuses = classifyLogs(uses.map(function (u) { return u[0]; }), unlocks, per.allotted);
    var used = uses.length, unlockedNow = unlocks.filter(function (u) { return u <= now; }).length;
    var future = unlocks.filter(function (u) { return u > now; }), cnt = { on_pace: 0, borrowed: 0, overdrawn: 0 };
    statuses.forEach(function (s) { cnt[s]++; });
    return { activity: act, month: month, allotted: per.allotted, estimate_cents: per.estimateCents, used: used, remaining: Math.max(0, per.allotted - used), unlocked: unlockedNow,
      available: Math.max(0, Math.min(unlockedNow, per.allotted) - used), next_at: future.length ? future[0] : null, unlocks: unlocks,
      on_pace: cnt.on_pace, borrowed: cnt.borrowed, overdrawn: cnt.overdrawn, log_ids: uses.map(function (u) { return u[1]; }), statuses: statuses,
      overdrawn_now: used > per.allotted, from_statements: stmt.length };
  }

  function activeActs(acts) { return acts.filter(function (a) { return !a.archived; }).sort(function (a, b) { return (a.sort - b.sort) || (a.id < b.id ? -1 : 1); }); }
  function allStates(acts, month, now, ctx) { return activeActs(acts).map(function (a) { return activityState(a, month, now, ctx); }); }

  // ---------------- the pet: the golden mean ----------------
  var CFG = { idle_ratio: 0.25, up_per_day: 0.34, down_per_day: 0.20, target: { idle: 0.0, moderate: 1.0, over: -0.6, excess: -1.0 }, thr: { happy: 0.5, neutral: -0.3, sick: -0.8 }, hyst: 0.05, annoyed_days: 2, grace_hours: 6 };
  var ORDER = ['evil', 'sick', 'neutral', 'happy'];

  function spendInputs(states) {
    var S = 0, E = 0, OD = 0, odN = 0;
    states.forEach(function (s) { S += s.used * s.estimate_cents; E += Math.min(s.unlocked, s.allotted) * s.estimate_cents; OD += s.overdrawn * s.estimate_cents; odN += s.overdrawn; });
    return { S: S, E: E, OD: OD, odN: odN };
  }
  function zone(i, buffer) {
    if (i.OD > 0) return (buffer > 0 ? i.OD >= buffer : i.odN >= 2) ? 'excess' : 'over';
    if (i.E <= 0 || i.S < CFG.idle_ratio * i.E) return 'idle';
    return 'moderate';
  }
  function step(w, target, dtDays) {
    if (target < 0 && target < w) return target;
    if (target > w) return Math.min(target, w + CFG.up_per_day * dtDays);
    if (target < w) return Math.max(target, w - CFG.down_per_day * dtDays);
    return w;
  }
  function band(w) { var t = CFG.thr; return w >= t.happy ? 'happy' : w >= t.neutral ? 'neutral' : w >= t.sick ? 'sick' : 'evil'; }
  function moodFor(w, prev) {
    var raw = band(w);
    if (ORDER.indexOf(prev) < 0) return raw;
    var i = ORDER.indexOf(prev), j = ORDER.indexOf(raw), hy = CFG.hyst, t = CFG.thr;
    if (i === j) return prev;
    if (j > i) {
      var best = prev;
      ORDER.slice(i + 1).forEach(function (m) {
        var lower = m === 'neutral' ? t.neutral : t.happy;
        if (m === 'sick') lower = t.sick;
        if (w >= lower + hy) best = m;
      });
      return best;
    }
    var cand = band(w + hy);
    return ORDER.indexOf(cand) < i ? cand : prev;
  }

  /** pet is {w, ts, bad, pre} (what is remembered between looks) or null at the start. prevMood is the last recorded mood. -> {w, mood, zone, ..., pet: new remembered state}. */
  function petCompute(pet, states, bufferCents, now, idleDays, prevMood) {
    var w = pet ? pet.w : 0.0, ts = pet ? parseIso(pet.ts) : null, dt = ts ? Math.max(0, (now - ts) / DAY_MS) : 0.0;
    var i = spendInputs(states), z = zone(i, bufferCents), bad = pet && pet.bad ? parseIso(pet.bad) : null, pre = pet && pet.bad ? pet.pre : null;
    if (z === 'over' || z === 'excess') { if (bad === null) { bad = now; pre = w; } }
    else if (bad !== null) {
      if ((now - bad) / 1000 < CFG.grace_hours * 3600) w = Math.max(w, pre);
      bad = pre = null;
    }
    w = step(w, CFG.target[z], dt);
    var mood = moodFor(w, ORDER.indexOf(prevMood) >= 0 ? prevMood : null), np = { w: w, ts: iso(now) };
    if (bad !== null) { np.bad = iso(bad); np.pre = pre; }
    return { w: w, mood: mood, zone: z, spent: i.S, expected: i.E, overdrawn_cents: i.OD, buffer: bufferCents, annoyed: idleDays >= CFG.annoyed_days && mood === 'neutral', idle_days: idleDays, pet: np };
  }
  /** A month-close verdict from the computer pulls wellbeing down to at most wFloor. */
  function petShock(pet, wFloor, now) { return { w: Math.min(pet ? pet.w : 0, wFloor), ts: iso(now) }; }

  // ---------------- reminders ----------------
  var NAG_AT = [2, 4, 7, 10, 14], NAG_REPEAT = 3;
  var NAG_COPY = {
    1: ['Hmph. Two days, and not one glance at my hoard. I notice these things.', 'Two days. The coffers sit unattended. I am not pleased.'],
    2: ['Four days of silence. I have begun a list of your failings. It is already long.', 'Four days! Even the Nibelungs report for duty, and they are in chains.'],
    3: ['A WEEK. Seven days! Does the Ring mean nothing to you? Open the app, wretch.', 'Seven days of neglect. My patience is a thin and glittering thing, and it is cracking.'],
    4: ['Ten days. I have forged a very small tombstone for your Purse. Open the app, you dawdling dolt.', 'Ten days! I am sharpening a very small axe. Do not test me.'],
    5: ['You absent, ledger-dodging sluggard! The Ring grows heavy with my contempt. OPEN. THE. APP.', 'FOURTEEN DAYS. I curse your slippers, your spoons, and your every unopened tab. COME HERE.']
  };
  var NAG_PLAIN = 'It has been {d} days since you opened Alberich. Check your credits when you can.';
  var UNLOCK_COPY = {
    today: ['The hoard permits it: you may {p} today. Neither squander it, nor hoard it.', 'Hmph. Fine. You may {p} today. Within reason.', 'The golden mean allows it: {p} today. Do not make me regret this.'],
    week: ['The hoard permits it: you may {p} this week. Not too much, not too little.', 'Hmph. You may {p} this week. Moderation, wretch.', 'The golden mean allows it: {p} this week. Do not make me regret this.']
  };
  var MOOD_NOTE = { happy: 'Good. Moderation suits you. I am... almost pleased.', neutral: 'Nothing to report. The hoard sits untouched. Dull.', sick: 'I feel faint. You have overspent, and the coin-sickness takes me.', evil: 'THE CURSE AWAKENS. You have spent beyond all reason!' };
  var DEFAULT_PREFS = {
    types: { credit_unlocked: true, pace_warning: true, neglect_nag: true, pet_state_change: true },
    quiet: { start: '22:00', end: '08:00' }, daily_cap: 2, snooze_until: '', tone: 'pet', nag_ceiling: 5
  };
  var TYPE_LABELS = { credit_unlocked: 'A credit unlocks', pace_warning: 'You borrow or overdraw a credit', neglect_nag: 'Alberich grows cross when you ignore him', pet_state_change: "Alberich's mood changes" };

  function mergePrefs(stored) {
    var p = JSON.parse(JSON.stringify(DEFAULT_PREFS));
    Object.keys(stored || {}).forEach(function (k) { var v = stored[k]; if (v && typeof v === 'object' && !Array.isArray(v) && p[k] && typeof p[k] === 'object') Object.assign(p[k], v); else p[k] = v; });
    return p;
  }
  function inQuiet(prefs, d) {
    var s = parseHHMM(prefs.quiet.start), e = parseHHMM(prefs.quiet.end), t = d.getHours() * 60 + d.getMinutes(), a = s[0] * 60 + s[1], b = e[0] * 60 + e[1];
    return a <= b ? (a <= t && t < b) : (t >= a || t < b);
  }
  /** The first moment at or after d that is outside quiet hours. */
  function afterQuiet(prefs, d) {
    if (!inQuiet(prefs, d)) return d;
    var e = parseHHMM(prefs.quiet.end), out = new Date(d.getFullYear(), d.getMonth(), d.getDate(), e[0], e[1]);
    if (out <= d) out = new Date(out.getFullYear(), out.getMonth(), out.getDate() + 1, e[0], e[1]);
    return out;
  }
  function nagStage(days) {
    if (days < NAG_AT[0]) return null;
    var level = 0; NAG_AT.forEach(function (d, i) { if (days >= d) level = i + 1; });
    var extra = days >= NAG_AT[NAG_AT.length - 1] ? Math.floor((days - NAG_AT[NAG_AT.length - 1]) / NAG_REPEAT) : 0;
    return { level: level, stage: level + extra };
  }
  function pick(list, seed) { var h = 0; String(seed).split('').forEach(function (c) { h = (h * 31 + c.charCodeAt(0)) >>> 0; }); return list[h % list.length]; }
  function nagText(level, days, prefs, seed) {
    level = Math.min(level, Math.max(1, parseInt(prefs.nag_ceiling, 10) || 5));
    if (prefs.tone === 'plain') return { title: 'Alberich', body: NAG_PLAIN.replace('{d}', String(Math.floor(days))), level: level };
    return { title: 'Alberich', body: pick(NAG_COPY[level], seed), level: level };
  }
  function phraseOf(act) { return (act.phrase || ('do ' + act.name.toLowerCase())).trim(); }
  function unlockText(act, spacingDays, prefs, seed) {
    if (prefs.tone === 'plain') return { title: act.name, body: act.name + ': one credit available.' };
    return { title: 'Alberich', body: pick(UNLOCK_COPY[spacingDays <= 3 ? 'today' : 'week'], seed).replace('{p}', phraseOf(act)) };
  }

  /** The reminders to arm on the phone for the next few weeks, worked out now: unlock announcements for each pouch, and the neglect nags counted from the last time
   *  the app was used. Quiet hours move a reminder to when they end; the daily cap and snooze drop the rest. -> [{id, at (ms), kind, title, body, mood, activityId}] */
  function buildSchedule(acts, periods, lastInteraction, prefs, now, horizonDays) {
    horizonDays = horizonDays || 35;
    var horizon = new Date(now.getTime() + horizonDays * DAY_MS), out = [], snoozed = prefs.snooze_until ? parseIso(prefs.snooze_until) : null;
    var month = monthOf(now), months = [month, shiftMonth(month, 1)];
    if (prefs.types.credit_unlocked) activeActs(acts).forEach(function (act) {
      if (!cadenceOf(act).notify) return;
      months.forEach(function (m, mi) {
        var per = periods[periodKey(m, act.id)] || (mi ? { allotted: act.creditsPerMonth, unlocks: computeUnlockTimes(m, act.creditsPerMonth, cadenceOf(act)).map(iso) } : null);
        if (!per || per.allotted <= 0) return;
        var b = monthBounds(m), spacing = (b[1] - b[0]) / DAY_MS / Math.max(1, per.allotted);
        per.unlocks.forEach(function (u, k) {
          var at = parseIso(u);
          if (at <= now || at > horizon) return;
          var t = unlockText(act, spacing, prefs, act.id + m + k);
          out.push({ id: 'u|' + act.id + '|' + m + '|' + k, at: at, kind: 'credit_unlocked', title: t.title, body: t.body, mood: 'neutral', activityId: act.id });
        });
      });
    });
    if (prefs.types.neglect_nag && lastInteraction) {
      for (var d = NAG_AT[0], idx = 0; d <= horizonDays + 60 && idx < 40; idx++) {
        var days = idx < NAG_AT.length ? NAG_AT[idx] : NAG_AT[NAG_AT.length - 1] + (idx - NAG_AT.length + 1) * NAG_REPEAT;
        var at = new Date(lastInteraction.getTime() + days * DAY_MS);
        if (at > now && at <= horizon) {
          var st = nagStage(days), t = nagText(st.level, days, prefs, 'nag' + idx);
          out.push({ id: 'n|' + idx, at: at, kind: 'neglect_nag', title: t.title, body: t.body, mood: t.level >= 4 ? 'evil' : 'neutral', activityId: null, idleDays: days });
        }
        d = days;
      }
    }
    out.forEach(function (n) { n.at = afterQuiet(prefs, n.at); });
    out = out.filter(function (n) { return n.at <= horizon && !(snoozed && n.at < snoozed); }).sort(function (a, b) { return a.at - b.at; });
    var perDay = {}, kept = [];
    out.forEach(function (n) { var k = n.at.getFullYear() + '-' + n.at.getMonth() + '-' + n.at.getDate(); perDay[k] = (perDay[k] || 0) + 1; if (prefs.daily_cap > 0 && perDay[k] <= prefs.daily_cap) kept.push(n); });
    return kept.map(function (n) { return Object.assign({}, n, { at: n.at.getTime() }); });
  }

  // ---------------- what the pages show ----------------
  var MOOD_TEXT = { neutral: ['Brooding', 'You have barely spent. The hoard sits untouched, and so does he.'], annoyed: ['Cross', 'You have been away. He keeps count.'],
    happy: ['Pleased', 'Spending within reason. The golden mean agrees with him.'], sick: ['Ailing', 'You have overspent. The coin-sickness has him.'], evil: ['Cursed', "Far past the plan. The Ring's curse is awake."] };
  var SAYINGS = { neutral: ['The hoard sits still. As do you. Hmph.', 'Nothing spent, nothing gained. Dull.'], annoyed: ['I count the days like coins. {d} of them.', 'You dare ignore a lord of the Nibelungs?'],
    happy: ['Moderation! Even I approve.', 'The golden mean. Hmph. Acceptable.'], sick: ['Ugh... the coin-sickness...', 'Too much. Far too much.'], evil: ['THE CURSE IS UPON YOU!', 'Overspend again and I shall haunt your pockets.'] };
  var ZONE_TEXT = { idle: 'barely spending', moderate: 'spending within reason', over: 'a little over', excess: 'far over' };
  var STARTERS = [['Ordered in', 'takeout', 'order in', 4000, 3], ['Drinks out', 'drinks', 'go out for drinks', 6000, 3], ['Toy', 'toy', 'buy a toy', 5000, 2], ['Shopping', 'shopping', 'go shopping', 12000, 2],
    ['Tickets', 'ticket', 'get tickets to something', 9000, 2], ['Medium grocery shop', 'cart', 'do a medium grocery shop', 9000, 3], ['Big grocery shop', 'basket', 'do a big grocery shop', 20000, 3]];

  function moodKey(st) { return st.annoyed ? 'annoyed' : st.mood; }
  function sayingFor(st, now) {
    var opts = SAYINGS[moodKey(st)], day = Math.floor(new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / DAY_MS);
    return opts[day % opts.length].replace('{d}', String(Math.floor(st.idle_days)));
  }
  function fmtWhen(dt, now) {
    var days = Math.round((new Date(dt.getFullYear(), dt.getMonth(), dt.getDate()) - new Date(now.getFullYear(), now.getMonth(), now.getDate())) / DAY_MS);
    var day = days === 0 ? 'today' : days === 1 ? 'tomorrow' : dt.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }).replace(',', '');
    var h = dt.getHours(), ap = h >= 12 ? 'pm' : 'am'; h = h % 12 || 12;
    return day + ' ' + h + ':' + pad2(dt.getMinutes()) + ' ' + ap;
  }
  function tileView(s, now) {
    var a = s.activity, status, label;
    if (s.allotted <= 0) { status = 'unset'; label = 'No credits this month'; }
    else if (s.overdrawn_now) { status = 'overdrawn'; label = 'Over by ' + (s.used - s.allotted); }
    else if (s.remaining === 0) { status = 'done'; label = 'All used'; }
    else if (s.available > 0) { status = 'available'; label = s.available + ' ready now'; }
    else { status = 'waiting'; label = s.next_at ? 'Next ' + fmtWhen(s.next_at, now) : 'Nothing more unlocks'; }
    return { id: a.id, name: a.name, icon: a.icon, allotted: s.allotted, used: s.used, remaining: s.allotted - s.used, available: s.available, status: status, label: label };
  }
  function money(cents) { return '$' + ((cents || 0) / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function toCents(s) { var n = parseFloat(String(s == null ? '' : s).replace(/[$,\s]/g, '')); return isFinite(n) ? Math.round(n * 100) : 0; }
  function uuid() {
    if (root.crypto && root.crypto.randomUUID) return root.crypto.randomUUID();
    var b = new Uint8Array(16); (root.crypto || require('crypto').webcrypto).getRandomValues(b); b[6] = (b[6] & 15) | 64; b[8] = (b[8] & 63) | 128;
    var h = Array.prototype.map.call(b, function (x) { return (x < 16 ? '0' : '') + x.toString(16); }).join('');
    return h.slice(0, 8) + '-' + h.slice(8, 12) + '-' + h.slice(12, 16) + '-' + h.slice(16, 20) + '-' + h.slice(20);
  }

  var api = { iso: iso, parseIso: parseIso, monthOf: monthOf, shiftMonth: shiftMonth, monthBounds: monthBounds, computeUnlockTimes: computeUnlockTimes, classifyLogs: classifyLogs, cadenceOf: cadenceOf,
    buildPeriod: buildPeriod, equalize: equalize, periodKey: periodKey, activityState: activityState, allStates: allStates, activeActs: activeActs, spendInputs: spendInputs, zone: zone, step: step, band: band, moodFor: moodFor,
    petCompute: petCompute, petShock: petShock, nagStage: nagStage, nagText: nagText, unlockText: unlockText, buildSchedule: buildSchedule, mergePrefs: mergePrefs, inQuiet: inQuiet, afterQuiet: afterQuiet,
    moodKey: moodKey, sayingFor: sayingFor, fmtWhen: fmtWhen, tileView: tileView, money: money, toCents: toCents, uuid: uuid, CFG: CFG, ORDER: ORDER, NAG_AT: NAG_AT, NAG_REPEAT: NAG_REPEAT,
    MOOD_TEXT: MOOD_TEXT, MOOD_NOTE: MOOD_NOTE, ZONE_TEXT: ZONE_TEXT, STARTERS: STARTERS, DEFAULT_PREFS: DEFAULT_PREFS, TYPE_LABELS: TYPE_LABELS, NAG_COPY: NAG_COPY, UNLOCK_COPY: UNLOCK_COPY, DAY_MS: DAY_MS };
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.AlberichCore = api;
})(typeof window !== 'undefined' ? window : globalThis);
