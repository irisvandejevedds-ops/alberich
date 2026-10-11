import io
from datetime import date
import pytest
from PIL import Image, ImageDraw, ImageFont
from finapp import receipts, ledger, clock
from test_ledger import tx
from test_ingest import acct

TEXT = """GROCERY MART
123 Main St Toronto
Tel 416-555-0199
2026-09-12  14:03
MILK 2%                 4.49 F
BREAD WHOLE WHEAT       3.29 F
DISH SOAP               5.99 T
BATTERIES AA 8PK       12.99 T
COUPON                  -2.00
SUBTOTAL               24.76
HST 13%                 2.34
TOTAL                  27.10
VISA DEBIT             27.10
CHANGE                  0.00
Thank you for shopping
"""
TODAY = date(2026, 10, 9)


def test_parse_reads_merchant_date_lines_and_totals():
    p = receipts.parse_text(TEXT, TODAY)
    assert p["merchant"] == "GROCERY MART" and p["date"] == "2026-09-12"
    assert p["total"] == 2710 and p["subtotal"] == 2476 and p["tax"] == 234
    assert [d for d, _ in p["lines"]] == ["MILK 2%", "BREAD WHOLE WHEAT", "DISH SOAP", "BATTERIES AA 8PK", "COUPON"]
    assert sum(a for _, a in p["lines"]) == 2476
    assert receipts.validate(p)[0] == "ok"


def test_validate_flags_a_missed_line_and_incomplete_receipts():
    p = receipts.parse_text(TEXT.replace("DISH SOAP               5.99 T\n", ""), TODAY)
    v, note = receipts.validate(p)
    assert v == "mismatch" and "add up" in note
    assert receipts.validate(receipts.parse_text("STORE\nhello", TODAY))[0] == "incomplete"


def test_tax_inclusive_prices_and_date_formats():
    p = receipts.parse_text("CAFE ONE\nSep 3, 2026\nLATTE 4.50\nMUFFIN 3.00\nTOTAL 7.50\n", TODAY)
    assert p["date"] == "2026-09-03" and receipts.validate(p)[0] == "ok"
    assert receipts.find_date("on 09/28/26 at noon", TODAY) == "2026-09-28"
    assert receipts.find_date("28/09/2026", TODAY) == "2026-09-28"
    assert receipts.find_date("no date here", TODAY) is None
    assert receipts.find_date("1999-01-01", TODAY) is None


def test_allocate_tax_is_exact():
    for amounts, tax in (([449, 329, 599, 1299, -200], 234), ([100, 100, 100], 1), ([333, 333, 334], 7), ([500], 65)):
        sh = receipts.allocate_tax(amounts, tax)
        assert sum(sh) == tax and all(s == 0 for s, a in zip(sh, amounts) if a < 0)


def mk_receipt(db, text=TEXT, status="parsed"):
    p = receipts.parse_text(text, TODAY)
    rid = db.ex("INSERT INTO receipts(sha256,stored_name,kind,status,created_at) VALUES(?,?,?,?,?)",
                (f"s{db.val('SELECT COUNT(*) FROM receipts')}", "x", "image", status, "2026-09-12T00:00:00Z")).lastrowid
    receipts.apply_parse(db, rid, p, "tesseract")
    return rid


def test_exact_amount_match_explodes_into_lines_that_sum_to_the_bank_amount(db):
    a = acct(db)
    t = tx(db, a, "2026-09-13", 2710, "GROCERY MART #123 TORONTO")
    ledger.assign_merchants(db)
    rid = mk_receipt(db)
    r = db.one("SELECT * FROM receipts WHERE id=?", (rid,))
    assert r["txn_id"] == t and r["status"] == "matched"
    lines = db.q("SELECT * FROM line_items WHERE txn_id=? ORDER BY id", (t,))
    assert len(lines) == 5 and sum(l["amount_cents"] for l in lines) == 2710 and all(l["receipt_id"] == rid for l in lines)
    assert lines[0]["description"] == "MILK 2%" and lines[0]["amount_cents"] > 449            # carries its share of tax


def test_tip_on_the_bank_amount_becomes_its_own_line(db):
    a = acct(db)
    t = tx(db, a, "2026-09-13", 3200, "GROCERY MART #123")
    ledger.assign_merchants(db)
    mk_receipt(db)
    lines = db.q("SELECT * FROM line_items WHERE txn_id=?", (t,))
    assert sum(l["amount_cents"] for l in lines) == 3200 and any(l["description"].startswith("Tip") for l in lines)


