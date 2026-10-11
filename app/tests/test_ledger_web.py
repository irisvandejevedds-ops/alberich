import json
from datetime import date
import pytest
import synth
from fastapi.testclient import TestClient
from finapp import auth, clock
from finapp.config import Config, set_config
from finapp.main import create_app
from test_web import _env, PASS


@pytest.fixture
def c(tmp_path):
    auth.reset_fails()
    set_config(Config(_env(tmp_path, FINAPP_FILE_KEY="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")))
    cl = TestClient(create_app(), follow_redirects=False)
    assert cl.post("/setup", data={"passphrase": PASS, "confirm": PASS}).status_code == 303
    yield cl
    clock.set_now(None)


def add_account(c, **kw):
    d = {"name": "Chequing", "institution": "Bank", "type": "chequing", "owner": "me", "last4": "8901", "track_from": "2026-08"}
    d.update(kw)
    assert c.post("/ledgers/accounts/save", data=d).status_code == 303


def upload(c, name, data, **form):
    r = c.post("/api/ledgers/upload", files={"files": (name, data)}, data=form)
    assert r.status_code == 200
    return r.json()["results"][0]


def test_pages_render_and_share_target_in_manifest(c):
    for p in ("/ledgers", "/ledgers/accounts"):
        assert c.get(p).status_code == 200
    m = c.get("/manifest.webmanifest").json()
    assert m["share_target"]["action"] == "/share"


def test_full_flow_unknown_pdf_to_matrix(c):
    add_account(c)
    pdf = synth.chequing_pdf(synth.gen(15, date(2026, 9, 1), 29, 5), 500000)
    r = upload(c, "sep.pdf", pdf)
    assert r["status"] == "pending_template"
    sid = r["id"]
    go = c.get(f"/statements/{sid}")
    assert go.status_code == 303 and go.headers["location"] == f"/statements/{sid}/template"
    assert c.get(f"/statements/{sid}?details=1").status_code == 200
    page = c.get(f"/statements/{sid}/template?atype=chequing")
    assert page.status_code == 200 and "Preview: 15 lines" in page.text and "Balances" in page.text
    import re, html
    spec = html.unescape(re.search(r'<textarea name="spec"[^>]*>(.*?)</textarea>', page.text, re.S).group(1))
    r2 = c.post(f"/statements/{sid}/template/save", data={"atype": "chequing", "spec": spec, "name": "T", "institution": "Bank"})
    assert r2.status_code == 303
    det = c.get(f"/statements/{sid}")
    assert "Balanced" in det.text and "15 lines" in det.text
    # a different month in the same layout is now recognised without any teaching
    pdf2 = synth.chequing_pdf(synth.gen(9, date(2026, 8, 1), 29, 6), 300000, period=("Aug 1, 2026", "Aug 31, 2026"))
    assert upload(c, "aug.pdf", pdf2)["status"] == "reconciled"
    page = c.get("/ledgers").text
    assert "Filed" in page and "Needs you" not in page
    assert upload(c, "aug-again.pdf", pdf2)["status"] == "duplicate"


def test_waiting_statements_are_filed_after_one_template_is_taught(c):
    add_account(c)
    a = synth.chequing_pdf(synth.gen(10, date(2026, 8, 1), 29, 6), 300000, period=("Aug 1, 2026", "Aug 31, 2026"))
    b = synth.chequing_pdf(synth.gen(10, date(2026, 9, 1), 29, 5), 500000)
    ra, rb = upload(c, "a.pdf", a), upload(c, "b.pdf", b)
    assert ra["status"] == rb["status"] == "pending_template"
    import re, html
    page = c.get(f"/statements/{rb['id']}/template?atype=chequing").text
    spec = html.unescape(re.search(r'<textarea name="spec"[^>]*>(.*?)</textarea>', page, re.S).group(1))
    c.post(f"/statements/{rb['id']}/template/save", data={"atype": "chequing", "spec": spec, "name": "T"})
    assert "Balanced" in c.get(f"/statements/{ra['id']}").text


def test_unsupported_and_oversize_are_reported_not_crashed(c):
    assert upload(c, "x.txt", b"hello there")["status"] == "unsupported"
    assert upload(c, "e.pdf", b"")["status"] == "unsupported"


def test_bad_template_json_is_a_400(c):
    add_account(c)
    r = upload(c, "x.pdf", synth.chequing_pdf(synth.gen(6, date(2026, 9, 1), 29, 5), 500000))
    assert c.post(f"/statements/{r['id']}/template/save", data={"atype": "chequing", "spec": "{nope"}).status_code == 400


