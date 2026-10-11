"""Statement ingestion: recognise, parse, file, reconcile, and mark complete (or say why not)."""
import hashlib
import json
from dataclasses import dataclass
from datetime import date

from . import clock, crypto, ledger, spending
from .statements import csvfmt, ofx, pdftext
from .statements.common import LIABILITY, dedupe_key, norm_desc, reconcile

SUBDIR = "statements"


@dataclass
class Result:
    status: str
    statement_id: int | None
    message: str


def detect_kind(data: bytes):
    if data[:5] == b"%PDF-":
        return "pdf"
    if ofx.looks_like_ofx(data):
        return "ofx"
    if csvfmt.looks_like_csv(data):
        return "csv"
    return None


def templates(db, kind):
    rows = db.q("SELECT * FROM statement_templates WHERE kind=? AND active=1", ("pdf-text" if kind == "pdf" else kind,))
    return [{"id": r["id"], "version": r["version"], "name": r["name"], "spec": json.loads(r["spec"])} for r in rows]


def _files(cfg):
    return cfg.files_dir / SUBDIR


def load_bytes(db, cfg, sid):
    st = db.one("SELECT stored_name FROM statements WHERE id=?", (sid,))
    return crypto.load(cfg.file_key, _files(cfg), st["stored_name"])


def ingest(db, cfg, filename, data, account_id=None, now=None):
    now = now or clock.now()
    sha = hashlib.sha256(data).hexdigest()
    dup = db.one("SELECT id, status FROM statements WHERE file_sha256=?", (sha,))
    if dup:
        return Result("duplicate", dup["id"], "This exact file is already filed.")
    kind = detect_kind(data)
    if kind is None:
        return Result("unsupported", None, "Alberich reads PDF, CSV and OFX/QFX statements. This file is none of those.")
    stored = crypto.store(cfg.file_key, _files(cfg), data, suffix={"pdf": ".pdf", "csv": ".csv", "ofx": ".ofx"}[kind])
    sid = db.ex("""INSERT INTO statements(kind, file_sha256, original_name, stored_name, status, received_at)
                   VALUES(?,?,?,?,?,?)""", (kind, sha, (filename or "statement")[:200], stored, "received", clock.iso(now))).lastrowid
    return process(db, cfg, sid, data=data, account_id=account_id, now=now)


def _set(db, sid, **f):
    cols = ", ".join(f"{k}=?" for k in f)
    db.ex(f"UPDATE statements SET {cols} WHERE id=?", (*f.values(), sid))


