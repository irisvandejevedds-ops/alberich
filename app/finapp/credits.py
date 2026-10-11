"""Activity credits: allocation, unlock schedule, log classification, pace inputs."""
import json
import math
from collections import Counter
from datetime import datetime, time as dtime, timedelta, timezone

from . import clock

UTC = timezone.utc
BORROW_WEIGHT = 0.25
DEFAULT_CADENCE = {"mode": "even", "time": "09:00", "dows": [], "notify": True}


def month_of(dt_utc: datetime, tz) -> str:
    return dt_utc.astimezone(tz).strftime("%Y-%m")


def month_bounds(month: str, tz):
    y, m = (int(x) for x in month.split("-"))
    start = datetime(y, m, 1, tzinfo=tz)
    end = datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=tz)
    return start, end


def parse_hhmm(s, default=dtime(9, 0)):
    try:
        h, m = str(s).split(":")
        return dtime(int(h), int(m))
    except Exception:
        return default


def cadence_of(act) -> dict:
    try:
        c = json.loads(act["cadence"] or "{}")
    except Exception:
        c = {}
    return {**DEFAULT_CADENCE, **c}


def compute_unlock_times(month: str, n: int, cadence: dict, tz):
    """Ideal unlock for credit k is start + k*D/N, snapped to the preferred time (and weekdays)."""
    if n <= 0:
        return []
    start, end = month_bounds(month, tz)
    total = (end - start).total_seconds()      # wall-clock seconds; schedule is defined in local time
    pref = parse_hhmm(cadence.get("time"))
    dows = set(cadence.get("dows") or []) if cadence.get("mode") == "weekdays" else None

    def snap(d):
        c = datetime.combine(d, pref, tzinfo=tz)
        if dows:
            for _ in range(8):
                if c.weekday() in dows:
                    break
                d = d + timedelta(days=1)
                c = datetime.combine(d, pref, tzinfo=tz)
        return c

    out, prev = [], None
    for k in range(n):
        ideal = start + timedelta(seconds=total * k / n)
        c = snap(ideal.date())
        if prev is not None and c <= prev:
            c = snap(prev.date() + timedelta(days=1))
        if c >= end:                               # no room left: pack the remainder into the last hours
            c = end - timedelta(minutes=(n - k))
            if prev is not None and c <= prev:
                c = prev + timedelta(minutes=1)
        out.append(c)
        prev = c
    return [c.astimezone(UTC) for c in out]


def classify_logs(log_times, unlocks, allotted):
    """Each log consumes the earliest credit: on_pace if one had unlocked, borrowed if allotment
    remains, overdrawn otherwise."""
    res = []
    for i, ts in enumerate(sorted(log_times), 1):
        unlocked = sum(1 for u in unlocks if u <= ts)
        res.append("on_pace" if i <= unlocked else "borrowed" if i <= allotted else "overdrawn")
    return res


def allocate(pool_cents: int, acts):
    """acts: dicts with id, weight, estimate_cents -> ({id: credits}, leftover_cents)."""
    tw = sum(max(1, a["weight"]) for a in acts) or 1
    out, spent = {}, 0
    for a in acts:
        share = pool_cents * max(1, a["weight"]) / tw
        est = a["estimate_cents"]
        n = int(math.floor(share / est)) if est > 0 else 0
        out[a["id"]] = n
        spent += n * est
    return out, pool_cents - spent


def build_period(db, month, act, tz, replace=False):
    unlocks = compute_unlock_times(month, act["credits_per_month"], cadence_of(act), tz)
    row = (month, act["id"], act["credits_per_month"], act["estimate_cents"],
           json.dumps([clock.iso(u) for u in unlocks]))
    if replace:
        db.ex("""INSERT INTO activity_credit_periods(month,activity_id,allotted,estimate_cents,unlock_times)
                 VALUES(?,?,?,?,?) ON CONFLICT(month,activity_id) DO UPDATE SET
                 allotted=excluded.allotted, estimate_cents=excluded.estimate_cents,
                 unlock_times=excluded.unlock_times""", row)
    else:
        db.ex("""INSERT OR IGNORE INTO activity_credit_periods(month,activity_id,allotted,estimate_cents,unlock_times)
                 VALUES(?,?,?,?,?)""", row)


def ensure_periods(db, month, tz):
    for act in db.q("SELECT * FROM activities WHERE archived=0"):
        build_period(db, month, act, tz)


