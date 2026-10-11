from datetime import date
import pytest
import synth
from finapp import ledger, ingest, clock
from test_ingest import acct, learn_pdf, T0

TODAY = date(2026, 10, 9)


def tx(db, a, d, amt, desc, kind="spend", key=None):
    from finapp.statements.common import norm_desc
    return db.ex("""INSERT INTO transactions(account_id,posted_date,amount_cents,description_raw,description_norm,kind,dedupe_key,created_at)
                    VALUES(?,?,?,?,?,?,?,?)""", (a, d, amt, desc, norm_desc(desc), kind, key or f"{d}{amt}{desc}", T0)).lastrowid


def test_merchant_keys_group_store_numbers_and_assign(db):
    a = acct(db)
    t1, t2 = tx(db, a, "2026-09-02", 5000, "GROCERY MART #123 TORONTO"), tx(db, a, "2026-09-09", 4000, "GROCERY MART #456 OTTAWA")
    ledger.assign_merchants(db)
    m = [db.val("SELECT merchant_id FROM transactions WHERE id=?", (t,)) for t in (t1, t2)]
    assert m[0] == m[1] and m[0] is not None
    assert db.val("SELECT name FROM categories WHERE id=(SELECT default_category_id FROM merchants WHERE id=?)", (m[0],)) == "Groceries"


def test_transfers_pair_only_when_unambiguous_and_look_like_transfers(db):
    chq, card = acct(db, "Chq", last4="1"), acct(db, "Card", "credit_card", last4="2")
    o = tx(db, chq, "2026-09-10", 30000, "CREDIT CARD PAYMENT")
    i = tx(db, card, "2026-09-11", -30000, "PAYMENT THANK YOU", kind="income")
    plain = tx(db, chq, "2026-09-12", 1000, "COFFEE HOUSE")
    ledger.detect_transfers(db)
    assert db.val("SELECT kind FROM transactions WHERE id=?", (o,)) == "transfer"
    assert db.val("SELECT transfer_pair_id FROM transactions WHERE id=?", (i,)) == o
    assert db.val("SELECT kind FROM transactions WHERE id=?", (plain,)) == "spend"


def test_two_possible_mates_leave_transfers_alone(db):
    chq, a2, a3 = acct(db, "Chq", last4="1"), acct(db, "S1", "savings", last4="2"), acct(db, "S2", "savings", last4="3")
    o = tx(db, chq, "2026-09-10", 10000, "TRANSFER OUT")
    tx(db, a2, "2026-09-10", -10000, "TRANSFER IN", kind="income")
    tx(db, a3, "2026-09-11", -10000, "TRANSFER IN", kind="income")
    ledger.detect_transfers(db)
    assert db.val("SELECT kind FROM transactions WHERE id=?", (o,)) == "spend"


def decide_n(db, a, desc, bps, n, start=1, month="2026-08"):
    out = []
    for k in range(n):
        t = tx(db, a, f"{month}-{start + k:02d}", 1000 + k, desc, key=f"{desc}{k}{month}{start}")
        ledger.assign_merchants(db)
        ledger.ensure_lines(db, month)
        li = db.val("SELECT id FROM line_items WHERE txn_id=?", (t,))
        ledger.decide(db, li, bps=bps)
        out.append(li)
    return out


def sug(db, desc):
    t = tx(db, db.val("SELECT id FROM accounts"), "2026-09-20", 999, desc, key="probe" + desc + str(db.val("SELECT COUNT(*) FROM transactions")))
    ledger.assign_merchants(db)
    r = db.one("SELECT merchant_id FROM transactions WHERE id=?", (t,))
    cat = db.val("SELECT default_category_id FROM merchants WHERE id=?", (r["merchant_id"],))
    return ledger.suggest(db, r["merchant_id"], cat, TODAY)


def test_no_history_proposes_default_and_asks(db):
    acct(db)
    s = sug(db, "BRAND NEW SHOP")
    assert s["bps"] == 5000 and not s["confident"] and s["source"] == "default"


def test_consistent_history_decides_without_asking_and_explains(db):
    a = acct(db)
    decide_n(db, a, "GROCERY MART #1", 7000, 4)
    s = sug(db, "GROCERY MART #9")
    assert s["bps"] == 7000 and s["confident"] and "4 of 4" in s["why"] and "70%" in s["why"]


def test_mixed_history_is_a_suggestion_not_a_decision(db):
    a = acct(db)
    decide_n(db, a, "PIZZA PALACE", 5000, 2)
    decide_n(db, a, "PIZZA PALACE", 10000, 2, start=10)
    s = sug(db, "PIZZA PALACE")
    assert not s["confident"] and "Not consistent" in s["why"]


