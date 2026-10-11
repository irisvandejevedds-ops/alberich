import json
import time
import pytest
from fastapi.testclient import TestClient
from finapp import auth, clock, ledger, llm, sorting
from finapp.config import Config, set_config
from finapp.db import DB
from finapp.main import create_app
from test_spending import Fake
from test_ingest import acct
from test_ledger import tx
from test_web import _env, PASS

KNOWN = {"PRESTO FARE": "Transport", "ESSO CIRCLE K": "Transport", "TIM HORTONS": "Snacks", "STARBUCKS TORONTO": "Snacks", "KRISPY KREME": "Snacks", "MACS CONV": "Snacks",
         "STOP GO": "Snacks", "GATEWAY NEWSTANDS": "Snacks", "AMAZON CA": "Shopping", "AMZN MKTP": "Shopping", "DOLLARAMA TORONTO": "Household", "LCBO RAO": "Fun",
         "BOOM KARAOKE": "Fun", "JUNO VET": "Pets", "PET VALU": "Pets", "DENTISTS ON": "Health", "MEGABUS CANADA": "Transport", "RAMEN ISSHIN": "Dining",
         "BANH MI": "Dining", "SHAWARMA DADDY": "Dining", "UBER EATS": "Dining", "USD CLOUDFLARE": "Bills", "USD ANTHROPIC": "Bills", "WITHDRAWAL FEE": "Bills",
         "CARLOAD FRUIT": "Groceries", "LOBLAWS 1234": "Groceries", "FRESHCO": "Groceries", "NO FRILLS": "Groceries", "METRO": "Groceries"}
UNKNOWN = ["DANIEL ALEXANDRA", "SQ BRYDEN", "TST BAMPOT", "FLAVOUR BEAST", "TO LIVE", "THE MONK"]


def test_the_built_in_sorter_knows_everyday_merchants_and_admits_when_it_does_not():
    wrong = {k: (ledger.guess_category(k), v) for k, v in KNOWN.items() if ledger.guess_category(k) != v}
    assert wrong == {}
    assert [u for u in UNKNOWN if ledger.guess_category(u)] == []


def test_snacks_and_pets_exist_as_categories(db):
    names = {r["name"] for r in db.q("SELECT name FROM categories")}
    assert {"Snacks", "Pets", "Dining", "Groceries"} <= names


def test_recategorise_files_other_lines_but_never_touches_what_the_owner_set_or_other_merchants(db):
    a = acct(db)
    other = ledger.category_by_name(db, "Other")
    dining = ledger.category_by_name(db, "Dining")
    t1 = tx(db, a, "2026-09-02", 450, "TIM HORTONS 123", key="a")
    t2 = tx(db, a, "2026-09-03", 900, "TIM HORTONS 456", key="b")
    t3 = tx(db, a, "2026-09-04", 700, "DANIEL ALEXANDRA", key="c")
    ledger.assign_merchants(db)
    for t in (t1, t2, t3):
        mid = db.val("SELECT merchant_id FROM transactions WHERE id=?", (t,))
        db.ex("UPDATE merchants SET default_category_id=? WHERE id=?", (other, mid))
        db.ex("INSERT INTO line_items(txn_id,description,amount_cents,category_id,created_at) VALUES(?,?,?,?,'2026-09-05T00:00:00Z')", (t, "x", 1, other))
    db.ex("UPDATE line_items SET category_id=? WHERE txn_id=?", (dining, t2))                # the owner chose Dining for this one
    db.ex("INSERT INTO line_items(txn_id,description,amount_cents,category_id,created_at) VALUES(?,?,?,NULL,'2026-09-05T00:00:00Z')", (t3, "uncategorised", 1))
    ledger.recategorize_other(db)
    cat = lambda t: [r[0] for r in db.q("SELECT category_id FROM line_items WHERE txn_id=? ORDER BY id", (t,))]
    snacks = ledger.category_by_name(db, "Snacks")
    assert cat(t1) == [snacks] and cat(t2) == [dining] and cat(t3) == [other, None]


def seed_other(db):
    a = acct(db)
    for i, name in enumerate(["DANIEL ALEXANDRA", "SQ BRYDEN", "TST BAMPOT", "FLAVOUR BEAST"]):
        for k in range(3 - i % 2):
            t = tx(db, a, f"2026-09-{2 + i + k:02d}", 1000 + 100 * i, name, key=f"{name}{k}")
    ledger.assign_merchants(db)
    ledger.ensure_lines(db, "2026-09")
    return a


def test_pending_lists_only_names_and_counts_never_amounts_dates_or_accounts(db):
    seed_other(db)
    p = sorting.payload(db)
    s = json.dumps(p)
    assert len(p["merchants"]) >= 3 and "Snacks" in p["allowed_categories"]
    for leak in ("2026-09", "Chequing", "1000", "1100", "amount", "date"):
        assert leak not in s
    assert all(set(m) == {"id", "name", "purchases"} for m in p["merchants"])


