"""OFX / QFX statements (SGML or XML flavour). No template needed: the format is standardised."""
import re
from decimal import ROUND_HALF_UP, Decimal

from .common import LIABILITY, Parsed, Txn

_BLK = re.compile(r"<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>|</BANKTRANLIST>|</CCSTMTRS>|</STMTRS>))", re.S | re.I)


def looks_like_ofx(data: bytes) -> bool:
    head = data[:2000].upper()
    return b"OFXHEADER" in head or b"<OFX>" in head


def _tag(block, name):
    m = re.search(rf"<{name}>\s*([^<\r\n]*)", block, re.I)
    return m.group(1).strip() if m else None


def _date(s):
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if s and len(s) >= 8 else None


def _cents(s):
    return int((Decimal(s) * 100).quantize(Decimal(1), ROUND_HALF_UP))


def parse(data: bytes) -> Parsed:
    text = data.decode("utf-8", "replace") if b"\x00" not in data else data.decode("utf-16", "replace")
    out = Parsed(kind="ofx")
    card = bool(re.search(r"<CCSTMTRS>|<CREDITCARDMSGSRSV1>", text, re.I))
    out.account_type_hint = "credit_card" if card else "chequing"
    acct = _tag(text, "ACCTID")
    out.account_hint = re.sub(r"\D", "", acct)[-4:] if acct else None
    out.period_start, out.period_end = _date(_tag(text, "DTSTART")), _date(_tag(text, "DTEND"))
    for b in _BLK.findall(text):
        amt, dt = _tag(b, "TRNAMT"), _date(_tag(b, "DTPOSTED"))
        if amt is None or dt is None:
            continue
        name = _tag(b, "NAME") or ""
        memo = _tag(b, "MEMO") or ""
        out.txns.append(Txn(dt, (name + (" " + memo if memo and memo not in name else "")).strip(), -_cents(amt), _tag(b, "FITID")))
    lb = re.search(r"<LEDGERBAL>.*?<BALAMT>\s*([-\d.,]+)", text, re.S | re.I)
    if lb:
        bal = _cents(lb.group(1).replace(",", ""))
        out.closing_cents = -bal if card else bal           # cards: OFX shows owed money as negative
        total = sum(t.out_cents for t in out.txns)
        out.opening_cents = out.closing_cents - total if card else out.closing_cents + total
        out.opening_derived = True
    if out.txns and not out.period_start:
        out.period_start, out.period_end = min(t.date for t in out.txns), max(t.date for t in out.txns)
    return out
