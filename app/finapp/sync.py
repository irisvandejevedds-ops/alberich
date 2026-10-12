"""Syncing with a paired phone. The phone is where taps happen, so it is the source of truth for logs; this side adds what only it can work out from the
statements (credits, which purchases already count, the monthly buffer) and sends it back. Each pouch and log has a stable id. When both sides changed the
same row, the later edit wins."""
import json
import re
from datetime import datetime, timedelta, timezone

from . import clock, credits, notify, pxicons, spending

MAX_ROWS = 5000
MAX_BODY = 6_000_000
ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
UTC = timezone.utc


def _iso(v, what):
    try:
        clock.parse_iso(v)
    except (TypeError, ValueError):
        raise ValueError(f"bad {what}")
    return v


def _uid(v, what="id"):
    v = str(v or "")
    if not ID.match(v):
        raise ValueError(f"bad {what}")
    return v


def clean_act(a):
    if not isinstance(a, dict):
        raise ValueError("a pouch is not an object")
    name = " ".join(str(a.get("name") or "").split())[:60]
    if not name:
        raise ValueError("a pouch has no name")
    c = a.get("cadence") if isinstance(a.get("cadence"), dict) else {}
    cad = {"mode": "weekdays" if c.get("mode") == "weekdays" else "even", "time": c.get("time") if HHMM.match(str(c.get("time") or "")) else "09:00",
           "dows": sorted({int(x) for x in (c.get("dows") or []) if isinstance(x, int) and 0 <= x <= 6}), "notify": bool(c.get("notify", True))}
    return {"uuid": _uid(a.get("id")), "name": name, "icon": pxicons.normalise(str(a.get("icon") or "")), "phrase": " ".join(str(a.get("phrase") or "").split())[:80],
            "estimate_cents": max(0, min(10_000_000, int(a.get("estimateCents") or 0))), "weight": max(1, min(5, int(a.get("weight") or 3))),
            "credits": max(0, min(60, int(a.get("creditsPerMonth") or 0))), "cadence": json.dumps(cad, sort_keys=True), "sort": max(0, min(10_000, int(a.get("sort") or 0))),
            "archived": 1 if a.get("archived") else 0, "updated_at": _iso(a.get("updatedAt") or clock.iso(clock.now()), "updatedAt")}


def clean_log(l):
    if not isinstance(l, dict):
        raise ValueError("a log is not an object")
    ts = _iso(l.get("ts"), "ts")
    if not ("2020" <= ts[:4] <= "2100"):
        raise ValueError("ts out of range")
    return {"uuid": _uid(l.get("id")), "act": _uid(l.get("activityId"), "activityId"), "ts": ts, "note": (str(l["note"])[:200] if l.get("note") else None),
            "undone_at": _iso(l["undoneAt"], "undoneAt") if l.get("undoneAt") else None, "updated_at": _iso(l.get("updatedAt") or ts, "updatedAt")}


def apply(db, cfg, acts, logs, now):
    """Merge the phone's changed pouches, then its changed logs. -> {accepted, stale, rejected: [reasons], act_ids, log_ids}."""
    if not isinstance(acts, list) or not isinstance(logs, list) or len(acts) > MAX_ROWS or len(logs) > MAX_ROWS:
        raise ValueError("too many rows in one sync")
    out = {"accepted": 0, "stale": 0, "rejected": [], "act_ids": set(), "log_ids": set()}
    month = credits.month_of(now, cfg.tz)
    nowiso = clock.iso(now)
    with db.tx():
        for a in acts:
            try:
                v = clean_act(a)
            except (ValueError, TypeError) as ex:
                out["rejected"].append(str(ex))
                continue
            out["act_ids"].add(v["uuid"])
            old = db.one("SELECT * FROM activities WHERE uuid=?", (v["uuid"],))
            if old and old["updated_at"] >= v["updated_at"]:
                out["stale"] += 1
                continue
            if old:
                db.ex("UPDATE activities SET name=?, icon=?, phrase=?, estimate_cents=?, weight=?, credits_per_month=?, cadence=?, sort=?, archived=?, updated_at=? WHERE id=?",
                      (v["name"], v["icon"], v["phrase"], v["estimate_cents"], v["weight"], v["credits"], v["cadence"], v["sort"], v["archived"], v["updated_at"], old["id"]))
                rid = old["id"]
            else:
                rid = db.ex("INSERT INTO activities(uuid,name,icon,phrase,estimate_cents,weight,credits_per_month,cadence,sort,archived,auto_credits,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?)",
                            (v["uuid"], v["name"], v["icon"], v["phrase"], v["estimate_cents"], v["weight"], v["credits"], v["cadence"], v["sort"], v["archived"], nowiso, v["updated_at"])).lastrowid
            credits.build_period(db, month, db.one("SELECT * FROM activities WHERE id=?", (rid,)), cfg.tz, replace=True)
            out["accepted"] += 1
        for l in logs:
            try:
                v = clean_log(l)
            except (ValueError, TypeError) as ex:
                out["rejected"].append(str(ex))
                continue
            act = db.one("SELECT id FROM activities WHERE uuid=?", (v["act"],))
            if not act:
                out["rejected"].append("a log for a pouch this computer does not know")
                continue
            out["log_ids"].add(v["uuid"])
            old = db.one("SELECT id, updated_at FROM activity_logs WHERE uuid=?", (v["uuid"],))
            if old and old["updated_at"] >= v["updated_at"]:
                out["stale"] += 1
                continue
            if old:
                db.ex("UPDATE activity_logs SET activity_id=?, ts=?, note=?, undone_at=?, updated_at=? WHERE id=?", (act["id"], v["ts"], v["note"], v["undone_at"], v["updated_at"], old["id"]))
            else:
                db.ex("INSERT INTO activity_logs(uuid,activity_id,ts,source,note,undone_at,updated_at) VALUES(?,?,?,?,?,?,?)", (v["uuid"], act["id"], v["ts"], "live", v["note"], v["undone_at"], v["updated_at"]))
            out["accepted"] += 1
    return out


