"""Text-based PDF statements driven by a small declarative template."""
import json
import pathlib
import re
import subprocess
import sys

from .common import DATE_FORMATS, LIABILITY, Parsed, Txn, is_amount, parse_amount, parse_date

AMT = r"-?\$?-?\(?[\d,]+\.\d{2}\)?(?:-|\s?(?:CR|DR|Cr|Dr))?"
DATE_PATS = [
    (r"\d{4}-\d{2}-\d{2}", ["%Y-%m-%d"]),
    (r"\d{4}/\d{2}/\d{2}", ["%Y/%m/%d"]),
    (r"\d{1,2}/\d{1,2}/\d{4}", ["%m/%d/%Y", "%d/%m/%Y"]),
    (r"\d{1,2} [A-Za-z]{3,9}\.? \d{4}", ["%d %b %Y", "%d %B %Y"]),
    (r"\d{1,2}-[A-Za-z]{3}-\d{2}", ["%d-%b-%y"]),
    (r"\d{1,2}/\d{1,2}/\d{2}", ["%m/%d/%y", "%d/%m/%y"]),
    (r"[A-Za-z]{3,9}\.? \d{1,2},? \d{4}", ["%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"]),
    (r"\d{1,2}/\d{1,2}", ["%m/%d"]),
    (r"\d{1,2}-[A-Za-z]{3}-\d{4}", ["%d-%b-%Y"]),
    (r"[A-Za-z]{3}\.? \d{1,2}", ["%b %d"]),
    (r"\d{1,2} [A-Za-z]{3}\.?", ["%d %b"]),
]
# balance lines inside the table are not transactions, whatever the layout says
BALANCE_ROW = re.compile(r"^(?:[\w.,/-]+\s+){0,3}?(?:opening|closing|beginning|ending|previous|new|starting)\s*(?:total\s*)?balance\b|^(?:[\w.,/-]+\s+){0,2}?balance\s*forward\b", re.I)
KEYWORDS = re.compile(r"balance|period|account|statement|date|description|withdraw|deposit|debit|credit|amount|payment|purchase", re.I)


class Lines(list):
    """Text lines of a PDF; .mode records how they were read (None = plain text, "table" = by table geometry)."""
    mode = None


def extract_lines(data: bytes, mode=None):
    env = {"PYTHONPATH": str(pathlib.Path(__file__).resolve().parents[2]), "PATH": "/usr/bin:/bin"}
    p = subprocess.run([sys.executable, "-m", "finapp.statements.pdf_worker"] + (["table"] if mode == "table" else []), input=data, capture_output=True,
                       timeout=90, env=env)
    if p.returncode != 0:
        raise ValueError("The PDF could not be read as text (it may be scanned or damaged).")
    out = Lines(json.loads(p.stdout.decode()))
    out.mode = "table" if mode == "table" else None
    return out


def identify(lines, templates):
    head = "\n".join(lines[:120])
    best = None
    for t in templates:
        ident = t["spec"].get("identify", {})
        alls, anys = ident.get("all", []), ident.get("any", [])
        if alls and all(a in head for a in alls) and (not anys or any(a in head for a in anys)):
            if best is None or len(alls) > len(best["spec"]["identify"]["all"]):
                best = t
    return best


def _grab(text, spec_part):
    if not spec_part or not spec_part.get("regex"):
        return None
    m = re.search(spec_part["regex"], text, re.I | re.M)
    return m if m else None


