"""Notification rules, preferences, Alberich's voice, and the scheduler tick."""
import json
import random

from . import clock, credits, pet, push, required

TYPES = {
    "credit_unlocked": "A credit unlocks", "credit_reminder": "Gentle reminder for an unused credit",
    "pace_warning": "You borrow or overdraw a credit", "neglect_nag": "Alberich grows cross when you ignore him",
    "statements_due": "Statements are due", "receipts_ready": "Receipts finished scanning",
    "review_queue": "Items waiting for a split decision", "month_closed": "A month is closed",
    "pet_state_change": "Alberich's mood changes", "weekly_digest": "Weekly digest",
}
DEFAULT_PREFS = {
    "types": {"credit_unlocked": True, "credit_reminder": False, "pace_warning": True, "neglect_nag": True,
              "statements_due": True, "receipts_ready": True, "review_queue": False, "month_closed": True,
              "pet_state_change": True, "weekly_digest": False},
    "quiet": {"start": "22:00", "end": "08:00"}, "daily_cap": 2, "snooze_until": "", "tone": "pet",
    "nag_ceiling": 5,
}
NAG_AT = [2, 4, 7, 10, 14]       # days of silence at which each level fires
NAG_REPEAT = 3                    # after the last level, repeat every N days
NAG_COPY = {
    1: ["Hmph. Two days, and not one glance at my hoard. I notice these things.",
        "Two days. The coffers sit unattended. I am not pleased."],
    2: ["Four days of silence. I have begun a list of your failings. It is already long.",
        "Four days! Even the Nibelungs report for duty, and they are in chains."],
    3: ["A WEEK. Seven days! Does the Ring mean nothing to you? Open the app, wretch.",
        "Seven days of neglect. My patience is a thin and glittering thing, and it is cracking."],
    4: ["Ten days. I have forged a very small tombstone for your Purse. Open the app, you dawdling dolt.",
        "Ten days! I am sharpening a very small axe. Do not test me."],
    5: ["You absent, ledger-dodging sluggard! The Ring grows heavy with my contempt. OPEN. THE. APP.",
        "FOURTEEN DAYS. I curse your slippers, your spoons, and your every unopened tab. COME HERE."],
}
NAG_PLAIN = "It has been {d} days since you opened Alberich. Check your credits when you can."
UNLOCK_COPY = {
    "today": ["The hoard permits it: you may {p} today. Neither squander it, nor hoard it.",
              "Hmph. Fine. You may {p} today. Within reason.",
              "The golden mean allows it: {p} today. Do not make me regret this."],
    "week": ["The hoard permits it: you may {p} this week. Not too much, not too little.",
             "Hmph. You may {p} this week. Moderation, wretch.",
             "The golden mean allows it: {p} this week. Do not make me regret this."],
}
MOOD_NOTE = {"happy": "Good. Moderation suits you. I am... almost pleased.",
             "neutral": "Nothing to report. The hoard sits untouched. Dull.",
             "sick": "I feel faint. You have overspent, and the coin-sickness takes me.",
             "evil": "THE CURSE AWAKENS. You have spent beyond all reason!"}


def get_prefs(db):
    try:
        stored = json.loads(db.get_setting("notif_prefs", "{}"))
    except Exception:
        stored = {}
    p = json.loads(json.dumps(DEFAULT_PREFS))
    for k, v in stored.items():
        if isinstance(v, dict) and isinstance(p.get(k), dict):
            p[k].update(v)
        else:
            p[k] = v
    return p


def save_prefs(db, prefs):
    db.set_setting("notif_prefs", json.dumps(prefs))


def in_quiet(prefs, now_local):
    s, e = credits.parse_hhmm(prefs["quiet"]["start"]), credits.parse_hhmm(prefs["quiet"]["end"])
    t = now_local.time()
    return (s <= t < e) if s <= e else (t >= s or t < e)


def snoozed(prefs, now):
    return bool(prefs.get("snooze_until")) and clock.iso(now) < prefs["snooze_until"]


def sent_today(db, tz, now):
    day = now.astimezone(tz).date()
    return sum(1 for r in db.q("SELECT sent_at FROM notifications_sent WHERE ok=1 AND rule_key NOT LIKE 'test%'")
               if clock.parse_iso(r["sent_at"]).astimezone(tz).date() == day)


def already(db, rule_key, period_key):
    return db.one("SELECT 1 FROM notifications_sent WHERE rule_key=? AND period_key=?", (rule_key, period_key)) is not None


