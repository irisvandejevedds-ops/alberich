import base64
import json

from cryptography.hazmat.primitives import serialization

from . import clock


def public_key_b64url(pem_path: str) -> str:
    with open(pem_path, "rb") as f:
        key = serialization.load_pem_private_key(f.read(), None)
    raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def send(sub: dict, payload: dict, cfg, ttl: int = 3600):
    """-> (ok, http_status, error)"""
    from pywebpush import WebPushException, webpush
    try:
        webpush(subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                data=json.dumps(payload), vapid_private_key=cfg.vapid_pem,
                vapid_claims={"sub": cfg.vapid_subject}, ttl=ttl)
        return True, 201, None
    except WebPushException as e:
        status = e.response.status_code if getattr(e, "response", None) is not None else None
        return False, status, str(e)[:300]
    except Exception as e:  # network, key errors
        return False, None, str(e)[:300]


def dispatch(db, cfg, payload: dict, sender=send, ttl: int = 3600) -> int:
    """Send to every active subscription; maintain health bookkeeping. Returns number delivered."""
    ok_count = 0
    for s in db.q("SELECT * FROM push_subscriptions WHERE active=1"):
        ok, status, err = sender(dict(s), payload, cfg, ttl)
        now = clock.iso(clock.now())
        if ok:
            ok_count += 1
            db.ex("UPDATE push_subscriptions SET last_success_at=?, last_error=NULL, fail_count=0 WHERE id=?", (now, s["id"]))
        elif status in (404, 410):
            db.ex("UPDATE push_subscriptions SET active=0, last_error=? WHERE id=?", (f"gone ({status})", s["id"]))
        else:
            db.ex("UPDATE push_subscriptions SET last_error=?, fail_count=fail_count+1 WHERE id=?", (err or "error", s["id"]))
    return ok_count
