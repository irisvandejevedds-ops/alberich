"""Invented table-style bank statements in many layouts (no real data). Each returns (pdf bytes, expected out_cents list, opening, closing)."""
import io
from datetime import date, timedelta

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

SHOPS = ["GROCERY MART", "COFFEE HOUSE TORONTO", "PIZZA PALACE", "GAS N GO", "BOOKSHOP DOWNTOWN", "STREAMING SERVICE", "HARDWARE DEPOT", "CORNER PHARMACY"]


def make_rows(n, start, seed, deposits=True):
    import random
    r = random.Random(seed)
    rows, d = [], start
    for i in range(n):
        d = d + timedelta(days=r.choice([0, 0, 1, 2]))
        if deposits and i % 6 == 5:
            rows.append((d, "PAYROLL DEPOSIT", -r.randrange(150000, 250000)))
        else:
            rows.append((d, r.choice(SHOPS), r.randrange(500, 14000)))
    return rows


def money(c, dollar=False):
    return ("$" if dollar else "") + f"{abs(c) / 100:,.2f}"


def build(rows, opening, *, cols, header, date_fmt, balance=True, liability=False, repeat_header=True, wrap="below", wrap_every=5,
          omit_repeat_dates=False, sidebar=False, period=("Sep 1", "Sep 30, 2026"), per_page=22, header_stack=False, dollar=False, two_dates=False,
          summary_labels=("Opening balance", "Closing balance"), amount_style="split", step=16):
    """cols: dict role -> (x, align) for date, desc, debit, credit, amount, balance (only those used)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    owed = opening
    closing = opening
    for _, _, o in rows:
        closing = closing + o if liability else closing - o
    y = 750

    def text(x, y, s, align="l"):
        (c.drawRightString if align == "r" else c.drawString)(x, y, s)

    def head(y):
        for role, labels in header:
            x, align = cols[role]
            for k, lab in enumerate(labels):
                text(x, y - 9 * k, lab, align)
        return y - (22 if header_stack else 14)

    c.setFont("Helvetica", 9)
    for line in ["Example Bank", "Statement of account", "Account number: 5555-123-4567", f"Statement period: {period[0]} to {period[1]}",
                 f"{summary_labels[0]} {money(opening, True)}", f"{summary_labels[1]} {money(closing, True)}"]:
        text(40, y, line)
        y -= 13
    y -= 12
    page, last_day = 1, None
    y = head(y)
    for i, (d, desc, o) in enumerate(rows):
        if y < 90:
            c.showPage()
            c.setFont("Helvetica", 9)
            page += 1
            y = 750
            text(40, 30, f"Page {page}")
            if repeat_header:
                y = head(y)
        wrapped = wrap and i % wrap_every == 2
        if wrapped and wrap == "center":
            text(cols["desc"][0], y + 9, "Ref 55123 CARD PURCHASE")
        text_date = d.strftime(date_fmt)
        if omit_repeat_dates and last_day == d:
            text_date = ""
        last_day = d
        if text_date:
            text(cols["date"][0], y, text_date)
        if two_dates:
            text(cols["date"][0] + 55, y, (d + timedelta(days=1)).strftime(date_fmt))
        text(cols["desc"][0], y, desc)
        if amount_style == "split":
            if o > 0:
                text(cols["debit"][0], y, money(o, dollar), cols["debit"][1])
            else:
                text(cols["credit"][0], y, money(o, dollar), cols["credit"][1])
        elif amount_style == "signed":
            text(cols["amount"][0], y, ("-" if o > 0 else "") + money(o, dollar), cols["amount"][1])
        elif amount_style == "bracket":
            text(cols["amount"][0], y, f"({money(o, dollar)})" if o > 0 else money(o, dollar), cols["amount"][1])
        else:
            s = money(o, dollar) if o > 0 else money(o, dollar) + " CR"
            text(cols["amount"][0], y, s, cols["amount"][1])
        if balance:
            owed = owed - o
            text(cols["balance"][0], y, money(owed), cols["balance"][1])
        if sidebar:
            text(480, y + 2, "Rates shown here")
        if wrapped and wrap == "below":
            y -= 10
            text(cols["desc"][0], y, "Ref 55123 CARD PURCHASE")
        if wrapped and wrap == "center":
            y -= 10
            text(cols["desc"][0], y, "MERCHANT CODE 5411")
        y -= step
    y -= 8
    text(40, y, "Totals and notices follow on the next statement.")
    c.save()
    return buf.getvalue(), [o for _, _, o in rows], opening, closing, [(d.isoformat(), desc) for d, desc, _ in rows]


LAYOUTS = {
    "split_with_balance_repeat_header": dict(
        cols={"date": (40, "l"), "desc": (95, "l"), "debit": (400, "r"), "credit": (470, "r"), "balance": (545, "r")},
        header=[("date", ["Date"]), ("desc", ["Description"]), ("debit", ["Withdrawals"]), ("credit", ["Deposits"]), ("balance", ["Balance"])],
        date_fmt="%b %d", balance=True),
    "header_only_on_first_page": dict(
        cols={"date": (40, "l"), "desc": (110, "l"), "debit": (400, "r"), "credit": (470, "r"), "balance": (545, "r")},
        header=[("date", ["Date"]), ("desc", ["Details"]), ("debit", ["Debit"]), ("credit", ["Credit"]), ("balance", ["Balance"])],
        date_fmt="%Y-%m-%d", balance=True, repeat_header=False),
    "stacked_header_money_out_in": dict(
        cols={"date": (40, "l"), "desc": (110, "l"), "debit": (390, "r"), "credit": (460, "r"), "balance": (540, "r")},
        header=[("date", ["Transaction", "date"]), ("desc", ["Transaction", "details"]), ("debit", ["Money", "out"]), ("credit", ["Money", "in"]), ("balance", ["Running", "balance"])],
        date_fmt="%d %b %Y", balance=True, header_stack=True),
    "date_printed_once_per_day": dict(
        cols={"date": (40, "l"), "desc": (95, "l"), "debit": (372, "r"), "credit": (470, "r"), "balance": (545, "r")},
        header=[("date", ["Date"]), ("desc", ["Description"]), ("debit", ["Cheques & debits"]), ("credit", ["Deposits & credits"]), ("balance", ["Balance"])],
        date_fmt="%m/%d", balance=True, omit_repeat_dates=True),
    "wrapped_above_and_below_with_sidebar": dict(
        cols={"date": (40, "l"), "desc": (120, "l"), "debit": (330, "r"), "credit": (395, "r"), "balance": (455, "r")},
        header=[("date", ["Date"]), ("desc", ["Description"]), ("debit", ["Debit"]), ("credit", ["Credit"]), ("balance", ["Balance"])],
        date_fmt="%b %d", balance=True, wrap="center", sidebar=True),
    "card_charges_and_payments_two_dates_no_balance": dict(
        cols={"date": (40, "l"), "desc": (155, "l"), "debit": (440, "r"), "credit": (540, "r")},
        header=[("date", ["Trans. date  Post date"]), ("desc", ["Description"]), ("debit", ["Charges"]), ("credit", ["Payments & credits"])],
        date_fmt="%m/%d", balance=False, liability=True, two_dates=True, wrap=None, dollar=True,
        summary_labels=("Previous balance", "New balance")),
    "single_amount_cr_marker": dict(
        cols={"date": (40, "l"), "desc": (110, "l"), "amount": (470, "r")},
        header=[("date", ["Date"]), ("desc", ["Description"]), ("amount", ["Amount"])],
        date_fmt="%b %d", balance=False, liability=True, amount_style="single", wrap="below",
        summary_labels=("Previous balance", "New balance")),
}

LAYOUTS.update({
    "signed_amount_and_balance": dict(
        cols={"date": (40, "l"), "desc": (100, "l"), "amount": (440, "r"), "balance": (540, "r")},
        header=[("date", ["Date"]), ("desc", ["Description"]), ("amount", ["Amount ($)"]), ("balance", ["Balance ($)"])],
        date_fmt="%b %d, %Y", balance=True, amount_style="signed", wrap="below"),
    "bracketed_debits_with_balance": dict(
        cols={"date": (40, "l"), "desc": (100, "l"), "amount": (440, "r"), "balance": (540, "r")},
        header=[("date", ["Posting date"]), ("desc", ["Transaction description"]), ("amount", ["Amount"]), ("balance", ["Balance"])],
        date_fmt="%d-%b-%Y", balance=True, amount_style="bracket", wrap=None),
    "left_aligned_numeric_columns": dict(
        cols={"date": (40, "l"), "desc": (100, "l"), "debit": (360, "l"), "credit": (430, "l"), "balance": (500, "l")},
        header=[("date", ["Date"]), ("desc", ["Description"]), ("debit", ["Debit"]), ("credit", ["Credit"]), ("balance", ["Balance"])],
        date_fmt="%b %d", balance=True, wrap="below"),
    "tight_rows_two_line_header": dict(
        cols={"date": (40, "l"), "desc": (100, "l"), "debit": (400, "r"), "credit": (470, "r"), "balance": (545, "r")},
        header=[("date", ["Posted", "date"]), ("desc", ["Description"]), ("debit", ["Paid", "out"]), ("credit", ["Paid", "in"]), ("balance", ["Balance"])],
        date_fmt="%Y/%m/%d", balance=True, header_stack=True, step=11, wrap=None, per_page=40),
})


def statement(name, n=45, seed=3, opening=500000):
    lay = dict(LAYOUTS[name])
    liability = lay.get("liability", False)
    rows = make_rows(n, date(2026, 9, 1), seed, deposits=not liability or True)
    if liability:   # cards: money in are payments
        rows = [(d, ("PAYMENT THANK YOU" if o < 0 else desc), (-(abs(o) // 5) if o < 0 else o)) for d, desc, o in rows]
        opening = 300000
    if lay.get("date_fmt") == "%m/%d" and not liability:
        pass
    return build(rows, opening, **lay), liability