def parse(lines, spec, account_type="chequing") -> Parsed:
    text = "\n".join(lines)
    out = Parsed(kind="pdf")
    pm = _grab(text, spec.get("period"))
    ps = pe = None
    if pm:
        fm = spec["period"].get("formats") or DATE_FORMATS
        try:
            pe = parse_date(pm.group("end"), fm)
            ps = parse_date(pm.group("start"), fm, None, pe)
            if ps > pe:
                ps = ps.replace(year=ps.year - 1)
            out.period_start, out.period_end = ps.isoformat(), pe.isoformat()
        except ValueError:
            out.notes.append("The statement period was found but its dates could not be read.")
    am = _grab(text, spec.get("account"))
    if am:
        digits = re.sub(r"\D", "", am.group(1))
        out.account_hint = digits[-4:] if digits else None
    raw_bal = {}
    for key, attr in (("opening", "opening_cents"), ("closing", "closing_cents")):
        m = _grab(text, spec.get(key))
        if m:
            try:
                v, suf = parse_amount(m.group("v"))
                raw_bal[key] = v
                setattr(out, attr, abs(v) if account_type in LIABILITY or v >= 0 else v)
            except ValueError:
                pass
    rows = spec.get("rows", {})
    rx = re.compile(rows["regex"])
    skips = [re.compile(s, re.I) for s in rows.get("skip", [])]
    fm = rows.get("date_formats") or DATE_FORMATS
    style = rows.get("style", "signed_negative_out")
    prev_bal = out.opening_cents
    liab = account_type in LIABILITY
    last_matched_idx = -2
    checked = bad = 0
    for i, ln in enumerate(lines):
        if any(s.search(ln) for s in skips) or BALANCE_ROW.match(ln.strip()):
            continue
        m = rx.match(ln.strip())
        if not m:
            if rows.get("continuation", True) and out.txns and i == last_matched_idx + 1 and not re.search(AMT + r"\s*$", ln) \
                    and len(ln.strip()) <= 60 and not KEYWORDS.search(ln):
                out.txns[-1].desc += " " + ln.strip()
                last_matched_idx = i
            continue
        g = m.groupdict()
        try:
            d = parse_date(g["date"], fm, ps, pe)
            bal = parse_amount(g["balance"])[0] if g.get("balance") else None
            if style == "split_columns":
                debit = parse_amount(g["debit"])[0] if g.get("debit") else 0
                credit = parse_amount(g["credit"])[0] if g.get("credit") else 0
                out_c = abs(debit) - abs(credit)
            else:
                v, suf = parse_amount(g["amount"])
                if style == "balance_delta" and bal is not None and prev_bal is not None:
                    out_c = (bal - prev_bal) if liab else (prev_bal - bal)
                    checked += 1                                  # the printed amount must equal what the balance actually moved by
                    bad += abs(abs(out_c) - abs(v)) > 1
                elif style == "signed_positive_out":
                    out_c = -abs(v) if (suf == "CR" or v < 0) else v
                else:
                    out_c = -v
                    if suf == "CR":
                        out_c = -abs(v)
                    elif suf == "DR":
                        out_c = abs(v)
        except (ValueError, KeyError):
            continue
        if spec.get("flip"):
            out_c = -out_c
        if bal is not None:
            prev_bal = bal
        out.txns.append(Txn(d.isoformat(), g["desc"].strip(), out_c, None, bal))
        last_matched_idx = i
    if out.txns and liab and any(v < 0 for v in raw_bal.values()) and len(raw_bal) == 2:
        # A negative card balance is ambiguous: "owed, shown negative" or "in credit". Keep whichever reading adds up.
        total = sum(t.out_cents for t in out.txns)
        for o, c in ((raw_bal["opening"], raw_bal["closing"]), (abs(raw_bal["opening"]), abs(raw_bal["closing"])),
                     (abs(raw_bal["opening"]), raw_bal["closing"]), (raw_bal["opening"], abs(raw_bal["closing"]))):
            if abs(o + total - c) <= 1:
                out.opening_cents, out.closing_cents = o, c
                break
    if checked >= 3:
        out.running_ok = bad == 0
    if out.txns:
        out.period_start = out.period_start or min(t.date for t in out.txns)
        out.period_end = out.period_end or max(t.date for t in out.txns)
    return out


# ---------------------------------------------------------------- drafting an unknown layout
def _row_regex(date_pat, two_dates, with_balance, extra=False, ref=False):
    d2 = rf"(?:\s+(?P<date2>{date_pat}))?" if two_dates else ""
    bal = rf"(?:\s+(?P<balance>{AMT}))?" if with_balance else (rf"(?:\s+(?:{AMT}|[\u2013\u2014-]))?" if extra else "")
    lead = r"(?:\d{1,8}\s+)?" if ref else ""      # a reference number before the dates
    return rf"^{lead}(?P<date>{date_pat}){d2}\s+(?P<desc>.+?)\s+(?P<amount>{AMT}){bal}$"


def fix_polarity(spec, lines, account_type):
    """If this layout's totals do not add up but do add up with money in and out swapped, swap them.
    -> (spec, note or None). Without balances to check, a card that reads mostly as payments is swapped too."""
    from .common import reconcile

    def read(sp):
        p = parse(lines, sp, account_type)
        return p, reconcile(account_type, p.opening_cents, p.closing_cents, p.txns)[0]
    try:
        p, v = read(spec)
        if v == "ok":
            return spec, None
        alt = {**spec, "flip": not spec.get("flip")}
        p2, v2 = read(alt)
    except Exception:
        return spec, None
    if v2 == "ok":
        return alt, "Money in and money out were the wrong way round, so they have been swapped: the totals now add up."
    n_out, n_in = sum(1 for t in p.txns if t.out_cents > 0), sum(1 for t in p.txns if t.out_cents < 0)
    if v == "unverifiable" and account_type in LIABILITY and n_in > n_out:
        return alt, "A card statement should be mostly purchases, so money in and out have been swapped. Check the preview."
    return spec, None


