import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from finapp import credits, clock
from conftest import mk_activity, utc

TZ = ZoneInfo("America/Toronto")


def local(dts):
    return [d.astimezone(TZ) for d in dts]


def test_even_schedule_september():
    u = local(credits.compute_unlock_times("2026-09", 4, {"mode": "even", "time": "09:00"}, TZ))
    assert [(d.day, d.hour) for d in u] == [(1, 9), (8, 9), (16, 9), (23, 9)]


def test_weekday_schedule_snaps_to_friday_and_stays_increasing():
    u = local(credits.compute_unlock_times("2026-09", 4, {"mode": "weekdays", "dows": [4], "time": "17:00"}, TZ))
    assert all(d.weekday() == 4 for d in u) and all(d.hour == 17 for d in u)
    assert u == sorted(u) and len(set(u)) == 4


def test_dst_fall_back_and_spring_forward_keep_local_time():
    fall = credits.compute_unlock_times("2026-11", 1, {"time": "09:00"}, TZ)[0]
    assert clock.iso(fall) == "2026-11-01T14:00:00Z"        # EST after the 2026-11-01 change
    spring = credits.compute_unlock_times("2027-03", 2, {"time": "09:00"}, TZ)
    assert [clock.iso(x) for x in spring] == ["2027-03-01T14:00:00Z", "2027-03-16T13:00:00Z"]   # EST then EDT


def test_more_credits_than_days_still_strictly_increasing_inside_month():
    u = credits.compute_unlock_times("2026-02", 40, {"time": "09:00"}, TZ)
    start, end = credits.month_bounds("2026-02", TZ)
    assert len(u) == 40 and u == sorted(u) and len(set(u)) == 40
    assert all(start.astimezone(timezone.utc) <= x < end.astimezone(timezone.utc) for x in u)


def test_classification():
    un = [utc("2026-09-01T13:00:00Z"), utc("2026-09-10T13:00:00Z")]
    logs = [utc("2026-09-02T00:00:00Z"), utc("2026-09-03T00:00:00Z"), utc("2026-09-04T00:00:00Z")]
    assert credits.classify_logs(logs, un, allotted=2) == ["on_pace", "borrowed", "overdrawn"]
    assert credits.classify_logs([utc("2026-08-31T00:00:00Z")], un, 2) == ["borrowed"]


def test_allocate_floors_and_returns_leftover():
    out, left = credits.allocate(10000, [{"id": 1, "weight": 3, "estimate_cents": 2000},
                                         {"id": 2, "weight": 3, "estimate_cents": 1000},
                                         {"id": 3, "weight": 3, "estimate_cents": 0}])
    # equal thirds of 10000 = 3333.33 each
    assert out == {1: 1, 2: 3, 3: 0} and left == 10000 - 2000 - 3000


def test_activity_state_available_and_next_unlock(db, cfg):
    a = mk_activity(db, n=4)
    now = utc("2026-09-10T00:00:00Z")                # after the 1st and 8th unlocks, before the 16th
    s = credits.activity_state(db, a, "2026-09", cfg.tz, now)
    assert (s["allotted"], s["unlocked"], s["available"], s["used"], s["remaining"]) == (4, 2, 2, 0, 4)
    assert clock.iso(s["next_at"]) == "2026-09-16T13:00:00Z"        # the 16th, 09:00 local
    for i in range(3):
        db.ex("INSERT INTO activity_logs(activity_id,ts,idempotency_key) VALUES(?,?,?)",
              (a["id"], f"2026-09-0{i + 3}T12:00:00Z", f"k{i}"))
    s = credits.activity_state(db, a, "2026-09", cfg.tz, now)
    assert s["used"] == 3 and s["available"] == 0 and s["borrowed"] == 2 and s["on_pace"] == 1   # only the 1st credit had unlocked
    db.ex("UPDATE activity_logs SET undone_at='2026-09-09T00:00:00Z' WHERE idempotency_key='k2'")
    s = credits.activity_state(db, a, "2026-09", cfg.tz, now)
    assert s["used"] == 2 and s["borrowed"] == 1 and s["available"] == 0


def test_overdraw_and_pace_inputs(db, cfg):
    a = mk_activity(db, n=1, est=5000)
    now = utc("2026-09-20T00:00:00Z")
    for i in range(3):
        db.ex("INSERT INTO activity_logs(activity_id,ts,idempotency_key) VALUES(?,?,?)",
              (a["id"], f"2026-09-1{i}T12:00:00Z", f"x{i}"))
    st = credits.all_states(db, "2026-09", cfg.tz, now)
    od, bc = credits.pace_inputs(st)
    assert st[0]["overdrawn"] == 2 and od == 10000 and bc == 0 and st[0]["overdrawn_now"]
