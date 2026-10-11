"""Invented bank statements (PDF/CSV/OFX) with known arithmetic, for tests. No real data."""
import io
from datetime import date, timedelta

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

SHOPS = ["GROCERY MART #123", "COFFEE HOUSE", "PIZZA PALACE", "GAS N GO 4471", "BOOKSHOP", "STREAMING SVC", "HARDWARE DEPOT"]


def fmt(c, dollar=False):
    s = f"{abs(c) / 100:,.2f}"
    return ("-" if c < 0 else "") + ("$" if dollar else "") + s


def gen(n, start: date, days: int, seed: int, deposits=True):
    """-> list of (date, desc, out_cents) sorted by date. out>0 is money out."""
    import random
    r = random.Random(seed)
    rows = []
    for i in range(n):
        d = start + timedelta(days=r.randrange(days))
        if deposits and i % 5 == 4:
            rows.append((d, "PAYROLL DEPOSIT", -r.randrange(150000, 250000)))
        else:
            rows.append((d, r.choice(SHOPS), r.randrange(500, 12000)))
    rows.sort(key=lambda x: x[0])
    return rows


def _pdf(draw):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    draw(c)
    c.save()
    return buf.getvalue()


def _lines(c, items, x=40, y=750, step=14, per_page=38, page_hdr=None):
    n = 0
    for it in items:
        if n and n % per_page == 0:
            c.drawString(40, 30, f"Page {n // per_page} of 9")
            c.showPage()
            y = 750
            if page_hdr:
                c.drawString(x, y, page_hdr)
                y -= step
        it(c, y) if callable(it) else c.drawString(x, y, it)
        y -= step
        n += 1
    return y


def chequing_pdf(rows, opening, period=("Sep 1, 2026", "Sep 30, 2026"), acct="1234-567-8901", tamper=0):
    """Layout A: signed amount + running balance, multi-page, one two-line description."""
    bal = opening

    def draw(c):
        c.setFont("Helvetica", 9)
        items = ["Maple Credit Union", "Statement of Account", f"Account Number: {acct}",
                 f"Statement period: {period[0]} to {period[1]}", f"Opening balance ${opening / 100:,.2f}",
                 "Date  Description  Amount  Balance"]
        b = opening
        for i, (d, desc, out) in enumerate(rows):
            b -= out
            amt = -out
            items.append(lambda c, y, d=d, desc=desc, amt=amt, b=b: (
                c.drawString(40, y, d.strftime("%b %d")), c.drawString(110, y, desc),
                c.drawRightString(470, y, fmt(amt)), c.drawRightString(560, y, f"{b / 100:,.2f}")))
            if i == 1:
                items.append("   REF 99120044 ACME")
        items.append(f"Closing balance ${(b + tamper) / 100:,.2f}")
        _lines(c, items, page_hdr="Date  Description  Amount  Balance")
    return _pdf(draw)


def card_pdf(rows, previous, period=("Dec 14, 2026", "Jan 13, 2027"), card="4500 1234 5678 9012"):
    """Layout B: credit card, MM/DD with two dates and no year, payments marked CR, no running balance."""
    def draw(c):
        c.setFont("Helvetica", 9)
        owed = previous
        items = ["Northern Bank Visa", "Your Statement", f"Card number: {card}",
                 f"Statement period: {period[0]} to {period[1]}", f"Previous balance ${previous / 100:,.2f}",
                 "Trans  Post  Description  Amount"]
        for d, desc, out in rows:
            owed += out
            post = d + timedelta(days=1)
            if out < 0:
                a = f"{abs(out) / 100:,.2f} CR"
            else:
                a = f"{out / 100:,.2f}"
            items.append(lambda c, y, d=d, post=post, desc=desc, a=a: (
                c.drawString(40, y, d.strftime("%m/%d")), c.drawString(85, y, post.strftime("%m/%d")),
                c.drawString(130, y, desc), c.drawRightString(520, y, a)))
        items.append(f"New balance {fmt(owed, True)}")
        _lines(c, items)
    return _pdf(draw)


def savings_pdf(rows, opening, period=("2026-09-01", "2026-09-30"), acct="7788 4321"):
    """Layout C: ISO dates, unsigned amounts, running balance, no dollar signs."""
    def draw(c):
        c.setFont("Helvetica", 9)
        items = ["Aurora Savings", "Account statement", f"Account: {acct}", f"Period {period[0]} - {period[1]}",
                 f"Opening balance {opening / 100:,.2f}", "Date  Details  Amount  Balance"]
        b = opening
        for d, desc, out in rows:
            b -= out
            items.append(lambda c, y, d=d, desc=desc, out=out, b=b: (
                c.drawString(40, y, d.isoformat()), c.drawString(110, y, desc),
                c.drawRightString(470, y, f"{abs(out) / 100:,.2f}"), c.drawRightString(560, y, f"{b / 100:,.2f}")))
        items.append(f"Closing balance {b / 100:,.2f}")
        _lines(c, items)
    return _pdf(draw)


def csv_split(rows, opening, with_balance=True, dfmt="%Y-%m-%d"):
    out = ["Date,Description,Debit,Credit" + (",Balance" if with_balance else "")]
    b = opening
    for d, desc, o in rows:
        b -= o
        out.append(f"{d.strftime(dfmt)},{desc},{(o / 100):.2f}," if o > 0 else f"{d.strftime(dfmt)},{desc},,{(-o / 100):.2f}")
        if with_balance:
            out[-1] += f",{b / 100:.2f}"
    return "\n".join(out).encode()


def csv_signed(rows, dfmt="%m/%d/%Y", positive_out=False):
    out = ["Transaction Date,Description,Amount"]
    for d, desc, o in rows:
        v = o if positive_out else -o
        out.append(f"{d.strftime(dfmt)},{desc},{v / 100:.2f}")
    return "\n".join(out).encode()


def ofx(rows, closing, acct="000123456789", card=False, start=None, end=None):
    body = ["OFXHEADER:100", "DATA:OFXSGML", "", "<OFX>", "<BANKMSGSRSV1><STMTTRNRS><STMTRS>" if not card else "<CREDITCARDMSGSRSV1><CCSTMTTRNRS><CCSTMTRS>",
            f"<BANKACCTFROM><ACCTID>{acct}</ACCTID></BANKACCTFROM>" if not card else f"<CCACCTFROM><ACCTID>{acct}</ACCTID></CCACCTFROM>",
            "<BANKTRANLIST>", f"<DTSTART>{start.strftime('%Y%m%d')}120000", f"<DTEND>{end.strftime('%Y%m%d')}120000"]
    for i, (d, desc, o) in enumerate(rows):
        body += ["<STMTTRN>", f"<TRNTYPE>{'DEBIT' if o > 0 else 'CREDIT'}", f"<DTPOSTED>{d.strftime('%Y%m%d')}120000[-5:EST]",
                 f"<TRNAMT>{-o / 100:.2f}", f"<FITID>F{d.strftime('%Y%m%d')}{i:03d}", f"<NAME>{desc}", "</STMTTRN>"]
    body += ["</BANKTRANLIST>", f"<LEDGERBAL><BALAMT>{closing / 100:.2f}<DTASOF>{end.strftime('%Y%m%d')}120000</LEDGERBAL>",
             "</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>" if not card else "</CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1></OFX>"]
    return "\n".join(body).encode()
