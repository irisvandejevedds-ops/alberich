// Replays parity.json (made by the Python rules) through core.js. Run with TZ=America/Toronto.
const test = require('node:test');
const assert = require('node:assert');
const C = require('../core.js');
const data = require('./parity.json');

test('timezone is the one the fixtures were made in', () => {
  assert.equal(new Date('2026-10-11T12:00:00Z').getTimezoneOffset(), 240, 'run with TZ=America/Toronto');
});

test('unlock schedules agree with Python for every month, count and rhythm (including the clock-change months)', () => {
  const bad = [];
  for (const s of data.schedules) {
    const got = C.computeUnlockTimes(s.month, s.n, s.cadence).map(C.iso);
    if (JSON.stringify(got) !== JSON.stringify(s.unlocks)) bad.push({ month: s.month, n: s.n, cadence: s.cadence, got: got.slice(0, 3), want: s.unlocks.slice(0, 3) });
  }
  assert.deepEqual(bad.slice(0, 3), [], bad.length + ' of ' + data.schedules.length + ' differ');
});

const near = (a, b) => Math.abs(a - b) < 1e-9;

data.scenarios.forEach((sc, i) => test('pouch standing and the pet agree with Python in scenario ' + i + ' (' + sc.month + ')', () => {
  const acts = sc.acts.map((a, k) => ({ id: 'A' + a.id, name: a.name, icon: 'star', phrase: '', estimateCents: a.estimateCents, creditsPerMonth: a.creditsPerMonth, cadence: a.cadence, sort: k, archived: false }));
  const allLogs = sc.logs.map((l) => ({ id: 'L' + l.id, activityId: 'A' + l.activityId, ts: l.ts, undoneAt: null }));
  const stmt = {}; Object.keys(sc.stmt).forEach((k) => { stmt['A' + k] = sc.stmt[k].map(C.parseIso); });
  const periods = {}, ctx = { periods, logs: [], stmtUses: (id, m) => (stmt[id] || []).filter((d) => C.monthOf(d) === m) };
  let pet = null, prevMood = null;
  sc.steps.forEach((st, si) => {
    const now = new Date(st.now), where = ' at step ' + si + ' ' + st.now;
    ctx.logs = allLogs.slice(0, st.n_logs);
    if (st.undone !== null) ctx.logs.find((l) => l.id === 'L' + st.undone) && (allLogs.find((l) => l.id === 'L' + st.undone).undoneAt = st.now);
    if (st.changed) { acts[0].creditsPerMonth += 3; C.buildPeriod(periods, sc.month, acts[0], true); }
    const states = C.allStates(acts, sc.month, now, ctx);
    assert.equal(states.length, st.states.length, 'pouch count' + where);
    states.forEach((s, k) => {
      const w = st.states[k], at = ' for pouch ' + k + where;
      assert.equal(s.activity.id, 'A' + w.id, 'order' + at);
      for (const f of ['allotted', 'used', 'remaining', 'unlocked', 'available', 'on_pace', 'borrowed', 'overdrawn', 'from_statements']) assert.equal(s[f], w[f], f + at);
      assert.equal(s.estimate_cents, w.estimate, 'estimate' + at);
      assert.equal(s.overdrawn_now, w.overdrawn_now, 'overdrawn_now' + at);
      assert.equal(s.next_at ? C.iso(s.next_at) : null, w.next_at, 'next unlock' + at);
      assert.deepEqual(s.statuses, w.statuses, 'statuses' + at);
      assert.deepEqual(s.log_ids, w.log_ids.map((x) => x === null ? null : 'L' + x), 'which log is which' + at);
    });
    const r = C.petCompute(pet, states, sc.buffer, now, st.idle, prevMood);
    pet = r.pet; prevMood = r.mood;
    assert.ok(near(r.w, st.pet.w), 'wellbeing ' + r.w + ' vs ' + st.pet.w + where);
    assert.equal(r.mood, st.pet.mood, 'mood' + where);
    assert.equal(r.zone, st.pet.zone, 'zone' + where);
    assert.equal(r.annoyed, st.pet.annoyed, 'annoyed' + where);
    assert.equal(r.spent, st.pet.spent); assert.equal(r.expected, st.pet.expected); assert.equal(r.overdrawn_cents, st.pet.od);
  });
}));
