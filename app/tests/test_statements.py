from datetime import date
import re
import pytest
import synth
from finapp.statements import common, csvfmt, ofx, pdftext
from finapp.statements.common import Txn, reconcile


def test_parse_amount_variants():
    P = common.parse_amount
    assert P("$1,234.56") == (123456, None) and P("-45.00") == (-4500, None) and P("(12.34)") == (-1234, None)
    assert P("12.34-") == (-1234, None) and P("500.00 CR") == (50000, "CR") and P("7.00DR") == (700, "DR")
    with pytest.raises(ValueError):
        P("hello")


def test_year_inference_across_december_january():
    ps, pe = date(2026, 12, 14), date(2027, 1, 13)
    assert common.parse_date("12/16", ["%m/%d"], ps, pe) == date(2026, 12, 16)
    assert common.parse_date("01/05", ["%m/%d"], ps, pe) == date(2027, 1, 5)
    assert common.parse_date("Sep 03", ["%b %d"], date(2026, 9, 1), date(2026, 9, 30)) == date(2026, 9, 3)
    assert common.parse_date("2026-09-03", ["%Y-%m-%d"]) == date(2026, 9, 3)


def test_norm_desc_strips_noise():
    assert common.norm_desc("Grocery Mart #123 TORONTO 4455521") == "GROCERY MART TORONTO"


def test_reconcile_asset_and_liability_conventions():
    t = [Txn("2026-09-01", "a", 1000), Txn("2026-09-02", "b", -500)]
    assert reconcile("chequing", 10000, 9500, t)[0] == "ok"            # 10000 - 1000 + 500
    assert reconcile("credit_card", 10000, 10500, t)[0] == "ok"        # 10000 + 1000 - 500
    assert reconcile("chequing", 10000, 9400, t)[0] == "mismatch"
    assert reconcile("chequing", None, 9500, t)[0] == "unverifiable"


def test_csv_split_columns_with_running_balance_verifies():
    rows = synth.gen(12, date(2026, 9, 1), 29, 1)
    data = synth.csv_split(rows, 250000)
    spec, notes = csvfmt.draft(data, "chequing")
    assert spec["amount_style"] == "split" and spec["columns"]["balance"] == "Balance"
    p = csvfmt.parse(data, spec, "chequing")
    assert len(p.txns) == 12 and p.running_ok is True and p.opening_cents == 250000
    assert reconcile("chequing", p.opening_cents, p.closing_cents, p.txns)[0] == "ok"
    assert csvfmt.identify(data, [{"spec": spec}]) is not None


def test_csv_running_balance_catches_a_tampered_row():
    rows = synth.gen(10, date(2026, 9, 1), 29, 2)
    ls = synth.csv_split(rows, 100000).decode().splitlines()
    ls[3] = ls[3].rsplit(",", 1)[0] + ",9999.99"                    # corrupt one running balance
    data = "\n".join(ls).encode()
    spec, _ = csvfmt.draft(data)
    assert csvfmt.parse(data, spec).running_ok is False


def test_csv_signed_and_date_ambiguity_note():
    rows = synth.gen(8, date(2026, 9, 1), 10, 3)
    spec, notes = csvfmt.draft(synth.csv_signed(rows, "%m/%d/%Y"), "chequing")
    assert spec["signed_means"] == "negative_is_out" and spec["date_formats"][0] == "%m/%d/%Y"
    p = csvfmt.parse(synth.csv_signed(rows, "%m/%d/%Y"), spec)
    assert sum(t.out_cents for t in p.txns) == sum(r[2] for r in rows)
    day_first = synth.csv_signed([(date(2026, 9, 25), "X", 100), (date(2026, 9, 13), "Y", 200)], "%d/%m/%Y")
    s2, _ = csvfmt.draft(day_first)
    assert s2["date_formats"][0] == "%d/%m/%Y"                       # 25 and 13 cannot be months


def test_ofx_bank_and_card_conventions():
    rows = synth.gen(6, date(2026, 9, 1), 28, 4)
    p = ofx.parse(synth.ofx(rows, 123456, start=date(2026, 9, 1), end=date(2026, 9, 30)))
    assert p.account_hint == "6789" and p.account_type_hint == "chequing" and len(p.txns) == 6
    assert p.closing_cents == 123456 and sum(t.out_cents for t in p.txns) == sum(r[2] for r in rows)
    assert p.opening_cents == 123456 + sum(r[2] for r in rows) and p.opening_derived
    card = ofx.parse(synth.ofx(rows, -80000, acct="4500999988887777", card=True, start=date(2026, 9, 1), end=date(2026, 9, 30)))
    assert card.account_type_hint == "credit_card" and card.closing_cents == 80000     # owed money is positive here


