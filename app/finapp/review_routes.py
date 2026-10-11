"""The monthly review (who owns each purchase, and what kind) and the reckoning (settle-up)."""
import re

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import clock, ledger, spending
from .db import DB

MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def shift(month, d):
    y, m = int(month[:4]), int(month[5:]) + d
    while m < 1:
        y, m = y - 1, m + 12
    while m > 12:
        y, m = y + 1, m - 12
    return f"{y}-{m:02d}"


def register(app, cfg, get_db, session, render):
    def current_month():
        t = clock.now().astimezone(cfg.tz)
        return f"{t.year}-{t.month:02d}"

    def pick_month(db, q):
        if q and MONTH.match(q):
            return q
        prev = shift(current_month(), -1)
        has_open = db.val("""SELECT COUNT(*) FROM transactions t WHERE t.posted_date LIKE ? AND t.kind IN ('spend','refund')""", (prev + "%",), 0)
        return prev if has_open else current_month()

    def partner(db):
        return db.get_setting("partner_label", "Partner")

    @app.get("/review", response_class=HTMLResponse)
    def review(request: Request, month: str = "", db: DB = Depends(get_db), _=Depends(session)):
        month = pick_month(db, month)
        items = ledger.month_items(db, month)
        prog = ledger.progress(items)
        ask = [i for i in items if not i["reviewed"] and not i["confident"]]
        auto = [i for i in items if not i["reviewed"] and i["confident"]]
        done = [i for i in items if i["reviewed"]]
        cats = db.q("SELECT id, name FROM categories ORDER BY sort, name")
        rules = {r["merchant_id"] for r in db.q("SELECT merchant_id FROM split_rules")}
        start = db.get_setting("tally_from", "")
        tally = ledger.partner_tally(db, start if MONTH.match(start or "") else None)
        return render(request, db, "review.html", tally=tally, today=clock.now().astimezone(cfg.tz).date().isoformat(), month=month, prev=shift(month, -1), nxt=shift(month, 1), prog=prog, ask=ask, auto=auto,
                      done=done, cats=cats, rules=rules, partner=partner(db), is_current=month == current_month())

    def state(db, month):
        return ledger.progress(ledger.month_items(db, month))

    @app.post("/api/review/decide")
    async def decide(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        b = await request.json()
        try:
            lid = int(b["line_id"])
            bps = b.get("bps")
            cat = int(b["category_id"]) if b.get("category_id") else None
            ledger.decide(db, lid, bps=None if bps is None else int(bps), category_id=cat, rule=bool(b.get("rule")))
        except (KeyError, ValueError, TypeError):
            raise HTTPException(400, "bad decision")
        month = db.val("SELECT substr(t.posted_date,1,7) FROM line_items li JOIN transactions t ON t.id=li.txn_id WHERE li.id=?", (lid,))
        ledger.resuggest_open(db, month)
        return {"ok": True, "progress": state(db, month)}

    @app.post("/api/review/category")
    async def set_category(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        """A category change on a card is saved at once, whether or not the card has been decided."""
        b = await request.json()
        try:
            lid, cid = int(b["line_id"]), int(b["category_id"])
            if not db.one("SELECT 1 FROM categories WHERE id=?", (cid,)):
                raise ValueError
            moved = ledger.set_line_category(db, lid, cid)
        except (KeyError, ValueError, TypeError):
            raise HTTPException(400, "bad category")
        return {"ok": True, "moved": moved, "category": db.val("SELECT name FROM categories WHERE id=?", (cid,))}

    @app.post("/api/review/confirm")
    async def confirm(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        b = await request.json()
        month = b.get("month")
        which = b.get("which")
        if not (month and MONTH.match(month)) or which not in ("auto", "ask"):
            raise HTTPException(400, "bad request")
        n = 0
        for i in ledger.month_items(db, month):
            if i["reviewed"] or bool(i["confident"]) != (which == "auto"):
                continue
            ledger.decide(db, i["id"], bps=i["my_bps"])
            n += 1
            if which == "ask":
                ledger.resuggest_open(db, month)
        return {"ok": True, "confirmed": n, "progress": state(db, month)}

    @app.post("/api/review/reopen")
    async def reopen(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        b = await request.json()
        ledger.reopen(db, int(b["line_id"]))
        return {"ok": True}

    @app.post("/review/rule/drop")
    async def drop_rule(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        ledger.drop_rule(db, int(f["merchant_id"]))
        return RedirectResponse(f.get("back") if (f.get("back") or "").startswith("/review") else "/review", 303)

    @app.get("/reckoning", response_class=HTMLResponse)
    def reckoning(request: Request, month: str = "", db: DB = Depends(get_db), _=Depends(session)):
        month = pick_month(db, month)
        r = ledger.reckoning(db, month)
        rules = db.q("SELECT s.my_bps, m.id, m.name FROM split_rules s JOIN merchants m ON m.id=s.merchant_id ORDER BY m.name")
        start = db.get_setting("tally_from", "")
        start = start if MONTH.match(start or "") else None
        tally = ledger.partner_tally(db, start)
        since = ((tally["start"] or month) + "-01")
        cands = ledger.repayment_candidates(db, shift(month, -2) + "-01" if not tally["start"] else max(since, shift(current_month(), -2) + "-01"))
        return render(request, db, "reckoning.html", tally=tally, cands=cands, today=clock.now().astimezone(cfg.tz).date().isoformat(), month=month, prev=shift(month, -1), nxt=shift(month, 1), r=r, partner=partner(db),
                      rules=rules, default_pct=ledger.default_bps(db) // 100, other_pct=round(100 * dict(r["by_category"]).get("Other", 0) / r["mine"]) if r["mine"] > 0 else 0, biggest=max([c for _, c in r["by_category"]] or [1]))

    @app.post("/reckoning/partner")
    async def partner_label(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        name = (f.get("name") or "").strip()[:30] or "Partner"
        db.set_setting("partner_label", name)
        bps = f.get("default_bps")
        if bps and bps.isdigit() and 0 <= int(bps) <= 100:
            db.set_setting("default_bps", int(bps) * 100)
        return RedirectResponse("/reckoning", 303)

    @app.post("/reckoning/pay")
    async def pay(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        try:
            cents = int(round(float((f.get("amount") or "0").replace("$", "").replace(",", "")) * 100))
        except ValueError:
            cents = 0
        d = f.get("date") or ""
        if cents <= 0 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            raise HTTPException(400, "Enter an amount above zero and the date it was paid.")
        direction = "to_partner" if f.get("direction") == "to_partner" else "from_partner"
        db.ex("INSERT INTO settlements(paid_on,amount_cents,direction,note,created_at) VALUES(?,?,?,?,?)",
              (d, cents, direction, (f.get("note") or "").strip()[:80], clock.iso(clock.now())))
        back = f.get("back") or ""
        return RedirectResponse(back if back.startswith("/") and not back.startswith("//") else "/reckoning#tally", 303)

    def back_to(f, default):
        b = f.get("back") or ""
        return b if b.startswith("/") and not b.startswith("//") else default

    def dollars_to_cents(v):
        v = (v or "").replace("$", "").replace(",", "").strip()
        if not v:
            return None
        try:
            return int(round(float(v) * 100))
        except ValueError:
            raise HTTPException(400, "Enter an amount like 150 or 150.50.")

    @app.post("/reckoning/pay/mark")
    async def pay_mark(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        if (f.get("txn_id") or "").isdigit():
            ledger.repayment_confirm(db, int(f["txn_id"]), dollars_to_cents(f.get("amount")))
        return RedirectResponse(back_to(f, "/reckoning#tally"), 303)

    @app.post("/review/repay")
    async def repay(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        """Answer a suggested repayment: yes (all or part of it counts against what the partner owes) or not from her."""
        f = await request.form()
        if not (f.get("txn_id") or "").isdigit():
            raise HTTPException(400, "Choose a deposit.")
        tid = int(f["txn_id"])
        if f.get("decision") == "no":
            ledger.repayment_reject(db, tid)
        else:
            if not ledger.repayment_confirm(db, tid, dollars_to_cents(f.get("amount"))):
                raise HTTPException(400, "That is not a deposit.")
        return RedirectResponse(back_to(f, "/review#iou"), 303)

    @app.post("/reckoning/waive")
    async def waive(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        """Let go of some or all of what the partner owes. It counts as settled, and is listed as waived."""
        f = await request.form()
        start = db.get_setting("tally_from", "")
        tally = ledger.partner_tally(db, start if MONTH.match(start or "") else None)
        cents = dollars_to_cents(f.get("amount"))
        cents = tally["balance"] if cents is None else cents
        if cents <= 0:
            raise HTTPException(400, "There is nothing owed to waive.")
        db.ex("INSERT INTO settlements(paid_on,amount_cents,direction,note,created_at) VALUES(?,?,'waived',?,?)",
              (clock.now().astimezone(cfg.tz).date().isoformat(), cents, (f.get("note") or "waived").strip()[:80], clock.iso(clock.now())))
        return RedirectResponse(back_to(f, "/reckoning#tally"), 303)

    @app.post("/reckoning/pay/remove")
    async def pay_remove(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        key = f.get("key") or ""
        if key[:1] == "m" and key[1:].isdigit():
            db.ex("UPDATE settlements SET removed_at=? WHERE id=?", (clock.iso(clock.now()), int(key[1:])))
        elif key[:1] == "t" and key[1:].isdigit():
            ledger.repayment_undo(db, int(key[1:]))
            spending.set_aside_non_pay(db)
        return RedirectResponse("/reckoning#tally", 303)

    @app.post("/reckoning/tally-start")
    async def tally_start(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        m = f.get("month") or ""
        db.set_setting("tally_from", m if MONTH.match(m) else "")
        return RedirectResponse("/reckoning#tally", 303)
