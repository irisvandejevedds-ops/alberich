import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
from zoneinfo import ZoneInfo
from finapp import clock
from finapp.config import Config, set_config
from finapp.db import open_db


@pytest.fixture
def cfg(tmp_path):
    c = Config({"FINAPP_DATA_DIR": str(tmp_path), "FINAPP_TZ": "America/Toronto", "FINAPP_REQUIRE_TS_HEADER": "0",
                "FINAPP_COOKIE_SECURE": "0", "FINAPP_FILE_KEY": "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
                "FINAPP_TS_LOGINS": "me@example.com"})
    set_config(c)
    return c


@pytest.fixture
def db(cfg):
    d = open_db(cfg.db_path)
    yield d
    d.close()
    clock.set_now(None)


def mk_activity(db, name="Drinks out", n=4, est=6000, cadence="{}", phrase="go out for drinks", **kw):
    cur = db.ex("""INSERT INTO activities(name,icon,phrase,estimate_cents,weight,credits_per_month,cadence,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (name, "", phrase, est, kw.get("weight", 3), n, cadence, "2026-01-01T00:00:00Z"))
    return db.one("SELECT * FROM activities WHERE id=?", (cur.lastrowid,))


def utc(s):
    return clock.parse_iso(s)