def test_recent_decisions_outweigh_old_ones(db):
    a = acct(db)
    decide_n(db, a, "HARDWARE DEPOT", 5000, 3, month="2025-01")
    decide_n(db, a, "HARDWARE DEPOT", 0, 3, month="2026-09")
    assert sug(db, "HARDWARE DEPOT")["bps"] == 0


def test_category_level_tier_helps_a_new_merchant(db):
    a = acct(db)
    for i, name in enumerate(["CAFE ONE", "CAFE TWO", "COFFEE THREE", "COFFEE FOUR"]):
        decide_n(db, a, name, 10000, 1, start=i + 1)
    s = sug(db, "COFFEE FIVE")
    assert s["bps"] == 10000 and "Snacks" in s["why"]


def test_standing_rule_beats_history(db):
    a = acct(db)
    ids = decide_n(db, a, "STREAMING SVC", 5000, 3)
    mid = db.val("SELECT merchant_id FROM transactions WHERE id=(SELECT txn_id FROM line_items WHERE id=?)", (ids[0],))
    ledger.decide(db, ids[0], bps=10000, rule=True)
    assert sug(db, "STREAMING SVC")["source"] == "rule"
    ledger.drop_rule(db, mid)
    assert sug(db, "STREAMING SVC")["source"] == "learned"


def test_changing_category_teaches_the_merchant(db):
    a = acct(db)
    t = tx(db, a, "2026-09-03", 2500, "MYSTERY STORE")
    ledger.assign_merchants(db); ledger.ensure_lines(db, "2026-09")
    li = db.val("SELECT id FROM line_items WHERE txn_id=?", (t,))
    ledger.decide(db, li, bps=5000, category_id=ledger.category_by_name(db, "Health"))
    mid = db.val("SELECT merchant_id FROM transactions WHERE id=?", (t,))
    assert db.val("SELECT default_category_id FROM merchants WHERE id=?", (mid,)) == ledger.category_by_name(db, "Health")


def test_reckoning_settles_up_by_who_paid(db):
    mine, hers, joint = acct(db, "Mine", last4="1"), acct(db, "Hers", last4="2", owner="partner"), acct(db, "Joint", last4="3", owner="joint")
    for a, amt, bps in ((mine, 10000, 5000), (hers, 6000, 5000), (joint, 8000, 5000), (mine, 2000, 0)):
        t = tx(db, a, "2026-09-05", amt, f"SHOP {amt}", key=f"k{amt}")
        ledger.assign_merchants(db); ledger.ensure_lines(db, "2026-09")
        ledger.decide(db, db.val("SELECT id FROM line_items WHERE txn_id=?", (t,)), bps=bps)
    r = ledger.reckoning(db, "2026-09")
    # I paid 10000+2000, my shares 5000+0 -> +7000; she paid 6000, my share 3000 -> -3000; joint: paid 4000 share 4000 -> 0
    assert r["partner_owes_me"] == 4000 and r["mine"] == 5000 + 3000 + 4000 + 0 and r["undecided"] == 0


def test_reprocessing_a_statement_keeps_your_decisions(db, cfg):
    acct(db)
    pdf = synth.chequing_pdf(synth.gen(10, date(2026, 9, 1), 29, 5), 500000)
    learn_pdf(db, pdf, "chequing")
    r = ingest.ingest(db, cfg, "a.pdf", pdf)
    items = ledger.month_items(db, "2026-09")
    spend = [i for i in items][:2]
    for i in spend:
        ledger.decide(db, i["id"], bps=2500)
    assert ingest.process(db, cfg, r.statement_id).status == "reconciled"
    kept = [i for i in ledger.month_items(db, "2026-09") if i["reviewed"]]
    assert len(kept) == 2 and all(k["my_bps"] == 2500 for k in kept)


def income(db, month="2026-09"):
    return ledger.reckoning(db, month)["income"]


def test_card_payments_are_transfers_never_income_and_never_double_counted_spending(db):
    chq, card = acct(db, "Debit", last4="1"), acct(db, "Visa", "credit_card", last4="2")
    tx(db, chq, "2026-09-02", -520000, "PAYROLL DEPOSIT", kind="income", key="pay")
    out = tx(db, chq, "2026-09-10", 30000, "BMO MASTERCARD PAYMENT", key="out")
    inn = tx(db, card, "2026-09-11", -30000, "PAYMENT THANK YOU", kind="income", key="in")
    tx(db, card, "2026-09-12", 4500, "PIZZA PALACE", key="buy")
    ledger.after_ingest(db)
    kinds = {t: db.val("SELECT kind FROM transactions WHERE id=?", (t,)) for t in (out, inn)}
    assert kinds == {out: "transfer", inn: "transfer"}
    assert income(db) == 520000


