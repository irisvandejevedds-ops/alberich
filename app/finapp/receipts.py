"""Receipt photos: read, understand, check the arithmetic, match to the bank, and explode into line items."""
import io
import json
import os
import re
import resource
import subprocess
from datetime import date, timedelta

from . import clock, crypto, ledger
from .statements.common import parse_date

SUBDIR = "receipts"
MAX_BYTES = 15 * 1024 * 1024
MAX_PIXELS = 60_000_000
OCR_LONG_EDGE = 2200
OCR_MEM = 1_500_000_000

AMT = r"-?\$?\s?\d[\d,]*\.\d{2}"
ITEM_RX = re.compile(rf"^(?P<desc>.*?[A-Za-z].*?)\s+(?P<amt>{AMT})\s*(?P<neg>-)?\s*(?P<flag>[A-Z0-9]{{1,3}})?\s*$")
ANY_AMT_END = re.compile(rf"(?P<amt>{AMT})\s*(?P<neg>-)?\s*(?:[A-Z0-9]{{1,3}})?\s*$")
SUBTOTAL = re.compile(r"sub[\s-]?total", re.I)
TAX = re.compile(r"\b(hst|gst|pst|qst|tvq|tps|tvh|vat|sales tax|tax)\b", re.I)
TOTAL = re.compile(r"\b(grand\s+total|total\s+due|amount\s+due|balance\s+due|total|amount)\b", re.I)
NOT_ITEM = re.compile(r"\b(visa|mastercard|master\s?card|debit|credit|cash|change|tender|interac|amex|payment|approved|auth|account|card|"
                      r"savings|you saved|items?\s+sold|balance|points?|rewards?|terminal|ref|trans|invoice|order)\b", re.I)
SKIP_TOTAL_WORDS = re.compile(r"total\s+(savings|items|discount|saved|points)", re.I)
MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()


def pix(n):
    return f"{n / 100:.2f}"


def to_cents(s):
    s = s.replace("$", "").replace(" ", "").replace(",", "")
    neg = s.startswith("-")
    v = round(abs(float(s)) * 100)
    return -v if neg else v


