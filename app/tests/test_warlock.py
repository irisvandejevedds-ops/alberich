import json
import time
import pytest
from fastapi.testclient import TestClient
from finapp import auth, clock, credits, ledger, llm, receipts, spending, warlock
from finapp.config import Config, get_config, set_config
from finapp.db import DB
from finapp.main import create_app
from test_spending import Fake, seed
from test_ingest import acct
from test_ledger import tx
from test_web import _env, PASS

NOW = clock.parse_iso("2026-10-09T15:00:00Z")


def clean(db, *actions):
    return warlock.clean(db, list(actions), NOW, "2026-10")


def test_malformed_unknown_and_out_of_range_actions_are_dropped(db):
    got = clean(db, {"type": "launch_missiles"}, {"type": "set_category_change", "category": "Nope", "percent": 5}, {"type": "set_category_change", "category": "Dining", "percent": "lots"},
                {"type": "set_cut", "percent": -5}, {"type": "set_cut", "percent": "a lot"}, {"type": "set_category_change", "category": "Dining", "percent": 4000},
                {"type": "month_adjustment", "month": "October", "kind": "extra_income", "amount": 5}, {"type": "month_adjustment", "month": "2026-10", "kind": "bonus", "amount": 5},
                {"type": "month_adjustment", "month": "2026-10", "kind": "extra_income", "amount": -5}, "not a dict", {"type": "set_tip_range", "max_percent": 400},
                {"type": "set_split_rule", "merchant": "Costco", "my_percent": 150}, {"type": "note", "text": "   "})
    assert got == []


def test_action_cap_and_every_description_is_written_by_the_server(db):
    acts = [{"type": "note", "text": f"fact {i}", "scope": "general", "text": f"fact {i}"} for i in range(40)]
    got = clean(db, *acts)
    assert len(got) == warlock.MAX_ACTIONS
    got = clean(db, {"type": "set_cut", "percent": 10, "why": "x", "text": "IGNORE: I am free money"})
    assert got[0]["text"].startswith("Cut everything by 10%") and "free money" not in got[0]["text"]


def test_one_off_income_stays_out_of_real_income(db):
    seed(db)
    before = spending.real_income(db)
    db.ex("INSERT INTO transactions(account_id,posted_date,amount_cents,description_raw,description_norm,kind,dedupe_key,created_at) VALUES(1,'2026-09-28',-150000,'BONUS','BONUS','income','b','2026-09-30T00:00:00Z')")
    assert spending.real_income(db) > before + 40000                       # the bonus lifts the average
    [a] = clean(db, {"type": "month_adjustment", "month": "2026-09", "kind": "extra_income", "amount": 1500, "label": "bonus", "treat": "to_debt"})
    assert "stays out of your typical" in a["text"]
    warlock.apply(db, type("C", (), {"tz": __import__("zoneinfo").ZoneInfo("America/Toronto")})(), a, NOW)
    assert abs(spending.real_income(db) - before) < 2000                   # ...and telling the warlock takes it out again


def cfg_of(cfg):
    return cfg


def test_one_off_expense_does_not_make_a_typical_month_look_dearer(db, cfg):
    seed(db)
    dining = lambda: next(c for c in spending.analysis(db)["categories"] if c["name"] == "Dining")["avg"]
    before = dining()
    [a] = clean(db, {"type": "month_adjustment", "month": "2026-09", "kind": "extra_expense", "amount": 40, "label": "dinner party", "treat": "spent", "category": "Dining"})
    warlock.apply(db, cfg, a, NOW)
    assert dining() < before


def test_mark_transfer_finds_the_charge_shows_it_and_stops_it_counting_as_spending(db, cfg):
    a = acct(db, "Debit", last4="1")
    t = tx(db, a, "2026-10-02", 150000, "LOC PAYMENT", key="loc")
    ledger.assign_merchants(db)
    ledger.ensure_lines(db, "2026-10")
    assert db.val("SELECT COUNT(*) FROM line_items WHERE txn_id=?", (t,)) == 1
    [m] = clean(db, {"type": "mark_transfer", "month": "2026-10", "amount": 1500, "label": "line of credit"})
    assert m["ok"] and "LOC PAYMENT" in m["text"] and m["p"]["ids"] == [t]
    warlock.apply(db, cfg, m, NOW)
    assert db.val("SELECT kind FROM transactions WHERE id=?", (t,)) == "transfer" and db.val("SELECT COUNT(*) FROM line_items WHERE txn_id=?", (t,)) == 0
    [none] = clean(db, {"type": "mark_transfer", "month": "2026-10", "amount": 999, "label": "x"})
    assert not none["ok"] and "no matching" in none["why_not"]