def test_an_unmatched_card_credit_is_a_payment_or_a_refund_and_is_never_income(db):
    card = acct(db, "Visa", "credit_card", last4="2")
    pay = tx(db, card, "2026-09-05", -190905, "ELECTRONIC FUNDS", kind="income", key="p")
    ref = tx(db, card, "2026-09-06", -1987, "SHOP RETURN CREDIT", kind="income", key="r")
    db.ex("INSERT INTO statements(account_id,kind,file_sha256,original_name,stored_name,status,received_at,opening_cents) VALUES(?,?,?,?,?,?,?,?)",
          (card, "pdf", "x", "s.pdf", "s", "reconciled", "2026-09-30T00:00:00Z", 190905))
    db.ex("UPDATE transactions SET statement_id=1 WHERE id=?", (pay,))                     # it equals the balance the statement opened with
    ledger.after_ingest(db)
    assert db.val("SELECT kind FROM transactions WHERE id=?", (pay,)) == "transfer"
    assert db.val("SELECT kind FROM transactions WHERE id=?", (ref,)) == "refund"
    assert income(db) == 0


def test_a_card_credit_that_slipped_in_as_income_is_not_counted_even_before_it_is_reclassified(db):
    chq, card = acct(db, "Debit", last4="1"), acct(db, "Visa", "credit_card", last4="2")
    tx(db, chq, "2026-09-02", -520000, "PAYROLL DEPOSIT", kind="income", key="pay")
    tx(db, card, "2026-09-11", -30000, "WHATEVER", kind="income", key="in")
    assert income(db) == 520000


def test_a_transfer_whose_partner_was_re_read_heals_and_pairs_again(db, cfg):
    chq, card = acct(db, "Debit", last4="1"), acct(db, "Visa", "credit_card", last4="2")
    out = tx(db, chq, "2026-09-10", 30000, "CARD PAYMENT", key="out")
    inn = tx(db, card, "2026-09-11", -30000, "PAYMENT THANK YOU", kind="income", key="in")
    ledger.after_ingest(db)
    assert db.val("SELECT transfer_pair_id FROM transactions WHERE id=?", (out,)) == inn
    db.ex("DELETE FROM transactions WHERE id=?", (inn,))                                    # as if the card statement were re-read
    new = tx(db, card, "2026-09-11", -30000, "PAYMENT THANK YOU", kind="income", key="in2")
    ledger.after_ingest(db)
    assert db.val("SELECT kind FROM transactions WHERE id=?", (out,)) == "transfer" and db.val("SELECT transfer_pair_id FROM transactions WHERE id=?", (out,)) == new


def test_partner_tally_nets_manual_and_marked_repayments_and_keeps_them_out_of_income(db):
    a = acct(db)
    tx(db, a, "2026-09-03", 10000, "COSTCO WHOLESALE")
    dep = tx(db, a, "2026-09-20", -2000, "E-TRANSFER FROM PARTNER", kind="income", key="dep1")
    ledger.assign_merchants(db)
    ledger.ensure_lines(db, "2026-09")
    db.ex("UPDATE line_items SET my_bps=5000, reviewed=1, confident=1")
    t = ledger.partner_tally(db)
    assert t["owed"] == 5000 and t["balance"] == 5000 and t["payments"] == []
    db.ex("INSERT INTO settlements(paid_on,amount_cents,direction,created_at) VALUES('2026-09-15',3000,'from_partner','x')")
    assert ledger.partner_tally(db)["balance"] == 2000
    assert [c["id"] for c in ledger.repayment_candidates(db, "2026-09-01")] == [dep]
    income_before = ledger.reckoning(db, "2026-09")["income"]
    db.ex("UPDATE transactions SET kind='settlement' WHERE id=?", (dep,))
    t = ledger.partner_tally(db)
    assert t["balance"] == 0 and t["received"] == 5000 and {p["src"] for p in t["payments"]} == {"manual", "txn"}
    assert ledger.reckoning(db, "2026-09")["income"] == income_before - 2000 and ledger.repayment_candidates(db, "2026-09-01") == []
    db.ex("INSERT INTO settlements(paid_on,amount_cents,direction,created_at) VALUES('2026-09-25',1000,'to_partner','x')")
    assert ledger.partner_tally(db)["balance"] == 1000
    assert ledger.partner_tally(db, "2026-10")["owed"] == 0