def test_accept_requires_reason_and_review_state(c):
    add_account(c)
    good = synth.chequing_pdf(synth.gen(12, date(2026, 9, 1), 29, 8), 500000)
    import re, html
    first = upload(c, "g.pdf", good)
    page = c.get(f"/statements/{first['id']}/template?atype=chequing").text
    spec = html.unescape(re.search(r'<textarea name="spec"[^>]*>(.*?)</textarea>', page, re.S).group(1))
    c.post(f"/statements/{first['id']}/template/save", data={"atype": "chequing", "spec": spec, "name": "T"})
    bad = upload(c, "b.pdf", synth.chequing_pdf(synth.gen(12, date(2026, 9, 1), 29, 8), 500000, tamper=5000))
    assert bad["status"] == "needs_review"
    assert c.post(f"/statements/{bad['id']}/accept", data={"reason": ""}).status_code == 400
    assert c.post(f"/statements/{bad['id']}/accept", data={"reason": "bank fee confirmed"}).status_code == 303
    assert "Balanced" in c.get(f"/statements/{bad['id']}").text
    assert c.post(f"/statements/{first['id']}/accept", data={"reason": "should not work"}).status_code == 400


def test_share_target_post_files_and_redirects(c):
    add_account(c)
    r = c.post("/share", files={"files": ("s.pdf", synth.chequing_pdf(synth.gen(6, date(2026, 9, 1), 29, 5), 500000))})
    assert r.status_code == 303 and r.headers["location"].startswith("/ledgers?shared=1")


def test_refine_requires_consent_and_shows_masked_text_only(c):
    add_account(c)
    r = upload(c, "x.pdf", synth.chequing_pdf(synth.gen(8, date(2026, 9, 1), 29, 5), 500000))
    page = c.get(f"/statements/{r['id']}/template?atype=chequing").text
    assert "No Anthropic key" in page
    resp = c.post(f"/statements/{r['id']}/template/refine", data={"atype": "chequing"})
    assert resp.status_code == 200 and ("Tick the box" in resp.text)


def test_delete_statement(c):
    add_account(c)
    r = upload(c, "x.pdf", synth.chequing_pdf(synth.gen(8, date(2026, 9, 1), 29, 5), 500000))
    assert c.post(f"/statements/{r['id']}/delete").status_code == 303
    assert c.get(f"/statements/{r['id']}").status_code == 404


def test_unauthenticated_upload_is_refused(c):
    other = TestClient(create_app(), follow_redirects=False)
    assert other.post("/api/ledgers/upload", files={"files": ("a.pdf", b"%PDF-")}).status_code == 401


def filed_september(c):
    add_account(c)
    import re, html
    r = upload(c, "sep.pdf", synth.chequing_pdf(synth.gen(14, date(2026, 9, 1), 29, 5), 500000))
    page = c.get(f"/statements/{r['id']}/template?atype=chequing").text
    spec = html.unescape(re.search(r'<textarea name="spec"[^>]*>(.*?)</textarea>', page, re.S).group(1))
    c.post(f"/statements/{r['id']}/template/save", data={"atype": "chequing", "spec": spec, "name": "T"})
    return r["id"]


def test_review_flow_decide_learn_and_reckon(c):
    filed_september(c)
    page = c.get("/review?month=2026-09")
    assert page.status_code == 200 and "Needs your call" in page.text
    from finapp.db import DB
    from finapp.config import get_config
    db = DB(get_config().db_path)
    lines = db.q("SELECT li.id, t.merchant_id FROM line_items li JOIN transactions t ON t.id=li.txn_id")
    assert lines
    H = {"X-Finapp": "1"}
    r = c.post("/api/review/decide", json={"line_id": lines[0]["id"], "bps": 10000, "rule": False}, headers=H)
    assert r.status_code == 200 and r.json()["progress"]["done"] == 1
    assert c.post("/api/review/decide", json={"line_id": lines[0]["id"], "bps": "x"}, headers=H).status_code == 400
    r = c.post("/api/review/confirm", json={"month": "2026-09", "which": "ask"}, headers=H)
    assert r.status_code == 200 and r.json()["progress"]["ask"] == 0
    assert c.post("/api/review/confirm", json={"month": "bad", "which": "ask"}, headers=H).status_code == 400
    rk = c.get("/reckoning?month=2026-09")
    assert rk.status_code == 200 and "Your share of spending" in rk.text and "still undecided" not in rk.text
    assert c.post("/api/review/reopen", json={"line_id": lines[0]["id"]}, headers=H).status_code == 200


