"""Runs in a subprocess with CPU and memory limits: reads a PDF on stdin, prints its text lines as JSON.

Mode "table" reads each transaction table by its geometry. It finds the header row, works out what every column is (date, description,
withdrawal, deposit, amount, balance, anything else) from the header labels, then places every word by its position. That is what lets it
cope with separate debit/credit columns, side columns and sidebars, descriptions wrapped above or below their row, dates printed once
per day, headers that wrap onto two lines, and tables that carry on over several pages. Each transaction comes out as one plain line:
dates, description, amounts (a CR or DR marker is added where the column, not the sign, says which way the money went)."""
import io
import json
import re
import resource
import sys

MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
MONTH = re.compile(rf"^{MONTHS}$", re.I)
DIGIT_DATE = re.compile(r"^(\d{4}[-/.]\d{2}[-/.]\d{2}|\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?|\d{1,2}[- ]" + MONTHS + r"(?:[- ,]+\d{2,4})?|" + MONTHS + r"[- ]\d{1,2}(?:,?[- ]\d{2,4})?)$", re.I)
DAY = re.compile(r"^\d{1,2}(?:st|nd|rd|th)?,?$")
YEAR = re.compile(r"^\d{4}$")
AMOUNT = re.compile(r"^[-–(]?\$?[-–]?[\d,]+\.\d{2}\)?(?:-|CR|DR|Cr|Dr)?$")
DASH = re.compile(r"^[–—-]+$")
SKIP_ROW = re.compile(r"total|balance forward|opening|closing|previous|new balance|subtotal|carried|brought|minimum|credit limit|available", re.I)
NUMERIC = ("amount", "debit", "credit", "balance")
MONEY = ("amount", "debit", "credit")


def _lines(words, tol=3):
    out = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if out and abs(out[-1]["top"] - w["top"]) <= tol:
            out[-1]["words"].append(w)
        else:
            out.append({"top": w["top"], "words": [w]})
    for ln in out:
        ln["words"].sort(key=lambda w: w["x0"])
        ln["tokens"] = [w["text"] for w in ln["words"]]
    return out


def _one_date(tokens, i):
    """Length in tokens of a date starting at tokens[i], or 0."""
    if i >= len(tokens):
        return 0
    t = tokens[i]
    if DIGIT_DATE.match(t):
        return 1
    if MONTH.match(t) and i + 1 < len(tokens) and DAY.match(tokens[i + 1]):
        return 3 if i + 2 < len(tokens) and YEAR.match(tokens[i + 2]) else 2
    if re.match(r"^\d{1,2}$", t) and i + 1 < len(tokens) and MONTH.match(tokens[i + 1]):
        return 3 if i + 2 < len(tokens) and YEAR.match(tokens[i + 2]) else 2
    return 0


def _date_len(tokens):
    n = _one_date(tokens, 0)
    return n + _one_date(tokens, n) if n else 0


def role_of(label):
    l = label.lower()
    if "balance" in l:
        return "balance"
    if re.search(r"\bdate\b|posted|posting", l):
        return "date"
    if re.search(r"withdraw|debit|money out|paid out|payments? out|charge|purchase|\bout\b|spent|\bdr\b", l):
        return "debit"
    if re.search(r"deposit|credit|money in|paid in|payments? in|\bin\b|refund|payment|\bcr\b", l):
        return "credit"
    if re.search(r"amount|value", l):
        return "amount"
    if re.search(r"descri|detail|particular|merchant|payee|narrat|memo|transaction|activity", l):
        return "desc"
    return "other"


def _clusters(words, gap=8):
    out = []
    for w in sorted(words, key=lambda w: w["x0"]):
        if out and w["x0"] - out[-1]["x1"] <= gap:
            out[-1]["words"].append(w)
            out[-1]["x1"] = max(out[-1]["x1"], w["x1"])
        else:
            out.append({"words": [w], "x0": w["x0"], "x1": w["x1"]})
    return out


