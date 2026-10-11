"""Shared parsing helpers and result types for statement ingestion."""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional

LIABILITY = ("credit_card", "line_of_credit")
_AMT = re.compile(r"^\(?\s*-?\s*\$?\s*-?[\d,]*\.?\d+\s*\)?\s*(-|CR|DR|Cr|Dr|cr|dr)?$")


def is_amount(s: str) -> bool:
    return bool(_AMT.match((s or "").strip()))


def parse_amount(s: str):
    """-> (signed_cents, suffix) where the sign reflects '-', parentheses or a trailing '-'; suffix is CR/DR or None."""
    raw = (s or "").strip()
    m = _AMT.match(raw)
    if not m:
        raise ValueError(f"not an amount: {s!r}")
    suffix = (m.group(1) or "").upper() or None
    neg = "(" in raw or raw.lstrip("($ ").startswith("-") or "-" in raw.replace("$", "").strip()[:2] or suffix == "-"
    digits = re.sub(r"[^\d.]", "", raw.replace(",", ""))
    try:
        cents = int((Decimal(digits or "0") * 100).quantize(Decimal(1), ROUND_HALF_UP))
    except InvalidOperation:
        raise ValueError(f"not an amount: {s!r}")
    return (-cents if neg else cents), (suffix if suffix in ("CR", "DR") else None)


def norm_desc(s: str) -> str:
    s = (s or "").upper()
    s = re.sub(r"[#*]\s*\d+", " ", s)
    s = re.sub(r"\b\d{4,}\b", " ", s)
    s = re.sub(r"[^A-Z0-9&' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%y", "%d/%m/%y",
                "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%d %b %Y", "%d %B %Y", "%b %d", "%B %d", "%m/%d", "%m-%d", "%d %b"]


def _to_date(s, fmt, year):
    s = re.sub(r"\s+", " ", s.strip().replace(".", ""))
    f = fmt.replace(".", "")
    if "%Y" in f or "%y" in f:
        return datetime.strptime(s, f).date()
    return datetime.strptime(f"{s} {year}", f + " %Y").date()


def parse_date(s, formats, period_start: Optional[date] = None, period_end: Optional[date] = None) -> date:
    err = None
    for fmt in formats:
        try:
            if "%Y" in fmt or "%y" in fmt:
                return _to_date(s, fmt, 2000)
            years = []
            if period_end:
                years = [period_end.year, period_end.year - 1]
                if period_start:
                    years = sorted({period_start.year, period_end.year}, reverse=True)
            else:
                years = [date.today().year]
            for y in years:
                try:
                    d = _to_date(s, fmt, y)
                except ValueError:
                    continue
                if period_start and period_end:
                    if period_start - timedelta(days=3) <= d <= period_end + timedelta(days=3):
                        return d
                else:
                    return d
            d = _to_date(s, fmt, years[0])
            return d
        except ValueError as e:
            err = e
    raise ValueError(f"unparseable date {s!r}: {err}")


def dedupe_key(d: str, out_cents: int, norm: str, occurrence: int) -> str:
    return hashlib.sha1(f"{d}|{out_cents}|{norm}|{occurrence}".encode()).hexdigest()[:20]


@dataclass
class Txn:
    date: str
    desc: str
    out_cents: int                      # positive = money out
    fitid: Optional[str] = None
    balance_cents: Optional[int] = None


@dataclass
class Parsed:
    kind: str
    txns: list = field(default_factory=list)
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    opening_cents: Optional[int] = None
    closing_cents: Optional[int] = None
    opening_derived: bool = False
    account_hint: Optional[str] = None      # last four digits when the file reveals them
    account_type_hint: Optional[str] = None
    notes: list = field(default_factory=list)
    running_ok: Optional[bool] = None       # True/False when a running-balance column could verify rows


def reconcile(account_type: str, opening: Optional[int], closing: Optional[int], txns, tol: int = 1):
    """Returns (verdict, note). verdict: 'ok' | 'mismatch' | 'unverifiable'."""
    if opening is None or closing is None:
        return "unverifiable", "No opening and closing balances in this file, so the arithmetic cannot be checked."
    total = sum(t.out_cents for t in txns)
    expected = opening + total if account_type in LIABILITY else opening - total
    if abs(expected - closing) <= tol:
        return "ok", f"Opening {opening / 100:,.2f} plus the {len(txns)} transactions reaches the closing balance {closing / 100:,.2f}."
    return "mismatch", (f"Opening {opening / 100:,.2f} and {len(txns)} transactions give {expected / 100:,.2f}, "
                        f"but the statement closes at {closing / 100:,.2f} (off by {(expected - closing) / 100:,.2f}).")