def test_review_and_reckoning_require_login(c):
    other = TestClient(create_app(), follow_redirects=False)
    assert other.get("/review").status_code == 303 and other.post("/api/review/decide", json={}).status_code == 401


def test_receipt_upload_review_and_pages(c):
    import io
    from PIL import Image
    from test_receipts import photo, TEXT
    add_account(c)
    from finapp.db import DB
    from finapp.config import get_config
    from finapp import receipts as R
    db = DB(get_config().db_path)
    db.ex("INSERT INTO transactions(account_id,posted_date,amount_cents,description_raw,description_norm,kind,dedupe_key,created_at) "
          "VALUES(1,'2026-09-13',2710,'GROCERY MART #123','GROCERY MART','spend','k1','2026-09-13T00:00:00Z')")
    r = c.post("/api/receipts/upload", files={"files": ("r.jpg", photo(TEXT), "image/jpeg")})
    assert r.status_code == 200 and r.json()["results"][0]["status"] == "queued"
    rid = r.json()["results"][0]["id"]
    assert R.process_queue(db, get_config()) == 1
    assert "Waiting to be read" not in c.get("/receipts").text
    d = c.get(f"/receipts/{rid}")
    assert d.status_code == 200 and "MILK" in d.text and "Bank charge" in d.text
    img = c.get(f"/receipts/{rid}/image")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert c.get("/api/receipts/busy").json() == {"busy": 0}
    rv = c.get("/review?month=2026-09")
    assert "MILK 2%" in rv.text and "on GROCERY MART #123" in rv.text
    assert c.post(f"/receipts/{rid}/model", data={}).status_code == 400
    assert c.post("/api/receipts/upload", files={"files": ("x.txt", b"nope")}).json()["results"][0]["status"] == "unsupported"
    assert c.post(f"/receipts/{rid}/delete").status_code == 303 and c.get(f"/receipts/{rid}").status_code == 404


def test_receipt_edit_validates_input(c):
    from test_receipts import photo, TEXT
    r = c.post("/api/receipts/upload", files={"files": ("r.jpg", photo(TEXT), "image/jpeg")}).json()["results"][0]
    assert c.post(f"/receipts/{r['id']}/save", data={"merchant": "X", "date": "yesterday", "total": "1"}).status_code == 400
    assert c.post(f"/receipts/{r['id']}/save", data={"merchant": "X", "date": "2026-09-01", "total": "abc"}).status_code == 400
    ok = c.post(f"/receipts/{r['id']}/save", data={"merchant": "X", "date": "2026-09-01", "total": "5.00", "tax": "", "desc": ["A", "B"], "amt": ["2.00", "3.00"]})
    assert ok.status_code == 303


def test_share_target_routes_photos_to_receipts(c):
    from test_receipts import photo, TEXT
    r = c.post("/share", files={"files": ("r.jpg", photo(TEXT), "image/jpeg")})
    assert r.status_code == 303 and r.headers["location"] == "/receipts"


def test_treasury_shows_averages_saves_cuts_and_the_purse_follows(c):
    from finapp import spending
    from finapp.db import DB
    from finapp.config import get_config
    empty = c.get("/treasury")
    assert empty.status_code == 200 and "No spending on file yet" in empty.text and "Budgets, per month" not in empty.text and "Close" not in empty.text.split("<main")[1]
    add_account(c)
    db = DB(get_config().db_path)
    for m, days in (("2026-08", (3, 9, 15, 21)), ("2026-09", (4, 10, 16, 22))):
        for k, d in enumerate(days):
            db.ex("INSERT INTO transactions(account_id,posted_date,amount_cents,description_raw,description_norm,kind,dedupe_key,created_at) VALUES(1,?,?,'PIZZA PALACE','PIZZA PALACE','spend',?,'x')", (f"{m}-{d:02d}", 5000, f"{m}{k}"))
        db.ex("INSERT INTO statements(account_id,kind,period_start,period_end,file_sha256,original_name,stored_name,status,received_at) VALUES(1,'pdf',?,?,?,'s.pdf','x','reconciled','x')", (f"{m}-01", f"{m}-28", m))
    from finapp import ledger
    for m in ("2026-08", "2026-09"):
        ledger.ensure_lines(db, m)
    db.ex("UPDATE line_items SET my_bps=10000")
    dining = db.val("SELECT id FROM categories WHERE name='Dining'")
    act = db.val("SELECT id FROM activities LIMIT 1")
    db.ex("UPDATE activities SET auto_credits=0 WHERE id<>?", (act,))              # the other starter pouches would share Dining
    assert c.post("/activities/save", data={"id": act, "name": "Drinks out", "estimate": "50", "weight": "3", "credits": "1", "mode": "even", "category_id": dining, "auto": "on"}).status_code == 303
    page = c.get("/treasury").text
    assert "Your real month" in page and "Spend less? (optional)" in page and "Dining" in page and "Drinks out" in page and "no limit" not in page.lower()
    credits_before = db.val("SELECT credits_per_month FROM activities WHERE id=?", (act,))
    assert credits_before == 4                                                  # about $200 a month in Dining, $50 a credit
    assert c.post("/treasury/cuts", data={"global": "abc"}).status_code == 400
    assert c.post("/treasury/cuts", data={"global": "25", f"m_{dining}": ""}).status_code == 303
    assert spending.reduce_pct(db) == 25 and db.val("SELECT credits_per_month FROM activities WHERE id=?", (act,)) == 3     # 25% off follows into the Purse
    page = c.get("/treasury").text
    assert "about $" in page and "% of your income" not in page                  # no deposits on file, so only dollars are shown
    assert c.post("/treasury/cuts", data={"global": "25", f"m_{dining}": "0"}).status_code == 303
    assert spending.mods(db)[dining] == 0 and db.val("SELECT credits_per_month FROM activities WHERE id=?", (act,)) == 4     # its own 0% beats the overall cut
    assert c.post("/treasury/budgets", data={}).status_code in (404, 405)       # the old budget form is gone



