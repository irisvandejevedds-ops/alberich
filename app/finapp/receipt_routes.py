"""Receipt capture, review and matching."""
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from fastapi import Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from . import llm, receipts
from .db import DB

CHIP = {"queued": ("blue", "Waiting to be read"), "processing": ("blue", "Reading"), "parsed": ("gold", "Waiting for its statement"),
        "matched": ("green", "Filed"), "needs_review": ("red", "Needs a look"), "failed": ("red", "Unreadable")}


def money_in(s):
    s = (s or "").replace("$", "").replace(",", "").strip()
    if not s:
        return None
    try:
        return int((Decimal(s) * 100).quantize(Decimal(1), ROUND_HALF_UP))
    except InvalidOperation:
        raise HTTPException(400, f"{s!r} is not an amount")


def register(app, cfg, get_db, session, render):
    def need(db, rid):
        r = db.one("SELECT * FROM receipts WHERE id=?", (rid,))
        if not r:
            raise HTTPException(404, "No such receipt")
        return r

    async def files_of(request):
        form = await request.form()
        return [(f.filename or "receipt", await f.read()) for f in form.getlist("files") if hasattr(f, "read")]

    @app.get("/receipts", response_class=HTMLResponse)
    def receipts_page(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        rows = db.q("""SELECT r.*, t.description_raw AS tdesc FROM receipts r LEFT JOIN transactions t ON t.id=r.txn_id
                       ORDER BY r.id DESC LIMIT 60""")
        busy = db.val("SELECT COUNT(*) FROM receipts WHERE status IN ('queued','processing')", (), 0)
        return render(request, db, "receipts.html", rows=rows, chip=CHIP, busy=busy, tool=_tesseract_ok())

    @app.post("/api/receipts/upload")
    async def upload(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        out = []
        for name, data in await files_of(request):
            rid, st = receipts.add(db, cfg, name, data)
            out.append({"name": name, "id": rid, "status": st})
        return JSONResponse({"results": out})

    @app.get("/api/receipts/busy")
    def busy(db: DB = Depends(get_db), _=Depends(session)):
        return {"busy": db.val("SELECT COUNT(*) FROM receipts WHERE status IN ('queued','processing')", (), 0)}

    @app.get("/receipts/{rid}", response_class=HTMLResponse)
    def detail(rid: int, request: Request, db: DB = Depends(get_db), _=Depends(session)):
        r = need(db, rid)
        lines = db.q("SELECT * FROM receipt_lines WHERE receipt_id=? ORDER BY pos", (rid,))
        txn = db.one("""SELECT t.*, a.name AS acct FROM transactions t JOIN accounts a ON a.id=t.account_id WHERE t.id=?""", (r["txn_id"],)) if r["txn_id"] else None
        cands = [] if txn else receipts.candidates(db, r)
        li = db.q("SELECT * FROM line_items WHERE receipt_id=?", (rid,)) if txn else []
        return render(request, db, "receipt.html", r=r, lines=lines, txn=txn, cands=cands, chip=CHIP, li_count=len(li), llm=llm.status(db, cfg),
                      lines_sum=sum(x["amount_cents"] for x in lines))

    @app.get("/receipts/{rid}/image")
    def image(rid: int, db: DB = Depends(get_db), _=Depends(session)):
        r = need(db, rid)
        data = receipts.load_bytes(db, cfg, rid)
        if r["kind"] == "pdf":
            return Response(data, media_type="application/pdf")
        from PIL import Image
        import io
        fmt = (Image.open(io.BytesIO(data)).format or "JPEG").lower()
        return Response(data, media_type=f"image/{'jpeg' if fmt == 'mpo' else fmt}")

    @app.post("/receipts/{rid}/save")
    async def save(rid: int, request: Request, db: DB = Depends(get_db), _=Depends(session)):
        need(db, rid)
        f = await request.form()
        lines = []
        for d, a in zip(f.getlist("desc"), f.getlist("amt")):
            d = (d or "").strip()[:80]
            if d and (a or "").strip():
                lines.append((d, money_in(a)))
        date_s = (f.get("date") or "").strip() or None
        if date_s and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_s):
            raise HTTPException(400, "date must look like 2026-09-12")
        receipts.save_edit(db, rid, f.get("merchant") or "", date_s, money_in(f.get("total")), money_in(f.get("tax")), lines, accept=f.get("accept") == "on")
        return RedirectResponse(f"/receipts/{rid}", 303)

    @app.post("/receipts/{rid}/match")
    async def match(rid: int, request: Request, db: DB = Depends(get_db), _=Depends(session)):
        need(db, rid)
        f = await request.form()
        t = db.one("SELECT id FROM transactions WHERE id=?", (int(f["txn_id"]),))
        if not t:
            raise HTTPException(404, "No such transaction")
        receipts.link(db, rid, t["id"])
        return RedirectResponse(f"/receipts/{rid}", 303)

    @app.post("/receipts/{rid}/unlink")
    def unlink(rid: int, db: DB = Depends(get_db), _=Depends(session)):
        need(db, rid)
        receipts.unlink(db, rid)
        return RedirectResponse(f"/receipts/{rid}", 303)

    @app.post("/receipts/{rid}/reread")
    def reread(rid: int, db: DB = Depends(get_db), _=Depends(session)):
        need(db, rid)
        receipts.unlink(db, rid)
        db.ex("UPDATE receipts SET status='queued', accepted=0 WHERE id=?", (rid,))
        return RedirectResponse(f"/receipts/{rid}", 303)

    @app.post("/receipts/{rid}/model")
    async def model(rid: int, request: Request, db: DB = Depends(get_db), _=Depends(session)):
        need(db, rid)
        f = await request.form()
        if f.get("consent") != "on":
            raise HTTPException(400, "Tick the box to agree to send this picture.")
        receipts.unlink(db, rid)
        try:
            await run_in_threadpool(receipts.read_with_model, db, cfg, rid)
        except llm.LLMUnavailable as e:
            raise HTTPException(400, str(e))
        return RedirectResponse(f"/receipts/{rid}", 303)

    @app.post("/receipts/{rid}/delete")
    def delete(rid: int, db: DB = Depends(get_db), _=Depends(session)):
        need(db, rid)
        receipts.delete(db, cfg, rid)
        return RedirectResponse("/receipts", 303)


def _tesseract_ok():
    import shutil
    return shutil.which("tesseract") is not None
