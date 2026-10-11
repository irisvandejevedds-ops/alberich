"""Revocable device tokens for the home-screen widget. They can read pouch state and log or undo, nothing else."""
import hashlib
import secrets
import time

from . import clock

PREFIX = "alw_"
ALLOWED = ("/api/state", "/api/log", "/api/undo")
_fails = []


def too_many_failures():
    cutoff = time.monotonic() - 300
    _fails[:] = [x for x in _fails if x > cutoff]
    return len(_fails) >= 30


def note_failure():
    _fails.append(time.monotonic())


def _h(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def create(db, name):
    raw = PREFIX + secrets.token_urlsafe(32)
    db.ex("INSERT INTO api_tokens(name,token_sha256,created_at) VALUES(?,?,?)", (name[:40] or "Widget", _h(raw), clock.iso(clock.now())))
    return raw


def verify(db, raw):
    if not raw or not raw.startswith(PREFIX) or len(raw) > 100:
        return None
    r = db.one("SELECT * FROM api_tokens WHERE token_sha256=? AND revoked_at IS NULL", (_h(raw),))
    if r:
        db.ex("UPDATE api_tokens SET last_used_at=? WHERE id=?", (clock.iso(clock.now()), r["id"]))
    return r


def revoke(db, tid):
    db.ex("UPDATE api_tokens SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (clock.iso(clock.now()), tid))


def listing(db):
    return db.q("SELECT * FROM api_tokens ORDER BY id DESC")