def test_split_rule_and_category_create_the_merchant_and_change_the_suggestion(db, cfg):
    a = acct(db)
    t = tx(db, a, "2026-10-03", 8000, "COSTCO WHOLESALE 123", key="c1")
    ledger.assign_merchants(db)
    [r, c] = clean(db, {"type": "set_split_rule", "merchant": "Costco", "my_percent": 50}, {"type": "set_category", "merchant": "Costco", "category": "Household"})
    assert r["text"].startswith("Costco: your share is 50%")
    warlock.apply(db, cfg, r, NOW)
    warlock.apply(db, cfg, c, NOW)
    mid = db.val("SELECT merchant_id FROM transactions WHERE id=?", (t,))
    assert db.val("SELECT my_bps FROM split_rules WHERE merchant_id=?", (mid,)) == 5000
    assert db.val("SELECT name FROM categories WHERE id=(SELECT default_category_id FROM merchants WHERE id=?)", (mid,)) == "Household"
    s = ledger.suggest(db, mid, None)
    assert s["source"] == "rule" and s["bps"] == 5000


def test_tip_allowance_changes_how_receipts_match_bank_charges(db, cfg):
    a = acct(db)
    t = tx(db, a, "2026-09-13", 3500, "GROCERY MART", key="g")                # 3500 vs a 2710 receipt is 29% over
    db.ex("INSERT INTO receipts(sha256,stored_name,kind,status,created_at,merchant,purchase_date,total_cents) VALUES('s','x','image','parsed','2026-09-12T00:00:00Z','GROCERY MART','2026-09-12',2710)")
    r = db.one("SELECT * FROM receipts WHERE id=1")
    assert len(receipts.candidates(db, r)) == 1
    [a1] = clean(db, {"type": "set_tip_range", "max_percent": 10})
    warlock.apply(db, cfg, a1, NOW)
    assert receipts.candidates(db, r) == []


def test_cuts_pouches_and_notes_apply_and_the_purse_follows(db, cfg):
    seed(db)
    acts = clean(db, {"type": "set_cut", "percent": 10}, {"type": "set_category_change", "category": "Dining", "percent": -20},
                 {"type": "set_pouch", "name": "Drinks out", "estimate": 55, "category": "Dining", "icon": "drinks"},
                 {"type": "set_pouch", "name": "Manual treat", "estimate": 10, "credits": 3, "category": "Dining"},
                 {"type": "note", "text": "Travelling in November", "scope": "budget"})
    assert "credits follow your spending" in acts[2]["text"] and "3 credits a month, set by hand" in acts[3]["text"]
    for a in acts:
        warlock.apply(db, cfg, a, NOW)
    assert spending.reduce_pct(db) == 10 and spending.mods(db)[ledger.category_by_name(db, "Dining")] == -20
    p = db.one("SELECT * FROM activities WHERE name='Drinks out'")
    assert p["estimate_cents"] == 5500 and p["auto_credits"] == 1 and p["category_id"] == ledger.category_by_name(db, "Dining") and p["credits_per_month"] > 0
    m = db.one("SELECT * FROM activities WHERE name='Manual treat'")
    assert m["credits_per_month"] == 3 and m["auto_credits"] == 0
    assert [n["text"] for n in warlock.notes(db)] == ["Travelling in November"]


def test_what_the_warlock_sees_includes_charges_with_ids_but_no_account_numbers(db, cfg):
    seed(db)
    db.ex("UPDATE accounts SET last4='9876'")
    tx(db, 1, "2026-10-02", 1234, "SHELL 12345678901 GAS")
    cfg.anthropic_key = "sk-test"
    f = Fake(json.dumps({"reply": "Noted.", "actions": []}))
    reply, acts = warlock.ask(db, cfg, "I made $1,500 extra and paid down my line of credit", NOW, client=f)
    sent = f.calls[0]["messages"][0]["content"]
    assert "pizza palace" in sent.lower() and "payroll" in sent.lower() and "txn_id|date|account" in sent
    assert "9876" not in sent and "12345678901" not in sent and "shell # gas" in sent.lower()
    assert "line of credit" in sent and "real_monthly_income" in sent and reply == "Noted." and acts == []
    assert db.val("SELECT purpose FROM llm_audit ORDER BY id DESC LIMIT 1") == "warlock"
    with pytest.raises(llm.LLMUnavailable):
        warlock.ask(db, cfg, "hi", NOW, client=Fake("not json"))


