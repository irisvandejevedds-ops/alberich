"""Revocable keys. A widget key (alw_, the old server-driven widget) can read pouch state and log or undo. A phone key (als_) belongs to a paired phone and
can reach only the one sync route."""
import hashlib
import secrets
import time

from . import clock

SCOPES = {"widget": ("alw_", ("/api/state", "/api/log", "/api/undo")), "sync": ("als_", ("/api/sync",))}
PREFIX = SCOPES["widget"][0]
ALLOWED = tuple(p for _, paths in SCOPES.values() for p in paths)
_fails = []


def too_many_failures():
    cutoff = time.monotonic() - 300
    _fails[:] = [x for x in _fails if x > cutoff]
    return len(_fails) >= 30


def note_failure():
    _fails.append(time.monotonic())


def _h(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def create(db, name, scope="widget"):
    raw = SCOPES[scope][0] + secrets.token_urlsafe(32)
    db.ex("INSERT INTO api_tokens(name,token_sha256,created_at,scope) VALUES(?,?,?,?)", (name[:40] or scope.title(), _h(raw), clock.iso(clock.now()), scope))
    return raw


def verify(db, raw, path=None):
    """The key's row, or None when it is unknown, cancelled, or not allowed to touch this path."""
    if not raw or len(raw) > 100:
        return None
    scope = next((s for s, (pre, _) in SCOPES.items() if raw.startswith(pre)), None)
    if scope is None or (path is not None and path not in SCOPES[scope][1]):
        return None
    r = db.one("SELECT * FROM api_tokens WHERE token_sha256=? AND revoked_at IS NULL AND scope=?", (_h(raw), scope))
    if r:
        db.ex("UPDATE api_tokens SET last_used_at=? WHERE id=?", (clock.iso(clock.now()), r["id"]))
    return r


def revoke(db, tid):
    db.ex("UPDATE api_tokens SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (clock.iso(clock.now()), tid))


def listing(db, scope=None):
    if scope:
        return db.q("SELECT * FROM api_tokens WHERE scope=? ORDER BY id DESC", (scope,))
    return db.q("SELECT * FROM api_tokens ORDER BY id DESC")
