"""Accounts, statement drop-in, templates, and the required-statements view."""
import json
import re
from datetime import date

from fastapi import Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import clock, ingest, llm, receipts, required
from .db import DB
from .statements import csvfmt, pdftext
from .statements.common import LIABILITY, reconcile

ACCOUNT_TYPES = [("chequing", "Chequing"), ("savings", "Savings"), ("credit_card", "Credit card"), ("line_of_credit", "Line of credit")]
OWNERS = [("me", "Mine"), ("partner", "Partner's"), ("joint", "Joint")]
MAX_BYTES = 30 * 1024 * 1024
ATTENTION = ("pending_template", "pending_account", "needs_review", "failed")
STATUS_CHIP = {"reconciled": ("green", "Balanced"), "parsed": ("blue", "Read, unverified"), "needs_review": ("gold", "Needs a look"),
               "pending_template": ("red", "Needs a template"), "pending_account": ("red", "Which account?"), "failed": ("red", "Could not read")}


def register(app, cfg, get_db, session, render):
    def month_ago(n):
        t = clock.now().astimezone(cfg.tz)
        y, m = t.year, t.month - n
        while m < 1:
            y, m = y - 1, m + 12
        return f"{y}-{m:02d}"

    def today():
        return clock.now().astimezone(cfg.tz).date()

    def need(db, sid):
        st = db.one("SELECT * FROM statements WHERE id=?", (sid,))
        if not st:
            raise HTTPException(404, "No such statement")
        return st

    # ------------------------------------------------------------------ the Ledgers page
    @app.get("/ledgers", response_class=HTMLResponse)
    def ledgers(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        cols, rows = required.matrix(db, today(), 12)
        first = next((i for i in range(len(cols)) if any(r["cells"][i]["status"] != "na" for r in rows)), len(cols) - 1)
        first = min(first, len(cols) - 3)
        cols = cols[first:]
        for r in rows:
            r["cells"] = r["cells"][first:]
        accounts = db.q("SELECT * FROM accounts WHERE active=1 ORDER BY owner, institution, name")
        attention = db.q("""SELECT s.*, a.name AS acct FROM statements s LEFT JOIN accounts a ON a.id=s.account_id
                            WHERE s.status IN ('pending_template','pending_account','needs_review','failed') ORDER BY s.received_at DESC""")
        recent = db.q("""SELECT s.*, a.name AS acct FROM statements s LEFT JOIN accounts a ON a.id=s.account_id
                         WHERE s.status IN ('reconciled','parsed') ORDER BY s.received_at DESC LIMIT 10""")
        return render(request, db, "ledgers.html", cols=cols, rows=rows, accounts=accounts, attention=attention, recent=recent,
                      summary=required.summary(rows), chip=STATUS_CHIP, short=lambda m: date(int(m[:4]), int(m[5:]), 1).strftime("%b %y"))

    async def read_uploads(form):
        out = []
        for f in form.getlist("files"):
            if not hasattr(f, "read"):
                continue
            data = await f.read()
            out.append((f.filename or "statement", data))
        return out

    def run_one(db, name, data, account_id):
        if len(data) > MAX_BYTES:
            return {"name": name, "status": "unsupported", "message": "That file is over 30 MB."}
        if len(data) == 0:
            return {"name": name, "status": "unsupported", "message": "That file is empty."}
        r = ingest.ingest(db, cfg, name, data, account_id=account_id)
        return {"name": name, "status": r.status, "message": r.message, "id": r.statement_id}

    def acct_id(v):
        return int(v) if v and str(v).isdigit() else None

    @app.post("/api/ledgers/upload")
    async def upload(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        form = await request.form()
        aid = acct_id(form.get("account_id"))
        files = await read_uploads(form)
        res = []
        for name, data in files:
            res.append(await run_in_threadpool(run_one, db, name, data, aid))
        return JSONResponse({"results": res})

    @app.post("/share")
    async def share_target(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        form = await request.form()
        files = await read_uploads(form)
        n_ok, n_rcpt = 0, 0
        for name, data in files:
            if receipts.sniff(data) == "image":
                rid, st = receipts.add(db, cfg, name, data)
                n_rcpt += st in ("queued", "duplicate")
                continue
            r = await run_in_threadpool(run_one, db, name, data, None)
            n_ok += r["status"] in ("reconciled", "parsed")
        if n_rcpt and not n_ok and n_rcpt == len(files):
            return RedirectResponse("/receipts", 303)
        return RedirectResponse(f"/ledgers?shared={len(files)}&ok={n_ok}", 303)

    # ------------------------------------------------------------------ accounts
    @app.get("/ledgers/accounts", response_class=HTMLResponse)
    def accounts_page(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        rows = db.q("SELECT a.*, (SELECT COUNT(*) FROM statements s WHERE s.account_id=a.id) AS n_statements FROM accounts a ORDER BY a.active DESC, a.owner, a.institution, a.name")
        return render(request, db, "accounts.html", rows=rows, types=ACCOUNT_TYPES, owners=OWNERS, default_from=month_ago(2))

    @app.post("/ledgers/accounts/save")
    async def account_save(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        name = (f.get("name") or "").strip()[:60]
        if not name:
            raise HTTPException(400, "name required")
        typ = f.get("type") if f.get("type") in dict(ACCOUNT_TYPES) else "chequing"
        owner = f.get("owner") if f.get("owner") in dict(OWNERS) else "me"
        last4 = re.sub(r"\D", "", f.get("last4") or "")[-4:]
        cd = f.get("cycle_day") or ""
        cd = int(cd) if cd.isdigit() and 1 <= int(cd) <= 31 else None
        tf = f.get("track_from") if re.fullmatch(r"\d{4}-\d{2}", f.get("track_from") or "") else None
        vals = ((f.get("institution") or "").strip()[:60], name, typ, owner, last4, cd, 1 if f.get("require_pdf") == "on" else 0, tf)
        if f.get("id"):
            db.ex("""UPDATE accounts SET institution=?,name=?,type=?,owner=?,last4=?,cycle_day=?,require_pdf=?,track_from=? WHERE id=?""",
                  (*vals, int(f["id"])))
        else:
            db.ex("""INSERT INTO accounts(institution,name,type,owner,last4,cycle_day,require_pdf,track_from,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?)""", (*vals, clock.iso(clock.now())))
        return RedirectResponse("/ledgers/accounts", 303)

    @app.post("/ledgers/accounts/archive")
    async def account_archive(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        db.ex("UPDATE accounts SET active=? WHERE id=?", (0 if f.get("archive") == "1" else 1, int(f["id"])))
        return RedirectResponse("/ledgers/accounts", 303)

    # ------------------------------------------------------------------ one statement
    @app.get("/statements/{sid}", response_class=HTMLResponse)
    def statement(sid: int, request: Request, details: int = 0, db: DB = Depends(get_db), _=Depends(session)):
        st = need(db, sid)
        if st["status"] == "pending_template" and not details:
            return RedirectResponse(f"/statements/{sid}/template", 303)
        acct = db.one("SELECT * FROM accounts WHERE id=?", (st["account_id"],)) if st["account_id"] else None
        txns = db.q("SELECT * FROM transactions WHERE statement_id=? ORDER BY posted_date, id", (sid,))
        cands = db.q("SELECT * FROM accounts WHERE active=1 ORDER BY name")
        tmpl = db.one("SELECT name FROM statement_templates WHERE id=?", (st["template_id"],)) if st["template_id"] else None
        return render(request, db, "statement.html", st=st, acct=acct, txns=txns, cands=cands, tmpl=tmpl, chip=STATUS_CHIP,
                      liability=acct is not None and acct["type"] in LIABILITY)

    @app.post("/statements/{sid}/account")
    async def statement_account(sid: int, request: Request, db: DB = Depends(get_db), _=Depends(session)):
        need(db, sid)
        f = await request.form()
        a = acct_id(f.get("account_id"))
        if not a:
            raise HTTPException(400, "choose an account")
        await run_in_threadpool(ingest.process, db, cfg, sid, None, a)
        return RedirectResponse(f"/statements/{sid}", 303)

    @app.post("/ledgers/reread-all")
    async def reread_all(db: DB = Depends(get_db), _=Depends(session)):
        """Read every filed statement again with the latest readers. Decisions already made in the Review are kept."""
        ids = [r["id"] for r in db.q("SELECT id FROM statements WHERE status IN ('reconciled','parsed','needs_review') AND account_id IS NOT NULL ORDER BY id")]
        changed = 0
        for sid in ids:
            before = tuple(db.one("SELECT status, txn_count FROM statements WHERE id=?", (sid,)))
            await run_in_threadpool(ingest.process, db, cfg, sid)
            changed += tuple(db.one("SELECT status, txn_count FROM statements WHERE id=?", (sid,))) != before
        return RedirectResponse(f"/ledgers?reread={len(ids)}&changed={changed}", 303)

    @app.post("/statements/{sid}/reprocess")
    async def statement_reprocess(sid: int, db: DB = Depends(get_db), _=Depends(session)):
        need(db, sid)
        await run_in_threadpool(ingest.process, db, cfg, sid)
        return RedirectResponse(f"/statements/{sid}", 303)

    @app.post("/statements/{sid}/accept")
    async def statement_accept(sid: int, request: Request, db: DB = Depends(get_db), _=Depends(session)):
        st = need(db, sid)
        f = await request.form()
        reason = (f.get("reason") or "").strip()
        if st["status"] != "needs_review" or len(reason) < 3:
            raise HTTPException(400, "a short reason is required")
        ingest.accept(db, sid, reason)
        return RedirectResponse(f"/statements/{sid}", 303)

    @app.post("/statements/{sid}/delete")
    def statement_delete(sid: int, db: DB = Depends(get_db), _=Depends(session)):
        need(db, sid)
        ingest.delete_statement(db, cfg, sid)
        return RedirectResponse("/ledgers", 303)

    # ------------------------------------------------------------------ teaching Alberich a new layout
    def draft_for(db, st, atype, flip=False):
        data = ingest.load_bytes(db, cfg, st["id"])
        lines = None
        if st["kind"] == "csv":
            spec, notes = csvfmt.draft(data, atype)
        else:
            lines, spec, notes = pdftext.best_draft(data, atype)
        if spec is not None and flip:
            spec["flip"] = True
        return data, lines, spec, notes

    def parse_with(st, data, lines, spec, atype):
        return csvfmt.parse(data, spec, atype) if st["kind"] == "csv" else pdftext.parse(lines, spec, atype)

    def preview(st, data, lines, spec, atype):
        if spec is None:
            return None
        try:
            p = parse_with(st, data, lines, spec, atype)
        except Exception as e:
            return {"error": f"With this layout the file cannot be read: {e}"}
        verdict, note = reconcile(atype, p.opening_cents, p.closing_cents, p.txns)
        if p.running_ok is True:
            verdict, note = "ok", "Every row's running balance follows from the row before it."
        elif p.running_ok is False:
            verdict, note = "mismatch", "A running balance in this file does not add up."
        rows = p.txns if len(p.txns) <= 14 else p.txns[:9] + p.txns[-4:]
        return {"count": len(p.txns), "n_out": sum(1 for x in p.txns if x.out_cents > 0), "n_in": sum(1 for x in p.txns if x.out_cents < 0), "rows": rows,
                "gap": len(p.txns) > 14, "verdict": verdict, "note": note, "period": (p.period_start, p.period_end),
                "opening": p.opening_cents, "closing": p.closing_cents, "hint": p.account_hint}

    def choose(db, st, q, first_load):
        """Which account, which kind of account, which direction. Returns (account_row|None, atype, flip)."""
        accts = db.q("SELECT * FROM accounts WHERE active=1")
        by_id = {str(a["id"]): a for a in accts}
        if "account_id" in q:
            acct = by_id.get(str(q.get("account_id")))
        else:
            acct = by_id.get(str(st["account_id"])) if st["account_id"] else None
        data = lines = None
        if acct is None and "account_id" not in q and first_load:
            # a card number in the file may identify the account
            try:
                data, lines, spec, _n = draft_for(db, st, "chequing")
                if spec is not None:
                    hint = parse_with(st, data, lines, spec, "chequing").account_hint
                    match = [a for a in accts if hint and a["last4"] == hint]
                    acct = match[0] if len(match) == 1 else None
            except Exception:
                acct = None
        if acct is not None:
            atype = acct["type"]
        elif q.get("atype") in dict(ACCOUNT_TYPES):
            atype = q["atype"]
        elif st["kind"] == "pdf":
            atype = pdftext.guess_account_type(lines or pdftext.extract_lines(ingest.load_bytes(db, cfg, st["id"])))
        else:
            atype = "chequing"
        if "go" in q:
            flip = q.get("flip") in ("on", "1", "true")
        elif st["template_id"]:
            old = db.val("SELECT spec FROM statement_templates WHERE id=?", (st["template_id"],))
            flip = bool(json.loads(old).get("flip")) if old else False
        else:
            flip = False
        return acct, atype, flip

    def template_page(request, db, st, acct, atype, flip, spec, notes, data, lines, source="heuristic"):
        if spec is not None:
            flip = bool(spec.get("flip"))
        pv = preview(st, data, lines, spec, atype)
        alt = None
        if spec is not None and pv and not pv.get("error"):
            other = preview(st, data, lines, {**spec, "flip": not spec.get("flip")}, atype)
            if other and not other.get("error") and pv["verdict"] != "ok" and other["verdict"] == "ok":
                alt = other
        excerpt = pdftext.mask_excerpt(lines) if lines else None
        return render(request, db, "template_edit.html", st=st, acct=acct, accounts=db.q("SELECT * FROM accounts WHERE active=1 ORDER BY owner, institution, name"),
                      atype=atype, flip=flip, types=ACCOUNT_TYPES, type_names=dict(ACCOUNT_TYPES), spec=spec, spec_json=json.dumps(spec, indent=1) if spec else "",
                      notes=notes, pv=pv, flip_helps=alt is not None, source=source, excerpt=excerpt, llm=llm.status(db, cfg),
                      inst=acct["institution"] if acct else "")

    @app.get("/statements/{sid}/template", response_class=HTMLResponse)
    def template_get(sid: int, request: Request, db: DB = Depends(get_db), _=Depends(session)):
        st = need(db, sid)
        if st["kind"] == "ofx":
            raise HTTPException(400, "OFX files need no template")
        acct, atype, flip = choose(db, st, dict(request.query_params), True)
        data, lines, spec, notes = draft_for(db, st, atype, flip)
        return template_page(request, db, st, acct, atype, flip, spec, notes, data, lines)

    @app.post("/statements/{sid}/template/update", response_class=HTMLResponse)
    async def template_update(sid: int, request: Request, db: DB = Depends(get_db), _=Depends(session)):
        """Update the preview after a change of account, type or direction, keeping the layout already on the page
        (the Warlock's, or one edited by hand) instead of drafting again from scratch."""
        st = need(db, sid)
        f = await request.form()
        acct, atype, flip = choose(db, st, {**dict(f), "go": "1"}, False)
        try:
            spec = json.loads(f.get("spec") or "")
            assert isinstance(spec, dict)
        except Exception:
            spec = None
        source = f.get("source") if f.get("source") in ("heuristic", "llm") else "heuristic"
        if spec is None or (source != "llm" and f.get("was_atype") != atype):
            data, lines, spec, notes = draft_for(db, st, atype, flip)
            return template_page(request, db, st, acct, atype, flip, spec, notes, data, lines)
        data = ingest.load_bytes(db, cfg, st["id"])
        lines = pdftext.extract_lines(data, spec.get("extract")) if st["kind"] == "pdf" else None
        if flip:
            spec["flip"] = True
        else:
            spec.pop("flip", None)
        return template_page(request, db, st, acct, atype, flip, spec, [], data, lines, source=source)

    @app.post("/statements/{sid}/template/refine", response_class=HTMLResponse)
    async def template_refine(sid: int, request: Request, db: DB = Depends(get_db), _=Depends(session)):
        st = need(db, sid)
        f = await request.form()
        acct, atype, flip = choose(db, st, {**dict(f), "go": "1"}, False)
        data, lines, spec, notes = draft_for(db, st, atype, flip)
        if st["kind"] != "pdf":
            raise HTTPException(400, "Model help is only for PDF layouts")
        if f.get("consent") != "on":
            notes = ["Tick the box to confirm you have read the masked text that would be sent."] + notes
            return template_page(request, db, st, acct, atype, flip, spec, notes, data, lines)
        excerpt = pdftext.mask_excerpt(lines)
        try:
            new, _p = await run_in_threadpool(llm.refine_pdf_template, db, cfg, excerpt, spec or {}, lines, atype)
        except llm.LLMUnavailable as e:
            return template_page(request, db, st, acct, atype, flip, spec, [str(e)] + notes, data, lines)
        if flip:
            new["flip"] = True
        if getattr(lines, "mode", None):
            new["extract"] = lines.mode
        return template_page(request, db, st, acct, atype, flip, new, ["This layout came from the Warlock. Check the preview before saving."] + _p.notes, data, lines, source="llm")

    @app.post("/statements/{sid}/template/save")
    async def template_save(sid: int, request: Request, db: DB = Depends(get_db), _=Depends(session)):
        st = need(db, sid)
        f = await request.form()
        acct = db.one("SELECT * FROM accounts WHERE id=? AND active=1", (int(f["account_id"]),)) if (f.get("account_id") or "").isdigit() else None
        atype = acct["type"] if acct else (f.get("atype") if f.get("atype") in dict(ACCOUNT_TYPES) else "chequing")
        try:
            spec = json.loads(f.get("spec") or "")
            assert isinstance(spec, dict)
        except Exception:
            raise HTTPException(400, "The layout text is not valid JSON.")
        data = ingest.load_bytes(db, cfg, sid)
        try:
            p = csvfmt.parse(data, spec, atype) if st["kind"] == "csv" else pdftext.parse(pdftext.extract_lines(data, spec.get("extract")), spec, atype)
        except Exception as e:
            raise HTTPException(400, f"This layout cannot read the file: {e}")
        if not p.txns:
            raise HTTPException(400, "This layout finds no transactions in the file.")
        spec["account_type"] = atype
        kind = "csv" if st["kind"] == "csv" else "pdf-text"
        src = f.get("source") if f.get("source") in ("heuristic", "llm") else "heuristic"
        tid = ingest.save_template(db, kind, (f.get("name") or "Statement layout").strip()[:80],
                                   (f.get("institution") or "").strip()[:60], spec, created_by=src, template_id=st["template_id"])
        if st["template_id"]:
            for o in db.q("SELECT id FROM statements WHERE template_id=? AND id<>?", (tid, sid)):
                await run_in_threadpool(ingest.process, db, cfg, o["id"])
        await run_in_threadpool(ingest.process, db, cfg, sid, None, acct["id"] if acct else None, tid)
        # other statements that were waiting for exactly this kind of layout can now be filed
        for o in db.q("SELECT id FROM statements WHERE status='pending_template' AND kind=? AND id<>?", (st["kind"], sid)):
            await run_in_threadpool(ingest.process, db, cfg, o["id"])
        return RedirectResponse(f"/statements/{sid}", 303)