def _act_json(r):
    try:
        cad = json.loads(r["cadence"] or "{}")
    except ValueError:
        cad = {}
    return {"id": r["uuid"], "name": r["name"], "icon": r["icon"], "phrase": r["phrase"], "estimateCents": r["estimate_cents"], "weight": r["weight"], "creditsPerMonth": r["credits_per_month"],
            "cadence": cad, "sort": r["sort"], "archived": bool(r["archived"]), "autoCredits": bool(r["auto_credits"]), "updatedAt": r["updated_at"]}


def export(db, since_acts, since_logs, skip_acts=(), skip_logs=(), limit=2000):
    """Pouches and logs changed after the given revisions. -> (acts, logs, cursor_acts, cursor_logs, more)."""
    arows = db.q("SELECT * FROM activities WHERE rev>? ORDER BY rev LIMIT ?", (int(since_acts), limit + 1))
    lrows = db.q("SELECT l.*, a.uuid AS act_uuid FROM activity_logs l JOIN activities a ON a.id=l.activity_id WHERE l.rev>? ORDER BY l.rev LIMIT ?", (int(since_logs), limit + 1))
    more = len(arows) > limit or len(lrows) > limit
    arows, lrows = arows[:limit], lrows[:limit]
    ca = arows[-1]["rev"] if arows else int(since_acts)           # the cursor passes rows this very request just stored too, so they do not come straight back
    cl = lrows[-1]["rev"] if lrows else int(since_logs)
    acts = [_act_json(r) for r in arows if r["uuid"] not in skip_acts]
    logs = [{"id": r["uuid"], "activityId": r["act_uuid"], "ts": r["ts"], "source": r["source"], "note": r["note"], "undoneAt": r["undone_at"], "updatedAt": r["updated_at"]}
            for r in lrows if r["uuid"] not in skip_logs]
    return acts, logs, ca, cl, more


def computed(db, cfg, now):
    """What only the computer can work out, for this month and last: the buffer, the credits and schedules it settled on, and which statement purchases already count."""
    month = credits.month_of(now, cfg.tz)
    months = [month, credits.shift_month(month, -1)]
    stmt, periods = {}, {}
    for a in db.q("SELECT * FROM activities WHERE archived=0"):
        uses = []
        for m in months:
            uses += [clock.iso(x) for x in spending.stmt_uses(db, a, m, cfg.tz)]
        if uses:
            stmt[a["uuid"]] = uses
    for r in db.q("SELECT p.*, a.uuid AS act_uuid FROM activity_credit_periods p JOIN activities a ON a.id=p.activity_id WHERE p.month IN (?,?)", tuple(months)):
        periods[f"{r['month']}|{r['act_uuid']}"] = {"allotted": r["allotted"], "estimateCents": r["estimate_cents"], "unlocks": json.loads(r["unlock_times"])}
    return {"buffer_cents": str(credits.get_plan(db, month)["buffer_cents"]), "stmt_uses": json.dumps(stmt, sort_keys=True), "periods": json.dumps(periods, sort_keys=True)}


def merge_settings(db, theirs):
    """Take the phone's wellbeing if it is newer, and the later of the two last-opened times, so the computer does not nag someone who is using the phone."""
    if not isinstance(theirs, dict):
        return
    try:
        pet = json.loads(theirs.get("pet") or "")
        clock.parse_iso(pet["ts"])
        float(pet["w"])
        mine = json.loads(db.get_setting("pet_w", "{}") or "{}")
        if not mine.get("ts") or pet["ts"] > mine["ts"]:
            db.set_setting("pet_w", json.dumps({k: pet[k] for k in ("w", "ts", "bad", "pre") if k in pet}))
    except (ValueError, TypeError, KeyError):
        pass
    li = theirs.get("last_interaction")
    try:
        clock.parse_iso(li)
        if li > (db.get_setting("last_interaction") or ""):
            db.set_setting("last_interaction", li)
    except (ValueError, TypeError):
        pass


def handle(db, cfg, body, now=None):
    """The whole sync exchange. body is the phone's request (a dict); returns the reply."""
    now = now or clock.now()
    sa, sl = body.get("since_acts", 0), body.get("since_logs", 0)
    sa, sl = (sa if isinstance(sa, int) and sa >= 0 else 0), (sl if isinstance(sl, int) and sl >= 0 else 0)
    got = apply(db, cfg, body.get("acts") or [], body.get("logs") or [], now)
    merge_settings(db, body.get("settings"))
    acts, logs, ca, cl, more = export(db, sa, sl, skip_acts=got["act_ids"], skip_logs=got["log_ids"])
    settings = computed(db, cfg, now)
    settings["pet"] = db.get_setting("pet_w", "") or ""
    settings["last_interaction"] = db.get_setting("last_interaction", "") or ""
    return {"ok": True, "accepted": got["accepted"], "stale": got["stale"], "rejected": got["rejected"][:20], "acts": acts, "logs": logs, "cursor_acts": ca, "cursor_logs": cl, "more": more,
            "settings": settings}