def card_pdf():
    return synth.card_pdf(synth.gen(20, date(2026, 8, 14), 28, 3), 100000, period=("Aug 14, 2026", "Sep 13, 2026"))


def spec_from(page):
    import re, html
    return html.unescape(re.search(r'<textarea name="spec"[^>]*>(.*?)</textarea>', page, re.S).group(1))


def test_template_screen_files_to_the_chosen_account_and_takes_its_type(c):
    add_account(c, name="Visa", type="credit_card", last4="")
    add_account(c, name="Chequing", type="chequing", last4="1111")
    r = upload(c, "card.pdf", card_pdf())
    assert r["status"] == "pending_template"
    page = c.get(f"/statements/{r['id']}/template").text
    assert "data-autosubmit" in page and "onchange" not in page and "File it to this account" in page
    assert "money out" in page and "Credit card" in page or "credit card" in page
    pick = c.get(f"/statements/{r['id']}/template?go=1&account_id=1").text                     # choose the card by hand
    assert 'value="1" selected' in pick and "Read as a <strong>Credit card</strong>" in pick
    assert "Save this layout and file the statement to Visa" in pick
    saved = c.post(f"/statements/{r['id']}/template/save", data={"account_id": "1", "atype": "credit_card", "spec": spec_from(pick), "name": "T"})
    assert saved.status_code == 303
    db = __import__("finapp.db", fromlist=["DB"]).DB(__import__("finapp.config", fromlist=["get_config"]).get_config().db_path)
    st = db.one("SELECT * FROM statements WHERE id=?", (r["id"],))
    assert st["account_id"] == 1 and st["status"] in ("reconciled", "needs_review")
    assert db.val("SELECT COUNT(*) FROM transactions WHERE statement_id=? AND amount_cents>0", (r["id"],)) > 10
    assert db.val("SELECT last4 FROM accounts WHERE id=1") == "9012"                              # the digits in the file were learned


def test_account_is_preselected_from_the_digits_in_the_file(c):
    add_account(c, name="Other", type="chequing", last4="1111")
    add_account(c, name="Visa", type="credit_card", last4="9012")
    r = upload(c, "card.pdf", card_pdf())
    page = c.get(f"/statements/{r['id']}/template").text
    assert 'value="2" selected' in page and "Read as a <strong>Credit card</strong>" in page


def test_flip_toggle_reverses_the_preview_and_says_when_it_would_help(c):
    add_account(c, name="Visa", type="credit_card", last4="9012")
    r = upload(c, "card.pdf", card_pdf())
    good = c.get(f"/statements/{r['id']}/template?go=1&account_id=1").text
    assert "Flipping the direction makes the totals add up" not in good and '"flip"' not in spec_from(good)
    flipped = c.get(f"/statements/{r['id']}/template?go=1&account_id=1&flip=on").text
    assert '"flip": true' in spec_from(flipped) and "checked" in flipped.split('name="flip"')[1][:20]
    assert "Flipping the direction makes the totals add up" in flipped                        # the un-flipped reading balances, the flipped does not
    assert "Does not add up" in flipped and "Does not add up" not in good


