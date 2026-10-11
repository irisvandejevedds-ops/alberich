import json
from finapp import notify, push, credits, clock
from conftest import mk_activity, utc


class Sender:
    def __init__(self, ok=True, status=201):
        self.sent, self.ok, self.status = [], ok, status

    def __call__(self, sub, payload, cfg, ttl=3600):
        self.sent.append(payload)
        return self.ok, self.status, None if self.ok else "boom"


def add_sub(db):
    db.ex("INSERT INTO push_subscriptions(endpoint,p256dh,auth,created_at) VALUES('https://x/1','k','a','2026-01-01T00:00:00Z')")


def test_unlock_notification_sent_once_and_in_pet_voice(db, cfg):
    add_sub(db)
    a = mk_activity(db, n=4)
    s = Sender()
    now = utc("2026-09-01T14:30:00Z")                 # 10:30 local, first unlock at 09:00 local passed
    out = notify.tick(db, cfg, now, s)
    assert [o[0] for o in out] == ["credit_unlocked"] and len(s.sent) == 1
    assert "go out for drinks" in s.sent[0]["body"] and s.sent[0]["title"] == "Alberich"
    assert [x["action"] for x in s.sent[0]["actions"]] == ["log", "later"]
    notify.tick(db, cfg, now, s)
    assert len(s.sent) == 1                           # idempotent


def test_quiet_hours_defer_and_cap_limits(db, cfg):
    add_sub(db)
    mk_activity(db, "A", n=30, cadence=json.dumps({"time": "00:30"}))
    s = Sender()
    # 02:00 local is inside quiet hours (22:00-08:00): nothing sent
    assert notify.tick(db, cfg, utc("2026-09-15T06:00:00Z"), s) == [] and not s.sent
    # 09:00 local: allowed
    assert len(notify.tick(db, cfg, utc("2026-09-15T13:00:00Z"), s)) == 1


def test_daily_cap(db, cfg):
    add_sub(db)
    for i in range(4):
        mk_activity(db, f"Act{i}", n=4)
    s = Sender()
    notify.tick(db, cfg, utc("2026-09-01T14:30:00Z"), s)
    assert len(s.sent) == 2                           # default cap of 2 per day


def test_skips_when_overdrawn_or_disabled(db, cfg):
    add_sub(db)
    a = mk_activity(db, n=1)
    for i in range(2):
        db.ex("INSERT INTO activity_logs(activity_id,ts,idempotency_key) VALUES(?,?,?)", (a["id"], f"2026-09-02T1{i}:00:00Z", f"q{i}"))
    s = Sender()
    notify.tick(db, cfg, utc("2026-09-03T14:30:00Z"), s)
    assert not [p for p in s.sent if "credit" in p["tag"]]
    b = mk_activity(db, "Toy", n=1)
    p = notify.get_prefs(db); p["types"]["credit_unlocked"] = False; notify.save_prefs(db, p)
    notify.tick(db, cfg, utc("2026-09-04T14:30:00Z"), s)
    assert not [p for p in s.sent if f"credit-{b['id']}" in p["tag"]]


def test_snooze_and_plain_tone(db, cfg):
    add_sub(db)
    mk_activity(db, n=4)
    p = notify.get_prefs(db); p["snooze_until"] = "2026-09-05T00:00:00Z"; p["tone"] = "plain"; notify.save_prefs(db, p)
    s = Sender()
    assert notify.tick(db, cfg, utc("2026-09-01T14:30:00Z"), s) == []
    out = notify.tick(db, cfg, utc("2026-09-05T14:30:00Z"), s)
    assert out and s.sent[0]["body"] == "Drinks out: one credit available."


def test_dead_subscription_is_deactivated(db, cfg):
    add_sub(db)
    n = push.dispatch(db, cfg, {"title": "t"}, Sender(ok=False, status=410))
    assert n == 0 and db.val("SELECT active FROM push_subscriptions") == 0


def test_pace_warning_on_borrowed_log(db, cfg):
    add_sub(db)
    a = mk_activity(db, n=1)
    now = utc("2026-09-02T15:00:00Z")
    lid1 = db.ex("INSERT INTO activity_logs(activity_id,ts,idempotency_key) VALUES(?,?,?)", (a["id"], "2026-09-02T14:00:00Z", "a")).lastrowid
    s = Sender()
    assert notify.on_log(db, cfg, a["id"], lid1, now, s) == "on_pace" and not s.sent
    lid2 = db.ex("INSERT INTO activity_logs(activity_id,ts,idempotency_key) VALUES(?,?,?)", (a["id"], "2026-09-02T14:30:00Z", "b")).lastrowid
    assert notify.on_log(db, cfg, a["id"], lid2, now, s) == "overdrawn" and len(s.sent) == 1


