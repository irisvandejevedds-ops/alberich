import json
from datetime import date
import pytest
import synth
from finapp import ingest, required, clock
from finapp.statements import csvfmt, pdftext

T0 = "2026-10-01T12:00:00Z"


def acct(db, name="Chequing", type="chequing", last4="8901", **kw):
    return db.ex("""INSERT INTO accounts(institution,name,type,owner,last4,cycle_day,require_pdf,track_from,created_at)
                    VALUES('Bank',?,?,?,?,?,?,?,?)""", (name, type, kw.get("owner", "me"), last4, kw.get("cycle_day"),
                                                        kw.get("require_pdf", 0), kw.get("track_from"), T0)).lastrowid


def learn_pdf(db, pdf, atype):
    spec, _ = pdftext.draft(pdftext.extract_lines(pdf), atype)
    spec["account_type"] = atype
    return ingest.save_template(db, "pdf-text", "Test PDF", "Bank", spec)


def test_unknown_pdf_waits_for_a_template_then_reconciles(db, cfg):
    acct(db)
    pdf = synth.chequing_pdf(synth.gen(20, date(2026, 9, 1), 29, 5), 500000)
    r = ingest.ingest(db, cfg, "sep.pdf", pdf)
    assert r.status == "pending_template"
    learn_pdf(db, pdf, "chequing")
    r2 = ingest.process(db, cfg, r.statement_id)
    assert r2.status == "reconciled", r2.message
    st = db.one("SELECT * FROM statements WHERE id=?", (r.statement_id,))
    assert st["account_id"] and st["txn_count"] == 20 and st["period_end"] == "2026-09-30"
    assert db.val("SELECT COUNT(*) FROM transactions WHERE statement_id=?", (r.statement_id,)) == 20


def test_same_file_twice_is_a_duplicate(db, cfg):
    acct(db)
    pdf = synth.chequing_pdf(synth.gen(8, date(2026, 9, 1), 29, 5), 500000)
    learn_pdf(db, pdf, "chequing")
    assert ingest.ingest(db, cfg, "a.pdf", pdf).status == "reconciled"
    assert ingest.ingest(db, cfg, "b.pdf", pdf).status == "duplicate"
    assert db.val("SELECT COUNT(*) FROM statements") == 1


def test_stored_file_is_encrypted_and_recoverable(db, cfg):
    acct(db)
    pdf = synth.chequing_pdf(synth.gen(8, date(2026, 9, 1), 29, 5), 500000)
    learn_pdf(db, pdf, "chequing")
    r = ingest.ingest(db, cfg, "a.pdf", pdf)
    name = db.val("SELECT stored_name FROM statements WHERE id=?", (r.statement_id,))
    assert b"%PDF" not in (cfg.files_dir / "statements" / name).read_bytes()
    assert ingest.load_bytes(db, cfg, r.statement_id) == pdf


def test_tampered_closing_balance_needs_review_and_can_be_accepted(db, cfg):
    acct(db)
    good = synth.chequing_pdf(synth.gen(12, date(2026, 9, 1), 29, 8), 500000)
    learn_pdf(db, good, "chequing")
    bad = synth.chequing_pdf(synth.gen(12, date(2026, 9, 1), 29, 8), 500000, tamper=5000)
    r = ingest.ingest(db, cfg, "bad.pdf", bad)
    assert r.status == "needs_review" and "off by" in r.message
    ingest.accept(db, r.statement_id, "bank confirmed a $50 fee")
    assert db.val("SELECT status FROM statements WHERE id=?", (r.statement_id,)) == "reconciled"


def test_two_accounts_same_type_ask_which_one(db, cfg):
    acct(db, "A", last4="1111")
    acct(db, "B", last4="2222")
    pdf = synth.chequing_pdf(synth.gen(8, date(2026, 9, 1), 29, 5), 500000)
    learn_pdf(db, pdf, "chequing")
    r = ingest.ingest(db, cfg, "a.pdf", pdf)
    assert r.status == "pending_account"
    a = db.val("SELECT id FROM accounts WHERE name='A'")
    assert ingest.process(db, cfg, r.statement_id, account_id=a).status == "reconciled"


