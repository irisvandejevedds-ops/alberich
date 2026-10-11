"""The Warlock page: talk, review what he proposes, tick what to apply."""
import json
import logging
import threading
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import clock, llm, warlock, warlock_art
from .db import DB

log = logging.getLogger("alberich.warlock")
STALE = timedelta(minutes=4)


def run_job(cfg, mid, uid, text):
    """The model call runs here, off the request, so leaving the page cannot lose the answer."""
    db = DB(cfg.db_path)
    try:
        try:
            reply, actions = warlock.ask(db, cfg, text, clock.now(), skip_id=uid)
            db.ex("UPDATE warlock_messages SET text=?, actions=?, status='done' WHERE id=?", (reply, json.dumps(actions), mid))
        except llm.LLMUnavailable as e:
            db.ex("UPDATE warlock_messages SET status='failed', error=? WHERE id=?", (str(e), mid))
        except Exception:
            log.exception("warlock failed")
            db.ex("UPDATE warlock_messages SET status='failed', error=? WHERE id=?", ("Something went wrong in the tower. Nothing was changed.", mid))
    finally:
        db.close()


def register(app, cfg, get_db, session, render):
    def expire(db):
        for r in db.q("SELECT id, created_at FROM warlock_messages WHERE status='running'"):
            if clock.now() - clock.parse_iso(r["created_at"]) > STALE:
                db.ex("UPDATE warlock_messages SET status='failed', error='That took too long and was given up on. Nothing was changed.' WHERE id=?", (r["id"],))

    @app.get("/warlock", response_class=HTMLResponse)
    def page(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        expire(db)
        msgs = [dict(m, actions=json.loads(m["actions"]), applied=json.loads(m["applied"])) for m in db.q("SELECT * FROM warlock_messages ORDER BY id DESC LIMIT 30")][::-1]
        running = any(m["status"] == "running" for m in msgs)
        ctx_json = json.dumps(warlock.render_prompt(db, cfg, "(your message goes here)", clock.now())["context"], separators=(",", ":")) if warlock.consent(db) else ""
        return render(request, db, "warlock.html", msgs=msgs, running=running, ok=warlock.consent(db), notes=warlock.notes(db), llm=llm.status(db, cfg),
                      art=warlock_art.svg("casting" if running else "idle", 4, "bob"), ctx_json=ctx_json, adjustments=warlock.adjustments(db),
                      money=warlock.money, month_label=warlock.month_label, treat_words=warlock.TREAT_WORDS)

    @app.post("/warlock/consent")
    async def consent(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        if f.get("grant") == "on":
            db.set_setting("warlock_ok_v2", "1")
        elif f.get("revoke") == "1":
            db.set_setting("warlock_ok_v2", "0")
        else:
            raise HTTPException(400, "Tick the box to grant the warlock leave to read your ledgers.")
        return RedirectResponse("/warlock", 303)

    @app.post("/warlock/say")
    async def say(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        text = (f.get("text") or "").strip()
        if not warlock.consent(db):
            raise HTTPException(400, "Grant the warlock leave to read your ledgers first.")
        st = llm.status(db, cfg)
        if not st["enabled"]:
            raise HTTPException(400, "The Warlock is not available right now (no key, or switched off).")
        if not text:
            raise HTTPException(400, "Say something to the warlock first.")
        if len(text) > warlock.MAX_TEXT:
            raise HTTPException(400, f"Keep it under {warlock.MAX_TEXT} characters.")
        if db.val("SELECT COUNT(*) FROM warlock_messages WHERE status='running'", (), 0):
            raise HTTPException(400, "The warlock is still thinking about your last message.")
        now = clock.iso(clock.now())
        uid = db.ex("INSERT INTO warlock_messages(role,text,created_at) VALUES('me',?,?)", (text, now)).lastrowid
        mid = db.ex("INSERT INTO warlock_messages(role,text,status,created_at) VALUES('warlock','','running',?)", (now,)).lastrowid
        threading.Thread(target=run_job, args=(cfg, mid, uid, text), daemon=True).start()
        return RedirectResponse("/warlock#end", 303)

    @app.get("/api/warlock/status")
    def status(db: DB = Depends(get_db), _=Depends(session)):
        expire(db)
        return {"status": "running" if db.val("SELECT COUNT(*) FROM warlock_messages WHERE status='running'", (), 0) else "done"}

    @app.post("/warlock/apply")
    async def apply(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        row = db.one("SELECT * FROM warlock_messages WHERE id=? AND role='warlock' AND status='done'", (int(f["id"]),)) if (f.get("id") or "").isdigit() else None
        if not row:
            raise HTTPException(404, "No such message")
        actions, applied = json.loads(row["actions"]), json.loads(row["applied"])
        picks = [int(x) for x in f.getlist("pick") if x.isdigit()]
        done, failed = 0, []
        with db.tx():
            for i in sorted(picks, key=lambda i: (0 if 0 <= i < len(actions) and actions[i].get('type') == 'add_category' else 1, i)):
                if i in applied or not (0 <= i < len(actions)) or not actions[i]["ok"]:
                    continue
                try:
                    warlock.apply(db, cfg, actions[i], clock.now())
                    applied.append(i)
                    done += 1
                except ValueError as e:
                    failed.append(str(e))
            db.ex("UPDATE warlock_messages SET applied=? WHERE id=?", (json.dumps(applied), row["id"]))
        if failed and not done:
            raise HTTPException(400, "Could not apply that: " + "; ".join(failed))
        return RedirectResponse("/warlock#end", 303)

    @app.post("/warlock/note/remove")
    async def note_remove(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        db.ex("UPDATE warlock_notes SET archived_at=? WHERE id=?", (clock.iso(clock.now()), int(f["id"])))
        return RedirectResponse("/warlock", 303)

    @app.post("/warlock/adjustment/remove")
    async def adjustment_remove(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        db.ex("UPDATE month_adjustments SET removed_at=? WHERE id=?", (clock.iso(clock.now()), int(f["id"])))
        back = f.get("back") or "/warlock"
        return RedirectResponse(back if back.startswith("/") and not back.startswith("//") else "/warlock", 303)

    @app.post("/warlock/reset")
    def reset(db: DB = Depends(get_db), _=Depends(session)):
        db.ex("DELETE FROM warlock_messages")
        return RedirectResponse("/warlock", 303)
