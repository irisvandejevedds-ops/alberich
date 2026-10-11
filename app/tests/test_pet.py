from finapp import clock, pet
from conftest import mk_activity, utc


def S(used=0, unlocked=0, allotted=4, od=0, est=6000):
    return {"used": used, "unlocked": unlocked, "allotted": allotted, "overdrawn": od, "estimate_cents": est}


def test_zones_follow_the_golden_mean():
    assert pet.zone(*pet.spend_inputs([S(0, 3)]), 10000) == "idle"             # not spending
    assert pet.zone(*pet.spend_inputs([S(0, 0)]), 10000) == "idle"             # nothing unlocked yet
    assert pet.zone(*pet.spend_inputs([S(2, 3)]), 10000) == "moderate"         # spending within reason
    assert pet.zone(*pet.spend_inputs([S(4, 4)]), 10000) == "moderate"         # all of it, still inside the plan
    assert pet.zone(*pet.spend_inputs([S(5, 4, od=1)]), 10000) == "over"       # one over
    assert pet.zone(*pet.spend_inputs([S(6, 4, od=2)]), 10000) == "excess"     # 12000 >= buffer
    assert pet.zone(*pet.spend_inputs([S(5, 4, od=1)]), 0) == "over"           # no buffer: one over is only "over"
    assert pet.zone(*pet.spend_inputs([S(6, 4, od=2)]), 0) == "excess"


def test_borrowing_inside_the_allotment_is_not_overspending():
    st = [S(used=3, unlocked=1, allotted=4)]                                   # used ahead of the schedule
    assert pet.zone(*pet.spend_inputs(st), 5000) == "moderate"


def test_sickness_is_immediate_and_recovery_is_gradual():
    w = 1.0
    w = pet.step(w, pet.CFG["target"]["over"], 0)
    assert w == -0.6 and pet.band(w) == "sick"
    w = pet.step(w, pet.CFG["target"]["excess"], 0)
    assert w == -1.0 and pet.band(w) == "evil"
    moods = []
    for _ in range(8):                                                         # a week of moderation
        w = pet.step(w, pet.CFG["target"]["moderate"], 1)
        moods.append(pet.band(w))
    assert moods[0] == "sick" and "neutral" in moods and moods[-1] == "happy"
    assert moods.index("neutral") > 1                                          # not instantly forgiven


def test_happiness_fades_to_neutral_without_spending():
    w = 1.0
    for _ in range(4):
        w = pet.step(w, pet.CFG["target"]["idle"], 1)
    assert pet.band(w) == "neutral"
    assert pet.step(0.0, 0.0, 5) == 0.0


def test_hysteresis_prevents_flapping():
    assert pet.mood_for(0.52, "neutral") == "neutral"      # needs 0.55 to climb
    assert pet.mood_for(0.56, "neutral") == "happy"
    assert pet.mood_for(0.47, "happy") == "happy"          # stays until it falls clearly
    assert pet.mood_for(0.40, "happy") == "neutral"
    assert pet.mood_for(-0.9, "happy") == "evil"


def test_compute_persists_wellbeing_and_logs_transitions(db, cfg):
    now = utc("2026-09-10T14:00:00Z")
    st, ch = pet.current(db, [S(2, 3)], 5000, now)
    assert st["mood"] == "neutral" and ch is None                              # first ever record, starts neutral
    st, ch = pet.current(db, [S(2, 3)], 5000, utc("2026-09-12T14:00:00Z"))
    assert st["mood"] == "happy" and ch == "neutral"                          # two days of moderation
    st, ch = pet.current(db, [S(6, 3, od=2)], 5000, utc("2026-09-12T15:00:00Z"))
    assert st["mood"] == "evil" and ch == "happy"
    assert db.val("SELECT COUNT(*) FROM pet_state_history") == 3


def test_annoyed_only_applies_to_a_neutral_neglected_pet(db, cfg):
    st = pet.compute(db, [], 0, utc("2026-09-10T14:00:00Z"), idle_days=3)
    assert st["mood"] == "neutral" and st["annoyed"]
    st = pet.compute(db, [], 0, utc("2026-09-10T14:00:00Z"), idle_days=0.5)
    assert not st["annoyed"]


def test_a_one_time_reset_clears_a_stuck_mood_and_only_runs_once(db):
    now = clock.parse_iso("2026-10-10T20:00:00Z")
    pet._save(db, -0.95, now)
    assert pet.reset_once(db, now, "pet_reset_test") is True
    assert pet._load(db)[0] == 0.0 and pet.band(0.0) == "neutral"
    pet._save(db, -0.5, now)
    assert pet.reset_once(db, now, "pet_reset_test") is False and pet._load(db)[0] == -0.5


def _states(allotted, used, est=1000, unlocked=None):
    return [{"used": used, "estimate_cents": est, "unlocked": allotted if unlocked is None else unlocked, "allotted": allotted, "overdrawn": max(0, used - allotted)}]


def test_a_short_overspend_that_clears_is_forgiven_but_a_lasting_one_is_not(db):
    t0 = clock.parse_iso("2026-10-10T12:00:00Z")
    hours = lambda h: t0 + __import__("datetime").timedelta(hours=h)
    pet._save(db, 0.6, t0)
    assert pet.compute(db, _states(6, 3), 0, t0)["mood"] == "happy"
    bad = pet.compute(db, _states(6, 9), 0, hours(1))                       # a bad count: overdrawn by three
    assert bad["zone"] in ("over", "excess") and bad["w"] <= -0.6 and bad["mood"] in ("sick", "evil")
    ok = pet.compute(db, _states(6, 3), 0, hours(2))                         # corrected an hour later
    assert ok["zone"] == "moderate" and ok["w"] >= 0.6 and ok["mood"] == "happy"
    pet.compute(db, _states(6, 9), 0, hours(3))                              # overspent again and this time it stays
    still = pet.compute(db, _states(6, 9), 0, hours(12))
    back = pet.compute(db, _states(6, 3), 0, hours(13))
    assert still["w"] <= -0.6 and back["w"] < 0 and back["mood"] != "happy"   # more than six hours: the penalty stands and recovers slowly