def header_at(lines, i):
    """-> {cols, top, right} if lines[i] is a table header, else None. A second line stacked right under it joins the labels."""
    ln = lines[i]
    clusters = _clusters(ln["words"])
    if len(clusters) < 3 or any(len(" ".join(w["text"] for w in c["words"])) > 32 for c in clusters):
        return None
    if any(AMOUNT.match(t) for t in ln["tokens"]) or _date_len(ln["tokens"]):
        return None
    stack = []
    if i + 1 < len(lines) and lines[i + 1]["top"] - ln["top"] <= 11:
        nxt = lines[i + 1]
        if not any(AMOUNT.match(t) or _one_date(nxt["tokens"], k) for k, t in enumerate(nxt["tokens"])) and len(nxt["tokens"]) <= len(clusters) + 1:
            stack = nxt["words"]
    cols = [{"label": " ".join(w["text"] for w in c["words"]), "x0": c["x0"], "x1": c["x1"]} for c in clusters]
    for w in stack:
        mid = (w["x0"] + w["x1"]) / 2
        best = min(cols, key=lambda c: abs((c["x0"] + c["x1"]) / 2 - mid))
        if abs((best["x0"] + best["x1"]) / 2 - mid) <= 45:
            best["label"] += " " + w["text"]
            best["x0"], best["x1"] = min(best["x0"], w["x0"]), max(best["x1"], w["x1"])
    for c in cols:
        c["role"] = role_of(c["label"])
    roles = [c["role"] for c in cols]
    if not any(r in MONEY for r in roles) or not ("date" in roles or ("desc" in roles and "balance" in roles)):
        return None
    return {"cols": cols, "top": ln["top"], "right": max(c["x1"] for c in cols) + 24}


def _nearest(tok, cols):
    cx = (tok["x0"] + tok["x1"]) / 2
    return min(cols, key=lambda c: min(abs(tok["x1"] - c["x1"]), abs(cx - (c["x0"] + c["x1"]) / 2), abs(tok["x0"] - c["x0"])))


def _text(words):
    return " ".join(w["text"] for w in words)


