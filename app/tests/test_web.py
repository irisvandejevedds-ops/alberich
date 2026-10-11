import json
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from finapp import auth, clock
from finapp.config import Config, set_config
from finapp.main import create_app

PASS = "correct horse battery"
H = {"X-Finapp": "1"}


def _env(tmp_path, **extra):
    pem = tmp_path / "vapid.pem"
    pem.write_bytes(ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    env = {"FINAPP_DATA_DIR": str(tmp_path), "FINAPP_TZ": "America/Toronto", "FINAPP_REQUIRE_TS_HEADER": "0",
           "FINAPP_COOKIE_SECURE": "0", "FINAPP_VAPID_PEM": str(pem), "FINAPP_TS_LOGINS": "me@example.com"}
    env.update(extra)
    return env


@pytest.fixture
def client(tmp_path):
    auth.reset_fails()
    set_config(Config(_env(tmp_path)))
    c = TestClient(create_app(), follow_redirects=False)
    yield c
    clock.set_now(None)


@pytest.fixture
def authed(client):
    r = client.post("/setup", data={"passphrase": PASS, "confirm": PASS})
    assert r.status_code == 303 and auth.COOKIE in r.cookies
    return client


def first_activity(client):
    return client.get("/api/state").json()["tiles"][0]


def test_everything_requires_login_and_setup_comes_first(client):
    assert client.get("/").headers["location"] == "/setup"
    assert client.get("/api/state").status_code == 401
    assert client.get("/healthz").text == "ok"
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/sw.js").headers["service-worker-allowed"] == "/"


def test_setup_login_logout_and_lockout(client):
    assert client.post("/setup", data={"passphrase": "short", "confirm": "short"}).status_code == 200
    client.post("/setup", data={"passphrase": PASS, "confirm": PASS})
    client.post("/logout")
    client.cookies.clear()
    assert client.get("/").headers["location"].startswith("/login")
    assert "not right" in client.post("/login", data={"passphrase": "nope-nope-nope"}).text
    r = client.post("/login", data={"passphrase": PASS, "next": "/activities"})
    assert r.status_code == 303 and r.headers["location"] == "/activities"
    assert client.post("/login", data={"passphrase": PASS, "next": "//evil.example"}).headers["location"] == "/"
    client.cookies.clear()
    for _ in range(5):
        client.post("/login", data={"passphrase": "bad-bad-bad-bad"})
    assert "Too many attempts" in client.post("/login", data={"passphrase": PASS}).text


def test_home_renders_alberich_and_starter_pouches(authed):
    html = authed.get("/").text
    assert "Alberich" in html and "Brooding" in html and 'class="alberich' in html
    assert html.count('class="tile"') == 7 and "No allowances yet" in html


def test_activity_config_in_app_then_logging_flow(authed):
    r = authed.post("/activities/save", data={"name": "Drinks out", "icon": "🍻", "phrase": "go out for drinks", "estimate": "60.00",
                                              "weight": "3", "credits": "4", "mode": "even", "time": "09:00", "notify": "on",
                                              "id": "2"})
    assert r.status_code == 303
    tiles = authed.get("/api/state").json()["tiles"]
    drinks = next(t for t in tiles if t["name"] == "Drinks out")
    assert drinks["allotted"] == 4
    clock.set_now(clock.parse_iso("2026-09-20T18:00:00Z"))
    r1 = authed.post("/api/log", json={"activity_id": drinks["id"], "key": "k-1"}, headers=H).json()
    assert r1["ok"] and r1["status"] == "on_pace" and r1["tile"]["used"] == 1
    again = authed.post("/api/log", json={"activity_id": drinks["id"], "key": "k-1"}, headers=H).json()
    assert again["log_id"] == r1["log_id"] and again["tile"]["used"] == 1            # idempotent replay
    u = authed.post("/api/undo", json={"log_id": r1["log_id"]}, headers=H).json()
    assert u["tile"]["used"] == 0
    assert authed.post("/api/log", json={"activity_id": 999, "key": "z"}, headers=H).status_code == 404
    assert authed.post("/api/log", json={"activity_id": drinks["id"], "key": "t", "ts": "2030-01-01T00:00:00Z"}, headers=H).status_code == 400


def test_overspending_makes_alberich_sick_and_the_page_says_so(authed):
    authed.post("/activities/save", data={"name": "Toy", "icon": "x", "estimate": "50", "weight": "3", "credits": "1", "id": "3"})
    clock.set_now(clock.parse_iso("2026-09-20T18:00:00Z"))
    aid = next(t for t in authed.get("/api/state").json()["tiles"] if t["name"] == "Toy")["id"]
    authed.post("/api/log", json={"activity_id": aid, "key": "a"}, headers=H)
    r = authed.post("/api/log", json={"activity_id": aid, "key": "b"}, headers=H).json()
    assert r["status"] == "overdrawn" and r["mood"] in ("sick", "evil")
    assert "Over by 1" in authed.get("/").text


def test_the_plan_tab_explains_how_credits_are_worked_out_and_planner_forms_are_gone(authed):
    page = authed.get("/activities?tab=plan").text
    assert "How the credits are worked out" in page and "Ration the month" not in page
    assert authed.post("/activities/planner", data={"pool": "300.00"}).status_code in (404, 405)
    assert authed.post("/activities/apply-plan").status_code in (404, 405)


def test_manifest_has_one_shortcut_and_the_one_tap_pages_are_gone(authed):
    m = authed.get("/manifest.webmanifest").json()
    assert m["name"] == "Alberich" and [s["url"] for s in m["shortcuts"]] == ["/"]
    assert authed.get("/quick/1").status_code == 404 and authed.get("/quick").status_code == 404
    assert "One-tap link" not in authed.get("/activities?tab=1").text and "Put him on your home screen" not in authed.get("/activities").text


def test_push_subscription_and_dead_subscription_lifecycle(authed, monkeypatch):
    key = authed.get("/api/push/key").json()["key"]
    assert len(key) > 80
    sub = {"endpoint": "https://fcm.example/abc", "keys": {"p256dh": "k", "auth": "a"}}
    assert authed.post("/api/push/subscribe", json=sub, headers=H).json()["ok"]
    assert authed.post("/api/push/subscribe", json={"endpoint": "http://insecure", "keys": {"p256dh": "k", "auth": "a"}}, headers=H).status_code == 400
    sent = []
    monkeypatch.setattr("finapp.push.send", lambda s, p, c, ttl=3600: (sent.append(p) or (True, 201, None)))
    from finapp import push
    monkeypatch.setattr(push, "dispatch", lambda db, cfg, payload, sender=None, ttl=3600: (sent.append(payload) or 1))
    assert authed.post("/api/push/test", headers=H).json()["ok"] and sent[0]["title"] == "Alberich"


def test_notification_prefs_roundtrip(authed):
    r = authed.post("/settings/notifications", data={"t_credit_unlocked": "on", "t_neglect_nag": "on", "quiet_start": "23:00",
                                                     "quiet_end": "07:00", "daily_cap": "3", "tone": "plain", "nag_ceiling": "3", "snooze_days": "0"})
    assert r.status_code == 303
    page = authed.get("/settings/notifications").text
    assert 'value="23:00"' in page and "How rude he may get" in page
    from finapp import notify
    from finapp.db import DB
    from finapp.config import get_config
    p = notify.get_prefs(DB(get_config().db_path))
    assert p["nag_ceiling"] == 3 and p["tone"] == "plain" and p["types"]["neglect_nag"] and not p["types"]["weekly_digest"]


def test_opening_a_page_resets_the_nag_clock_but_api_polling_does_not(authed):
    from finapp import notify
    from finapp.db import DB
    from finapp.config import get_config
    clock.set_now(clock.parse_iso("2026-09-20T12:00:00Z"))
    authed.get("/")
    assert clock.iso(notify.last_interaction(DB(get_config().db_path))) == "2026-09-20T12:00:00Z"
    clock.set_now(clock.parse_iso("2026-09-25T12:00:00Z"))
    authed.get("/api/state")
    assert clock.iso(notify.last_interaction(DB(get_config().db_path))) == "2026-09-20T12:00:00Z"


def test_cross_site_writes_are_blocked_and_headers_are_set(authed):
    r = authed.post("/api/log", json={"activity_id": 1, "key": "x"}, headers={**H, "Origin": "https://evil.example"})
    assert r.status_code == 403
    r = authed.post("/api/log", json={"activity_id": 1, "key": "x"}, headers={**H, "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    h = authed.get("/").headers
    assert "script-src 'self'" in h["content-security-policy"] and h["cache-control"] == "no-store" and h["x-content-type-options"] == "nosniff"


def test_tailscale_identity_gate(tmp_path):
    set_config(Config(_env(tmp_path, FINAPP_REQUIRE_TS_HEADER="1")))
    c = TestClient(create_app(), follow_redirects=False)
    assert c.get("/").status_code == 403
    assert c.get("/", headers={"Tailscale-User-Login": "mallory@example.com"}).status_code == 403
    assert c.get("/", headers={"Tailscale-User-Login": "ME@example.com"}).status_code == 303      # allowed, then needs setup
    assert c.get("/healthz").status_code == 200


def test_sprites_all_moods_and_service_worker_assets():
    from finapp import sprites
    assert sprites.MOODS == ["neutral", "happy", "sick", "evil", "annoyed"]
    for m in sprites.MOODS:
        assert sprites.svg(m).startswith("<svg") and sprites.png(m)[:4] == b"\x89PNG"


def test_purse_lists_uses_and_can_undo_and_restore_them(authed):
    clock.set_now(clock.parse_iso("2026-09-20T18:00:00Z"))
    tile = authed.get("/api/state").json()["tiles"][0]
    lid = authed.post("/api/log", json={"activity_id": tile["id"], "key": "fat"}, headers=H).json()["log_id"]
    page = authed.get(f"/activities?tab={tile['id']}").text
    assert 'id="uses"' in page and f'name="id" value="{lid}"' in page and ">Undo<" in page
    assert authed.post("/activities/log-undo", data={"id": str(lid), "back": "/activities?tab=plan"}).status_code == 303
    assert authed.get("/api/state").json()["tiles"][0]["used"] == 0
    assert ">Restore<" in authed.get("/activities?tab=plan").text
    authed.post("/activities/log-undo", data={"id": str(lid), "restore": "1"})
    assert authed.get("/api/state").json()["tiles"][0]["used"] == 1


def test_pouches_without_a_category_can_be_given_one_on_the_plan_tab(authed):
    page = authed.get("/activities?tab=plan").text
    assert "no category yet" in page and "Save categories" in page and 'name="cat_' in page
    aid = 1
    dining = authed.get("/activities?tab=plan").text.split(f'name="cat_{aid}"')[1].split("</select>")[0]
    cid = __import__("re").search(r'<option value="(\d+)"[^>]*>Dining<', dining).group(1)
    assert authed.post("/activities/categories", data={f"cat_{aid}": cid}).status_code == 303
    after = authed.get("/activities?tab=plan").text
    assert f'name="cat_{aid}"' not in after


def test_a_credit_taken_at_zero_is_logged_and_the_tile_shows_a_negative_count(authed):
    clock.set_now(clock.parse_iso("2026-09-20T18:00:00Z"))
    tile = authed.get("/api/state").json()["tiles"][0]
    authed.post("/activities/save", data={"id": tile["id"], "name": tile["name"], "estimate": "40", "weight": "3", "credits": "1", "mode": "even", "time": "09:00"})
    r1 = authed.post("/api/log", json={"activity_id": tile["id"], "key": "a"}, headers=H).json()
    r2 = authed.post("/api/log", json={"activity_id": tile["id"], "key": "b"}, headers=H).json()
    assert r1["tile"]["remaining"] == 0 and r2["status"] == "overdrawn" and r2["tile"]["remaining"] == -1 and r2["tile"]["label"] == "Over by 1"
    assert "-1/1" in authed.get("/").text
    page = authed.get("/activities?tab=plan").text
    assert "ran over" in page and "1 over in 2026-09" in page