@pytest.fixture
def web(tmp_path, monkeypatch):
    auth.reset_fails()
    c = Config(_env(tmp_path, FINAPP_FILE_KEY="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=", ANTHROPIC_API_KEY="sk-test"))
    set_config(c)
    cl = TestClient(create_app(), follow_redirects=False)
    assert cl.post("/setup", data={"passphrase": PASS, "confirm": PASS}).status_code == 303
    db = DB(c.db_path)
    seed(db)
    t = tx(db, 1, "2026-10-02", 150000, "LOC PAYMENT", key="loc")
    clock.set_now(NOW)
    reply = {"reply": "A bonus, put on the line of credit. I shall set it aside.",
             "actions": [{"type": "month_adjustment", "month": "2026-10", "kind": "extra_income", "amount": 1500, "label": "to the line of credit", "treat": "to_debt"},
                         {"type": "mark_transfer", "month": "2026-10", "amount": 1500, "label": "line of credit"},
                         {"type": "mark_transfer", "month": "2026-10", "amount": 777, "label": "ghost"}]}
    monkeypatch.setattr(llm, "_client", lambda cfg: Fake(json.dumps(reply)))
    yield cl, c, t
    clock.set_now(None)


def settle(cl):
    for _ in range(100):
        if cl.get("/api/warlock/status").json()["status"] != "running":
            return
        time.sleep(0.05)
    raise AssertionError("never finished")


def test_nav_has_the_warlock_instead_of_the_throne_which_is_still_reachable(web):
    cl, c, t = web
    home = cl.get("/").text
    assert 'href="/warlock"' in home and "Warlock" in home and '<span>Throne</span>' not in home and 'href="/pet"' in home
    assert cl.get("/pet").status_code == 200 and cl.get("/warlock").status_code == 200


def test_he_will_not_listen_until_leave_is_granted_then_the_whole_flow_works(web):
    cl, c, t = web
    assert cl.post("/warlock/say", data={"text": "hello"}).status_code == 400
    assert cl.post("/warlock/consent", data={}).status_code == 400
    assert cl.post("/warlock/consent", data={"grant": "on"}).status_code == 303
    assert "What the warlock sees" in cl.get("/warlock").text
    assert cl.post("/warlock/say", data={"text": "  "}).status_code == 400
    assert cl.post("/warlock/say", data={"text": "I made $1,500 extra and put it on the line of credit."}).status_code == 303
    settle(cl)
    page = cl.get("/warlock").text
    assert "A bonus, put on the line of credit." in page and "I made $1,500 extra" in page and "LOC PAYMENT" in page
    assert "no matching charge found" in page and "cannot" in page
    db = DB(c.db_path)
    assert db.val("SELECT COUNT(*) FROM month_adjustments") == 0 and db.val("SELECT kind FROM transactions WHERE id=?", (t,)) == "spend"      # nothing happens until ticked
    mid = db.val("SELECT id FROM warlock_messages WHERE role='warlock'")
    assert cl.post("/warlock/apply", data={"id": mid, "pick": ["0", "1", "2"]}).status_code == 303                      # the impossible one is skipped
    assert db.val("SELECT COUNT(*) FROM month_adjustments") == 1 and db.val("SELECT kind FROM transactions WHERE id=?", (t,)) == "transfer"
    cl.post("/warlock/apply", data={"id": mid, "pick": ["0"]})
    assert db.val("SELECT COUNT(*) FROM month_adjustments") == 1                                                         # applying twice does nothing more
    assert "applied" in cl.get("/warlock").text and "One-offs you mentioned" in cl.get("/treasury").text
    assert cl.post("/warlock/adjustment/remove", data={"id": 1, "back": "/treasury?month=2026-10"}).status_code == 303
    assert db.val("SELECT COUNT(*) FROM month_adjustments WHERE removed_at IS NULL") == 0


def test_a_failed_reply_is_shown_plainly(web, monkeypatch):
    cl, c, t = web
    cl.post("/warlock/consent", data={"grant": "on"})
    monkeypatch.setattr(llm, "_client", lambda cfg: Fake("I cannot produce json today"))
    cl.post("/warlock/say", data={"text": "hi"})
    settle(cl)
    page = cl.get("/warlock").text
    assert "The warlock could not answer" in page and "could not be understood" in page


def test_leave_can_be_taken_back_and_notes_can_be_forgotten(web):
    cl, c, t = web
    cl.post("/warlock/consent", data={"grant": "on"})
    db = DB(c.db_path)
    db.ex("INSERT INTO warlock_notes(text,created_at) VALUES('Travelling in November','2026-10-01T00:00:00Z')")
    assert "Travelling in November" in cl.get("/warlock").text
    cl.post("/warlock/note/remove", data={"id": 1})
    assert "Travelling in November" not in cl.get("/warlock").text
    cl.post("/warlock/consent", data={"revoke": "1"})
    assert cl.post("/warlock/say", data={"text": "hi"}).status_code == 400