def test_claude_sorting_is_applied_only_where_ticked_and_leaves_owner_choices_alone(db, cfg):
    seed_other(db)
    cfg.anthropic_key = "sk-test"
    data = sorting.payload(db)
    ids = {m["name"]: m["id"] for m in data["merchants"]}
    reply = {"sorted": [{"id": ids["DANIEL ALEXANDRA"], "category": "Dining", "why": "a person's name, likely a meal"},
                        {"id": ids["SQ BRYDEN"], "category": "Snacks", "why": "coffee"}, {"id": ids["TST BAMPOT"], "category": "Nonsense"}]}
    rows = sorting.run(db, cfg, data, client=Fake(json.dumps(reply)))
    byname = {r["name"]: r["category"] for r in rows}
    assert byname["SQ BRYDEN"] == "Snacks" and byname["TST BAMPOT"] == "Other" and byname["FLAVOUR BEAST"] == "Other"          # invented or missing -> Other
    dining = ledger.category_by_name(db, "Dining")
    mine = db.val("SELECT li.id FROM line_items li JOIN transactions t ON t.id=li.txn_id JOIN merchants m ON m.id=t.merchant_id WHERE m.pattern LIKE 'DANIEL%' LIMIT 1")
    db.ex("UPDATE line_items SET category_id=? WHERE id=?", (ledger.category_by_name(db, "Health"), mine))                       # the owner already chose Health
    moved = sorting.apply(db, {ids["DANIEL ALEXANDRA"]: "Dining", ids["SQ BRYDEN"]: "Snacks"})
    assert moved >= 2
    assert db.val("SELECT category_id FROM line_items WHERE id=?", (mine,)) == ledger.category_by_name(db, "Health")
    assert db.val("SELECT default_category_id FROM merchants WHERE id=?", (ids["DANIEL ALEXANDRA"],)) == dining
    with pytest.raises(llm.LLMUnavailable):
        sorting.run(db, cfg, data, client=Fake("no json here"))


@pytest.fixture
def web(tmp_path, monkeypatch):
    auth.reset_fails()
    c = Config(_env(tmp_path, FINAPP_FILE_KEY="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=", ANTHROPIC_API_KEY="sk-test"))
    set_config(c)
    cl = TestClient(create_app(), follow_redirects=False)
    assert cl.post("/setup", data={"passphrase": PASS, "confirm": PASS}).status_code == 303
    db = DB(c.db_path)
    seed_other(db)
    clock.set_now(clock.parse_iso("2026-10-09T15:00:00Z"))
    ids = {m["name"]: m["id"] for m in sorting.payload(db)["merchants"]}
    monkeypatch.setattr(llm, "_client", lambda cfg: Fake(json.dumps({"sorted": [{"id": ids["SQ BRYDEN"], "category": "Snacks", "why": "coffee"}]})))
    yield cl, c, ids
    clock.set_now(None)


def test_the_sorting_flow_shows_the_list_needs_consent_runs_behind_the_scenes_and_applies(web):
    cl, c, ids = web
    page = cl.get("/review/sort").text
    assert "What will be sent" in page and "SQ BRYDEN" in page and "data-needs-consent" in page
    assert cl.post("/review/sort/run", data={}).status_code == 400
    r = cl.post("/review/sort/run", data={"consent": "on"})
    assert r.status_code == 303
    sid = r.headers["location"].split("id=")[1]
    for _ in range(100):
        if cl.get(f"/api/sort/{sid}").json()["status"] != "running":
            break
        time.sleep(0.05)
    assert cl.get(f"/api/sort/{sid}").json()["status"] == "done"
    assert "The Warlock's sorting" in cl.get(r.headers["location"]).text
    ap = cl.post("/review/sort/apply", data={"id": sid, "take": [str(ids["SQ BRYDEN"])], f"cat_{ids['SQ BRYDEN']}": "Snacks"})
    assert ap.status_code == 303 and "moved=" in ap.headers["location"]
    db = DB(c.db_path)
    assert db.val("SELECT name FROM categories WHERE id=(SELECT default_category_id FROM merchants WHERE id=?)", (ids["SQ BRYDEN"],)) == "Snacks"
    assert cl.post("/review/sort/apply", data={"id": 999}).status_code == 404


def test_the_reckoning_points_to_the_sorter_when_too_much_is_under_other(web):
    cl, c, ids = web
    page = cl.get("/reckoning?month=2026-09").text
    assert "filed under Other" in page and "/review/sort" in page