def process(db, cfg, sid, data=None, account_id=None, template_id=None, now=None):
    now = now or clock.now()
    st = db.one("SELECT * FROM statements WHERE id=?", (sid,))
    data = data if data is not None else load_bytes(db, cfg, sid)
    kind = st["kind"]
    acct = db.one("SELECT * FROM accounts WHERE id=?", (account_id or st["account_id"],)) if (account_id or st["account_id"]) else None
    atype = acct["type"] if acct else None
    tmpl, lines = None, None
    try:
        if kind == "ofx":
            parsed = ofx.parse(data)
            atype = atype or parsed.account_type_hint or "chequing"
        elif kind == "csv":
            cands = templates(db, "csv")
            tmpl = next((t for t in cands if t["id"] == template_id), None) if template_id else csvfmt.identify(data, cands)
            if not tmpl:
                _set(db, sid, status="pending_template", note="A new kind of CSV. Alberich needs to learn its columns first.")
                return Result("pending_template", sid, "New CSV layout: set up a template.")
            atype = atype or tmpl["spec"].get("account_type") or "chequing"
            parsed = csvfmt.parse(data, tmpl["spec"], atype)
        else:
            lines = pdftext.extract_lines(data)
            cands = templates(db, "pdf")
            tmpl = next((t for t in cands if t["id"] == template_id), None) if template_id else pdftext.identify(lines, cands)
            if not tmpl:
                _set(db, sid, status="pending_template", note="A new kind of PDF statement. Alberich needs to learn its layout first.")
                return Result("pending_template", sid, "New PDF layout: set up a template.")
            atype = atype or tmpl["spec"].get("account_type") or "chequing"
            if tmpl["spec"].get("extract"):
                lines = pdftext.extract_lines(data, tmpl["spec"]["extract"])
            parsed = pdftext.parse(lines, tmpl["spec"], atype)
            fixed_spec, why = pdftext.fix_polarity(tmpl["spec"], lines, atype)      # a saved layout with money in and out swapped heals itself
            if why:
                parsed = pdftext.parse(lines, fixed_spec, atype)
                parsed.notes.append(why)
    except Exception as e:
        _set(db, sid, status="failed", note=f"Could not read this file: {e}"[:300])
        return Result("failed", sid, "Could not read this file.")

    quiet = parsed.opening_cents is not None and parsed.opening_cents == parsed.closing_cents
    if not parsed.txns and not quiet:
        _set(db, sid, status="failed", note="The template found no transactions in this file.",
             template_id=tmpl["id"] if tmpl else None, template_version=tmpl["version"] if tmpl else None)
        return Result("failed", sid, "No transactions were found. The template may not match this layout.")

    if acct is not None and parsed.account_hint and not acct["last4"]:
        db.ex("UPDATE accounts SET last4=? WHERE id=?", (parsed.account_hint, acct["id"]))
    if acct is None:
        cands = db.q("SELECT * FROM accounts WHERE active=1 AND type=?", (atype,)) if atype else []
        if parsed.account_hint:
            exact = [a for a in cands if a["last4"] == parsed.account_hint]
            cands = exact or [a for a in cands if not a["last4"]]
        if len(cands) == 1:
            acct = cands[0]
            if parsed.account_hint and not acct["last4"]:
                db.ex("UPDATE accounts SET last4=? WHERE id=?", (parsed.account_hint, acct["id"]))
        else:
            _set(db, sid, status="pending_account", period_start=parsed.period_start, period_end=parsed.period_end,
                 template_id=tmpl["id"] if tmpl else None, template_version=tmpl["version"] if tmpl else None,
                 note=("Which account is this? " + (f"It ends in {parsed.account_hint}. " if parsed.account_hint else "")
                       + ("Several accounts match." if len(cands) > 1 else "No account matches yet; add it first.")))
            return Result("pending_account", sid, "Choose which account this belongs to.")

    with db.tx():
        kept = _remember_decisions(db, sid)
        marks = [(r["dedupe_key"], r["kind"]) for r in db.q("SELECT dedupe_key, kind FROM transactions WHERE statement_id=? AND kind IN ('settlement','setaside')", (sid,))]
        db.ex("DELETE FROM transactions WHERE statement_id=?", (sid,))
        ledger.repair_pairs(db)
        seen, added, skipped = {}, 0, 0
        for t in parsed.txns:
            norm = norm_desc(t.desc)
            k0 = (t.date, t.out_cents, norm)
            seen[k0] = seen.get(k0, 0) + 1
            key = dedupe_key(t.date, t.out_cents, norm, seen[k0])
            if t.fitid and db.one("SELECT 1 FROM transactions WHERE account_id=? AND fitid=?", (acct["id"], t.fitid)):
                skipped += 1
                continue
            cur = db.ex("""INSERT OR IGNORE INTO transactions(account_id,statement_id,posted_date,amount_cents,description_raw,
                           description_norm,kind,dedupe_key,fitid,source,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (acct["id"], sid, t.date, t.out_cents, t.desc[:300], norm, "spend" if t.out_cents > 0 else "income",
                         key, t.fitid, "statement", clock.iso(now)))
            if cur.rowcount:
                added += 1
            else:
                skipped += 1

        verdict, note = reconcile(acct["type"], parsed.opening_cents, parsed.closing_cents, parsed.txns)
        if parsed.running_ok is True and verdict != "mismatch":
            verdict, note = "ok", "Every row's running balance follows from the row before it."
        elif parsed.running_ok is False:
            verdict, note = "mismatch", "A running balance in this file does not add up. A row may be missing, altered or misread."
        prev = None
        if parsed.period_start:
            prev = db.one("""SELECT closing_cents, period_end FROM statements WHERE account_id=? AND id<>? AND period_end<?
                             AND status IN ('reconciled','parsed') AND closing_cents IS NOT NULL ORDER BY period_end DESC LIMIT 1""",
                          (acct["id"], sid, parsed.period_start))
        if parsed.opening_derived and parsed.running_ok is None:
            if prev is None:
                verdict, note = "unverifiable", ("This is the first statement filed for this account, so its opening balance was "
                                                 "worked out from the file and cannot be checked independently.")
            elif prev["closing_cents"] == parsed.opening_cents:
                verdict, note = "ok", "Its opening balance equals the previous statement's closing balance."
            else:
                verdict, note = "unverifiable", (f"Its opening balance {parsed.opening_cents / 100:,.2f} (worked out from the file) differs from the previous "
                                                 f"statement's closing balance {prev['closing_cents'] / 100:,.2f}. A statement may be missing.")
        elif verdict == "ok" and prev is not None and parsed.opening_cents is not None and prev["closing_cents"] != parsed.opening_cents \
                and not parsed.opening_derived:
            note += (f" Note: the opening balance {parsed.opening_cents / 100:,.2f} differs from the previous statement's closing balance "
                     f"{prev['closing_cents'] / 100:,.2f}, so a statement may be missing.")
        if verdict == "mismatch" and parsed.opening_cents is not None and parsed.closing_cents is not None and parsed.running_ok is None:
            note += _suspect(parsed, acct["type"])
        if skipped:
            note += f" {skipped} transaction(s) were already filed from an overlapping statement and were not added twice."
        status = {"ok": "reconciled", "mismatch": "needs_review"}.get(verdict, "parsed")     # nothing to check against is not a reason to ask
        ins = sum(1 for x in parsed.txns if x.out_cents < 0)
        if acct["type"] in LIABILITY and len(parsed.txns) >= 8 and ins / len(parsed.txns) >= 0.9:
            status = "needs_review"
            note = ("Every line reads as money coming in, which is not how a credit card statement looks. The layout was probably taught as a bank account "
                    "instead of a card. Open 'Edit the layout' and choose Credit card, then check the preview. " + note)
        _restore_decisions(db, acct["id"], kept)
        for key, kind in marks:        # what the owner said a deposit was (a partner paying back, not income) is not forgotten
            db.ex("UPDATE transactions SET kind=? WHERE account_id=? AND dedupe_key=? AND kind='income'", (kind, acct["id"], key))
        ledger.after_ingest(db)
        spending.set_aside_non_pay(db, statement_id=sid)       # only pay counts as income unless the owner says a deposit does
        ledger.match_manual_repayments(db)                    # repayments entered by hand find their deposit in the new statement
        _set(db, sid, account_id=acct["id"], template_id=tmpl["id"] if tmpl else None,
             template_version=tmpl["version"] if tmpl else None, period_start=parsed.period_start, period_end=parsed.period_end,
             opening_cents=parsed.opening_cents, closing_cents=parsed.closing_cents,
             opening_derived=int(parsed.opening_derived), status=status, note=note, txn_count=len(parsed.txns))
        spending.sync_purse(db, cfg.tz, now)       # new statement, new averages: the Purse follows, and this month's purchases use up their credits
    return Result(status, sid, note)


def _suspect(parsed, atype):
    """When the totals are off, point at the likeliest culprit: one row that should not be there, or one whose direction is wrong."""
    liab = atype in LIABILITY
    total = sum(t.out_cents for t in parsed.txns)
    expected = (parsed.closing_cents - parsed.opening_cents) if liab else (parsed.opening_cents - parsed.closing_cents)
    diff = total - expected
    if diff == 0:
        return ""
    for t in parsed.txns:
        if t.out_cents == diff:
            return f" The difference is exactly {abs(diff) / 100:,.2f}, the amount of '{t.desc[:40]}' on {t.date}: that row may not belong, or may be read twice."
        if t.out_cents * 2 == diff:
            return f" The difference is exactly twice '{t.desc[:40]}' on {t.date} ({abs(t.out_cents) / 100:,.2f}): that row may have money in and out the wrong way round."
    return ""


def _remember_decisions(db, sid):
    rows = db.q("""SELECT t.dedupe_key, li.* FROM line_items li JOIN transactions t ON t.id=li.txn_id
                   WHERE t.statement_id=? AND li.reviewed=1""", (sid,))
    return [dict(r) for r in rows]


def _restore_decisions(db, account_id, kept):
    for k in kept:
        t = db.one("SELECT id FROM transactions WHERE account_id=? AND dedupe_key=?", (account_id, k["dedupe_key"]))
        if t and not db.val("SELECT 1 FROM line_items WHERE txn_id=?", (t["id"],)):
            db.ex("""INSERT INTO line_items(txn_id,receipt_id,description,amount_cents,category_id,my_bps,split_source,confident,why,reviewed,decided_at,created_at,item_key)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (t["id"], k["receipt_id"], k["description"], k["amount_cents"], k["category_id"], k["my_bps"],
                                                           k["split_source"], k["confident"], k["why"], k["reviewed"], k["decided_at"], k["created_at"], k["item_key"]))


