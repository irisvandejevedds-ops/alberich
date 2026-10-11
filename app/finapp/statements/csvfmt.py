"""CSV statements: header-signature templates with a draft-by-guessing step."""
import csv
import io
import re
from datetime import date

from .common import DATE_FORMATS, LIABILITY, Parsed, Txn, is_amount, parse_amount, parse_date

KEYS = {
    "date": {"date", "posted date", "transaction date", "trans date", "posting date", "date posted", "post date"},
    "desc": {"description", "details", "memo", "payee", "name", "transaction", "narrative", "merchant", "description 1"},
    "debit": {"debit", "debits", "withdrawal", "withdrawals", "money out", "paid out", "amount debit"},
    "credit": {"credit", "credits", "deposit", "deposits", "money in", "paid in", "amount credit"},
    "amount": {"amount", "transaction amount", "amount (cad)", "cad$", "value"},
    "balance": {"balance", "running balance", "available balance"},
}


def decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")


def _rows(text: str):
    sample = text[:4000]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delim = ","
    return delim, [r for r in csv.reader(io.StringIO(text), delimiter=delim)]


def _norm(h):
    return re.sub(r"\s+", " ", (h or "").strip().lower())


def _header_row(rows):
    for i, r in enumerate(rows[:15]):
        cells = [c.strip() for c in r if c.strip()]
        if len(cells) >= 3 and not any(is_amount(c) for c in cells) and any(_norm(c) in KEYS["date"] for c in r):
            return i
    return None


def signature(headers):
    return "|".join(sorted(_norm(h) for h in headers if _norm(h)))


def looks_like_csv(data: bytes) -> bool:
    try:
        _, rows = _rows(decode(data))
    except Exception:
        return False
    return _header_row(rows) is not None


def identify(data: bytes, templates):
    delim, rows = _rows(decode(data))
    hi = _header_row(rows)
    if hi is None:
        return None
    sig = signature(rows[hi])
    for t in templates:
        if t["spec"].get("signature") == sig:
            return t
    return None


def _guess_formats(samples):
    ok = [f for f in DATE_FORMATS if all(_try(s, f) for s in samples)]
    notes = []
    if "%m/%d/%Y" in ok and "%d/%m/%Y" in ok:
        first = [int(re.split(r"[/-]", s)[0]) for s in samples]
        second = [int(re.split(r"[/-]", s)[1]) for s in samples]
        if any(a > 12 for a in first):
            ok = [f for f in ok if f != "%m/%d/%Y"]
        elif any(b > 12 for b in second):
            ok = [f for f in ok if f != "%d/%m/%Y"]
        else:
            ok = [f for f in ok if f != "%d/%m/%Y"]
            notes.append("Dates like 03/04 could be day-first or month-first; month-first was assumed.")
    return ok[:2], notes


def _try(s, f):
    try:
        parse_date(s, [f], None, None)
        return True
    except Exception:
        return False


def draft(data: bytes, account_type: str = "chequing"):
    """-> (spec, notes) or (None, [reason])"""
    delim, rows = _rows(decode(data))
    hi = _header_row(rows)
    if hi is None:
        return None, ["No header row with a date column was found."]
    headers = rows[hi]
    cols = {}
    for i, h in enumerate(headers):
        for k, names in KEYS.items():
            if _norm(h) in names and k not in cols:
                cols[k] = headers[i].strip()
    if "date" not in cols or "desc" not in cols or not (("amount" in cols) or ("debit" in cols or "credit" in cols)):
        return None, [f"Could not match the columns automatically (found {sorted(cols)})."]
    body = [r for r in rows[hi + 1:] if any(c.strip() for c in r)]
    di = headers.index(next(h for h in headers if h.strip() == cols["date"]))
    samples = [r[di].strip() for r in body[:12] if len(r) > di and r[di].strip()]
    fmts, notes = _guess_formats(samples) if samples else (["%Y-%m-%d"], [])
    if not fmts:
        return None, ["The date column uses a format Alberich does not recognise."]
    spec = {"kind": "csv", "signature": signature(headers), "delimiter": delim, "columns": cols, "date_formats": fmts,
            "amount_style": "split" if ("debit" in cols or "credit" in cols) else "signed"}
    if spec["amount_style"] == "signed":
        ai = headers.index(next(h for h in headers if h.strip() == cols["amount"]))
        vals = [parse_amount(r[ai])[0] for r in body[:50] if len(r) > ai and is_amount(r[ai])]
        neg, pos = any(v < 0 for v in vals), any(v > 0 for v in vals)
        spec["signed_means"] = ("negative_is_out" if (neg and pos) or account_type not in LIABILITY else "positive_is_out")
        if not (neg and pos):
            notes.append(f"All amounts share one sign, so {'purchases' if spec['signed_means'] == 'positive_is_out' else 'withdrawals'} were assumed to be "
                         f"{'positive' if spec['signed_means'] == 'positive_is_out' else 'negative'}. Check the preview.")
    return spec, notes


def parse(data: bytes, spec: dict, account_type: str = "chequing") -> Parsed:
    delim, rows = _rows(decode(data))
    hi = _header_row(rows)
    headers = [h.strip() for h in rows[hi]]
    idx = {k: headers.index(v) for k, v in spec["columns"].items() if v in headers}
    out = Parsed(kind="csv")
    txns = []
    for r in rows[hi + 1:]:
        if len(r) <= idx["date"] or not r[idx["date"]].strip():
            continue
        try:
            d = parse_date(r[idx["date"]].strip(), spec["date_formats"])
        except ValueError:
            continue
        desc = r[idx["desc"]].strip() if "desc" in idx and len(r) > idx["desc"] else ""
        try:
            if spec["amount_style"] == "split":
                debit = parse_amount(r[idx["debit"]])[0] if "debit" in idx and len(r) > idx["debit"] and is_amount(r[idx["debit"]]) else 0
                credit = parse_amount(r[idx["credit"]])[0] if "credit" in idx and len(r) > idx["credit"] and is_amount(r[idx["credit"]]) else 0
                out_c = abs(debit) - abs(credit)
            else:
                v, suf = parse_amount(r[idx["amount"]])
                out_c = -v if spec.get("signed_means") == "negative_is_out" else v
                if suf == "CR":
                    out_c = -abs(v)
                elif suf == "DR":
                    out_c = abs(v)
        except (ValueError, IndexError):
            continue
        if spec.get("flip"):
            out_c = -out_c
        bal = None
        if "balance" in idx and len(r) > idx["balance"] and is_amount(r[idx["balance"]]):
            bal = parse_amount(r[idx["balance"]])[0]
        txns.append(Txn(d.isoformat(), desc, out_c, None, bal))
    out.txns = txns
    if txns:
        ordered = txns if txns[0].date <= txns[-1].date else list(reversed(txns))
        out.period_start, out.period_end = min(t.date for t in txns), max(t.date for t in txns)
        if all(t.balance_cents is not None for t in txns):
            liab = account_type in LIABILITY
            ok = True
            for a, b in zip(ordered, ordered[1:]):
                exp = a.balance_cents + b.out_cents if liab else a.balance_cents - b.out_cents
                if abs(exp - b.balance_cents) > 1:
                    ok = False
                    break
            out.running_ok = ok
            first = ordered[0]
            out.opening_cents = first.balance_cents - first.out_cents if liab else first.balance_cents + first.out_cents
            out.closing_cents = ordered[-1].balance_cents
            out.opening_derived = True
    return out
