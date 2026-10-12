import json
import shutil
import sqlite3
import subprocess
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from conftest import mk_activity, utc
from fastapi.testclient import TestClient
from finapp import auth, clock, credits, pairing, spending, sync, tokens
from finapp.config import Config, set_config
from finapp.db import DB, MIGRATIONS, open_db
from finapp.main import create_app

PASS = "correct horse battery"
A1, A2 = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
L1, L2, L3 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3"
NOW = utc("2026-10-12T15:00:00Z")


@pytest.fixture(autouse=True)
def no_real_network_lookups(monkeypatch):
    monkeypatch.setattr(pairing, "tailscale_ip", lambda: None)


def act(uid=A1, name="Ordered in", credits_n=4, est=4000, updated="2026-10-01T00:00:00Z", **kw):
    d = {"id": uid, "name": name, "icon": "takeout", "phrase": "order in", "estimateCents": est, "weight": 3, "creditsPerMonth": credits_n,
         "cadence": {"mode": "even", "time": "09:00", "dows": [], "notify": True}, "sort": 0, "archived": False, "autoCredits": False, "updatedAt": updated}
    d.update(kw)
    return d


def log(uid=L1, aid=A1, ts="2026-10-10T18:00:00Z", **kw):
    d = {"id": uid, "activityId": aid, "ts": ts, "source": "live", "note": None, "undoneAt": None, "updatedAt": ts}
    d.update(kw)
    return d


def test_pouches_and_logs_get_ids_and_rising_revisions(db):
    a = mk_activity(db)
    l = db.ex("INSERT INTO activity_logs(activity_id,ts,source) VALUES(?,?,?)", (a["id"], "2026-10-10T18:00:00Z", "live")).lastrowid
    ra, rl = db.one("SELECT * FROM activities WHERE id=?", (a["id"],)), db.one("SELECT * FROM activity_logs WHERE id=?", (l,))
    assert len(ra["uuid"]) == 36 and len(rl["uuid"]) == 36 and ra["updated_at"] and rl["updated_at"]
    db.ex("UPDATE activity_logs SET undone_at=? WHERE id=?", ("2026-10-10T19:00:00Z", l))
    assert db.one("SELECT rev FROM activity_logs WHERE id=?", (l,))["rev"] > rl["rev"]