def test_choosing_a_category_for_one_purchase_in_review_moves_the_merchants_other_purchases_too(db):
    a = acct(db)
    other, groceries = ledger.category_by_name(db, "Other"), ledger.category_by_name(db, "Groceries")
    ts = [tx(db, a, d, 2000, "DANIEL ALEXANDRA S N", key=d) for d in ("2026-08-30", "2026-09-10", "2026-09-20")]
    ledger.assign_merchants(db)
    mid = db.val("SELECT merchant_id FROM transactions WHERE id=?", (ts[0],))
    db.ex("UPDATE merchants SET default_category_id=? WHERE id=?", (other, mid))
    lines = [db.ex("INSERT INTO line_items(txn_id,description,amount_cents,category_id,created_at) VALUES(?,?,?,?,'2026-09-05T00:00:00Z')", (t, "x", 2000, other)).lastrowid for t in ts]
    ledger.decide(db, lines[1], category_id=groceries)
    assert [db.val("SELECT category_id FROM line_items WHERE id=?", (l,)) for l in lines] == [groceries] * 3
    assert all(p["id"] != mid for p in sorting.pending(db))


def test_several_merchants_are_sorted_by_hand_with_one_move_into_existing_or_brand_new_categories(web):
    cl, c, ids = web
    db = DB(c.db_path)
    a, b = ids["DANIEL ALEXANDRA"], ids["SQ BRYDEN"]
    page = cl.get("/review/sort").text
    byhand = page.split('id="byhand"')[1].split("</form>")[0]
    assert byhand.count('type="submit"') == 1 and f'name="cat_{a}"' in byhand and f'name="new_{a}"' in byhand and f'name="cat_{b}"' in byhand
    assert cl.post("/review/sort/set", data={f"cat_{a}": "", f"new_{a}": ""}).status_code == 400            # nothing chosen
    groceries = db.val("SELECT id FROM categories WHERE name='Groceries'")
    r = cl.post("/review/sort/set", data={f"new_{a}": "  Corner   shop ", f"cat_{b}": str(groceries)})
    assert r.status_code == 303 and "merchants=2" in r.headers["location"]
    cid = db.val("SELECT id FROM categories WHERE name='Corner shop'")
    assert cid and db.val("SELECT default_category_id FROM merchants WHERE id=?", (a,)) == cid
    assert db.val("SELECT default_category_id FROM merchants WHERE id=?", (b,)) == groceries
    left = {p["id"] for p in sorting.pending(db)}
    assert a not in left and b not in left
    again = cl.post("/review/sort/set", data={f"new_{ids.get('X', a)}": "corner SHOP"})          # the same name typed again reuses the category
    assert db.val("SELECT COUNT(*) FROM categories WHERE lower(name)='corner shop'") == 1


def test_changing_a_category_on_any_review_card_saves_at_once_and_the_merchants_undecided_purchases_follow(web):
    cl, c, ids = web
    db = DB(c.db_path)
    a = acct(db, "Chq2", last4="1234")
    other, transport, snacks = (ledger.category_by_name(db, n) for n in ("Other", "Transport", "Snacks"))
    ts = [tx(db, a, d, 900, "ESSO CIRCLE K TORONTO ON", key=f"es{d}") for d in ("2026-09-07", "2026-09-08", "2026-09-09")]
    ledger.assign_merchants(db)
    ledger.ensure_lines(db, "2026-09")
    mid = db.val("SELECT merchant_id FROM transactions WHERE id=?", (ts[0],))
    db.ex("UPDATE line_items SET category_id=? WHERE txn_id IN (%s)" % ",".join(map(str, ts)), (transport,))
    first = db.val("SELECT id FROM line_items WHERE txn_id=?", (ts[0],))
    second = db.val("SELECT id FROM line_items WHERE txn_id=?", (ts[1],))
    ledger.decide(db, second, bps=10000)                                                  # one purchase already decided
    r = cl.post("/api/review/category", json={"line_id": first, "category_id": snacks}, headers={"X-Finapp": "1"})
    assert r.status_code == 200 and r.json()["category"] == "Snacks" and r.json()["moved"] == 1
    cat = lambda t: db.val("SELECT category_id FROM line_items WHERE txn_id=?", (t,))
    assert (cat(ts[0]), cat(ts[1]), cat(ts[2])) == (snacks, transport, snacks)            # the decided one is left alone
    assert db.val("SELECT default_category_id FROM merchants WHERE id=?", (mid,)) == snacks
    assert db.val("SELECT reviewed FROM line_items WHERE id=?", (first,)) == 0           # choosing a category does not decide the split
    r = cl.post("/api/review/category", json={"line_id": second, "category_id": snacks}, headers={"X-Finapp": "1"})   # a decided card can change too
    assert r.status_code == 200 and cat(ts[1]) == snacks and db.val("SELECT reviewed FROM line_items WHERE id=?", (second,)) == 1
    assert cl.post("/api/review/category", json={"line_id": first, "category_id": 9999}, headers={"X-Finapp": "1"}).status_code == 400