def activity_state(db, act, month, tz, now):
    build_period(db, month, act, tz)
    per = db.one("SELECT * FROM activity_credit_periods WHERE month=? AND activity_id=?", (month, act["id"]))
    unlocks = [clock.parse_iso(s) for s in json.loads(per["unlock_times"])]
    start, end = month_bounds(month, tz)
    taps = db.q("""SELECT * FROM activity_logs WHERE activity_id=? AND undone_at IS NULL
                   AND ts>=? AND ts<? ORDER BY ts, id""",
                (act["id"], clock.iso(start), clock.iso(end)))
    # what the statements show was spent is the truth; a tap that a statement purchase already accounts for is not counted twice
    from . import spending
    stmt = spending.stmt_uses(db, act, month, tz)
    free, kept = list(stmt), []
    for l in taps:
        t = clock.parse_iso(l["ts"])
        j = next((i for i, s in enumerate(free) if abs((s - t).total_seconds()) <= spending.MATCH_DAYS * 86400), None)
        if j is None:
            kept.append(l)
        else:
            free.pop(j)
    uses = sorted([(s, None) for s in stmt] + [(clock.parse_iso(l["ts"]), l["id"]) for l in kept], key=lambda u: (u[0], u[1] or 0))
    logs = [{"id": i} for _, i in uses]
    statuses = classify_logs([t for t, _ in uses], unlocks, per["allotted"])
    used = len(uses)
    unlocked_now = sum(1 for u in unlocks if u <= now)
    counts = Counter(statuses)
    future = [u for u in unlocks if u > now]
    nxt = future[0] if future else None
    return {
        "activity": act, "month": month, "allotted": per["allotted"], "estimate_cents": per["estimate_cents"],
        "used": used, "remaining": max(0, per["allotted"] - used), "unlocked": unlocked_now,
        "available": max(0, min(unlocked_now, per["allotted"]) - used),
        "next_at": nxt, "unlocks": unlocks,
        "on_pace": counts["on_pace"], "borrowed": counts["borrowed"], "overdrawn": counts["overdrawn"],
        "log_ids": [l["id"] for l in logs], "statuses": statuses,
        "overdrawn_now": used > per["allotted"], "from_statements": len(stmt),
    }


def shift_month(month, d):
    y, m = int(month[:4]), int(month[5:]) + d
    while m < 1:
        y, m = y - 1, m + 12
    return f"{y}-{m + 0:02d}" if m <= 12 else f"{y + 1}-{m - 12:02d}"


def overdraw_history(db, act, month, tz, now, months=4):
    """How often and by how much this pouch ran past its credits, newest month first: [{month, allotted, used, over}].
    Only months that have a stored period count (so past months are judged by the credits they really had). Used to tune the cadence."""
    out = []
    for k in range(months):
        m = shift_month(month, -k)
        if k and not db.one("SELECT 1 FROM activity_credit_periods WHERE month=? AND activity_id=?", (m, act["id"])):
            continue
        s = activity_state(db, act, m, tz, now)
        out.append({"month": m, "allotted": s["allotted"], "used": s["used"], "over": max(0, s["used"] - s["allotted"])})
    return out


def all_states(db, month, tz, now):
    return [activity_state(db, a, month, tz, now)
            for a in db.q("SELECT * FROM activities WHERE archived=0 ORDER BY sort, id")]


def pace_inputs(states):
    od = sum(s["overdrawn"] * s["estimate_cents"] for s in states)
    bc = sum(s["borrowed"] * s["estimate_cents"] * BORROW_WEIGHT for s in states)
    return od, bc


def get_plan(db, month):
    r = db.one("SELECT * FROM month_plan WHERE month=?", (month,))
    return dict(r) if r else {"month": month, "income_cents": 0, "savings_cents": 0, "buffer_cents": 0,
                              "pool_override_cents": None, "notes": ""}


def save_plan(db, month, **fields):
    cur = get_plan(db, month)
    cur.update({k: v for k, v in fields.items()})
    db.ex("""INSERT INTO month_plan(month,income_cents,savings_cents,buffer_cents,pool_override_cents,notes)
             VALUES(?,?,?,?,?,?) ON CONFLICT(month) DO UPDATE SET income_cents=excluded.income_cents,
             savings_cents=excluded.savings_cents, buffer_cents=excluded.buffer_cents,
             pool_override_cents=excluded.pool_override_cents, notes=excluded.notes""",
          (month, cur["income_cents"], cur["savings_cents"], cur["buffer_cents"],
           cur["pool_override_cents"], cur.get("notes", "")))