def best_draft(data, account_type="chequing"):
    """Reads the PDF both ways and keeps whichever draft makes the arithmetic work (or finds more transactions).
    -> (lines, spec or None, notes). The plain reading wins ties, so layouts that already work are unchanged."""
    from .common import reconcile
    cands = []
    for mode in (None, "table"):
        try:
            lines = extract_lines(data, mode)
        except ValueError:
            if mode is None:
                raise
            continue
        spec, notes = draft(lines, account_type)
        if spec is None:
            cands.append(((False, -1), lines, None, notes))
            continue
        if lines.mode:
            spec["extract"] = lines.mode
        spec, fixed = fix_polarity(spec, lines, account_type)
        if fixed:
            notes = notes + [fixed]
        try:
            p = parse(lines, spec, account_type)
            score = (reconcile(account_type, p.opening_cents, p.closing_cents, p.txns)[0] == "ok", len(p.txns))
        except Exception:
            score = (False, 0)
        cands.append((score, lines, spec, notes))
    best = max(cands, key=lambda c: c[0])
    if best[2] is None:
        table = next((c for c in cands if c[1].mode), None)
        return (table or cands[0])[1], None, cands[0][3]
    return best[1], best[2], best[3]


def draft(lines, account_type="chequing"):
    """-> (spec, notes) or (None, [reason]). Guesses; the owner confirms against a preview."""
    liab = account_type in LIABILITY
    tiers = (2, 1) if getattr(lines, "mode", None) == "table" else (3, 2)      # a statement can have very few transactions
    best = None
    for min_hits in tiers:
        for ref in (False, True):
            for pat, fmts in DATE_PATS:
                rx = re.compile(_row_regex(pat, True, True, ref=ref))
                hits = [rx.match(l.strip()) for l in lines]
                hits = [h for h in hits if h]
                if len(hits) >= min_hits and (best is None or len(hits) > len(best[2])):
                    best = (pat, fmts, hits, ref)
        if best:
            break
    if not best:
        return None, ["No block of dated transaction rows was found. This PDF may be scanned, or laid out in an unusual way."]
    pat, fmts, hits, ref = best
    with_bal = sum(1 for h in hits if h.group("balance")) >= max(min_hits, int(0.6 * len(hits)))
    extra = False
    if with_bal:      # a trailing figure is a running balance only if it moves by each row's amount; otherwise it is a side column (rewards, say)
        ok = n = 0
        prev = None
        for h in hits:
            if h.group("balance"):
                b, a = parse_amount(h.group("balance"))[0], abs(parse_amount(h.group("amount"))[0])
                if prev is not None:
                    n += 1
                    ok += abs(abs(b - prev) - a) <= 1
                prev = b
        if n and ok < 0.6 * n:
            with_bal, extra = False, True
    two_dates = sum(1 for h in hits if h.group("date2")) >= max(min(2, min_hits), int(0.6 * len(hits)))
    notes = []
    rows = {"regex": _row_regex(pat, two_dates, with_bal, extra, ref), "date_formats": fmts, "continuation": True,
            "skip": [r"^\s*Page \d+", r"continued"]}
    if with_bal:
        rows["style"] = "balance_delta"
    else:
        final = re.compile(rows["regex"])
        signs = [parse_amount(h.group("amount")) for h in (final.match(l.strip()) for l in lines) if h]
        rows["style"] = "signed_positive_out" if liab else "signed_negative_out"
        if not any(v < 0 or s == "CR" for v, s in signs):
            notes.append("Every amount has the same sign, so all rows are read as money out. Payments or deposits would need the CR/minus marker.")
    if len(fmts) > 1:
        notes.append("Slash dates may be month-first or day-first; month-first is tried before day-first.")
    spec = {"kind": "pdf-text", "rows": rows}
    head = [l.strip() for l in lines[:8]]
    ident = [l for l in head if 4 <= len(l) <= 60 and not re.search(r"\d", l) and re.search(r"[A-Za-z]{3}", l)][:2]
    if ident:
        spec["identify"] = {"all": ident}
    else:
        notes.append("No stable heading text was found to recognise this bank next time; add one in the template editor.")
    text = "\n".join(lines[:150])
    dpat = "|".join(p for p, _ in DATE_PATS if "," in p or "{4}" in p) or DATE_PATS[0][0]
    pm = re.search(rf"(?P<start>{dpat})\s*(?:to|-|–|through|thru)\s*(?P<end>{dpat})", text)
    if pm:
        fm = []
        for part in ("start", "end"):
            fm += [f for f in next((f for p, f in DATE_PATS if re.fullmatch(p, pm.group(part))), []) if f not in fm]
        spec["period"] = {"regex": rf"(?P<start>{dpat})\s*(?:to|-|–|through|thru)\s*(?P<end>{dpat})", "formats": fm}
    else:
        yearless = re.search(r"(?P<start>[A-Za-z]{3,9}\.? \d{1,2}),?\s*(?:to|-|–|through|thru)\s*(?P<end>[A-Za-z]{3,9}\.? \d{1,2},? \d{4})", text)
        if yearless:
            spec["period"] = {"regex": r"(?P<start>[A-Za-z]{3,9}\.? \d{1,2}),?\s*(?:to|-|–|through|thru)\s*(?P<end>[A-Za-z]{3,9}\.? \d{1,2},? \d{4})",
                              "formats": ["%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"]}
    AMT_D = r"-?\(?[\d,]+\.\d{2}\)?"
    for key, tries in (("opening", [r"(?:opening|previous|beginning)[ a-z]*balance", r"balance forward"]),
                       ("closing", [r"(?:closing|new|ending|statement)[ a-z]*balance", r"^total balance", r"^balance due"])):
        for words in tries:
            m = re.search(rf"(?P<label>{words})[^$\n]{{0,60}}?(?P<v>-?\$\s?{AMT_D})", text, re.I | re.M)
            tail = rf"[^$\n]{{0,60}}?(?P<v>-?\$\s?{AMT_D})"
            if not m:
                m = re.search(rf"(?P<label>{words})[^\d$(\-\n]*(?P<v>{AMT})", text, re.I | re.M)
                tail = rf"[^\d$(\-\n]*(?P<v>{AMT})"
            if m:
                at_start = m.start() == 0 or text[m.start() - 1] == "\n"
                lab = re.escape(m.group("label").lstrip("^"))
                spec[key] = {"regex": ("^" if at_start else "") + lab + tail}
                break
    masked = re.search(r"(?:account|acct|card)[^\n\d]{0,25}?(\d{4}(?:[ -]?[xX*\u2022]{4}){1,2}[ -]?\d{4})", text, re.I)
    am = re.search(r"(?P<label>(?:account|acct|card)[^\n\d]{0,25}?)(?P<num>\d[\d -]{4,}\d)", text, re.I)
    if masked:
        spec["account"] = {"regex": r"(?:account|acct|card)[^\n\d]{0,25}?(\d{4}(?:[ -]?[xX*\u2022]{4}){1,2}[ -]?\d{4})"}
    elif am:
        spec["account"] = {"regex": rf"{re.escape(am.group('label').strip())}[^\n\d]{{0,25}}?(\d[\d -]{{4,}}\d)"}
    if "period" not in spec:
        notes.append("No statement period line was found; the period will be taken from the first and last transaction dates.")
    if "opening" not in spec or "closing" not in spec:
        notes.append("Opening or closing balance lines were not found, so the arithmetic check may not be possible.")
    return spec, notes