def test_last4_picks_the_account_automatically(db, cfg):
    acct(db, "A", last4="1111")
    b = acct(db, "B", last4="8901")
    pdf = synth.chequing_pdf(synth.gen(8, date(2026, 9, 1), 29, 5), 500000)
    learn_pdf(db, pdf, "chequing")
    r = ingest.ingest(db, cfg, "a.pdf", pdf)
    assert r.status == "reconciled" and db.val("SELECT account_id FROM statements WHERE id=?", (r.statement_id,)) == b


def test_csv_with_running_balance_reconciles_and_overlap_is_not_double_counted(db, cfg):
    a = acct(db, "Cheq", last4="")
    rows = synth.gen(12, date(2026, 9, 1), 29, 1)
    data = synth.csv_split(rows, 250000)
    spec, _ = csvfmt.draft(data, "chequing")
    spec["account_type"] = "chequing"
    ingest.save_template(db, "csv", "T", "Bank", spec)
    r = ingest.ingest(db, cfg, "a.csv", data)
    assert r.status == "reconciled", r.message
    # a second export covering the same rows plus none new: different bytes, same transactions
    data2 = data + b"\n"
    r2 = ingest.ingest(db, cfg, "b.csv", data2)
    assert r2.status in ("duplicate", "reconciled", "parsed", "needs_review")
    assert db.val("SELECT COUNT(*) FROM transactions WHERE account_id=?", (a,)) == 12


def test_unreadable_file_is_unsupported_not_a_crash(db, cfg):
    assert ingest.ingest(db, cfg, "x.bin", b"\x00\x01\x02garbage").status == "unsupported"


def test_delete_statement_removes_rows_and_file(db, cfg):
    acct(db)
    pdf = synth.chequing_pdf(synth.gen(8, date(2026, 9, 1), 29, 5), 500000)
    learn_pdf(db, pdf, "chequing")
    r = ingest.ingest(db, cfg, "a.pdf", pdf)
    name = db.val("SELECT stored_name FROM statements WHERE id=?", (r.statement_id,))
    ingest.delete_statement(db, cfg, r.statement_id)
    assert db.val("SELECT COUNT(*) FROM transactions") == 0 and not (cfg.files_dir / "statements" / name).exists()