def clean(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w .,'&/#%@()+*-]", " ", s)).strip()


# ---------------------------------------------------------------- understanding the text
def find_date(text, today):
    lo, hi = today - timedelta(days=365 * 5), today + timedelta(days=1)
    pats = [(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b", "ymd"),
            (r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})\b", "mdy"),
            (r"\b(" + "|".join(MONTHS) + r")[a-z]*\.?\s+(\d{1,2}),?\s+(20\d{2})\b", "mon"),
            (r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")[a-z]*\.?,?\s+(20\d{2})\b", "dmon")]
    found = []
    for rx, kind in pats:
        for m in re.finditer(rx, text, re.I):
            g = m.groups()
            try:
                if kind == "ymd":
                    d = date(int(g[0]), int(g[1]), int(g[2]))
                elif kind == "mdy":
                    a, b, y = int(g[0]), int(g[1]), int(g[2])
                    y = y + 2000 if y < 100 else y
                    d = date(y, a, b) if a <= 12 else date(y, b, a)
                    if not lo <= d <= hi and b <= 12:
                        d = date(y, b, a) if a <= 31 else d
                elif kind == "mon":
                    d = date(int(g[2]), MONTHS.index(g[0][:3].lower()) + 1, int(g[1]))
                else:
                    d = date(int(g[2]), MONTHS.index(g[1][:3].lower()) + 1, int(g[0]))
            except ValueError:
                continue
            if lo <= d <= hi:
                found.append((m.start(), d))
    return min(found)[1].isoformat() if found else None


def find_merchant(lines):
    for ln in lines[:10]:
        s = clean(ln)
        letters = len(re.findall(r"[A-Za-z]", s))
        if letters < 3 or re.search(r"receipt|invoice|welcome|thank|www\.|\.com|tel|phone|store\s*#|\d{3}[- .]\d{3}[- .]\d{4}", s, re.I):
            continue
        if re.search(AMT, s) or re.match(r"^[\d\W]+$", s):
            continue
        return s[:60]
    return ""


def parse_text(text, today=None):
    today = today or clock.now().date()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out = {"merchant": find_merchant(lines), "date": find_date(text, today), "total": None, "subtotal": None, "tax": None, "lines": []}
    totals, taxes = [], []
    for ln in lines:
        m = ANY_AMT_END.search(ln)
        if not m:
            continue
        try:
            val = to_cents(m.group("amt"))
        except ValueError:
            continue
        if m.group("neg"):
            val = -abs(val)
        label = ln[:m.start()]
        if SUBTOTAL.search(label):
            out["subtotal"] = val
        elif TAX.search(label) and not re.search(r"total\s+(before|incl)", label, re.I):
            taxes.append(val)
        elif TOTAL.search(label) and not SKIP_TOTAL_WORDS.search(label):
            totals.append(val)
        elif NOT_ITEM.search(label):
            continue
        else:
            im = ITEM_RX.match(ln)
            if im:
                d = clean(im.group("desc"))
                if len(re.findall(r"[A-Za-z]", d)) >= 2:
                    out["lines"].append((d[:80], val))
    if taxes:
        out["tax"] = sum(taxes)
    if totals:
        out["total"] = max(totals)
    elif out["lines"]:
        out["total"] = None
    return out


def validate(p):
    lines_sum = sum(a for _, a in p["lines"])
    tot, sub, tax = p["total"], p["subtotal"], p["tax"] or 0
    if tot is None:
        return "incomplete", "No total was found on the receipt."
    if not p["lines"]:
        return "incomplete", "No item lines were found, so only the total is known."
    if sub is not None and abs(lines_sum - sub) <= 1:
        if abs(sub + tax - tot) <= 1:
            return "ok", "The items add up to the subtotal, and subtotal plus tax equals the total."
        return "ok", "The items add up to the subtotal. (Tax and total differ a little; the bank amount will settle it.)"
    if abs(lines_sum + tax - tot) <= 1:
        return "ok", "The items plus tax add up to the total."
    if abs(lines_sum - tot) <= 1:
        return "ok", "The items add up to the total (prices include tax)."
    return "mismatch", (f"The items add up to {lines_sum / 100:,.2f} but the receipt says {tot / 100:,.2f}. "
                        "A line was probably missed or misread. Fix the lines below, or accept it as it is.")


# ---------------------------------------------------------------- reading the picture
def _prep(data, long_edge=OCR_LONG_EDGE):
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)
    if max(im.size) > long_edge:
        im.thumbnail((long_edge, long_edge))
    return im


def sniff(data):
    """-> 'pdf' | 'image' | None"""
    if data[:5] == b"%PDF-":
        return "pdf"
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        return "image"
    except Exception:
        return None


def _limits():
    resource.setrlimit(resource.RLIMIT_AS, (OCR_MEM, OCR_MEM))
    os.nice(5)


def ocr_image(data, lang="eng"):
    from PIL import ImageOps
    im = _prep(data).convert("L")
    im = ImageOps.autocontrast(im, cutoff=1)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    env = {"PATH": "/usr/bin:/bin", "OMP_THREAD_LIMIT": "1", "LC_ALL": "C"}
    p = subprocess.run(["tesseract", "stdin", "stdout", "-l", lang, "--psm", "6"], input=buf.getvalue(), capture_output=True,
                       timeout=150, env=env, preexec_fn=_limits)
    if p.returncode != 0:
        raise RuntimeError("tesseract failed")
    return p.stdout.decode("utf-8", "replace")


def pdf_text_or_ocr(data):
    from .statements import pdftext
    try:
        lines = pdftext.extract_lines(data)
        if len(" ".join(lines)) > 40:
            return "\n".join(lines), "text"
    except Exception:
        pass
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(data)
    img = pdf[0].render(scale=2.2).to_pil()
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return ocr_image(buf.getvalue()), "tesseract"


# ---------------------------------------------------------------- storage and queue
def _dir(cfg):
    return cfg.files_dir / SUBDIR


def load_bytes(db, cfg, rid):
    r = db.one("SELECT stored_name FROM receipts WHERE id=?", (rid,))
    return crypto.load(cfg.file_key, _dir(cfg), r["stored_name"])