def test_ambiguous_candidates_wait_for_you(db):
    a = acct(db)
    t1 = tx(db, a, "2026-09-13", 2710, "SOMEWHERE ELSE", key="a")
    t2 = tx(db, a, "2026-09-14", 2710, "OTHER PLACE", key="b")
    ledger.assign_merchants(db)
    rid = mk_receipt(db)
    assert db.val("SELECT txn_id FROM receipts WHERE id=?", (rid,)) is None
    assert len(receipts.candidates(db, db.one("SELECT * FROM receipts WHERE id=?", (rid,)))) == 2
    receipts.link(db, rid, t2)
    assert db.val("SELECT status FROM receipts WHERE id=?", (rid,)) == "matched"
    assert db.val("SELECT COUNT(*) FROM line_items WHERE txn_id=?", (t2,)) == 5


def test_a_statement_that_arrives_later_matches_the_waiting_receipt(db):
    a = acct(db)
    rid = mk_receipt(db)
    assert db.val("SELECT txn_id FROM receipts WHERE id=?", (rid,)) is None
    t = tx(db, a, "2026-09-14", 2710, "GROCERY MART")
    ledger.after_ingest(db)
    assert db.val("SELECT txn_id FROM receipts WHERE id=?", (rid,)) == t


def test_a_mismatched_receipt_is_not_exploded_until_accepted(db):
    a = acct(db)
    t = tx(db, a, "2026-09-13", 2710, "GROCERY MART")
    ledger.assign_merchants(db)
    rid = mk_receipt(db, TEXT.replace("DISH SOAP               5.99 T\n", ""))
    assert db.val("SELECT status FROM receipts WHERE id=?", (rid,)) == "needs_review"
    assert db.val("SELECT COUNT(*) FROM line_items WHERE txn_id=?", (t,)) == 0
    r = db.one("SELECT * FROM receipts WHERE id=?", (rid,))
    receipts.save_edit(db, rid, r["merchant"], r["purchase_date"], r["total_cents"], r["tax_cents"], [("MILK", 449), ("OTHER", 1200)], accept=True)
    assert db.val("SELECT status FROM receipts WHERE id=?", (rid,)) == "matched"
    assert sum(l["amount_cents"] for l in db.q("SELECT * FROM line_items WHERE txn_id=?", (t,))) == 2710


def test_item_level_learning_beats_merchant_level(db):
    a = acct(db)
    for n in range(3):
        t = tx(db, a, f"2026-08-{10 + n}", 2710, "GROCERY MART", key=f"g{n}")
        ledger.assign_merchants(db)
        rid = mk_receipt(db, TEXT.replace('2026-09-12', f'2026-08-{10 + n}'))
        for li in db.q("SELECT id, item_key FROM line_items WHERE txn_id=?", (t,)):
            ledger.decide(db, li["id"], bps=0 if li["item_key"] == "DISH SOAP" else 5000)
    t = tx(db, a, "2026-09-13", 2710, "GROCERY MART", key="new")
    ledger.assign_merchants(db)
    mk_receipt(db)
    soap = db.one("SELECT * FROM line_items WHERE txn_id=? AND item_key='DISH SOAP'", (t,))
    milk = db.one("SELECT * FROM line_items WHERE txn_id=? AND item_key='MILK'", (t,))
    assert soap["my_bps"] == 0 and soap["confident"] and "Dish Soap" in soap["why"]
    assert milk["my_bps"] == 5000


def test_reexploding_the_same_receipt_keeps_your_decisions(db):
    a = acct(db)
    t = tx(db, a, "2026-09-13", 2710, "GROCERY MART")
    ledger.assign_merchants(db)
    rid = mk_receipt(db)
    li = db.val("SELECT id FROM line_items WHERE txn_id=? ORDER BY id LIMIT 1", (t,))
    ledger.decide(db, li, bps=0)
    receipts.explode(db, rid)
    assert db.val("SELECT reviewed FROM line_items WHERE id=?", (li,)) == 1


def test_unlink_returns_to_a_single_line(db):
    a = acct(db)
    t = tx(db, a, "2026-09-13", 2710, "GROCERY MART")
    ledger.assign_merchants(db)
    rid = mk_receipt(db)
    receipts.unlink(db, rid)
    assert db.val("SELECT COUNT(*) FROM line_items WHERE txn_id=?", (t,)) == 0
    ledger.ensure_lines(db, "2026-09")
    assert db.val("SELECT COUNT(*) FROM line_items WHERE txn_id=?", (t,)) == 1


