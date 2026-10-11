"""Review: sort the merchants that are still under Other."""
import json
import logging
import threading
from datetime import timedelta
from urllib.parse import quote, urlencode

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import clock, llm, sorting
from .db import DB

log = logging.getLogger("alberich.sort")
STALE = timedelta(minutes=4)


def run_job(cfg, sid, data):
    db = DB(cfg.db_path)
    try:
        try:
            rows = sorting.run(db, cfg, data)
            db.ex("UPDATE merchant_sorts SET status='done', rows=? WHERE id=?", (json.dumps(rows), sid))
        except llm.LLMUnavailable as e:
            db.ex("UPDATE merchant_sorts SET status='failed', error=? WHERE id=?", (str(e), sid))
        except Exception:
            log.exception("merchant sort failed")
            db.ex("UPDATE merchant_sorts SET status='failed', error=? WHERE id=?", ("Something unexpected went wrong. Nothing was changed.", sid))
    finally:
        db.close()


def register(app, cfg, get_db, session, render):
    def fresh(db, sid):
        row = db.one("SELECT * FROM merchant_sorts WHERE id=?", (sid,))
        if row and row["status"] == "running" and clock.now() - clock.parse_iso(row["created_at"]) > STALE:
            db.ex("UPDATE merchant_sorts SET status='failed', error='That took too long and was given up on. Nothing was changed.' WHERE id=?", (sid,))
            row = db.one("SELECT * FROM merchant_sorts WHERE id=?", (sid,))
        return row

    @app.get("/review/sort", response_class=HTMLResponse)
    def page(request: Request, id: str = "", db: DB = Depends(get_db), _=Depends(session)):
        row = fresh(db, int(id)) if id.isdigit() else None
        cats = [r["name"] for r in db.q("SELECT name FROM categories ORDER BY sort, name")]
        return render(request, db, "sort.html", pending=sorting.pending(db), job=row, rows=json.loads(row["rows"]) if row and row["status"] == "done" else [],
                      cats=cats, cat_rows=db.q("SELECT id, name FROM categories WHERE name<>'Other' ORDER BY sort, name"), llm=llm.status(db, cfg))

    @app.post("/review/sort/run")
    async def run(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        if f.get("consent") != "on":
            raise HTTPException(400, "Tick the box to agree to send exactly these merchant names.")
        data = sorting.payload(db)
        if not data["merchants"]:
            raise HTTPException(400, "Nothing is filed under Other right now.")
        sid = db.ex("INSERT INTO merchant_sorts(created_at) VALUES(?)", (clock.iso(clock.now()),)).lastrowid
        threading.Thread(target=run_job, args=(cfg, sid, data), daemon=True).start()
        return RedirectResponse(f"/review/sort?id={sid}", 303)

    @app.get("/api/sort/{sid}")
    def status(sid: int, db: DB = Depends(get_db), _=Depends(session)):
        row = fresh(db, sid)
        if not row:
            raise HTTPException(404, "No such job")
        return {"status": row["status"]}

    @app.post("/review/sort/set")
    async def set_many(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        """Sort by hand: every merchant row that has a category chosen, or a new one typed, is moved in one go."""
        f = await request.form()
        picks, made = {}, {}
        for mid in sorted({k.split("_", 1)[1] for k in f.keys() if k.startswith(("cat_", "new_"))}):
            if not mid.isdigit() or not db.one("SELECT 1 FROM merchants WHERE id=?", (int(mid),)):
                continue
            new = " ".join((f.get(f"new_{mid}") or "").split())[:40]
            if new:
                name = db.val("SELECT name FROM categories WHERE lower(name)=lower(?)", (new,))
                if not name:
                    db.ex("INSERT INTO categories(name,kind,sort) VALUES(?, 'variable', (SELECT COALESCE(MAX(sort),0)+1 FROM categories WHERE sort<99))", (new,))
                    name = new
            else:
                name = db.val("SELECT name FROM categories WHERE id=?", (int(f[f"cat_{mid}"]),)) if (f.get(f"cat_{mid}") or "").isdigit() else None
            if name and name != "Other":
                picks[int(mid)] = name
        if not picks:
            raise HTTPException(400, "Choose a category, or type a new one, for at least one merchant first.")
        with db.tx():
            moved = sorting.apply(db, picks)
        return RedirectResponse("/review/sort?" + urlencode({"moved": moved, "merchants": len(picks)}, quote_via=quote), 303)

    @app.post("/review/sort/apply")
    async def apply(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        row = db.one("SELECT * FROM merchant_sorts WHERE id=? AND status='done'", (int(f["id"]),)) if (f.get("id") or "").isdigit() else None
        if not row:
            raise HTTPException(404, "That sorting is not ready, or it failed.")
        known = {r["id"] for r in json.loads(row["rows"])}
        picks = {}
        for mid in f.getlist("take"):
            if mid.isdigit() and int(mid) in known and f.get(f"cat_{mid}"):
                picks[int(mid)] = f.get(f"cat_{mid}")
        with db.tx():
            moved = sorting.apply(db, picks)
            db.ex("UPDATE merchant_sorts SET applied_at=? WHERE id=?", (clock.iso(clock.now()), row["id"]))
        return RedirectResponse(f"/reckoning?moved={moved}", 303)