def add(db, cfg, name, data, now=None):
    """-> (receipt_id, 'queued' | 'duplicate' | 'unsupported' | 'too_big')"""
    import hashlib
    if len(data) > MAX_BYTES:
        return None, "too_big"
    kind = sniff(data)
    if kind is None:
        return None, "unsupported"
    sha = hashlib.sha256(data).hexdigest()
    dup = db.one("SELECT id FROM receipts WHERE sha256=?", (sha,))
    if dup:
        return dup["id"], "duplicate"
    stored = crypto.store(cfg.file_key, _dir(cfg), data, suffix=".pdf" if kind == "pdf" else ".img")
    rid = db.ex("INSERT INTO receipts(sha256,stored_name,original_name,kind,status,created_at) VALUES(?,?,?,?,?,?)",
                (sha, stored, (name or "receipt")[:120], kind, "queued", clock.iso(now or clock.now()))).lastrowid
    return rid, "queued"


def apply_parse(db, rid, p, engine, now=None):
    verdict, note = validate(p)
    db.ex("DELETE FROM receipt_lines WHERE receipt_id=?", (rid,))
    for i, (d, a) in enumerate(p["lines"]):
        db.ex("INSERT INTO receipt_lines(receipt_id,pos,description,amount_cents) VALUES(?,?,?,?)", (rid, i, d, a))
    db.ex("""UPDATE receipts SET merchant=?,purchase_date=?,total_cents=?,subtotal_cents=?,tax_cents=?,verdict=?,note=?,ocr_engine=?,
             processed_at=?,accepted=0 WHERE id=?""",
          (p["merchant"], p["date"], p["total"], p["subtotal"], p["tax"], verdict, note, engine, clock.iso(now or clock.now()), rid))
    complete = verdict == "ok" and p["date"] and p["merchant"]
    db.ex("UPDATE receipts SET status=? WHERE id=?", ("parsed" if complete else "needs_review", rid))
    settle(db, rid)


def process(db, cfg, rid, now=None):
    r = db.one("SELECT * FROM receipts WHERE id=?", (rid,))
    db.ex("UPDATE receipts SET status='processing' WHERE id=?", (rid,))
    try:
        data = load_bytes(db, cfg, rid)
        if r["kind"] == "pdf":
            text, engine = pdf_text_or_ocr(data)
        else:
            text, engine = ocr_image(data), "tesseract"
    except Exception as e:
        db.ex("UPDATE receipts SET status='failed', note=?, processed_at=? WHERE id=?",
              (f"Could not read this picture ({type(e).__name__}). Try a flatter, brighter photo, or have the Warlock read it.", clock.iso(now or clock.now()), rid))
        return
    db.ex("UPDATE receipts SET ocr_text=? WHERE id=?", (text, rid))
    apply_parse(db, rid, parse_text(text, (now or clock.now()).astimezone(cfg.tz).date()), engine, now)


def process_queue(db, cfg, limit=1):
    n = 0
    for r in db.q("SELECT id FROM receipts WHERE status='queued' ORDER BY id LIMIT ?", (limit,)):
        process(db, cfg, r["id"])
        n += 1
    db.ex("UPDATE receipts SET status='queued' WHERE status='processing' AND processed_at IS NULL AND ocr_text='' AND 0")
    return n


def recover_stuck(db):
    """A crash mid-read leaves 'processing' forever; put such receipts back in the queue at worker start."""
    db.ex("UPDATE receipts SET status='queued' WHERE status='processing'")


# ---------------------------------------------------------------- matching to the bank
def tokens(s):
    return {w for w in re.findall(r"[A-Za-z]{3,}", s.upper())}