def test_changing_the_account_on_a_filed_statement_resets_the_reading(c):
    add_account(c, name="Visa", type="credit_card", last4="9012")
    add_account(c, name="Chequing", type="chequing", last4="1111")
    r = upload(c, "card.pdf", card_pdf())
    page = c.get(f"/statements/{r['id']}/template?go=1&account_id=1").text
    c.post(f"/statements/{r['id']}/template/save", data={"account_id": "1", "atype": "credit_card", "spec": spec_from(page), "name": "T"})
    again = c.get(f"/statements/{r['id']}/template").text
    assert 'value="1" selected' in again


def test_refusals_on_forms_are_a_readable_page_with_a_way_back_but_the_api_stays_json(c):
    r = c.post("/treasury/cuts", data={"global": "abc"}, headers={"referer": "http://testserver/treasury"})
    assert r.status_code == 400 and "text/html" in r.headers["content-type"] and "Go back" in r.text and 'href="/treasury"' in r.text
    assert "{" not in r.text.split("<main")[1].split("</main>")[0]
    assert c.get("/no-such-page").status_code == 404 and "Nothing here" in c.get("/no-such-page").text
    j = c.post("/api/review/confirm", json={"month": "bad", "which": "ask"}, headers={"X-Finapp": "1"})
    assert j.status_code == 400 and j.headers["content-type"].startswith("application/json")


def test_model_buttons_wait_for_the_consent_box(c):
    for url in ("/treasury/suggest",):
        pass
    page = c.get("/settings/widget").text      # sanity: pages still render
    assert "Widget" in page
    js = c.get("/static/app.js").text
    assert "data-needs-consent" in js


def test_review_cards_show_a_custom_split_as_selected_and_offer_looks_right(c):
    filed_september(c)
    from finapp.db import DB
    from finapp.config import get_config
    db = DB(get_config().db_path)
    page = c.get("/review?month=2026-09").text
    li = db.val("SELECT id FROM line_items ORDER BY id LIMIT 1")
    assert "data-keep" in page and 'id="decided-fold"' not in page                                          # nothing decided yet
    assert c.post("/api/review/decide", json={"line_id": li, "bps": 6600}, headers={"X-Finapp": "1"}).status_code == 200
    page = c.get("/review?month=2026-09").text
    assert 'id="decided-fold"' in page and '<span id="decided-count">1</span>' in page
    done = page.split('id="decided-list"')[1]
    assert 'value="66"' in done and 'class="opt on" data-custom' in done and "data-reopen" in done and "data-keep" not in done.split("</article>")[0]


def test_reckoning_records_marks_and_removes_repayments(c):
    add_account(c)
    from finapp.db import DB
    from finapp.config import get_config
    db = DB(get_config().db_path)
    db.ex("INSERT INTO transactions(account_id,posted_date,amount_cents,description_raw,description_norm,kind,dedupe_key,created_at) VALUES(1,'2026-09-03',10000,'COSTCO','COSTCO','spend','a','x')")
    dep = db.ex("INSERT INTO transactions(account_id,posted_date,amount_cents,description_raw,description_norm,kind,dedupe_key,created_at) VALUES(1,'2026-09-20',-2000,'ETRANSFER IN','ETRANSFER IN','income','b','x')").lastrowid
    page = c.get("/reckoning?month=2026-09").text
    assert 'id="tally"' in page and "owes you $50" in page and "ETRANSFER IN" in page
    assert c.post("/reckoning/pay", data={"amount": "$30.00", "date": "2026-09-15", "direction": "from_partner", "note": "cash"}).status_code == 303
    assert "owes you $20" in c.get("/reckoning?month=2026-09").text
    assert c.post("/reckoning/pay", data={"amount": "0", "date": "2026-09-15"}).status_code == 400
    c.post("/reckoning/pay/mark", data={"txn_id": str(dep)})
    page = c.get("/reckoning?month=2026-09").text
    assert "All square" in page and "from Chequing" in page
    c.post("/reckoning/pay/remove", data={"key": f"t{dep}"})
    c.post("/reckoning/pay/remove", data={"key": "m1"})
    assert "owes you $50" in c.get("/reckoning?month=2026-09").text
    db.close()


def test_flip_after_a_suggested_layout_keeps_that_layout(c):
    add_account(c)
    pdf = synth.chequing_pdf(synth.gen(15, date(2026, 9, 1), 29, 5), 500000)
    sid = upload(c, "sep.pdf", pdf)["id"]
    import re, html
    page = c.get(f"/statements/{sid}/template?atype=chequing")
    spec = json.loads(html.unescape(re.search(r'<textarea name="spec"[^>]*>(.*?)</textarea>', page.text, re.S).group(1)))
    spec["identify"] = {"all": ["A heading only the suggestion knows"]}     # stands in for a layout that heuristics would not draft
    base = {"go": "1", "atype": "chequing", "was_atype": "chequing", "source": "llm", "spec": json.dumps(spec)}
    on = c.post(f"/statements/{sid}/template/update", data={**base, "flip": "on"}).text
    assert "A heading only the suggestion knows" in on and 'name="flip" checked' in on and '&#34;flip&#34;: true' in on
    off = c.post(f"/statements/{sid}/template/update", data=base).text
    assert "A heading only the suggestion knows" in off and 'name="flip" checked' not in off