def _roundtrip(pdf, acct_type, **expect):
    lines = pdftext.extract_lines(pdf)
    spec, notes = pdftext.draft(lines, acct_type)
    assert spec is not None, notes
    p = pdftext.parse(lines, spec, acct_type)
    return lines, spec, p


def test_pdf_layout_a_chequing_with_balance_column_multipage_and_continuation():
    rows = synth.gen(60, date(2026, 9, 1), 29, 5)
    lines, spec, p = _roundtrip(synth.chequing_pdf(rows, 500000), "chequing")
    assert spec["rows"]["style"] == "balance_delta" and p.account_hint == "8901"
    assert (p.period_start, p.period_end) == ("2026-09-01", "2026-09-30") and p.opening_cents == 500000
    assert len(p.txns) == 60 and any("ACME" in t.desc for t in p.txns)
    assert reconcile("chequing", p.opening_cents, p.closing_cents, p.txns)[0] == "ok"
    assert pdftext.identify(lines, [{"spec": spec}]) is not None


def test_pdf_layout_b_credit_card_no_year_two_dates_cr_payments_dec_to_jan():
    rows = synth.gen(20, date(2026, 12, 14), 30, 6, deposits=True)
    rows = [(d, desc, o) for d, desc, o in rows if date(2026, 12, 14) <= d <= date(2027, 1, 13)]
    lines, spec, p = _roundtrip(synth.card_pdf(rows, 700000), "credit_card")
    assert spec["rows"]["style"] == "signed_positive_out" and p.account_hint == "9012"
    assert p.period_start == "2026-12-14" and p.period_end == "2027-01-13"
    assert {t.date[:4] for t in p.txns} <= {"2026", "2027"} and len(p.txns) == len(rows)
    assert reconcile("credit_card", p.opening_cents, p.closing_cents, p.txns)[0] == "ok"
    assert any(t.out_cents < 0 for t in p.txns)                      # CR payments read as money in


def test_pdf_layout_c_iso_dates_unsigned_amounts_balance_delta():
    rows = synth.gen(14, date(2026, 9, 1), 29, 7)
    lines, spec, p = _roundtrip(synth.savings_pdf(rows, 500000), "savings")
    assert p.account_hint == "4321" and len(p.txns) == 14
    assert reconcile("savings", p.opening_cents, p.closing_cents, p.txns)[0] == "ok"


def test_pdf_tampered_closing_balance_is_a_mismatch():
    rows = synth.gen(12, date(2026, 9, 1), 29, 8)
    lines, spec, p = _roundtrip(synth.chequing_pdf(rows, 500000, tamper=5000), "chequing")
    assert reconcile("chequing", p.opening_cents, p.closing_cents, p.txns)[0] == "mismatch"


def test_masked_excerpt_leaks_no_digits_or_merchants():
    rows = synth.gen(12, date(2026, 9, 1), 29, 9)
    lines = pdftext.extract_lines(synth.chequing_pdf(rows, 500000))
    m = pdftext.mask_excerpt(lines)
    assert not re.search(r"[0-8]", m) and "GROCERY" not in m and "PAYROLL" not in m and "1234-567-8901" not in m
    assert "Opening balance" in m and "Sep xx" not in m and re.search(r"Sep 99", m)     # date format survives, digits do not
    assert len([l for l in m.splitlines() if re.match(r"^[A-Za-z]{3} 99", l)]) <= 8


def test_scanned_or_garbage_pdf_gives_a_clear_failure():
    with pytest.raises(ValueError):
        pdftext.extract_lines(b"%PDF-1.4 not really a pdf")


def test_card_in_credit_balance_and_owed_shown_negative_both_reconcile():
    rows = synth.gen(12, date(2026, 8, 14), 28, 3)
    for prev in (100000, 700000):                                   # 100000 ends the month in credit (negative)
        lines = pdftext.extract_lines(synth.card_pdf(rows, prev, period=("Aug 14, 2026", "Sep 13, 2026")))
        spec, _ = pdftext.draft(lines, "credit_card")
        p = pdftext.parse(lines, spec, "credit_card")
        assert reconcile("credit_card", p.opening_cents, p.closing_cents, p.txns)[0] == "ok", prev


def test_account_type_is_guessed_from_the_statement_wording():
    card = pdftext.extract_lines(synth.card_pdf(synth.gen(10, date(2026, 8, 14), 28, 3), 100000, period=("Aug 14, 2026", "Sep 13, 2026")))
    bank = pdftext.extract_lines(synth.chequing_pdf(synth.gen(10, date(2026, 9, 1), 29, 5), 500000))
    assert pdftext.guess_account_type(card) == "credit_card" and pdftext.guess_account_type(bank) == "chequing"