def test_matrix_marks_filed_missing_soon_and_future(db, cfg):
    a = acct(db, track_from="2026-07")
    pdf = synth.chequing_pdf(synth.gen(8, date(2026, 8, 1), 29, 5), 500000, period=("Aug 1, 2026", "Aug 31, 2026"))
    learn_pdf(db, pdf, "chequing")
    assert ingest.ingest(db, cfg, "aug.pdf", pdf).status == "reconciled"
    cols, rows = required.matrix(db, date(2026, 10, 3), 5)
    by = {c["month"]: c["status"] for c in rows[0]["cells"]}
    assert cols == ["2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    assert by == {"2026-06": "na", "2026-07": "missing", "2026-08": "complete", "2026-09": "soon", "2026-10": "future"}
    assert required.summary(rows) == {"missing": 1, "review": 0, "soon": 1}
    _, rows2 = required.matrix(db, date(2026, 10, 20), 5)
    assert {c["month"]: c["status"] for c in rows2[0]["cells"]}["2026-09"] == "missing"


def test_custom_cycle_day_shifts_the_due_date(db, cfg):
    acct(db, cycle_day=13, track_from="2026-01")
    _, rows = required.matrix(db, date(2026, 10, 10), 2)
    assert {c["month"]: c["status"] for c in rows[0]["cells"]}["2026-10"] == "future"


def test_single_account_without_last4_is_chosen_and_learns_its_digits(db, cfg):
    a = acct(db, last4="")
    pdf = synth.chequing_pdf(synth.gen(8, date(2026, 9, 1), 29, 5), 500000)
    learn_pdf(db, pdf, "chequing")
    r = ingest.ingest(db, cfg, "a.pdf", pdf)
    assert r.status == "reconciled" and db.val("SELECT last4 FROM accounts WHERE id=?", (a,)) == "8901"


def test_account_with_a_different_last4_is_not_guessed(db, cfg):
    acct(db, last4="1111")
    pdf = synth.chequing_pdf(synth.gen(8, date(2026, 9, 1), 29, 5), 500000)
    learn_pdf(db, pdf, "chequing")
    assert ingest.ingest(db, cfg, "a.pdf", pdf).status == "pending_account"


def test_a_card_taught_as_a_bank_account_is_not_silently_trusted(db, cfg):
    card = acct(db, "Visa", "credit_card", last4="9012")
    pdf = synth.card_pdf(synth.gen(20, date(2026, 8, 14), 28, 3), 100000, period=("Aug 14, 2026", "Sep 13, 2026"))
    learn_pdf(db, pdf, "chequing")                                   # the mistake: taught as chequing
    r = ingest.ingest(db, cfg, "card.pdf", pdf, account_id=card)
    assert r.status == "needs_review" and "money coming in" in r.message and "Credit card" in r.message
    ins = db.val("SELECT COUNT(*) FROM transactions WHERE statement_id=? AND amount_cents<0", (r.statement_id,))
    assert ins == db.val("SELECT txn_count FROM statements WHERE id=?", (r.statement_id,))


def test_retaught_layout_updates_in_place_and_fixes_every_statement_using_it(db, cfg):
    from finapp.statements import pdftext
    card = acct(db, "Visa", "credit_card", last4="9012")
    rows1, rows2 = synth.gen(20, date(2026, 8, 14), 28, 3), synth.gen(20, date(2026, 8, 14), 28, 4)
    pdf1 = synth.card_pdf(rows1, 100000, period=("Aug 14, 2026", "Sep 13, 2026"))
    pdf2 = synth.card_pdf(rows2, 100000, period=("Aug 14, 2026", "Sep 13, 2026"))
    tid = learn_pdf(db, pdf1, "chequing")
    a, b = ingest.ingest(db, cfg, "a.pdf", pdf1, account_id=card), ingest.ingest(db, cfg, "b.pdf", pdf2, account_id=card)
    assert a.status == b.status == "needs_review"
    spec, _ = pdftext.draft(pdftext.extract_lines(pdf1), "credit_card")
    spec["account_type"] = "credit_card"
    assert ingest.save_template(db, "pdf-text", "Fixed", "Bank", spec, template_id=tid) == tid
    for sid in (a.statement_id, b.statement_id):
        assert ingest.process(db, cfg, sid).status in ("reconciled", "needs_review")
        assert db.val("SELECT COUNT(*) FROM transactions WHERE statement_id=? AND amount_cents>0", (sid,)) > 10
    assert db.val("SELECT COUNT(*) FROM statement_templates") == 1


import synth_grid
from finapp.statements.common import Parsed, Txn


def _grid_statement(layout, rows, opening, **over):
    kw = dict(synth_grid.LAYOUTS[layout])
    kw.update(over)
    return synth_grid.build(rows, opening, **kw)[0]


def test_a_statement_with_no_balances_to_check_is_filed_as_unverified_not_sent_for_review(db, cfg):
    acct(db, "Card", type="credit_card", last4="4567")
    rows = synth_grid.make_rows(14, date(2026, 9, 1), 4, deposits=False)
    pdf = _grid_statement("single_amount_cr_marker", rows, 100000, summary_labels=("Notice", "Notice"))
    learn_pdf(db, pdf, "credit_card")
    r = ingest.ingest(db, cfg, "card.pdf", pdf)
    assert r.status == "parsed", r.message


def test_a_gap_between_statements_does_not_block_filing_a_statement_whose_arithmetic_works(db, cfg):
    acct(db, last4="4567")
    sep = _grid_statement("split_with_balance_repeat_header", synth_grid.make_rows(12, date(2026, 9, 1), 5), 500000)
    learn_pdf(db, sep, "chequing")
    assert ingest.ingest(db, cfg, "sep.pdf", sep).status == "reconciled"
    octo = _grid_statement("split_with_balance_repeat_header", synth_grid.make_rows(10, date(2026, 10, 1), 6), 123456, period=("Oct 1", "Oct 31, 2026"))
    r = ingest.ingest(db, cfg, "oct.pdf", octo)
    assert r.status == "reconciled" and "may be missing" in r.message


def test_a_real_mismatch_points_at_the_row_that_explains_it():
    t = lambda d, desc, c: Txn(d, desc, c, None, None)
    p = Parsed(kind="pdf")
    p.txns = [t("2026-09-01", "GROCERY", 5000), t("2026-09-02", "ELECTRIC BILL", 9000), t("2026-09-03", "COFFEE", 400)]
    p.opening_cents, p.closing_cents = 100000, 100000 - 5400          # the 90.00 bill is not part of this statement
    assert "exactly 90.00" in ingest._suspect(p, "chequing") and "ELECTRIC BILL" in ingest._suspect(p, "chequing")
    p.closing_cents = 100000 - 5000 - 400 + 9000                      # the 90.00 bill is money in, not out
    assert "wrong way round" in ingest._suspect(p, "chequing")
    p.closing_cents = 100000 - 14400
    assert ingest._suspect(p, "chequing") == ""


def test_a_quiet_month_with_no_transactions_is_filed_as_balanced_not_failed(db, cfg):
    acct(db)
    learn_pdf(db, synth.chequing_pdf(synth.gen(12, date(2026, 8, 1), 29, 5), 500000, period=("Aug 1, 2026", "Aug 31, 2026")), "chequing")
    quiet = synth.chequing_pdf([], 500000, period=("Sep 1, 2026", "Sep 30, 2026"))
    r = ingest.ingest(db, cfg, "quiet.pdf", quiet)
    assert r.status == "reconciled", r.message
    assert db.val("SELECT txn_count FROM statements WHERE id=?", (r.statement_id,)) == 0


def test_only_pay_counts_as_income_by_default_and_a_deposit_you_insist_on_stays_counted(db, cfg):
    from finapp import spending
    acct(db)
    rows = [(date(2026, 9, 2), "GROCERY MART", 4500), (date(2026, 9, 3), "E-TRANSFER FROM FRIEND", -20000), (date(2026, 9, 5), "COFFEE HOUSE", 450),
            (date(2026, 9, 9), "PAYROLL DEPOSIT", -200000), (date(2026, 9, 12), "PIZZA PALACE", 3200), (date(2026, 9, 16), "HARDWARE DEPOT", 7800)]
    pdf = synth.chequing_pdf(rows, 500000)
    learn_pdf(db, pdf, "chequing")
    r = ingest.ingest(db, cfg, "sep.pdf", pdf)
    kind = lambda like: db.val("SELECT kind FROM transactions WHERE description_raw LIKE ?", (like,))
    assert r.status == "reconciled" and kind("%PAYROLL%") == "income" and kind("%FRIEND%") == "setaside"
    row = db.one("SELECT account_id, dedupe_key FROM transactions WHERE description_raw LIKE '%FRIEND%'")
    db.ex("UPDATE transactions SET kind='income' WHERE description_raw LIKE '%FRIEND%'")
    db.ex("INSERT INTO deposit_overrides(account_id,dedupe_key) VALUES(?,?)", (row["account_id"], row["dedupe_key"]))
    ingest.process(db, cfg, r.statement_id)                                            # read again: the one you insisted on stays counted
    assert kind("%FRIEND%") == "income"
    db.ex("DELETE FROM deposit_overrides")
    ingest.process(db, cfg, r.statement_id)
    assert kind("%FRIEND%") == "setaside" and kind("%PAYROLL%") == "income"
    db.ex("UPDATE transactions SET kind='income'")
    db.ex("DELETE FROM settings WHERE key='pay_default_v1'") if db.val("SELECT COUNT(*) FROM settings WHERE key='pay_default_v1'") else None
    assert spending.apply_pay_default_once(db) >= 1 and spending.apply_pay_default_once(db) == 0      # existing deposits get it once