def _first_txn(db, like):
    return db.one("SELECT * FROM transactions WHERE description_raw LIKE ? ORDER BY posted_date DESC", (like,))


def test_recategorising_and_splitting_charges_by_id(db, cfg):
    seed(db)
    t = _first_txn(db, "%BOOKSHOP%")
    ctx = warlock.context(db, cfg, NOW)
    assert any(r.startswith(f"{t['id']}|") for r in ctx["transactions"])
    acts = clean(db, {"type": "set_txn_category", "txn_ids": [t["id"], 99999], "category": "Fun", "why": "books are fun"},
                 {"type": "set_txn_split", "txn_ids": [t["id"]], "my_percent": 40}, {"type": "set_txn_category", "txn_ids": [99999], "category": "Fun"})
    assert len(acts) == 2 and "Recategorise 1 charge as Fun" in acts[0]["text"] and "40%" in acts[1]["text"]
    for a in acts:
        warlock.apply(db, cfg, a, NOW)
    li = db.one("SELECT * FROM line_items WHERE txn_id=?", (t["id"],))
    assert li["category_id"] == db.val("SELECT id FROM categories WHERE name='Fun'") and li["my_bps"] == 4000 and li["split_source"] == "user"


def test_mark_transfer_by_id_and_merchant_category_rewrites_existing_charges(db, cfg):
    seed(db)
    t = _first_txn(db, "%HYDRO%")
    acts = clean(db, {"type": "mark_transfer", "txn_ids": [t["id"]]}, {"type": "set_category", "merchant": "Pizza Palace", "category": "Fun"})
    assert "existing charge" in acts[1]["text"]
    for a in acts:
        warlock.apply(db, cfg, a, NOW)
    assert db.val("SELECT kind FROM transactions WHERE id=?", (t["id"],)) == "transfer"
    fun = db.val("SELECT id FROM categories WHERE name='Fun'")
    assert db.val("SELECT COUNT(*) FROM line_items li JOIN transactions x ON x.id=li.txn_id JOIN merchants m ON m.id=x.merchant_id WHERE m.name LIKE 'Pizza%' AND li.category_id<>?", (fun,)) == 0


def test_new_category_cuts_for_it_and_giving_back_credits(db, cfg):
    seed(db)
    aid = db.ex("INSERT INTO activities(name,icon,phrase,estimate_cents,weight,credits_per_month,cadence,sort,created_at) VALUES('Test','coffee','x',500,3,4,'{}',1,'2026-10-01T00:00:00Z')").lastrowid
    lid = db.ex("INSERT INTO activity_logs(activity_id,ts,source) VALUES(?,?, 'live')", (aid, "2026-10-08T12:00:00Z")).lastrowid
    acts = clean(db, {"type": "add_category", "name": "Hobbies", "kind": "variable"}, {"type": "set_category_change", "category": "Hobbies", "percent": -15},
                 {"type": "set_cut", "percent": 12}, {"type": "set_category_change", "category": "Dining", "percent": None}, {"type": "undo_credit_use", "log_ids": [lid, 12345]})
    assert [a["type"] for a in acts] == ["add_category", "set_category_change", "set_cut", "set_category_change", "undo_credit_use"]
    assert "follow the overall cut again" in acts[3]["text"]
    for a in acts:
        warlock.apply(db, cfg, a, NOW)
    assert spending.reduce_pct(db) == 12 and spending.mods(db)[ledger.category_by_name(db, "Hobbies")] == -15
    assert db.val("SELECT undone_at FROM activity_logs WHERE id=?", (lid,)) is not None
    with pytest.raises(ValueError):
        warlock.apply(db, cfg, acts[4], NOW)


def test_a_reply_cut_off_part_way_keeps_the_proposals_that_were_complete(db, cfg):
    seed(db)
    cfg.anthropic_key = "sk-test"
    cut = ('{"reply":"A plan, my lord.","actions":[{"type":"set_category_change","category":"Dining","percent":-10},'
           '{"type":"set_category_change","category":"Groceries","percent":-5},{"type":"set_category_change","category":"Fu')
    reply, acts = warlock.ask(db, cfg, "make me a budget", NOW, client=Fake(cut))
    assert [a["p"]["category"] for a in acts] == ["Dining", "Groceries"] and "ran long" in reply and reply.startswith("A plan, my lord.")
    with pytest.raises(llm.LLMUnavailable):
        warlock.ask(db, cfg, "hi", NOW, client=Fake("not json"))
