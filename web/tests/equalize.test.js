const test = require('node:test');
const assert = require('node:assert');
const C = require('../core.js');

const st = (id, allotted, used, est) => ({ activity: { id, name: id, sort: 0 }, allotted, used, remaining: Math.max(0, allotted - used), estimate_cents: est });

test('empty pouches are refilled from full ones and the month total is kept', () => {
  const states = [st('toy', 7, 3, 5000), st('order', 13, 5, 4000), st('grocery', 7, 7, 6000), st('treat', 23, 23, 500)];
  const r = C.equalize(states), pool = (g) => states.reduce((n, s) => n + g(s) * s.estimate_cents, 0);
  const before = pool((s) => s.allotted), after = states.reduce((n, s) => n + (r.allot[s.activity.id] ?? s.allotted) * s.estimate_cents, 0);
  assert.ok(after <= before && before - after < 5000, 'dollars kept to within one credit');
  assert.ok(r.allot.grocery > 7 && r.allot.treat > 23, 'empty pouches receive');
  assert.ok(r.allot.toy < 7 && r.allot.order < 13, 'full pouches give');
  states.forEach((s) => assert.ok((r.allot[s.activity.id] ?? s.allotted) >= s.used, 'never below what was used'));
});

test('nothing to do when pouches are already even', () => {
  assert.deepEqual(C.equalize([st('a', 4, 2, 1000), st('b', 8, 4, 1000)]).moves, []);
});
