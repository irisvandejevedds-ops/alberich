"""Runs the real Python rules (credits.py, pet.py) over synthetic pouches, logs and time series and writes parity.json, which parity.test.js replays against core.js.
    cd app && /opt/finapp/venv/bin/python ../web/tests/gen_parity.py
All data here is made up. Timezone is America/Toronto, so run the node test with TZ=America/Toronto."""
import json
import pathlib
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "app"))
from finapp import clock, credits, pet, spending  # noqa: E402
from finapp.db import open_db  # noqa: E402

TZ = ZoneInfo("America/Toronto")
UTC = timezone.utc
rnd = random.Random(20261012)
MONTHS = ["2026-02", "2026-03", "2026-05", "2026-10", "2026-11", "2026-12"]       # March and November hold the clock changes


def cad(mode="even", time="09:00", dows=(), notify=True):
    return {"mode": mode, "time": time, "dows": list(dows), "notify": notify}


# ---- 1. unlock schedules
CADENCES = [cad(), cad(time="18:30"), cad("weekdays", "09:00", [5, 6]), cad("weekdays", "12:15", [0, 2, 4]), cad("weekdays", "09:00", [3]), cad("weekdays", "21:00", []), cad(time="00:05")]
schedules = []
for m in MONTHS:
    for n in (0, 1, 2, 3, 5, 8, 12, 31, 40):
        for c in CADENCES:
            u = credits.compute_unlock_times(m, n, c, TZ)
            schedules.append({"month": m, "n": n, "cadence": c, "unlocks": [clock.iso(x) for x in u]})


# ---- 2. a pouch's standing, and 3. the pet over time
def new_db():
    return open_db(pathlib.Path(tempfile.mkdtemp()) / "t.db")


def add_act(db, name, credits_n, est, c, cat=None):
    return db.ex("INSERT INTO activities(name,icon,phrase,estimate_cents,weight,credits_per_month,cadence,sort,archived,created_at,category_id,auto_credits) VALUES(?,?,?,?,?,?,?,?,0,?,?,0)",
                 (name, "star", "", est, 3, credits_n, json.dumps(c), 0, "2026-01-01T00:00:00Z", cat)).lastrowid