def test_balances_are_found_even_with_a_date_between_the_label_and_the_amount():
    rows = synth.gen(12, date(2026, 8, 14), 28, 3)
    lines = [l for l in pdftext.extract_lines(synth.card_pdf(rows, 100000, period=("Aug 14, 2026", "Sep 13, 2026"))) if "balance" not in l.lower()]
    lines = ["Previous total balance, Sep. 9, 2026 $1,000.00 Statement date Oct. 9, 2026", "Payments and credits -50.00 Statement period Aug 14, 2026 - Sep 13, 2026",
             "Total balance $1,500.00", "Minimum payment due $35.00"] + lines
    spec, _ = pdftext.draft(lines, "credit_card")
    p = pdftext.parse(lines, spec, "credit_card")
    assert p.opening_cents == 100000 and p.closing_cents == 150000


def test_flip_reverses_every_line_in_both_readers():
    rows = synth.gen(10, date(2026, 9, 1), 29, 5)
    lines = pdftext.extract_lines(synth.chequing_pdf(rows, 500000))
    spec, _ = pdftext.draft(lines, "chequing")
    a = pdftext.parse(lines, spec, "chequing")
    b = pdftext.parse(lines, {**spec, "flip": True}, "chequing")
    assert [t.out_cents for t in b.txns] == [-t.out_cents for t in a.txns] and any(t.out_cents > 0 for t in a.txns)
    data = synth.csv_signed(synth.gen(8, date(2026, 9, 1), 10, 3), "%m/%d/%Y")
    cspec, _ = csvfmt.draft(data, "chequing")
    ca, cb = csvfmt.parse(data, cspec), csvfmt.parse(data, {**cspec, "flip": True})
    assert [t.out_cents for t in cb.txns] == [-t.out_cents for t in ca.txns]


def _card_with_side_column():
    """An invented card statement: dates like 09-Oct-2026, a rewards column that is not a balance, descriptions that wrap above and
    below their row, and a sidebar of unrelated text running alongside the table."""
    import io
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    top = 760
    for i, t in enumerate(["Here's your", "Examplebank Rewards Card statement.", "Account number 4111 xxxx xxxx 0343", "Statement period Sep 6 to Oct 6, 2026",
                           "Previous balance $100.00", "New balance $182.50"]):
        c.drawString(40, top - 14 * i, t)
    c.showPage()
    y = 740
    c.setFont("Helvetica", 8)
    for x, w in ((40, "Transaction"), (105, "Posted"), (170, "Description"), (290, "Category"), (350, "Amount"), (400, "Reward")):
        c.drawString(x, y, w)
    rows = [("09-Sep-2026", "COFFEE HOUSE TORONTO", 12.50, False), ("11-Sep-2026", "BIG LONG MERCHANT NAME ANYTOWN BC", 40.00, True),
            ("15-Sep-2026", "PAYMENT - THANK YOU", -50.00, False), ("20-Sep-2026", "GAS N GO", 80.00, False)]
    y -= 30
    for i, (d, desc, amt, wrapped) in enumerate(rows):
        c.drawString(40, y, d); c.drawString(105, y, d)
        if wrapped:
            c.drawString(170, y + 9, desc[:17]); c.drawString(170, y - 9, desc[18:])
        else:
            c.drawString(170, y, desc[:24])
        c.drawString(350, y, f"${amt:,.2f}" if amt >= 0 else f"-${-amt:,.2f}")
        c.drawString(405, y, "$0.10" if amt >= 0 else "-")
        c.drawString(485, y + 3, "Interest rates shown")
        y -= 40
    c.save()
    return buf.getvalue()


def test_card_statement_with_side_column_wrapped_descriptions_and_text_month_dates():
    data = _card_with_side_column()
    plain = pdftext.extract_lines(data)
    spec, _ = pdftext.draft(plain, "credit_card")
    assert spec is None or not pdftext.parse(plain, spec, "credit_card").txns            # the plain reader cannot make sense of it
    lines = pdftext.extract_lines(data, "table")
    spec, notes = pdftext.draft(lines, "credit_card")
    assert spec is not None and notes == []
    p = pdftext.parse(lines, spec, "credit_card")
    assert [t.out_cents for t in p.txns] == [1250, 4000, -5000, 8000]
    assert "BIG LONG MERCHANT NAME ANYTOWN BC" in p.txns[1].desc and p.account_hint == "0343"
    assert (p.period_start, p.period_end) == ("2026-09-06", "2026-10-06")
    assert common.reconcile("credit_card", p.opening_cents, p.closing_cents, p.txns)[0] == "ok"