def photo(text, size=30, skew=0):
    font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf", size)
    lines = text.splitlines()
    im = Image.new("RGB", (900, (len(lines) + 2) * (size + 8)), (245, 243, 236))
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        d.text((30, 20 + i * (size + 8)), ln, fill=(20, 20, 20), font=font)
    if skew:
        im = im.rotate(skew, expand=True, fillcolor=(245, 243, 236))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def test_a_real_photo_goes_through_ocr_to_a_matched_receipt(db, cfg):
    a = acct(db)
    t = tx(db, a, "2026-09-13", 2710, "GROCERY MART #123 TORONTO")
    ledger.assign_merchants(db)
    rid, st = receipts.add(db, cfg, "r.jpg", photo(TEXT))
    assert st == "queued"
    receipts.process_queue(db, cfg)
    r = db.one("SELECT * FROM receipts WHERE id=?", (rid,))
    assert r["total_cents"] == 2710 and r["purchase_date"] == "2026-09-12" and r["verdict"] == "ok", r["ocr_text"]
    assert r["status"] == "matched" and r["txn_id"] == t


def test_duplicates_junk_and_encryption(db, cfg):
    d = photo(TEXT)
    rid, _ = receipts.add(db, cfg, "r.jpg", d)
    assert receipts.add(db, cfg, "again.jpg", d) == (rid, "duplicate")
    assert receipts.add(db, cfg, "x.txt", b"nope") == (None, "unsupported")
    name = db.val("SELECT stored_name FROM receipts WHERE id=?", (rid,))
    assert b"JFIF" not in (cfg.files_dir / "receipts" / name).read_bytes()
    assert receipts.load_bytes(db, cfg, rid) == d
    receipts.delete(db, cfg, rid)
    assert not (cfg.files_dir / "receipts" / name).exists()


def test_each_receipt_item_is_its_own_review_card_with_its_own_split_and_settle_up_uses_them(db):
    a = acct(db)
    t = tx(db, a, "2026-09-13", 2710, "GROCERY MART #123 TORONTO")
    ledger.assign_merchants(db)
    mk_receipt(db)
    items = [i for i in ledger.month_items(db, "2026-09") if i["txn_id"] == t]
    assert len(items) == 5 and all(i["receipt_id"] and i["txn_cents"] == 2710 for i in items)           # five cards, each knowing its purchase
    share = {"MILK": 3300, "BREAD WHOLE WHEAT": 5000, "DISH SOAP": 0, "BATTERIES AA PK": 10000, "COUPON": 5000}
    for i in items:
        ledger.decide(db, i["id"], bps=share.get(i["item_key"], 5000))
    got = {i["item_key"]: i["my_bps"] for i in ledger.month_items(db, "2026-09") if i["txn_id"] == t}
    assert got["MILK"] == 3300 and got["DISH SOAP"] == 0 and got["BATTERIES AA PK"] == 10000
    owes = sum(i["amount_cents"] - ledger.my_part(i["amount_cents"], i["my_bps"]) for i in ledger.month_items(db, "2026-09") if i["txn_id"] == t)
    assert ledger.reckoning(db, "2026-09")["partner_owes_me"] == owes and 0 < owes < 2710


def test_a_receipt_for_a_purchase_you_already_decided_puts_its_items_back_up_for_review(db):
    a = acct(db)
    t = tx(db, a, "2026-09-13", 2710, "GROCERY MART #123")
    ledger.assign_merchants(db)
    ledger.ensure_lines(db, "2026-09")
    ledger.decide(db, db.val("SELECT id FROM line_items WHERE txn_id=?", (t,)), bps=10000)             # decided long before the receipt
    assert [i["reviewed"] for i in ledger.month_items(db, "2026-09") if i["txn_id"] == t] == [1]
    mk_receipt(db)
    items = [i for i in ledger.month_items(db, "2026-09") if i["txn_id"] == t]
    assert len(items) == 5 and all(i["receipt_id"] for i in items)
    assert all(not i["reviewed"] and not i["confident"] for i in items)                                  # all of them need your call again
    assert all("already decided" in i["why"] for i in items)
    assert ledger.progress(ledger.month_items(db, "2026-09"))["ask"] == 5
