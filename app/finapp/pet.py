"""Alberich's wellbeing follows the golden mean.

Neutral when you barely spend, happy when you spend in moderation, sick and then evil when you overspend.
A single wellbeing value w in [-1, 1] chases a target set by the current spending zone: illness hits at once,
recovery and fading are gradual. All constants are tunable in CFG.
"""
import json

from . import clock

CFG = {
    "idle_ratio": 0.25,          # spent less than this share of what has unlocked so far = "not spending"
    "up_per_day": 0.34, "down_per_day": 0.20,
    "target": {"idle": 0.0, "moderate": 1.0, "over": -0.6, "excess": -1.0},
    "thr": {"happy": 0.5, "neutral": -0.3, "sick": -0.8}, "hyst": 0.05,
    "annoyed_days": 2,
    "grace_hours": 6,            # an overspend that clears within this long is forgiven: the drop it caused is undone
}
ORDER = ["evil", "sick", "neutral", "happy"]


def spend_inputs(states):
    S = sum(s["used"] * s["estimate_cents"] for s in states)
    E = sum(min(s["unlocked"], s["allotted"]) * s["estimate_cents"] for s in states)
    OD = sum(s["overdrawn"] * s["estimate_cents"] for s in states)
    od_n = sum(s["overdrawn"] for s in states)
    return S, E, OD, od_n


def zone(S, E, OD, od_n, buffer):
    if OD > 0:
        excess = OD >= buffer if buffer > 0 else od_n >= 2
        return "excess" if excess else "over"
    if E <= 0 or S < CFG["idle_ratio"] * E:
        return "idle"
    return "moderate"


def step(w, target, dt_days):
    if target < 0 and target < w:
        return target                                   # sickness arrives immediately
    if target > w:
        return min(target, w + CFG["up_per_day"] * dt_days)
    if target < w:
        return max(target, w - CFG["down_per_day"] * dt_days)
    return w


def band(w):
    t = CFG["thr"]
    return "happy" if w >= t["happy"] else "neutral" if w >= t["neutral"] else "sick" if w >= t["sick"] else "evil"


def mood_for(w, prev=None):
    raw = band(w)
    if prev not in ORDER:
        return raw
    i, j = ORDER.index(prev), ORDER.index(raw)
    hy, t = CFG["hyst"], CFG["thr"]
    if i == j:
        return prev
    if j > i:
        best = prev
        for m in ORDER[i + 1:]:
            lower = t["neutral"] if m == "neutral" else t["happy"]
            if m == "sick":
                lower = t["sick"]
            if w >= lower + hy:
                best = m
        return best
    cand = band(w + hy)
    return cand if ORDER.index(cand) < i else prev


def _load(db):
    try:
        d = json.loads(db.get_setting("pet_w", "{}"))
        return float(d["w"]), clock.parse_iso(d["ts"])
    except Exception:
        return 0.0, None


def _bad(db):
    """(when the current overspend began, his wellbeing just before it) or (None, None)."""
    try:
        d = json.loads(db.get_setting("pet_w", "{}"))
        return (clock.parse_iso(d["bad"]), float(d["pre"])) if d.get("bad") else (None, None)
    except Exception:
        return None, None


def _save(db, w, now, bad=None, pre=None):
    d = {"w": w, "ts": clock.iso(now)}
    if bad is not None:
        d["bad"], d["pre"] = clock.iso(bad), pre
    db.set_setting("pet_w", json.dumps(d))


def shock(db, w_floor, now):
    """Apply a month-close verdict: pull wellbeing down to at most w_floor (illness is immediate)."""
    w, _ = _load(db)
    _save(db, min(w, w_floor), now)


def reset_once(db, now, key):
    """Start his wellbeing from neutral, once per key. Used when the rules that fed it changed, so a mood earned under the old ones cannot linger."""
    if db.get_setting(key):
        return False
    db.set_setting(key, "1")
    _save(db, 0.0, now)
    return True


def compute(db, states, buffer_cents, now, idle_days=0.0, persist=True):
    w, ts = _load(db)
    dt = max(0.0, (now - ts).total_seconds() / 86400) if ts else 0.0
    S, E, OD, od_n = spend_inputs(states)
    z = zone(S, E, OD, od_n, buffer_cents)
    bad_since, pre = _bad(db)
    if z in ("over", "excess"):
        if bad_since is None:
            bad_since, pre = now, w                       # the overspend starts now; remember how he was
    elif bad_since is not None:
        if (now - bad_since).total_seconds() < CFG["grace_hours"] * 3600:
            w = max(w, pre)                               # it did not last: a slip, a mistaken log, a count since corrected. No harm done.
        bad_since = pre = None
    w = step(w, CFG["target"][z], dt)
    prev = db.one("SELECT mood FROM pet_state_history ORDER BY id DESC LIMIT 1")
    mood = mood_for(w, prev["mood"] if prev and prev["mood"] in ORDER else None)
    if persist:
        _save(db, w, now, bad_since, pre)
    return {"w": w, "mood": mood, "zone": z, "spent": S, "expected": E, "overdrawn_cents": OD,
            "buffer": buffer_cents, "annoyed": idle_days >= CFG["annoyed_days"] and mood == "neutral",
            "idle_days": idle_days}


def current(db, states, buffer_cents, now, idle_days=0.0, record=True):
    """Compute state and log mood transitions. Returns (state, previous_mood_if_changed)."""
    st = compute(db, states, buffer_cents, now, idle_days, persist=record)
    prev = db.one("SELECT * FROM pet_state_history ORDER BY id DESC LIMIT 1")
    changed = None
    if record and (prev is None or prev["mood"] != st["mood"]):
        db.ex("INSERT INTO pet_state_history(ts,health,mood,evil,inputs) VALUES(?,?,?,?,?)",
              (clock.iso(now), st["w"], st["mood"], int(st["mood"] == "evil"),
               json.dumps({k: st[k] for k in ("zone", "spent", "expected", "overdrawn_cents", "buffer")})))
        changed = prev["mood"] if prev else None
    return st, changed