def test_receipt_item_cards_in_the_real_review_page_carry_every_control(c):
    add_account(c)
    from finapp import ledger, receipts
    from finapp.db import DB
    from finapp.config import get_config
    from test_receipts import TEXT, TODAY
    db = DB(get_config().db_path)
    db.ex("INSERT INTO transactions(account_id,posted_date,amount_cents,description_raw,description_norm,kind,dedupe_key,created_at) VALUES(1,'2026-09-13',2710,'GROCERY MART #123','GROCERY MART','spend','g','x')")
    ledger.assign_merchants(db)
    rid = db.ex("INSERT INTO receipts(sha256,stored_name,kind,status,created_at) VALUES('s','x','image','parsed','2026-09-12T00:00:00Z')").lastrowid
    receipts.apply_parse(db, rid, receipts.parse_text(TEXT, TODAY), "tesseract")
    page = c.get("/review?month=2026-09").text
    cards = page.split('<article class="card rv')[1:]
    receipt_cards = [x for x in cards if 'data-rcpt="%d"' % rid in x]
    assert len(receipt_cards) == 5
    for card in receipt_cards:
        for must in ('data-bps="10000"', 'data-bps="5000"', 'data-bps="0"', "data-custom", "Set %", 'aria-label="Your share in percent"', 'class="cat"', 'class="rule"', "from a receipt"):
            assert must in card, must
        assert "data-keep" in card or "data-reopen" in card
    db.close()


def test_deposits_that_are_not_income_can_be_marked_and_stop_counting(c):
    from finapp import spending
    from finapp.db import DB
    from finapp.config import get_config
    add_account(c)
    db = DB(get_config().db_path)
    db.ex("INSERT INTO transactions(account_id,posted_date,amount_cents,description_raw,description_norm,kind,dedupe_key,created_at) VALUES(1,'2026-09-05',-222000,'PAYROLL','PAYROLL','income','p','x')")
    r = db.ex("INSERT INTO transactions(account_id,posted_date,amount_cents,description_raw,description_norm,kind,dedupe_key,created_at) VALUES(1,'2026-09-06',-247500,'E-TRANSFER FROM PARTNER','E-TRANSFER FROM PARTNER','income','e','x')").lastrowid
    db.ex("INSERT INTO transactions(account_id,posted_date,amount_cents,description_raw,description_norm,kind,dedupe_key,created_at) VALUES(1,'2026-09-07',5000,'PIZZA PALACE','PIZZA PALACE','spend','s','x')")
    db.ex("INSERT INTO statements(account_id,kind,period_start,period_end,file_sha256,original_name,stored_name,status,received_at) VALUES(1,'pdf','2026-09-01','2026-09-30','h','s.pdf','x','reconciled','x')")
    both = spending.real_income(db)
    page = c.get("/treasury").text
    assert "deposits counted as money in" in page and "E-TRANSFER FROM PARTNER" in page and "Partner paid me back" in page and "Not income" in page
    assert c.post("/treasury/deposit", data={"txn_id": str(r), "as": "bogus"}).status_code == 400
    assert c.post("/treasury/deposit", data={"txn_id": str(r), "as": "repayment"}).status_code == 303
    assert db.val("SELECT kind FROM transactions WHERE id=?", (r,)) == "settlement"
    assert spending.real_income(db) < both - 200000 and 200000 < spending.real_income(db) < 260000          # only the pay is left


def test_reading_everything_again_picks_up_improvements_and_keeps_decisions(c):
    pdf = synth.chequing_pdf(synth.gen(10, date(2026, 9, 1), 29, 5), 500000)
    add_account(c, last4="8901")
    sid = upload(c, "sep.pdf", pdf)["id"]
    import re, html
    page = c.get(f"/statements/{sid}/template?atype=chequing")
    spec = html.unescape(re.search(r'<textarea name="spec"[^>]*>(.*?)</textarea>', page.text, re.S).group(1))
    c.post(f"/statements/{sid}/template/save", data={"atype": "chequing", "spec": spec, "name": "T", "institution": "Bank"})
    r = c.post("/ledgers/reread-all")
    assert r.status_code == 303 and "reread=1" in r.headers["location"]
    assert "Read 1 statement again" in c.get(r.headers["location"]).text and "Read all statements again" in c.get("/ledgers").text