def _owed_setup(db):
    a = acct(db)
    tx(db, a, "2026-09-03", 100000, "COSTCO WHOLESALE", key="c1")
    ledger.assign_merchants(db)
    ledger.ensure_lines(db, "2026-09")
    db.ex("UPDATE line_items SET my_bps=5000, reviewed=1, confident=1")
    return a


def test_repayments_are_suggested_adjustable_and_remembered_and_nothing_is_counted_until_you_say(db):
    a = _owed_setup(db)
    et = tx(db, a, "2026-09-16", -30000, "INTERAC e-Transfer Received J SMITH", kind="setaside", key="e1")
    pay = tx(db, a, "2026-09-15", -222018, "Payroll dep. Active Products", kind="income", key="p1")
    small = tx(db, a, "2026-09-20", -2000, "Deposit Free Interac E-Transfer", kind="setaside", key="e2")
    t = ledger.partner_tally(db)
    assert t["owed"] == 50000 and t["balance"] == 50000
    assert {x["id"] for x in t["suggested"]} == {et, small} and pay not in {x["id"] for x in t["suggested"]}        # pay is never a repayment
    assert t["suggested_total"] == 0 and t["maybe_total"] == 32000 and t["balance_after_suggested"] == 50000        # maybes are shown, not counted
    assert ledger.repayment_confirm(db, et, 25000)                                                                      # only $250 of the $300 was hers
    t = ledger.partner_tally(db)
    assert t["received"] == 25000 and t["balance"] == 25000 and t["payments"][0]["full"] == 30000 and t["payments"][0]["cents"] == 25000
    ledger.repayment_reject(db, small)
    assert ledger.partner_tally(db)["suggested"] == []
    ledger.repayment_undo(db, et)
    assert ledger.partner_tally(db)["balance"] == 50000 and {x["id"] for x in ledger.partner_tally(db)["suggested"]} == {et}


def test_a_deposit_like_one_you_already_confirmed_is_marked_sure_and_waiving_settles_the_rest(db):
    a = _owed_setup(db)
    first = tx(db, a, "2026-09-10", -10000, "INTERAC e-Transfer Received PARTNER NAME", kind="setaside", key="e1")
    ledger.repayment_confirm(db, first)
    later = tx(db, a, "2026-09-24", -15000, "INTERAC e-Transfer Received PARTNER NAME", kind="setaside", key="e2")
    sug = ledger.partner_tally(db)["suggested"]
    assert [(x["id"], x["sure"], x["counted"]) for x in sug] == [(later, True, 15000)]                 # counted for now, but never past what is owed
    assert ledger.partner_tally(db)["balance_after_suggested"] == 40000 - 15000
    db.ex("INSERT INTO settlements(paid_on,amount_cents,direction,note,created_at) VALUES('2026-09-30',40000,'waived','let it go','x')")
    t = ledger.partner_tally(db)
    assert t["waived"] == 40000 and t["received"] == 10000 and t["balance"] == 0 and any(p["waived"] for p in t["payments"])


def test_a_repayment_entered_by_hand_finds_its_deposit_when_the_statement_arrives_and_is_not_counted_twice(db):
    a = _owed_setup(db)
    db.ex("INSERT INTO settlements(paid_on,amount_cents,direction,note,created_at) VALUES('2026-09-20',76000,'from_partner','entered by hand','x')")
    db.ex("INSERT INTO settlements(paid_on,amount_cents,direction,note,created_at) VALUES('2026-09-28',5000,'from_partner','entered by hand','x')")
    assert ledger.match_manual_repayments(db) == 0 and ledger.partner_tally(db)["received"] == 81000     # nothing to match yet
    tx(db, a, "2026-09-12", -76000, "INTERAC e-Transfer Received", kind="setaside", key="early")           # too early for the first entry
    pay = tx(db, a, "2026-09-21", -222018, "Payroll dep. Active Products", kind="income", key="pp")
    dep = tx(db, a, "2026-09-22", -76000, "INTERAC e-Transfer Received", kind="setaside", key="e1")
    assert ledger.match_manual_repayments(db) == 1
    t = ledger.partner_tally(db)
    assert db.val("SELECT kind FROM transactions WHERE id=?", (dep,)) == "settlement" and db.val("SELECT kind FROM transactions WHERE id=?", (pay,)) == "income"
    assert t["received"] == 76000 + 5000 and len([p for p in t["payments"] if p["src"] == "manual"]) == 1       # the hand entry retired; the $50 one still waits
    assert db.val("SELECT note FROM settlements WHERE amount_cents=76000").startswith("matched to the deposit on 2026-09-22")
    assert ledger.match_manual_repayments(db) == 0                                                         # and it never matches twice