# ---------------------------------------------------------------- masked excerpt for an LLM
_DATE_START = re.compile("^(" + "|".join(p for p, _ in DATE_PATS) + ")")


def _mask_digits(s):
    return re.sub(r"\d", "9", s)


def mask_excerpt(lines, max_rows=8):
    """Digits become 9; letters in transaction descriptions become x; headings and keyword lines stay verbatim.
    Returns the exact text that would be sent to a model, so the owner can read it first."""
    out, rows = [], 0
    for i, ln in enumerate(lines[:200]):
        s = ln.strip()
        dm = _DATE_START.match(s)
        if dm and re.search(AMT + r"\s*$", s):
            if rows >= max_rows:
                continue
            rows += 1
            head, rest = s[:dm.end()], s[dm.end():]
            rest = re.sub(r"(?<![A-Za-z])(CR|DR)(?![A-Za-z])", "\0\\1\0", rest)
            rest = re.sub(r"[A-Za-z]", "x", rest).replace("\0CR\0", "CR").replace("\0DR\0", "DR")
            out.append(_mask_digits(head + rest))
        elif i < 4 or KEYWORDS.search(s):
            out.append(_mask_digits(s))
    return "\n".join(out)


CARD_WORDS = re.compile(r"credit limit|minimum payment|payment due|previous (?:total )?balance|new balance|interest charges|rewards|mastercard|visa|amex", re.I)
BANK_WORDS = re.compile(r"withdrawals?|deposits?|cheques?|debits?\b|e-?transfer|chequing|savings", re.I)


def guess_account_type(lines):
    """Best guess at the kind of account from the statement's own wording."""
    head = "\n".join(lines[:150])
    card, bank = len(CARD_WORDS.findall(head)), len(BANK_WORDS.findall(head))
    return "credit_card" if card >= 2 and card > bank else "chequing"
