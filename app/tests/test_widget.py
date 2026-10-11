import json
import pytest
from fastapi.testclient import TestClient
from finapp import auth, clock, tokens
from finapp.config import Config, set_config
from finapp.main import create_app
from finapp.db import DB
from test_web import _env, PASS


@pytest.fixture
def c(tmp_path):
    auth.reset_fails()
    set_config(Config(_env(tmp_path)))
    cl = TestClient(create_app(), follow_redirects=False)
    assert cl.post("/setup", data={"passphrase": PASS, "confirm": PASS}).status_code == 303
    cl.post("/activities/save", data={"id": "1", "name": "Ordered in", "estimate": "40", "weight": "3", "credits": "4", "mode": "even"})
    yield cl
    clock.set_now(None)


def new_token(c, tmp_path_cfg=None):
    r = c.post("/settings/widget/new", data={"name": "Pixel"})
    assert r.status_code == 200 and "intent://setup" in r.text
    import re
    return re.search(r'value="(alw_[^"]+)"', r.text).group(1)


def anon():
    return TestClient(create_app(), follow_redirects=False)


def test_token_can_read_state_log_and_undo_but_nothing_else(c):
    tok = new_token(c)
    a, H = anon(), {"Authorization": f"Bearer {tok}"}
    st = a.get("/api/state", headers=H)
    assert st.status_code == 200 and st.json()["tiles"][0]["name"] == "Ordered in"
    r = a.post("/api/log", json={"activity_id": 1, "key": "k1"}, headers=H)
    assert r.status_code == 200 and r.json()["tile"]["used"] == 1
    assert a.post("/api/log", json={"activity_id": 1, "key": "k1"}, headers=H).json()["log_id"] == r.json()["log_id"]
    assert a.get("/api/state").status_code == 401
    assert a.post("/api/undo", json={"log_id": r.json()["log_id"]}, headers=H).status_code == 200
    for path in ("/api/push/key", "/", "/ledgers", "/settings/widget"):
        assert a.get(path, headers=H).status_code in (401, 303), path


def test_wrong_and_revoked_tokens_are_refused(c):
    tok = new_token(c)
    a = anon()
    assert a.get("/api/state", headers={"Authorization": "Bearer alw_nope"}).status_code == 401
    assert a.get("/api/state", headers={"Authorization": "Bearer " + tok[:-1] + "x"}).status_code == 401
    kid = DB(__import__("finapp.config", fromlist=["get_config"]).get_config().db_path).val("SELECT id FROM api_tokens")
    assert c.post("/settings/widget/revoke", data={"id": kid}).status_code == 303
    assert a.get("/api/state", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_raw_token_is_not_stored(c):
    tok = new_token(c)
    from finapp.config import get_config
    db = DB(get_config().db_path)
    assert db.val("SELECT COUNT(*) FROM api_tokens WHERE token_sha256=?", (tok,), 0) == 0
    assert tokens.verify(db, tok) is not None


def test_repeated_bad_tokens_get_rate_limited(c):
    tokens._fails.clear()
    a = anon()
    for _ in range(30):
        a.get("/api/state", headers={"Authorization": "Bearer alw_bad"})
    assert a.get("/api/state", headers={"Authorization": "Bearer alw_bad"}).status_code == 429
    tokens._fails.clear()


def test_widget_page_and_apk_download(c):
    assert c.get("/settings/widget").status_code == 200
    assert c.get("/download/alberich-widget.apk").status_code == 404
    from finapp.config import get_config
    d = get_config().data_dir / "apk"
    d.mkdir(parents=True, exist_ok=True)
    (d / "alberich-widget.apk").write_bytes(b"PK\x03\x04fake")
    r = c.get("/download/alberich-widget.apk")
    assert r.status_code == 200 and r.headers["content-type"] == "application/vnd.android.package-archive"
    assert anon().get("/download/alberich-widget.apk").status_code == 303