def _region(words, hdr, prev_dates=()):
    """One table region -> (output lines, number of transaction rows, top of the last row, the last date seen), or (None, 0, 0, ()) when it holds no transactions."""
    cols = hdr["cols"]
    num_left = min(c["x0"] for c in cols if c["role"] in NUMERIC) - 25
    cand = [c for c in cols if c["role"] in NUMERIC or (c["role"] == "other" and c["x0"] >= num_left)]
    split = any(c["role"] in ("debit", "credit") for c in cols)
    date_x0 = min([c["x0"] for c in cols if c["role"] == "date"] or [0])
    info, seen_date = [], bool(prev_dates)
    for ln in _lines([w for w in words if w["text"] != "$"]):
        amts, left, marks = [], [], []
        for w in ln["words"]:
            if w["x0"] >= num_left - 4 and AMOUNT.match(w["text"]):
                c = _nearest(w, cand)
                if c["role"] in NUMERIC:
                    amts.append((w, c))
            elif w["x0"] >= num_left - 4 and re.fullmatch(r"(?:CR|DR|Cr|Dr)", w["text"]):
                marks.append(w)
            elif w["x1"] <= date_x0 - 3 and re.fullmatch(r"[\w#.-]{1,10}", w["text"]):
                continue      # a reference number printed before the dates
            elif not (w["x0"] >= num_left and DASH.match(w["text"])):
                left.append(w)
        for m in marks:      # a loose CR/DR belongs to the amount just before it
            before = [a for a in amts if a[0]["x1"] <= m["x0"] + 3 and m["x0"] - a[0]["x1"] < 30]
            if before and not re.search(r"(?:CR|DR|Cr|Dr)$", max(before, key=lambda a: a[0]["x1"])[0]["text"]):
                w0, c0 = max(before, key=lambda a: a[0]["x1"])
                amts[amts.index((w0, c0))] = (dict(w0, text=w0["text"] + " " + m["text"].upper()), c0)
        dlen = _date_len([w["text"] for w in left])
        money = any(c["role"] in MONEY for _, c in amts)
        desc_txt = _text(left[dlen:] if dlen else left)
        row = money and (dlen > 0 or (seen_date and any(ch.isalnum() for ch in desc_txt) and not SKIP_ROW.search(desc_txt)))
        seen_date = seen_date or (money and dlen > 0)
        info.append({"ln": ln, "dlen": dlen, "left": left, "amts": amts, "row": row, "money": money})
    row_idx = [i for i, r in enumerate(info) if r["row"]]
    if not row_idx:
        return None, 0, 0, ()
    tops = [info[i]["ln"]["top"] for i in row_idx]
    gaps = sorted(b - a for a, b in zip(tops, tops[1:]) if b - a > 1) or [14]
    thr = max(6.0, min(0.6 * gaps[len(gaps) // 2], 14.0))
    frags, absorbed = {i: [] for i in row_idx}, set()
    for i, r in enumerate(info):
        if r["row"] or r["money"] or r["dlen"] or r["amts"] or not any(ch.isalnum() for ch in _text(r["left"])):
            continue
        near = min(row_idx, key=lambda k: abs(info[k]["ln"]["top"] - r["ln"]["top"]))
        if abs(info[near]["ln"]["top"] - r["ln"]["top"]) <= thr:
            frags[near].append(i)
            absorbed.add(i)
    out, last_dates = [], list(prev_dates)
    for i, r in enumerate(info):
        if i in absorbed:
            continue
        if not r["row"]:
            out.append(" ".join(r["ln"]["tokens"]))
            continue
        ltoks = [w["text"] for w in r["left"]]
        dates, desc = ltoks[:r["dlen"]], ltoks[r["dlen"]:]
        if dates:
            last_dates = dates
        else:
            dates = last_dates
        before = [t for j in frags[i] if info[j]["ln"]["top"] < r["ln"]["top"] for t in info[j]["ln"]["tokens"]]
        after = [t for j in frags[i] if info[j]["ln"]["top"] > r["ln"]["top"] for t in info[j]["ln"]["tokens"]]
        parts = []
        for w, c in sorted(r["amts"], key=lambda p: p[0]["x0"]):
            t = w["text"]
            if split and c["role"] in ("debit", "credit") and not re.search(r"(?:CR|DR|Cr|Dr)$", t):
                t += " DR" if c["role"] == "debit" else " CR"
            parts.append(t)
        out.append(" ".join(dates + before + desc + after + parts))
    return out, len(row_idx), tops[-1], last_dates


def table_lines(page, carry):
    """-> lines for the page, or None when no transaction table is found (the caller then reads it as plain text).
    carry holds the previous page's column layout, for tables whose header is not repeated."""
    words = [w for w in page.extract_words(x_tolerance=2, y_tolerance=3) if w["text"] != "$"]
    lines = _lines(words)
    cands = []
    for i in range(len(lines)):
        h = header_at(lines, i)
        if not h:
            continue
        res, n, last, _d = _region([w for w in words if w["top"] >= h["top"] - 2 and w["x0"] < h["right"]], h, carry.get("dates", ()))
        if n:
            h["rows"], h["last"] = n, last
            cands.append(h)
    heads = []
    for h in sorted(cands, key=lambda c: -c["rows"]):
        if all(h["last"] < o["top"] - 2 or h["top"] > o["last"] + 2 for o in heads):
            heads.append(h)
    heads.sort(key=lambda h: h["top"])
    continued = not heads and bool(carry.get("hdr"))
    if continued:
        heads = [dict(carry["hdr"], top=0)]
    if not heads:
        return None

    def plain(y0, y1):
        if y1 - y0 < 3:
            return []
        t = page.crop((0, max(0, y0), page.width, min(page.height, y1))).extract_text(x_tolerance=2, y_tolerance=3) or ""
        return [l.rstrip() for l in t.splitlines() if l.strip()]

    out, found = [], 0
    if not continued:
        out += plain(0, heads[0]["top"] - 2)
    for k, h in enumerate(heads):
        y0 = 0 if continued else h["top"] - 2
        y1 = heads[k + 1]["top"] - 2 if k + 1 < len(heads) else page.height
        cut = [w for w in words if y0 <= w["top"] < y1]
        inside = [w for w in cut if w["x0"] < h["right"]]
        res, n, _last, dates = _region(inside, h, carry.get("dates", ()))
        if res is None:
            out += plain(y0, y1)
            continue
        found += n
        out += res
        carry["hdr"], carry["dates"] = h, dates
        last_top = max(w["top"] for w in inside)
        beyond = [w for w in cut if w["x0"] >= h["right"] and w["top"] > last_top + 2]
        out += [" ".join(l["tokens"]) for l in _lines(beyond)]
    return out if found else None


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_AS, (1500 * 1024 * 1024, 1500 * 1024 * 1024))
    import pdfplumber
    table = len(sys.argv) > 1 and sys.argv[1] == "table"
    lines, carry = [], {}
    with pdfplumber.open(io.BytesIO(sys.stdin.buffer.read())) as pdf:
        if len(pdf.pages) > 80:
            raise SystemExit("too many pages")
        for page in pdf.pages:
            got = table_lines(page, carry) if table else None
            if got is None:
                got = [l.rstrip() for l in (page.extract_text(x_tolerance=2, y_tolerance=3) or "").splitlines() if l.strip()]
            lines.extend(got)
    sys.stdout.write(json.dumps(lines))


if __name__ == "__main__":
    main()