import synth_grid


@pytest.mark.parametrize("name", list(synth_grid.LAYOUTS))
def test_many_digital_pdf_layouts_are_read_and_balance(name):
    (data, expected, opening, closing, shape), liability = synth_grid.statement(name)
    atype = "credit_card" if liability else "chequing"
    lines, spec, notes = pdftext.best_draft(data, atype)
    assert spec is not None, (name, notes)
    p = pdftext.parse(lines, spec, atype)
    assert [t.out_cents for t in p.txns] == expected, name
    assert reconcile(atype, p.opening_cents, p.closing_cents, p.txns)[0] == "ok", name
    assert p.opening_cents == opening and p.closing_cents == closing
    assert [(t.date, t.desc.split(" Ref ")[0].split(" MERCHANT")[0].replace("Ref 55123 CARD PURCHASE ", "")) for t in p.txns] == [(d, desc) for d, desc in shape] or \
        all(t.date == d and desc in t.desc and not t.desc[:1].isdigit() for t, (d, desc) in zip(p.txns, shape)), name


SCOTIA_LIKE = ["Statement Period Aug 29, 2026 - Sep 28, 2026", "Account # 4500 XXXX XXXX 2469", "Previous balance,Aug 28 $2,238.50", "New Balance = $500.00",
               "Transactions since your last statement", "TRANS. POST", "REF.# DATE DATE DETAILS AMOUNT($)",
               "001 Aug 31 Aug 31 PAYMENT FROM - *****12*6327 2,238.50-", "002 Sep 25 Sep 25 CHEXY RENT 289-9690776 ON 500.00",
               "SUB-TOTAL CREDITS $2,238.50-"]


def test_two_row_card_statement_with_reference_numbers_and_trailing_minus_credits_is_drafted_and_balances():
    spec, notes = pdftext.draft(SCOTIA_LIKE, "credit_card")
    assert spec is not None
    p = pdftext.parse(SCOTIA_LIKE, spec, "credit_card")
    assert [t.out_cents for t in p.txns] == [-223850, 50000] and reconcile("credit_card", p.opening_cents, p.closing_cents, p.txns)[0] == "ok"


def test_swapped_polarity_is_corrected_when_the_totals_only_add_up_the_other_way():
    spec, _ = pdftext.draft(SCOTIA_LIKE, "credit_card")
    wrong = {**spec, "flip": True}
    assert reconcile("credit_card", *(lambda p: (p.opening_cents, p.closing_cents, p.txns))(pdftext.parse(SCOTIA_LIKE, wrong, "credit_card")))[0] != "ok"
    fixed, note = pdftext.fix_polarity(wrong, SCOTIA_LIKE, "credit_card")
    assert note and not fixed.get("flip")
    assert pdftext.fix_polarity(spec, SCOTIA_LIKE, "credit_card") == (spec, None)


def test_opening_and_closing_balance_lines_in_the_table_are_not_transactions():
    lines = ["Opening Balance on August 18, 2026 $49.28", "Closing Balance on September 17, 2026 $2,243.23",
             "Date Transactions withdrawn($) deposited ($) Balance ($)", "Aug 18 Opening Balance 49.28",
             "Aug 22 Deposit 4287 Free Interac 100.00 149.28", "Aug 25 Deposit 4921 Free Interac 35.73 185.01", "Aug 27 Withdrawal 6711 Free Interac 10.00 175.01",
             "Aug 29 Deposit 6037 Free Interac 25.00 200.01", "Sep 17 Service charge Monthly Fees 16.95 183.06", "Sep 17 Closing Balance $183.06"]
    spec, _ = pdftext.draft(lines, "chequing")
    p = pdftext.parse(lines, spec, "chequing")
    assert len(p.txns) == 5 and not any("Balance" in t.desc for t in p.txns)


def test_balance_lines_are_skipped_however_they_are_spaced_or_dated():
    for line in ("Openingbalance 1,024.49", "Aug 22 Opening Balance 49.28", "01 Sep 2026 Opening Balance 0.00", "30 Sep 2026 Closing Balance 0.00", "Previous balance $9.00"):
        assert pdftext.BALANCE_ROW.match(line), line
    for line in ("Aug 22 Walmart 49.28", "Aug 22 Online Bill Payment, New Balance Transfer 5.00", "Sep 03 Pre-Authorized Payment 18.00"):
        assert not pdftext.BALANCE_ROW.match(line), line