def candidates(db, r):
    if r["total_cents"] is None or not r["purchase_date"]:
        return []
    d = date.fromisoformat(r["purchase_date"])
    lo, hi = (d - timedelta(days=2)).isoformat(), (d + timedelta(days=10)).isoformat()
    mt = tokens(r["merchant"])
    out = []
    for t in db.q("""SELECT t.*, a.name AS acct FROM transactions t JOIN accounts a ON a.id=t.account_id
                     WHERE t.kind='spend' AND t.amount_cents>0 AND t.posted_date BETWEEN ? AND ?
                     AND NOT EXISTS (SELECT 1 FROM receipts x WHERE x.txn_id=t.id AND x.id<>?)""", (lo, hi, r["id"])):
        exact = t["amount_cents"] == r["total_cents"]
        tip = r["total_cents"] < t["amount_cents"] <= r["total_cents"] * (1 + int(db.get_setting("tip_max_pct", "30")) / 100)
        sim = len(mt & tokens(t["description_raw"]))
        if not (exact or (tip and sim)):
            continue
        gap = abs((date.fromisoformat(t["posted_date"]) - d).days)
        out.append({"txn": t, "exact": exact, "sim": sim, "score": (100 if exact else 40) + 10 * sim - gap})
    return sorted(out, key=lambda c: -c["score"])


def settle(db, rid):
    """Try to match automatically; explode into line items once it is safe."""
    r = db.one("SELECT * FROM receipts WHERE id=?", (rid,))
    if r["txn_id"] is None and r["status"] in ("parsed", "needs_review", "matched"):
        cands = candidates(db, r)
        exact = [c for c in cands if c["exact"]]
        pick = None
        if len(exact) == 1:
            pick = exact[0]
        elif len(exact) > 1 and len([c for c in exact if c["sim"] > 0]) == 1:
            pick = [c for c in exact if c["sim"] > 0][0]
        elif not exact and len(cands) == 1:
            pick = cands[0]
        if pick:
            db.ex("UPDATE receipts SET txn_id=? WHERE id=?", (pick["txn"]["id"], rid))
            r = db.one("SELECT * FROM receipts WHERE id=?", (rid,))
    if r["txn_id"] is not None and (r["verdict"] == "ok" or r["accepted"]):
        explode(db, rid)


def match_pending(db):
    for r in db.q("SELECT id FROM receipts WHERE txn_id IS NULL AND status IN ('parsed','needs_review','matched')"):
        settle(db, r["id"])


def link(db, rid, txn_id):
    db.ex("UPDATE receipts SET txn_id=? WHERE id=?", (txn_id, rid))
    r = db.one("SELECT * FROM receipts WHERE id=?", (rid,))
    if r["verdict"] == "ok" or r["accepted"]:
        explode(db, rid)


def unlink(db, rid):
    r = db.one("SELECT txn_id FROM receipts WHERE id=?", (rid,))
    if r and r["txn_id"]:
        db.ex("DELETE FROM line_items WHERE txn_id=? AND receipt_id=?", (r["txn_id"], rid))
    db.ex("UPDATE receipts SET txn_id=NULL, status=CASE WHEN verdict='ok' THEN 'parsed' ELSE 'needs_review' END WHERE id=?", (rid,))


# ---------------------------------------------------------------- receipt lines -> line items
def item_key(desc):
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z ]", " ", desc.upper())).strip()[:40] or None


def allocate_tax(amounts, tax):
    """Share tax over positive lines in proportion, exactly (largest remainder)."""
    pos = [i for i, a in enumerate(amounts) if a > 0]
    base = sum(amounts[i] for i in pos)
    if not pos or base <= 0 or tax == 0:
        return [0] * len(amounts)
    raw = {i: tax * amounts[i] / base for i in pos}
    out = {i: int(raw[i]) if raw[i] >= 0 else -int(-raw[i]) for i in pos}
    left = tax - sum(out.values())
    for i in sorted(pos, key=lambda i: -(abs(raw[i] - out[i]))):
        if left == 0:
            break
        step = 1 if left > 0 else -1
        out[i] += step
        left -= step
    return [out.get(i, 0) for i in range(len(amounts))]