def test_the_migration_backfills_ids_for_rows_that_already_exist(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db")
    for f in sorted(MIGRATIONS.glob("*.sql")):
        if f.name < "016":
            conn.executescript(f.read_text())
    conn.execute("INSERT INTO activities(name,created_at) VALUES('a','2026-01-01T00:00:00Z')")
    conn.execute("INSERT INTO activities(name,created_at) VALUES('b','2026-01-01T00:00:00Z')")
    conn.execute("INSERT INTO activity_logs(activity_id,ts) VALUES(1,'2026-10-01T10:00:00Z')")
    conn.commit()
    conn.executescript((MIGRATIONS / "016_sync.sql").read_text())
    assert len({r[0] for r in conn.execute("SELECT uuid FROM activities")}) == 2 and all(len(r[0]) == 36 for r in conn.execute("SELECT uuid FROM activity_logs"))


def test_a_phone_pouch_and_its_taps_merge_in_and_replays_change_nothing(db, cfg):
    r = sync.handle(db, cfg, {"acts": [act()], "logs": [log()]}, NOW)
    assert r["accepted"] == 2 and r["rejected"] == []
    row = db.one("SELECT * FROM activities WHERE uuid=?", (A1,))
    assert (row["credits_per_month"], row["estimate_cents"], row["auto_credits"], row["icon"]) == (4, 4000, 0, "takeout")
    assert db.one("SELECT 1 FROM activity_credit_periods WHERE activity_id=? AND month='2026-10'", (row["id"],)), "this month's schedule is made"
    assert db.val("SELECT COUNT(*) FROM activity_logs") == 1
    again = sync.handle(db, cfg, {"acts": [act()], "logs": [log()]}, NOW)
    assert again["accepted"] == 0 and again["stale"] == 2 and db.val("SELECT COUNT(*) FROM activity_logs") == 1


def test_the_later_edit_wins_and_changing_credits_rebuilds_the_schedule(db, cfg):
    sync.handle(db, cfg, {"acts": [act(credits_n=2)]}, NOW)
    pid = db.val("SELECT id FROM activities WHERE uuid=?", (A1,))
    n0 = len(json.loads(db.val("SELECT unlock_times FROM activity_credit_periods WHERE activity_id=?", (pid,))))
    sync.handle(db, cfg, {"acts": [act(credits_n=6, updated="2026-10-05T00:00:00Z")]}, NOW)
    assert n0 == 2 and len(json.loads(db.val("SELECT unlock_times FROM activity_credit_periods WHERE activity_id=?", (pid,)))) == 6
    r = sync.handle(db, cfg, {"acts": [act(credits_n=1, updated="2026-10-03T00:00:00Z")]}, NOW)
    assert r["stale"] == 1 and db.val("SELECT credits_per_month FROM activities WHERE id=?", (pid,)) == 6
    sync.handle(db, cfg, {"acts": [act(credits_n=6, archived=True, updated="2026-10-06T00:00:00Z")]}, NOW)
    assert db.val("SELECT archived FROM activities WHERE id=?", (pid,)) == 1


def test_undoing_a_tap_on_the_phone_reaches_the_computer(db, cfg):
    sync.handle(db, cfg, {"acts": [act()], "logs": [log()]}, NOW)
    sync.handle(db, cfg, {"logs": [log(undoneAt="2026-10-10T18:05:00Z", updatedAt="2026-10-10T18:05:00Z")]}, NOW)
    assert db.val("SELECT undone_at FROM activity_logs WHERE uuid=?", (L1,)) == "2026-10-10T18:05:00Z"
    sync.handle(db, cfg, {"logs": [log(undoneAt=None, updatedAt="2026-10-10T18:09:00Z")]}, NOW)
    assert db.val("SELECT undone_at FROM activity_logs WHERE uuid=?", (L1,)) is None


def test_bad_rows_are_reported_and_good_ones_still_land(db, cfg):
    r = sync.handle(db, cfg, {"acts": [act(), {"id": "x"}, act("../../etc", "evil"), act(A2, name="  ")], "logs": [log(), log(L2, ts="nope"), log(L3, aid="99999999-9999-4999-8999-999999999999")]}, NOW)
    assert r["accepted"] == 2 and len(r["rejected"]) == 5 and db.val("SELECT COUNT(*) FROM activities") == 1 and db.val("SELECT COUNT(*) FROM activity_logs") == 1
    with pytest.raises(ValueError):
        sync.handle(db, cfg, {"acts": [act()] * (sync.MAX_ROWS + 1)}, NOW)


def test_odd_values_are_cleaned_not_trusted(db, cfg):
    sync.handle(db, cfg, {"acts": [act(icon="<script>", name="x" * 200, creditsPerMonth=9999, weight=77, cadence={"mode": "evil", "time": "25:99", "dows": [1, 9, "x", 3, 3]})]}, NOW)
    r = db.one("SELECT * FROM activities WHERE uuid=?", (A1,))
    assert r["icon"] == "star" and len(r["name"]) == 60 and r["credits_per_month"] == 60 and r["weight"] == 5
    assert json.loads(r["cadence"]) == {"mode": "even", "time": "09:00", "dows": [1, 3], "notify": True}


def test_what_the_computer_knows_comes_back_and_what_the_phone_sent_does_not(db, cfg, monkeypatch):
    a = mk_activity(db, "Drinks out", 3, 6000)
    uid = db.val("SELECT uuid FROM activities WHERE id=?", (a["id"],))
    credits.build_period(db, "2026-10", db.one("SELECT * FROM activities WHERE id=?", (a["id"],)), cfg.tz)
    credits.save_plan(db, "2026-10", buffer_cents=12000)
    monkeypatch.setattr(spending, "stmt_uses", lambda _db, ac, m, tz: [utc("2026-10-04T16:00:00Z")] if (ac["id"] == a["id"] and m == "2026-10") else [])
    r = sync.handle(db, cfg, {"acts": [act()], "logs": [log()]}, NOW)
    assert [x["id"] for x in r["acts"]] == [uid] and r["acts"][0]["name"] == "Drinks out", "its own pouch comes down, the phone's does not come straight back"
    assert r["settings"]["buffer_cents"] == "12000"
    assert json.loads(r["settings"]["stmt_uses"]) == {uid: ["2026-10-04T16:00:00Z"]}
    per = json.loads(r["settings"]["periods"])[f"2026-10|{uid}"]
    assert per["allotted"] == 3 and len(per["unlocks"]) == 3
    nxt = sync.handle(db, cfg, {"since_acts": r["cursor_acts"], "since_logs": r["cursor_logs"]}, NOW)
    assert nxt["acts"] == [] and nxt["logs"] == []
    db.ex("UPDATE activities SET credits_per_month=5 WHERE id=?", (a["id"],))
    again = sync.handle(db, cfg, {"since_acts": r["cursor_acts"], "since_logs": r["cursor_logs"]}, NOW)
    assert [x["creditsPerMonth"] for x in again["acts"]] == [5]


def test_the_newer_wellbeing_and_the_later_open_time_win(db, cfg):
    db.set_setting("pet_w", json.dumps({"w": 0.5, "ts": "2026-10-10T10:00:00Z"}))
    db.set_setting("last_interaction", "2026-10-11T10:00:00Z")
    sync.handle(db, cfg, {"settings": {"pet": json.dumps({"w": -0.2, "ts": "2026-10-09T10:00:00Z"}), "last_interaction": "2026-10-09T10:00:00Z"}}, NOW)
    assert json.loads(db.get_setting("pet_w"))["w"] == 0.5 and db.get_setting("last_interaction") == "2026-10-11T10:00:00Z"
    sync.handle(db, cfg, {"settings": {"pet": json.dumps({"w": -0.2, "ts": "2026-10-12T10:00:00Z"}), "last_interaction": "2026-10-12T14:00:00Z"}}, NOW)
    assert json.loads(db.get_setting("pet_w"))["w"] == -0.2 and db.get_setting("last_interaction") == "2026-10-12T14:00:00Z"
    sync.handle(db, cfg, {"settings": {"pet": "junk", "last_interaction": "junk"}}, NOW)
    assert db.get_setting("last_interaction") == "2026-10-12T14:00:00Z"


@pytest.fixture
def c(tmp_path):
    auth.reset_fails()
    set_config(Config({"FINAPP_DATA_DIR": str(tmp_path), "FINAPP_TZ": "America/Toronto", "FINAPP_REQUIRE_TS_HEADER": "0", "FINAPP_COOKIE_SECURE": "0"}))
    cl = TestClient(create_app(), follow_redirects=False)
    cl.post("/setup", data={"passphrase": PASS, "confirm": PASS})
    cl.dbpath, cl.tmp = tmp_path / "finapp.db", tmp_path
    cl.cookies.clear()
    return cl


def key(c, scope="sync"):
    d = DB(c.dbpath)
    k = tokens.create(d, "My phone", scope)
    d.close()
    return k


def post(c, k, body, **kw):
    return c.post("/api/sync", content=body if isinstance(body, (bytes, str)) else json.dumps(body), headers={"Authorization": "Bearer " + k, "Content-Type": "application/json"}, **kw)


def test_a_phone_key_syncs_and_nothing_else_can(c):
    k = key(c)
    r = post(c, k, {"acts": [act()], "logs": [log()]})
    assert r.status_code == 200 and r.json()["accepted"] == 2
    assert c.post("/api/sync", content="{}").status_code == 401 and post(c, "als_wrong", {}).status_code == 401
    assert post(c, key(c, "widget"), {}).status_code == 401, "the old widget key cannot sync"
    assert c.post("/api/log", content=json.dumps({"activity_id": 1}), headers={"Authorization": "Bearer " + k}).status_code == 401
    assert c.get("/api/state", headers={"Authorization": "Bearer " + k}).status_code == 401
    d = DB(c.dbpath)
    assert d.val("SELECT last_used_at FROM api_tokens WHERE scope='sync' ORDER BY id LIMIT 1") is not None
    d.close()


def test_a_cancelled_phone_is_cut_off_and_bad_requests_are_refused(c):
    k = key(c)
    d = DB(c.dbpath); tokens.revoke(d, d.val("SELECT id FROM api_tokens")); d.close()
    assert post(c, k, {}).status_code == 401
    k2 = key(c)
    assert post(c, k2, "{not json").status_code == 400 and post(c, k2, "[1,2]").status_code == 400
    assert post(c, k2, b"x" * (sync.MAX_BODY + 10)).status_code == 413


def test_the_companion_listener_answers_only_the_sync_route(tmp_path):
    auth.reset_fails()
    base = {"FINAPP_DATA_DIR": str(tmp_path), "FINAPP_TZ": "America/Toronto", "FINAPP_COOKIE_SECURE": "0"}
    set_config(Config(dict(base, FINAPP_REQUIRE_TS_HEADER="0")))
    TestClient(create_app()).post("/setup", data={"passphrase": PASS, "confirm": PASS})
    d = DB(tmp_path / "finapp.db"); k = tokens.create(d, "p", "sync"); d.close()
    set_config(Config(dict(base, FINAPP_SYNC_ONLY="1", FINAPP_REQUIRE_TS_HEADER="1", FINAPP_TS_LOGINS="me@example.com")))
    cl = TestClient(create_app(), follow_redirects=False)
    assert cl.get("/healthz").status_code == 200
    for path in ("/", "/activities", "/login", "/settings/phone", "/static/app.css", "/ledgers"):
        assert cl.get(path).status_code == 404, path
    assert post(cl, k, {"acts": [act()]}).status_code == 200 and post(cl, "als_wrong", {}).status_code == 401


def make_cert(d):
    d.mkdir(parents=True, exist_ok=True)
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30", "-subj", "/CN=t", "-keyout", str(d / "key.pem"), "-out", str(d / "cert.pem")], check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is not installed")
def test_the_pairing_page_makes_a_code_with_every_address_and_the_pin(c, monkeypatch):
    make_cert(c.tmp / "tls")
    monkeypatch.setattr(pairing, "lan_ip", lambda: "192.168.1.20")
    cfg = Config({"FINAPP_DATA_DIR": str(c.tmp)})
    admin = TestClient(create_app(), follow_redirects=False)
    admin.post("/login", data={"passphrase": PASS})
    assert "Pair a phone" in admin.get("/settings/phone").text
    page = admin.post("/settings/phone/new", data={"name": "Pixel"}, headers={"host": "box.tail1234.ts.net:8443"}).text
    assert "<svg" in page and 'class="qr"' in page
    link = next(x for x in page.split('"') if x.startswith("alberich://pair?")).replace("&amp;", "&")
    q = parse_qs(urlparse(link).query)
    assert q["key"][0].startswith("als_") and len(q["e"]) == 2
    home, tail = (unquote(x) for x in q["e"])
    assert home == "https://192.168.1.20:8121#" + pairing.pin(cfg) and tail == "https://box.tail1234.ts.net:8443"
    assert post(c, q["key"][0], {}).status_code == 200
    assert "Pixel" in admin.get("/settings/phone").text and "als_" not in admin.get("/settings/phone").text
    admin.post("/settings/phone/revoke", data={"id": "1"})
    assert post(c, q["key"][0], {}).status_code == 401
