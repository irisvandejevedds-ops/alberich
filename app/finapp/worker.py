"""Background process: notification ticks, daily local snapshot. Run: python -m finapp.worker"""
import sqlite3
import time
import traceback
from datetime import timedelta

from . import clock, notify, pet, receipts, spending
from .config import get_config
from .db import DB, open_db

SNAPSHOT_KEEP = 14


def snapshot(cfg, now):
    cfg.snapshots_dir.mkdir(parents=True, exist_ok=True)
    day = now.astimezone(cfg.tz).strftime("%Y%m%d")
    dest = cfg.snapshots_dir / f"finapp-{day}.db"
    if dest.exists():
        return None
    src = sqlite3.connect(str(cfg.db_path))
    dst = sqlite3.connect(str(dest))
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    dest.chmod(0o600)
    for old in sorted(cfg.snapshots_dir.glob("finapp-*.db"))[:-SNAPSHOT_KEEP]:
        old.unlink()
    return dest


def run_once(db, cfg, now=None):
    now = now or clock.now()
    sent = notify.tick(db, cfg, now)
    try:
        spending.apply_pay_default_once(db)
        pet.reset_once(db, now, "pet_reset_v2")
        pet.reset_once(db, now, "pet_reset_v3")        # the evil of 16:46 on Oct 10 came from a pouch briefly overdrawn by a miscount; start clean        # credits are now worked out from your spending, so the old mood no longer means anything
        spending.sync_purse(db, cfg.tz, now)
    except Exception:
        traceback.print_exc()
    local = now.astimezone(cfg.tz)
    if local.hour >= 3:
        snapshot(cfg, now)
    return sent


def main():
    cfg = get_config()
    open_db(cfg.db_path).close()
    try:
        db = DB(cfg.db_path)
        receipts.recover_stuck(db)
        db.close()
    except Exception:
        traceback.print_exc()
    last_tick = 0.0
    while True:
        try:
            db = DB(cfg.db_path)
            try:
                if time.monotonic() - last_tick >= 60:
                    last_tick = time.monotonic()
                    run_once(db, cfg)
                done = receipts.process_queue(db, cfg, limit=1)
                if done and not db.val("SELECT COUNT(*) FROM receipts WHERE status IN ('queued','processing')", (), 0):
                    since = clock.iso(clock.now() - timedelta(minutes=10))
                    total = db.val("SELECT COUNT(*) FROM receipts WHERE processed_at>=?", (since,), 0)
                    look = db.val("SELECT COUNT(*) FROM receipts WHERE processed_at>=? AND status='needs_review'", (since,), 0)
                    notify.receipts_read(db, cfg, total, look)
            finally:
                db.close()
        except Exception:
            traceback.print_exc()
        time.sleep(3)


if __name__ == "__main__":
    main()