def test_a_deposit_set_aside_can_be_counted_again_and_survives_reading_the_statement_again(c):
    from finapp import spending
    from finapp.db import DB
    from finapp.config import get_config
    pdf = synth.chequing_pdf(synth.gen(12, date(2026, 9, 1), 29, 5), 500000)
    add_account(c, last4="8901")
    sid = upload(c, "sep.pdf", pdf)["id"]
    import re, html
    page = c.get(f"/statements/{sid}/template?atype=chequing")
    spec = html.unescape(re.search(r'<textarea name="spec"[^>]*>(.*?)</textarea>', page.text, re.S).group(1))
    c.post(f"/statements/{sid}/template/save", data={"atype": "chequing", "spec": spec, "name": "T", "institution": "Bank"})
    db = DB(get_config().db_path)
    dep = db.one("SELECT id FROM transactions WHERE kind='income' ORDER BY id LIMIT 1")["id"]
    assert c.post("/treasury/deposit", data={"txn_id": str(dep), "as": "not_income"}).status_code == 303
    assert db.val("SELECT kind FROM transactions WHERE id=?", (dep,)) == "setaside" and "Count as income again" in c.get("/treasury").text
    key = db.val("SELECT dedupe_key FROM transactions WHERE id=?", (dep,))
    c.post("/ledgers/reread-all")                                              # reading the statement again keeps what you said
    assert db.val("SELECT kind FROM transactions WHERE dedupe_key=?", (key,)) == "setaside"
    assert c.post("/treasury/deposit", data={"txn_id": str(db.val("SELECT id FROM transactions WHERE dedupe_key=?", (key,))), "as": "recount"}).status_code == 303
    assert db.val("SELECT kind FROM transactions WHERE dedupe_key=?", (key,)) == "income"
    db.close()


def test_one_tap_counts_only_deposits_that_look_like_pay(c):
    from finapp import spending
    from finapp.db import DB
    from finapp.config import get_config
    add_account(c)
    db = DB(get_config().db_path)
    for k, (d, cents, desc) in enumerate((("2026-09-05", 222018, "Payroll dep. Active Products Inc."), ("2026-09-06", 247500, "Deposit Free Interac E-Transfer"), ("2026-09-19", 222018, "Payroll dep. Active Products Inc."), ("2026-09-20", 3500, "INTERAC e-Transfer Received"))):
        db.ex("INSERT INTO transactions(account_id,posted_date,amount_cents,description_raw,description_norm,kind,dedupe_key,created_at) VALUES(1,?,?,?,?,'income',?,'x')", (d, -cents, desc, desc, f"k{k}"))
    db.ex("INSERT INTO transactions(account_id,posted_date,amount_cents,description_raw,description_norm,kind,dedupe_key,created_at) VALUES(1,'2026-09-07',5000,'PIZZA PALACE','PIZZA PALACE','spend','sp','x')")
    db.ex("INSERT INTO statements(account_id,kind,period_start,period_end,file_sha256,original_name,stored_name,status,received_at) VALUES(1,'pdf','2026-09-01','2026-09-30','h','s.pdf','x','reconciled','x')")
    before = spending.real_income(db)
    assert "looks like pay" in c.get("/treasury").text
    assert c.post("/treasury/deposit", data={"as": "only_pay"}).status_code == 303
    assert [r[0] for r in db.q("SELECT kind FROM transactions ORDER BY id")] == ["income", "setaside", "income", "setaside", "spend"]
    assert abs(spending.real_income(db) - 450549) < 200 < before - 200000 and "Set aside, not counted" in c.get("/treasury").text
    db.close()