def test_file_encryption_roundtrip(cfg, tmp_path):
    from finapp import crypto
    name = crypto.store(cfg.file_key, tmp_path / "f", b"secret statement")
    assert crypto.load(cfg.file_key, tmp_path / "f", name) == b"secret statement"
    assert b"secret" not in (tmp_path / "f" / name).read_bytes()


def test_nag_stages_escalate_then_repeat():
    assert notify.nag_stage(1.9) is None
    assert notify.nag_stage(2.0) == (1, 1) and notify.nag_stage(5) == (2, 2) and notify.nag_stage(8) == (3, 3)
    assert notify.nag_stage(11) == (4, 4) and notify.nag_stage(14) == (5, 5)
    assert notify.nag_stage(17.5) == (5, 6) and notify.nag_stage(21) == (5, 7)       # keeps nagging every 3 days


def test_nag_sends_once_per_stage_and_escalates(db, cfg):
    add_sub(db)
    s = Sender()
    t0 = utc("2026-09-01T14:00:00Z")
    notify.tick(db, cfg, t0, s)                                   # first run just starts the clock
    assert not [p for p in s.sent if p["tag"] == "nag"]
    day = lambda d: utc(f"2026-09-{1 + d:02d}T14:00:00Z")
    notify.tick(db, cfg, day(1), s)
    assert not [p for p in s.sent if p["tag"] == "nag"]            # one day of silence is fine
    notify.tick(db, cfg, day(2), s); notify.tick(db, cfg, day(2), s)
    assert len([p for p in s.sent if p["tag"] == "nag"]) == 1      # level 1, once
    notify.tick(db, cfg, day(4), s)
    notify.tick(db, cfg, day(14), s)
    nags = [p for p in s.sent if p["tag"] == "nag"]
    assert len(nags) >= 3 and nags[-1]["body"] in notify.NAG_COPY[5] and nags[-1]["icon"].endswith("mood-evil.png")
    assert nags[0]["body"] in notify.NAG_COPY[1]


def test_interaction_resets_the_nag_clock(db, cfg):
    add_sub(db)
    s = Sender()
    notify.tick(db, cfg, utc("2026-09-01T14:00:00Z"), s)
    notify.touch(db, utc("2026-09-05T14:00:00Z"))                  # you showed up
    notify.tick(db, cfg, utc("2026-09-06T14:00:00Z"), s)
    assert not [p for p in s.sent if p["tag"] == "nag"]


def test_nag_ceiling_and_toggle_and_plain_tone(db, cfg):
    add_sub(db)
    p = notify.get_prefs(db); p["nag_ceiling"] = 2; notify.save_prefs(db, p)
    s = Sender()
    notify.tick(db, cfg, utc("2026-09-01T14:00:00Z"), s)
    notify.tick(db, cfg, utc("2026-09-15T14:00:00Z"), s)
    nag = [x for x in s.sent if x["tag"] == "nag"][-1]
    assert nag["body"] in notify.NAG_COPY[2]                       # never gets ruder than the ceiling
    p = notify.get_prefs(db); p["types"]["neglect_nag"] = False; notify.save_prefs(db, p)
    before = len(s.sent)
    notify.tick(db, cfg, utc("2026-09-30T14:00:00Z"), s)
    assert not [x for x in s.sent[before:] if x["tag"] == "nag"]
    assert "days since you opened Alberich" in notify.nag_text(3, 8, {"tone": "plain", "nag_ceiling": 5})[1]


def test_statements_due_nudges_once_then_again_on_the_tenth(db, cfg):
    from finapp import clock as ck
    db.ex("""INSERT INTO accounts(institution,name,type,owner,last4,track_from,created_at) VALUES('B','Cheq','chequing','me','','2026-07','2026-07-01T00:00:00Z')""")
    snd = Sender()
    db.ex("INSERT INTO push_subscriptions(endpoint,p256dh,auth,created_at) VALUES('https://x/1','p','a','2026-01-01T00:00:00Z')")
    def kinds(now):
        return [x[0] for x in notify.tick(db, cfg, utc(now), sender=snd)]
    assert "statements_due" not in kinds("2026-10-02T15:00:00Z")
    assert "statements_due" in kinds("2026-10-04T15:00:00Z")
    assert "statements_due" not in kinds("2026-10-05T15:00:00Z")
    assert "statements_due" in kinds("2026-10-12T15:00:00Z")