def scenario(seed, month, n_acts, with_stmts, buffer, idle_day_offsets, change_at=None):
    r = random.Random(seed)
    db = new_db()
    acts = []
    for i in range(n_acts):
        c = r.choice(CADENCES)
        a = {"name": f"pouch {i}", "credits": r.choice([0, 1, 2, 3, 4, 6, 8]), "est": r.choice([2500, 4000, 6000, 9000, 12000]), "cadence": c}
        a["id"] = add_act(db, a["name"], a["credits"], a["est"], c, cat=1 if with_stmts else None)
        acts.append(a)
    start, end = credits.month_bounds(month, TZ)
    stmt = {}
    if with_stmts:
        for a in acts:
            stmt[a["id"]] = sorted(start + timedelta(days=r.uniform(0, 27)) for _ in range(r.choice([0, 0, 1, 2, 3])))
    real = spending.stmt_uses
    spending.stmt_uses = lambda _db, act, m, tz: [x.astimezone(UTC) for x in stmt.get(act["id"], []) if credits.month_of(x, tz) == m]
    logs, pending = [], []
    for a in acts:
        for _ in range(r.choice([0, 1, 2, 3, 5, 9])):
            t = start + timedelta(days=r.uniform(0, 29), hours=r.uniform(0, 3))
            if t < end:
                pending.append((t.astimezone(UTC), a["id"]))
    for _ in range(r.randint(0, 3)):                                               # a tap near a statement purchase (so matching kicks in)
        if stmt:
            aid = r.choice([k for k in stmt if stmt[k]] or [acts[0]["id"]])
            base = stmt[aid][0] if stmt.get(aid) else start + timedelta(days=3)
            pending.append(((base + timedelta(hours=r.uniform(-30, 30))).astimezone(UTC), aid))
    pending.sort()
    steps, inserted, state_pet_mood = [], 0, None
    nows = sorted(start + timedelta(days=d, hours=r.uniform(0, 23)) for d in idle_day_offsets)
    for si, now in enumerate(nows):
        now_u = now.astimezone(UTC).replace(microsecond=0)
        while inserted < len(pending) and pending[inserted][0] <= now_u:
            t, aid = pending[inserted]
            lid = db.ex("INSERT INTO activity_logs(activity_id,ts,source) VALUES(?,?,?)", (aid, clock.iso(t), "live")).lastrowid
            logs.append({"id": lid, "activityId": aid, "ts": clock.iso(t)})
            inserted += 1
        undone = None
        if logs and r.random() < 0.15:                                               # one tap undone
            undone = r.choice(logs)["id"]
            db.ex("UPDATE activity_logs SET undone_at=? WHERE id=?", (clock.iso(now_u), undone))
        if change_at is not None and si == change_at:
            a = acts[0]
            db.ex("UPDATE activities SET credits_per_month=? WHERE id=?", (a["credits"] + 3, a["id"]))
            credits.build_period(db, month, db.one("SELECT * FROM activities WHERE id=?", (a["id"],)), TZ, replace=True)
            a["credits"] += 3
        states = credits.all_states(db, month, TZ, now_u)
        idle = idle_day_offsets[si] * 0 + r.choice([0, 0.5, 1.9, 2.0, 3.5, 9.0])
        st, _ = pet.current(db, states, buffer, now_u, idle_days=idle)
        steps.append({"now": clock.iso(now_u), "undone": undone, "changed": change_at is not None and si == change_at, "idle": idle, "n_logs": len(logs),
                      "states": [{"id": s["activity"]["id"], "allotted": s["allotted"], "estimate": s["estimate_cents"], "used": s["used"], "remaining": s["remaining"], "unlocked": s["unlocked"],
                                  "available": s["available"], "next_at": clock.iso(s["next_at"]) if s["next_at"] else None, "on_pace": s["on_pace"], "borrowed": s["borrowed"],
                                  "overdrawn": s["overdrawn"], "overdrawn_now": s["overdrawn_now"], "statuses": s["statuses"], "log_ids": s["log_ids"], "from_statements": s["from_statements"]} for s in states],
                      "pet": {"w": st["w"], "mood": st["mood"], "zone": st["zone"], "annoyed": st["annoyed"], "spent": st["spent"], "expected": st["expected"], "od": st["overdrawn_cents"]}})
    spending.stmt_uses = real
    return {"month": month, "buffer": buffer, "acts": [{"id": a["id"], "name": a["name"], "creditsPerMonth": a["credits"] - (3 if (change_at is not None and a is acts[0]) else 0), "estimateCents": a["est"], "cadence": a["cadence"]} for a in acts],
            "stmt": {str(k): [clock.iso(x.astimezone(UTC)) for x in v] for k, v in stmt.items()}, "logs": logs, "steps": steps}


days = lambda n: sorted(rnd.sample(range(0, 28), n))
scens = [scenario(1, "2026-10", 5, False, 0, days(10)), scenario(2, "2026-03", 4, False, 20000, days(9)), scenario(3, "2026-11", 6, True, 0, days(12)),
         scenario(4, "2026-05", 3, True, 15000, days(8), change_at=3), scenario(5, "2026-10", 7, False, 5000, days(14), change_at=5), scenario(6, "2026-02", 4, True, 0, days(11))]
(HERE / "parity.json").write_text(json.dumps({"schedules": schedules, "scenarios": scens}))
import collections  # noqa: E402
moods = collections.Counter(s["pet"]["mood"] for sc in scens for s in sc["steps"])
zones = collections.Counter(s["pet"]["zone"] for sc in scens for s in sc["steps"])
print(len(schedules), "schedules;", sum(len(s["steps"]) for s in scens), "steps; moods", dict(moods), "zones", dict(zones),
      "| statuses seen:", sorted({x for sc in scens for st in sc["steps"] for s in st["states"] for x in s["statuses"]}))