def explode(db, rid, now=None):
    r = db.one("SELECT * FROM receipts WHERE id=?", (rid,))
    t = db.one("SELECT * FROM transactions WHERE id=?", (r["txn_id"],)) if r["txn_id"] else None
    if not t or t["amount_cents"] <= 0 or t["kind"] != "spend":
        return
    rl = db.q("SELECT description, amount_cents FROM receipt_lines WHERE receipt_id=? ORDER BY pos", (rid,))
    if not rl:
        return
    amts = [x["amount_cents"] for x in rl]
    s = sum(amts)
    tax = r["tax_cents"] or 0
    included = abs(s - (r["total_cents"] or 0)) <= 1 and tax != 0 and abs(s + tax - (r["total_cents"] or 0)) > 1
    shares = [0] * len(amts) if included else allocate_tax(amts, tax)
    final = [a + sh for a, sh in zip(amts, shares)]
    diff = t["amount_cents"] - sum(final)
    rows = [(x["description"], f) for x, f in zip(rl, final)]
    if diff:
        rows.append(("Tip and other charges" if diff > 0 else "Adjustment", diff))
    now_s = clock.iso(now or clock.now())
    merch = t["merchant_id"]
    default_cat = db.val("SELECT default_category_id FROM merchants WHERE id=?", (merch,)) or ledger.category_by_name(db, "Other")
    have = [(x["description"], x["amount_cents"]) for x in
            db.q("SELECT description, amount_cents FROM line_items WHERE txn_id=? AND receipt_id=? ORDER BY id", (t["id"], rid))]
    if have and have == rows:
        db.ex("UPDATE receipts SET status='matched' WHERE id=?", (rid,))
        return
    decided_before = db.val("SELECT COUNT(*) FROM line_items WHERE txn_id=? AND reviewed=1", (t["id"],), 0) > 0
    db.ex("DELETE FROM line_items WHERE txn_id=?", (t["id"],))
    for desc, amt in rows:
        key = item_key(desc)
        learned = db.val("""SELECT category_id FROM line_items WHERE item_key=? AND reviewed=1 ORDER BY decided_at DESC LIMIT 1""", (key,)) if key else None
        cat = learned or default_cat
        sg = ledger.suggest(db, merch, cat, item_key=key)
        # a receipt always trumps what was chosen for the purchase before: its items come back for the owner's call, with the learner's proposal
        sure = int(sg["confident"]) and not decided_before
        why = ("A receipt came in for a purchase you had already decided, so each item is back for your call. " + sg["why"]) if decided_before else sg["why"]
        db.ex("""INSERT INTO line_items(txn_id,receipt_id,description,amount_cents,category_id,my_bps,split_source,confident,why,item_key,created_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (t["id"], rid, desc, amt, cat, sg["bps"], sg["source"], sure, why, key, now_s))
    db.ex("UPDATE receipts SET status='matched' WHERE id=?", (rid,))


def save_edit(db, rid, merchant, date_s, total, tax, lines, accept=False):
    """Apply the owner's corrections, re-check, and re-match. `lines` = [(description, cents)]."""
    p = {"merchant": merchant.strip()[:60], "date": date_s or None, "total": total, "subtotal": None, "tax": tax, "lines": lines}
    old = db.one("SELECT txn_id FROM receipts WHERE id=?", (rid,))
    if old and old["txn_id"]:
        db.ex("DELETE FROM line_items WHERE txn_id=? AND receipt_id=?", (old["txn_id"], rid))
    apply_parse(db, rid, p, "edited")
    if accept:
        db.ex("UPDATE receipts SET accepted=1 WHERE id=?", (rid,))
        settle(db, rid)


def delete(db, cfg, rid):
    r = db.one("SELECT stored_name FROM receipts WHERE id=?", (rid,))
    if not r:
        return
    unlink(db, rid)
    db.ex("DELETE FROM receipts WHERE id=?", (rid,))
    (_dir(cfg) / r["stored_name"]).unlink(missing_ok=True)


def read_with_model(db, cfg, rid, client=None):
    from . import llm
    im = _prep(load_bytes(db, cfg, rid) if db.val("SELECT kind FROM receipts WHERE id=?", (rid,)) == "image" else _pdf_first_page(load_bytes(db, cfg, rid)), 1568)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=85)
    p = llm.read_receipt(db, cfg, buf.getvalue(), client=client)
    if p["date"]:
        try:
            date.fromisoformat(p["date"])
        except ValueError:
            p["date"] = None
    apply_parse(db, rid, p, "model")


def _pdf_first_page(data):
    import pypdfium2 as pdfium
    img = pdfium.PdfDocument(data)[0].render(scale=2.0).to_pil().convert("RGB")
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()