def reprocess(db, cfg, sid, **kw):
    return process(db, cfg, sid, **kw)


def save_template(db, kind, name, institution, spec, created_by="heuristic", template_id=None, now=None):
    now = clock.iso(now or clock.now())
    spec = {**spec, "kind": spec.get("kind") or ("pdf-text" if kind == "pdf-text" else "csv")}
    if template_id:
        v = db.val("SELECT version FROM statement_templates WHERE id=?", (template_id,), 1)
        db.ex("UPDATE statement_templates SET spec=?, name=?, institution=?, version=?, created_by=? WHERE id=?",
              (json.dumps(spec), name, institution, v + 1, created_by, template_id))
        return template_id
    return db.ex("""INSERT INTO statement_templates(institution,name,kind,spec,version,created_by,created_at)
                    VALUES(?,?,?,?,1,?,?)""", (institution, name, kind, json.dumps(spec), created_by, now)).lastrowid


def accept(db, sid, reason):
    st = db.one("SELECT note FROM statements WHERE id=?", (sid,))
    _set(db, sid, status="reconciled", note=f"Accepted by you: {reason.strip()[:200]}. (Was: {(st['note'] or '')[:160]})")


def delete_statement(db, cfg, sid):
    st = db.one("SELECT stored_name FROM statements WHERE id=?", (sid,))
    with db.tx():
        kept = _remember_decisions(db, sid)
        db.ex("DELETE FROM transactions WHERE statement_id=?", (sid,))
        db.ex("DELETE FROM statements WHERE id=?", (sid,))
        ledger.repair_pairs(db)
    if st:
        (_files(cfg) / st["stored_name"]).unlink(missing_ok=True)
