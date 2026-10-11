"""The Treasury: what you really spend, month over month, and the optional cuts the Purse follows."""
import re
from datetime import date

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import clock, ledger, spending, warlock
from .db import DB


def pct_or_none(v, lo=-90.0, hi=300.0):
    v = (v or "").strip().replace("%", "")
    if not v:
        return None
    try:
        return max(lo, min(hi, float(v)))
    except ValueError:
        raise HTTPException(400, f"{v!r} is not a percentage.")


def register(app, cfg, get_db, session, render):
    @app.get("/treasury", response_class=HTMLResponse)
    def treasury_page(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        today = clock.now().astimezone(cfg.tz).date()
        p = spending.plan(db)
        derived = spending.derive(db, p)
        pouches = {}
        for a in db.q("SELECT * FROM activities WHERE archived=0 AND category_id IS NOT NULL ORDER BY sort, id"):
            d = derived.get(a["id"], {})
            pouches.setdefault(a["category_id"], []).append({"name": a["name"], "credits": a["credits_per_month"], "estimate": a["estimate_cents"], "auto": bool(a["auto_credits"]),
                                                             "allowance": d.get("allowance")})
        latest = date.fromisoformat(p["latest"] + "-01").strftime("%b %Y") if p["latest"] else None
        behind = [c for c in spending.coverage_notes(db, today) if c["behind_days"] > 35]
        deposits = db.q("""SELECT t.id, t.posted_date, -t.amount_cents AS cents, substr(t.description_raw,1,44) AS d, a.name AS acct FROM transactions t JOIN accounts a ON a.id=t.account_id
                           WHERE t.kind='income' AND a.owner IN ('me','joint') AND a.type NOT IN ('credit_card','line_of_credit') ORDER BY t.posted_date DESC, t.id DESC LIMIT 60""")
        aside = db.q("""SELECT t.id, t.posted_date, -t.amount_cents AS cents, substr(t.description_raw,1,44) AS d, a.name AS acct FROM transactions t JOIN accounts a ON a.id=t.account_id
                        WHERE t.kind='setaside' ORDER BY t.posted_date DESC, t.id DESC""")
        deposits = [dict(x, likely=bool(spending.looks_like_pay(x["d"]))) for x in deposits]
        start = db.get_setting("tally_from", "")
        tally = ledger.partner_tally(db, start if re.fullmatch(r"\d{4}-\d{2}", start or "") else None)
        return render(request, db, "treasury.html", tally=tally, salary=spending.anchors(db, "salary"), rent=spending.anchors(db, "rent"), monthly=spending.monthly,
                      partner=db.get_setting("partner_label", "Partner"), every_words=spending.EVERY_WORDS, suggest_pay=spending.pay_suggestion(db), changed=spending.pay_changed(db), today=today.isoformat(),
                      deposits=deposits, aside=aside, p=p, pouches=pouches, latest=latest, behind=behind, notes=spending.coverage_notes(db, today),
                      extras=warlock.adjustments(db), money_w=warlock.money, month_label=warlock.month_label, treat_words=warlock.TREAT_WORDS)

    @app.post("/treasury/deposit")
    async def deposit(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        """Say what a deposit really is: your partner paying you back, or money that is not income (so it stops counting as money in)."""
        f = await request.form()
        if f.get("as") == "only_pay":
            spending.set_aside_non_pay(db, ignore_overrides=True)
            return RedirectResponse("/treasury#real", 303)
        if f.get("as") == "repayment" and (f.get("txn_id") or "").isdigit():
            ledger.repayment_confirm(db, int(f["txn_id"]))
            return RedirectResponse("/treasury#real", 303)
        kind = {"repayment": "settlement", "not_income": "setaside", "recount": "income"}.get(f.get("as"))
        if not kind or not (f.get("txn_id") or "").isdigit():
            raise HTTPException(400, "Choose what this deposit is.")
        row = db.one("SELECT account_id, dedupe_key FROM transactions WHERE id=?", (int(f["txn_id"]),))
        done = db.ex("UPDATE transactions SET kind=? WHERE id=? AND kind=?", (kind, int(f["txn_id"]), "setaside" if kind == "income" else "income")).rowcount
        if row and done:       # remember a deposit the owner insists is income, so reading the statement again does not set it aside
            if kind == "income":
                db.ex("INSERT OR IGNORE INTO deposit_overrides(account_id,dedupe_key) VALUES(?,?)", (row["account_id"], row["dedupe_key"]))
            else:
                db.ex("DELETE FROM deposit_overrides WHERE account_id=? AND dedupe_key=?", (row["account_id"], row["dedupe_key"]))
        return RedirectResponse("/treasury#real", 303)

    @app.post("/treasury/cuts")
    async def save_cuts(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        g = pct_or_none(f.get("global"), 0.0, 90.0)
        spending.set_reduce_pct(db, g or 0.0)
        for k, v in f.items():
            if k.startswith("m_") and k[2:].isdigit():
                spending.set_mod(db, int(k[2:]), pct_or_none(v))
        spending.sync_purse(db, cfg.tz, clock.now())
        return RedirectResponse("/treasury#cuts", 303)

    @app.post("/treasury/anchor")
    async def save_anchor(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        """Add a dated reference point: your pay, or your rent and your share of it. A raise or a new split is a new line from its date."""
        f = await request.form()
        kind = f.get("kind")
        if kind not in ("salary", "rent"):
            raise HTTPException(400, "Choose salary or rent.")
        try:
            cents = int(round(float((f.get("amount") or "").replace("$", "").replace(",", "")) * 100))
        except ValueError:
            cents = 0
        start = f.get("start") or ""
        if cents <= 0 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
            raise HTTPException(400, "Give an amount above zero and the date it starts from.")
        every = f.get("every") if f.get("every") in spending.PER_MONTH else "month"
        try:
            pct = max(0.0, min(100.0, float(f.get("share") or 100))) if kind == "rent" else 100.0
        except ValueError:
            raise HTTPException(400, "Your share should be a percentage like 50.")
        db.ex("INSERT INTO anchors(kind,amount_cents,every,my_pct,start_date,match,created_at) VALUES(?,?,?,?,?,?,?)",
              (kind, cents, "month" if kind == "rent" else every, pct, start, (f.get("match") or "rent").strip()[:40] if kind == "rent" else "", clock.iso(clock.now())))
        spending.sync_purse(db, cfg.tz, clock.now())
        spending.set_aside_non_pay(db)
        return RedirectResponse("/treasury#anchors", 303)

    @app.post("/treasury/anchor/remove")
    async def remove_anchor(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        if (f.get("id") or "").isdigit():
            db.ex("DELETE FROM anchors WHERE id=?", (int(f["id"]),))
        return RedirectResponse("/treasury#anchors", 303)