def record(db, rule_key, period_key, ok, title, body, now):
    db.ex("""INSERT INTO notifications_sent(rule_key,period_key,sent_at,ok,title,body) VALUES(?,?,?,?,?,?)
             ON CONFLICT(rule_key,period_key) DO UPDATE SET sent_at=excluded.sent_at, ok=excluded.ok""",
          (rule_key, period_key, clock.iso(now), int(ok), title, body))


# ---- interaction tracking (drives the neglect nags) ----
def touch(db, now=None):
    db.set_setting("last_interaction", clock.iso(now or clock.now()))


def last_interaction(db):
    v = db.get_setting("last_interaction")
    return clock.parse_iso(v) if v else None


def idle_days(db, now):
    last = last_interaction(db)
    return 0.0 if last is None else max(0.0, (now - last).total_seconds() / 86400)


def nag_stage(days):
    """-> (level 1..5, stage) or None. `stage` is unique per notification within one silence."""
    if days < NAG_AT[0]:
        return None
    level = max(i + 1 for i, d in enumerate(NAG_AT) if days >= d)
    extra = int((days - NAG_AT[-1]) // NAG_REPEAT) if days >= NAG_AT[-1] else 0
    return level, level + extra


def nag_text(level, days, prefs, rng=random):
    level = min(level, max(1, int(prefs.get("nag_ceiling", 5))))
    if prefs["tone"] == "plain":
        return "Alberich", NAG_PLAIN.format(d=int(days)), level
    return "Alberich", rng.choice(NAG_COPY[level]), level


def _phrase(act):
    return (act["phrase"] or f"do {act['name'].lower()}").strip()


def unlock_text(act, spacing_days, prefs, rng=random):
    if prefs["tone"] == "plain":
        return act["name"], f"{act['name']}: one credit available."
    key = "today" if spacing_days <= 3 else "week"
    return "Alberich", rng.choice(UNLOCK_COPY[key]).format(p=_phrase(act))


def last_mood(db):
    r = db.one("SELECT mood FROM pet_state_history ORDER BY id DESC LIMIT 1")
    return r["mood"] if r else "neutral"


def payload(title, body, mood="neutral", tag="", url="/", actions=None, data=None):
    return {"title": title, "body": body, "tag": tag, "icon": f"/static/icons/mood-{mood}.png",
            "badge": "/static/icons/badge.png", "actions": actions or [], "data": {"url": url, **(data or {})}}


def gate(db, cfg, now, prefs, kind):
    if not prefs["types"].get(kind):
        return False
    if snoozed(prefs, now) or in_quiet(prefs, now.astimezone(cfg.tz)):
        return False
    return sent_today(db, cfg.tz, now) < prefs["daily_cap"]


def tick(db, cfg, now=None, sender=push.send):
    """Evaluate every rule once; returns a list of what was sent (for logs and tests)."""
    now = now or clock.now()
    month = credits.month_of(now, cfg.tz)
    prefs = get_prefs(db)
    credits.ensure_periods(db, month, cfg.tz)
    states = credits.all_states(db, month, cfg.tz, now)
    sent = []

    for s in states:
        act = s["activity"]
        cad = credits.cadence_of(act)
        if not cad.get("notify", True) or s["allotted"] <= 0:
            continue
        k = sum(1 for u in s["unlocks"] if u <= now)
        if k == 0:
            continue
        key = f"{month}:{k}"
        if already(db, f"credit_unlocked:{act['id']}", key):
            continue
        if s["available"] <= 0 or s["overdrawn_now"]:
            record(db, f"credit_unlocked:{act['id']}", key, False, "", "skipped: nothing available", now)
            continue
        if not gate(db, cfg, now, prefs, "credit_unlocked"):
            continue
        start, end = credits.month_bounds(month, cfg.tz)
        title, body = unlock_text(act, (end - start).total_seconds() / 86400 / max(1, s["allotted"]), prefs)
        pl = payload(title, body, mood=last_mood(db), tag=f"credit-{act['id']}-{month}",
                     actions=[{"action": "log", "title": "Log it"}, {"action": "later", "title": "Not now"}],
                     data={"activity_id": act["id"], "key": f"push-{act['id']}-{key}"})
        n = push.dispatch(db, cfg, pl, sender, ttl=12 * 3600)
        record(db, f"credit_unlocked:{act['id']}", key, n > 0, title, body, now)
        sent.append(("credit_unlocked", act["id"], n))

    # Alberich's temper: only starts counting from the first moment the app has been used.
    last = last_interaction(db)
    if last is None:
        touch(db, now)
        last = now
    days = idle_days(db, now)
    plan = credits.get_plan(db, month)
    st, changed = pet.current(db, states, plan["buffer_cents"], now, idle_days=days)

    if changed is not None and gate(db, cfg, now, prefs, "pet_state_change"):
        hist = db.one("SELECT id FROM pet_state_history ORDER BY id DESC LIMIT 1")
        if not already(db, "pet_state_change", str(hist["id"])):
            txt = MOOD_NOTE[st["mood"]]
            n = push.dispatch(db, cfg, payload("Alberich", txt, mood=st["mood"], tag="pet-mood", url="/pet"), sender)
            record(db, "pet_state_change", str(hist["id"]), n > 0, "Alberich", txt, now)
            sent.append(("pet_state_change", st["mood"], n))


    # Once a month Alberich asks for the statements; again on the 10th if some are still owed.
    local = now.astimezone(cfg.tz)
    if local.day >= 3:
        _cols, mrows = required.matrix(db, local.date(), 12)
        owed = [(r["account"]["name"], c["month"]) for r in mrows for c in r["cells"] if c["status"] == "missing"]
        stage_key = f"{month}:{'b' if local.day >= 10 else 'a'}"
        if owed and not already(db, "statements_due", stage_key) and gate(db, cfg, now, prefs, "statements_due"):
            first = ", ".join(f"{n} ({m})" for n, m in owed[:3]) + (f" and {len(owed) - 3} more" if len(owed) > 3 else "")
            body = (f"The ledgers lack {len(owed)} statement{'s' if len(owed) != 1 else ''}: {first}. Fetch them, and I shall file them."
                    if prefs.get("tone") != "plain" else f"{len(owed)} statement(s) missing: {first}.")
            pl = payload("Alberich", body, mood=last_mood(db), tag="statements-due", url="/ledgers")
            n = push.dispatch(db, cfg, pl, sender)
            record(db, "statements_due", stage_key, n > 0, "Alberich", body, now)
            sent.append(("statements_due", len(owed), n))

    stage = nag_stage(days)
    if stage:
        level, idx = stage
        key = f"{clock.iso(last)}:{idx}"
        if not already(db, "neglect_nag", key) and gate(db, cfg, now, prefs, "neglect_nag"):
            title, body, eff = nag_text(level, days, prefs)
            pl = payload(title, body, mood="evil" if eff >= 4 else "neutral", tag="nag", url="/",
                         actions=[{"action": "open", "title": "Fine, I'm here"}])
            n = push.dispatch(db, cfg, pl, sender)
            record(db, "neglect_nag", key, n > 0, title, body, now)
            sent.append(("neglect_nag", eff, n))
    return sent


def on_log(db, cfg, activity_id, log_id, now=None, sender=push.send):
    """After a live log: Alberich grumbles once when a credit is borrowed or overdrawn."""
    now = now or clock.now()
    month = credits.month_of(now, cfg.tz)
    act = db.one("SELECT * FROM activities WHERE id=?", (activity_id,))
    s = credits.activity_state(db, act, month, cfg.tz, now)
    if log_id not in s["log_ids"]:
        return None
    status = s["statuses"][s["log_ids"].index(log_id)]
    if status == "on_pace":
        return status
    prefs = get_prefs(db)
    if gate(db, cfg, now, prefs, "pace_warning") and not already(db, "pace_warning", str(log_id)):
        body = ("You draw on next week's coin already? Hmph. Moderation, wretch." if status == "borrowed"
                else "You DARE overspend?! The Ring's curse stirs...")
        n = push.dispatch(db, cfg, payload("Alberich", body, mood="neutral" if status == "borrowed" else "sick",
                                           tag=f"pace-{log_id}"), sender)
        record(db, "pace_warning", str(log_id), n > 0, "Alberich", body, now)
    return status


def receipts_read(db, cfg, count, need_look, now=None, sender=push.send):
    now = now or clock.now()
    prefs = get_prefs(db)
    seen = last_interaction(db)
    if seen and (now - seen).total_seconds() < 180:
        return False                                  # you are looking right at them
    if not gate(db, cfg, now, prefs, "receipts_ready"):
        return False
    body = (f"{count} receipt{'s' if count != 1 else ''} read. " + (f"{need_look} need{'s' if need_look == 1 else ''} your eye." if need_look else "All tidy.")
            if prefs.get("tone") != "plain" else f"{count} receipt(s) scanned, {need_look} need review.")
    pl = payload("Alberich", f"I have deciphered your scribbles. {body}" if prefs.get("tone") != "plain" else body,
                 mood=last_mood(db), tag="receipts", url="/receipts")
    n = push.dispatch(db, cfg, pl, sender)
    record(db, "receipts_ready", clock.iso(now), n > 0, "Alberich", pl["body"], now)
    return n > 0
