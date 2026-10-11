"""The required-statements matrix: which accounts have which months filed, and how well."""
import calendar
from datetime import date, timedelta

GRACE_DAYS = 5
LABEL = {"complete": "Filed", "csv": "Unverified", "review": "Needs a look", "missing": "Missing", "soon": "Due soon",
         "future": "Not yet due", "na": "Before tracking"}


def month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def month_list(today: date, n: int = 12):
    y, m, out = today.year, today.month, []
    for _ in range(n):
        out.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def cycle_end(account, month: str) -> date:
    y, m = (int(x) for x in month.split("-"))
    dom = calendar.monthrange(y, m)[1]
    cd = account["cycle_day"]
    return date(y, m, min(cd, dom) if cd else dom)


def _covers(stmt, month: str) -> bool:
    if not stmt["period_end"]:
        return False
    pe = stmt["period_end"][:7]
    if stmt["kind"] == "pdf":
        return pe == month
    return (stmt["period_start"] or stmt["period_end"])[:7] <= month <= pe


def matrix(db, today: date, months: int = 12):
    cols = month_list(today, months)
    rows = []
    for a in db.q("SELECT * FROM accounts WHERE active=1 ORDER BY owner, institution, name"):
        stmts = db.q("SELECT * FROM statements WHERE account_id=?", (a["id"],))
        start = (a["track_from"] or "0000-00")[:7]
        cells = []
        for m in cols:
            mine = [s for s in stmts if _covers(s, m)]
            sid, status = None, None
            ok = [s for s in mine if s["status"] == "reconciled"]
            if ok:
                best = sorted(ok, key=lambda s: s["kind"] != "pdf")[0]
                sid = best["id"]
                status = "complete" if (best["kind"] == "pdf" or not a["require_pdf"]) else "csv"
            elif [s for s in mine if s["status"] == "parsed"]:
                s = [s for s in mine if s["status"] == "parsed"][0]
                sid, status = s["id"], "csv"
            elif mine:
                sid, status = mine[0]["id"], "review"
            elif m < start:
                status = "na"
            else:
                ce = cycle_end(a, m)
                status = "future" if today <= ce else "soon" if today <= ce + timedelta(days=GRACE_DAYS) else "missing"
            cells.append({"month": m, "status": status, "statement_id": sid, "label": LABEL[status]})
        rows.append({"account": a, "cells": cells})
    return cols, rows


def summary(rows):
    out = {"missing": 0, "review": 0, "soon": 0}
    for r in rows:
        for c in r["cells"]:
            if c["status"] in out:
                out[c["status"]] += 1
    return out