def test_review_shows_what_the_partner_owes_detects_repayments_and_lets_you_adjust_or_waive(c):
    from finapp import ledger
    from finapp.db import DB
    from finapp.config import get_config
    add_account(c)
    db = DB(get_config().db_path)
    ins = lambda d, cents, desc, kind, key: db.ex("INSERT INTO transactions(account_id,posted_date,amount_cents,description_raw,description_norm,kind,dedupe_key,created_at) VALUES(1,?,?,?,?,?,?,'x')", (d, cents, desc, desc.upper(), kind, key)).lastrowid
    ins("2026-09-03", 100000, "COSTCO WHOLESALE", "spend", "c")
    et = ins("2026-09-16", -30000, "INTERAC e-Transfer Received J SMITH", "setaside", "e")
    pay = ins("2026-09-15", -222018, "Payroll dep. Active Products", "income", "p")
    ledger.assign_merchants(db)
    ledger.ensure_lines(db, "2026-09")
    db.ex("UPDATE line_items SET my_bps=5000, reviewed=1, confident=1")
    page = c.get("/review?month=2026-09").text
    assert 'id="iou"' in page and "owes you" in page and "$500" in page and "J SMITH" in page and "Her repayment" in page and "Not from her" in page
    assert "Payroll dep" not in page.split('id="iou"')[1].split("</div>")[0]
    assert c.post("/review/repay", data={"txn_id": str(et), "decision": "yes", "amount": "250.00", "back": "/review?month=2026-09#iou"}).status_code == 303
    assert db.val("SELECT kind FROM transactions WHERE id=?", (et,)) == "settlement" and ledger.partner_tally(db)["balance"] == 25000
    assert "counted of" in c.get("/reckoning?month=2026-09").text
    assert c.post("/review/repay", data={"txn_id": str(pay), "decision": "no"}).status_code == 303
    assert c.post("/reckoning/waive", data={"amount": "100.00"}).status_code == 303
    t = ledger.partner_tally(db)
    assert t["waived"] == 10000 and t["balance"] == 15000 and "Waived $100" in c.get("/reckoning?month=2026-09").text
    assert c.post("/reckoning/waive", data={}).status_code == 303 and ledger.partner_tally(db)["balance"] == 0          # blank waives the whole balance
    assert c.post("/reckoning/waive", data={}).status_code == 400                                                       # nothing left to waive
    db.close()


def test_reference_points_can_be_added_and_removed_and_the_real_month_shows_the_partners_share_as_money_in(c):
    from finapp.db import DB
    from finapp.config import get_config
    add_account(c)
    db = DB(get_config().db_path)
    db.ex("INSERT INTO transactions(account_id,posted_date,amount_cents,description_raw,description_norm,kind,dedupe_key,created_at) VALUES(1,'2026-09-04',60000,'COSTCO WHOLESALE','COSTCO WHOLESALE','spend','c','x')")
    db.ex("INSERT INTO statements(account_id,kind,period_start,period_end,file_sha256,original_name,stored_name,status,received_at) VALUES(1,'pdf','2026-09-01','2026-09-30','h','s.pdf','x','reconciled','x')")
    page = c.get("/treasury").text
    assert "Reference points" in page and "share</td>" in page and "Everything you paid" in page and "Money in" in page and "Left over" in page
    assert c.post("/treasury/anchor", data={"kind": "salary", "amount": "2220.18", "every": "2weeks", "start": "2026-08-01"}).status_code == 303
    assert c.post("/treasury/anchor", data={"kind": "rent", "amount": "2238.50", "share": "60", "match": "rent", "start": "2026-09-01"}).status_code == 303
    assert c.post("/treasury/anchor", data={"kind": "bogus", "amount": "5", "start": "2026-09-01"}).status_code == 400
    assert c.post("/treasury/anchor", data={"kind": "rent", "amount": "0", "start": "2026-09-01"}).status_code == 400
    page = c.get("/treasury").text
    assert "from your salary reference point" in page and "your share 60%" in page and "every two weeks" in page
    aid = db.val("SELECT id FROM anchors WHERE kind='rent'")
    assert c.post("/treasury/anchor/remove", data={"id": str(aid)}).status_code == 303 and db.val("SELECT COUNT(*) FROM anchors WHERE kind='rent'") == 0
    db.close()


def test_a_repayment_not_yet_in_any_statement_can_be_recorded_from_the_review_card(c):
    from finapp import ledger
    from finapp.db import DB
    from finapp.config import get_config
    add_account(c)
    db = DB(get_config().db_path)
    db.ex("INSERT INTO transactions(account_id,posted_date,amount_cents,description_raw,description_norm,kind,dedupe_key,created_at) VALUES(1,'2026-09-03',100000,'COSTCO WHOLESALE','COSTCO WHOLESALE','spend','c','x')")
    ledger.assign_merchants(db)
    ledger.ensure_lines(db, "2026-09")
    db.ex("UPDATE line_items SET my_bps=5000, reviewed=1, confident=1")
    assert "paid me ($)" in c.get("/review?month=2026-09").text
    r = c.post("/reckoning/pay", data={"amount": "250.00", "date": "2026-10-05", "direction": "from_partner", "note": "entered by hand", "back": "/review?month=2026-09#iou"})
    assert r.status_code == 303 and r.headers["location"] == "/review?month=2026-09#iou"
    assert ledger.partner_tally(db)["balance"] == 25000
    db.close()
