import json
from datetime import date
from types import SimpleNamespace as NS
import pytest
from fastapi.testclient import TestClient
from finapp import auth, clock, credits, ledger, llm, spending
from finapp.config import Config, get_config, set_config
from finapp.db import DB
from finapp.main import create_app
from test_ingest import acct
from test_ledger import tx
from test_web import _env, PASS


def seed(db, months=("2026-07", "2026-08", "2026-09")):
    a = acct(db, track_from="2026-07")
    n = 0
    for m in months:
        for i, (desc, amt) in enumerate([("PIZZA PALACE", 4500), ("PIZZA PALACE", 3800), ("PIZZA PALACE", 5200), ("PIZZA PALACE", 4100), ("GROCERY MART", 21000),
                                         ("GROCERY MART", 18000), ("HYDRO ONE", 9000), ("BOOKSHOP", 3000)]):
            n += 1
            tx(db, a, f"{m}-{3 + i:02d}", amt + (n % 3) * 300, desc, key=f"k{n}")
        tx(db, a, f"{m}-25", -520000, "PAYROLL DEPOSIT", kind="income", key=f"inc{m}")
    ledger.assign_merchants(db)
    for m in months:
        ledger.ensure_lines(db, m)
    db.ex("UPDATE line_items SET my_bps=10000, reviewed=1, confident=1")
    return a


class Fake:
    def __init__(self, text):
        self.messages, self.calls = self, []
        self.r = NS(content=[NS(type="text", text=text)], usage=NS(input_tokens=1500, output_tokens=900), stop_reason="end_turn")

    def create(self, **kw):
        self.calls.append(kw)
        return self.r



def _statement(db, account, start, end):
    db.ex("""INSERT INTO statements(account_id,kind,period_start,period_end,file_sha256,original_name,stored_name,status,received_at)
             VALUES(?,?,?,?,?,?,?,?,?)""", (account, "pdf", start, end, f"{account}{start}{end}", "s.pdf", "x", "reconciled", "2026-10-01T00:00:00Z"))


def _pouch(db, name, category, est, weight=3, credits_n=1, auto=1):
    cid = ledger.category_by_name(db, category)
    return db.ex("""INSERT INTO activities(name,icon,phrase,estimate_cents,weight,credits_per_month,cadence,sort,category_id,auto_credits,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (name, "coffee", name.lower(), est, weight, credits_n, "{}", 1, cid, auto, "2026-10-01T00:00:00Z")).lastrowid


def test_averages_are_per_month_rates_and_the_latest_month_is_compared_with_the_others(db):
    a = seed(db)
    _statement(db, a, "2026-07-01", "2026-09-30")
    tx(db, a, "2026-09-28", 30000, "PIZZA PALACE", key="spike")
    ledger.ensure_lines(db, "2026-09")
    db.ex("UPDATE line_items SET my_bps=10000")
    an = spending.analysis(db)
    assert an["usable_months"] == ["2026-07", "2026-08", "2026-09"] and an["latest"] == "2026-09"
    dining = next(c for c in an["categories"] if c["name"] == "Dining")
    assert 20000 < dining["avg"] < 32000 and dining["delta"] > 25000                    # about 190 a month, with a 300 purchase in September
    assert "Pizza Palace" in dining["callout"] and "one $300 purchase" in dining["callout"] and dining["callout"].startswith("Up ")
    quiet = next(c for c in an["categories"] if c["name"] == "Groceries")
    assert quiet["callout"] == "" and abs(quiet["delta"]) < 2500
    assert [c["avg"] for c in an["categories"]] == sorted((c["avg"] for c in an["categories"]), reverse=True)


def test_a_thinly_covered_month_counts_in_proportion_but_is_not_used_for_comparison(db):
    a = seed(db, months=("2026-08", "2026-09"))
    _statement(db, a, "2026-08-01", "2026-09-30")
    tx(db, a, "2026-10-02", 4500, "PIZZA PALACE", key="oct")
    _statement(db, a, "2026-10-01", "2026-10-08")
    an = spending.analysis(db)
    assert an["usable_months"] == ["2026-08", "2026-09"] and "2026-10" in an["months"]
    dining = next(c for c in an["categories"] if c["name"] == "Dining")
    assert 15000 < dining["avg"] < 20000                    # the eight October days do not drag the average down or blow it up


def test_cuts_global_and_per_category_with_savings_against_real_income(db):
    a = seed(db)
    _statement(db, a, "2026-07-01", "2026-09-30")
    base = spending.plan(db)
    assert base["saving"] == 0 and base["global"] == 0 and 400000 < base["income"] < 600000
    spending.set_reduce_pct(db, 10)
    dining_id = ledger.category_by_name(db, "Dining")
    spending.set_mod(db, dining_id, 0)                      # an explicit 0 beats the global cut
    groceries_id = ledger.category_by_name(db, "Groceries")
    spending.set_mod(db, groceries_id, -30)
    p = spending.plan(db)
    cat = {c["name"]: c for c in p["categories"]}
    assert cat["Dining"]["target"] == cat["Dining"]["avg"] and cat["Dining"]["custom"] is True
    assert cat["Groceries"]["target"] == round(cat["Groceries"]["avg"] * 0.7)
    assert cat["Bills"]["target"] == round(cat["Bills"]["avg"] * 0.9) and cat["Bills"]["custom"] is False
    assert p["saving"] == p["avg_total"] - p["target_total"] > 0
    assert p["saving_pct"] == round(100.0 * p["saving"] / p["income"], 1)
    spending.set_mod(db, groceries_id, None)
    assert {c["name"]: c for c in spending.plan(db)["categories"]}["Groceries"]["custom"] is False


def test_pouch_credits_follow_the_categorys_target_shared_by_weight_and_manual_pouches_are_left_alone(db, cfg):
    a = seed(db)
    _statement(db, a, "2026-07-01", "2026-09-30")
    p1 = _pouch(db, "Ordered in", "Dining", 4000, weight=3)
    p2 = _pouch(db, "Drinks out", "Dining", 6000, weight=3, credits_n=7)
    p3 = _pouch(db, "Manual one", "Dining", 5000, weight=3, credits_n=9, auto=0)
    dining = next(c for c in spending.plan(db)["categories"] if c["name"] == "Dining")
    share = dining["target"] * 3 / 9
    got = spending.derive(db)
    assert got[p1]["credits"] == int(share / 4000 + 0.5) and got[p2]["credits"] == int(share / 6000 + 0.5)
    changed = spending.sync_purse(db, cfg.tz, clock.parse_iso("2026-10-10T15:00:00Z"))
    assert {n for n, _, _ in changed} == {"Ordered in", "Drinks out"}
    assert db.val("SELECT credits_per_month FROM activities WHERE id=?", (p1,)) == got[p1]["credits"]
    assert db.val("SELECT credits_per_month FROM activities WHERE id=?", (p3,)) == 9
    assert spending.sync_purse(db, cfg.tz, clock.parse_iso("2026-10-10T15:00:00Z")) == []          # nothing more to change
    spending.set_reduce_pct(db, 50)
    assert spending.sync_purse(db, cfg.tz, clock.parse_iso("2026-10-10T15:00:00Z"))               # a cut shrinks the credits


def test_this_months_statement_purchases_use_up_the_matching_credits_and_a_tap_is_not_counted_twice(db, cfg):
    a = seed(db, months=("2026-09",))
    _statement(db, a, "2026-09-01", "2026-10-08")
    small = _pouch(db, "Ordered in", "Dining", 4500, credits_n=4)
    big = _pouch(db, "Drinks out", "Dining", 9000, credits_n=4)
    t1, t2, t3 = (tx(db, a, d, c, "PIZZA PALACE", key=k) for d, c, k in (("2026-10-02", 4500, "o1"), ("2026-10-05", 9000, "o2"), ("2026-10-07", 4600, "o3")))
    ledger.ensure_lines(db, "2026-10")
    db.ex("UPDATE line_items SET my_bps=10000")
    now = clock.parse_iso("2026-10-10T15:00:00Z")
    states = {s["activity"]["id"]: s for s in credits.all_states(db, "2026-10", cfg.tz, now)}
    assert states[small]["used"] == 2 and states[small]["from_statements"] == 2 and states[big]["used"] == 1
    db.ex("INSERT INTO activity_logs(activity_id,ts,source) VALUES(?,?, 'live')", (small, "2026-10-02T23:00:00Z"))      # the same coffee, tapped on the widget
    db.ex("INSERT INTO activity_logs(activity_id,ts,source) VALUES(?,?, 'live')", (small, "2026-10-09T23:00:00Z"))      # a new one the statement has not reached
    again = credits.activity_state(db, db.one("SELECT * FROM activities WHERE id=?", (small,)), "2026-10", cfg.tz, now)
    assert again["used"] == 3 and again["from_statements"] == 2
    assert credits.activity_state(db, db.one("SELECT * FROM activities WHERE id=?", (small,)), "2026-09", cfg.tz, now)["from_statements"] >= 0   # months reset on their own


def test_real_income_is_the_deposit_rate_without_one_off_extra_income(db):
    a = seed(db)
    _statement(db, a, "2026-07-01", "2026-09-30")
    assert 450000 < spending.real_income(db) < 550000
    db.ex("INSERT INTO month_adjustments(month,kind,amount_cents,label,treat,created_at) VALUES('2026-09','extra_income',150000,'bonus','exclude','x')")
    assert spending.real_income(db) < 500000



def test_pouches_with_obvious_names_get_their_category_on_their_own_and_odd_ones_are_left(db, cfg):
    a = seed(db)
    _statement(db, a, "2026-07-01", "2026-09-30")
    ids = {}
    for name in ("Ordered in", "Tickets", "Big grocery shop", "Mystery thing"):
        ids[name] = db.ex("""INSERT INTO activities(name,icon,phrase,estimate_cents,weight,credits_per_month,cadence,sort,created_at)
                             VALUES(?,?,?,?,?,?,?,?,?)""", (name, "coffee", "x", 4000, 3, 0, "{}", 1, "2026-10-01T00:00:00Z")).lastrowid
    spending.sync_purse(db, cfg.tz, clock.parse_iso("2026-10-10T15:00:00Z"))
    cat = lambda n: db.val("SELECT c.name FROM activities a LEFT JOIN categories c ON c.id=a.category_id WHERE a.id=?", (ids[n],))
    assert (cat("Ordered in"), cat("Tickets"), cat("Big grocery shop"), cat("Mystery thing")) == ("Dining", "Fun", "Groceries", None)
    assert db.val("SELECT credits_per_month FROM activities WHERE id=?", (ids["Ordered in"],)) > 0
    assert db.val("SELECT credits_per_month FROM activities WHERE id=?", (ids["Mystery thing"],)) == 0


def test_the_partners_share_is_money_in_and_the_full_amounts_are_what_you_paid_with_the_same_net_result(db):
    a = seed(db)
    _statement(db, a, "2026-07-01", "2026-09-30")
    db.ex("UPDATE line_items SET my_bps=5000")
    p = spending.plan(db)
    assert p["partner_share"] > 0 and p["paid"] > p["avg_total"]
    assert abs(p["paid"] - (p["avg_total"] + p["partner_share"])) <= 5                              # what you paid = your share + hers
    assert p["money_in"] == p["income"] + p["partner_share"]
    assert abs(p["left_over"] - (p["income"] - p["avg_total"])) <= 5                                  # showing it gross changes the picture, not the net


def test_a_salary_reference_point_is_the_pay_and_its_size_makes_a_deposit_count_as_pay(db):
    a = seed(db)
    _statement(db, a, "2026-07-01", "2026-09-30")
    spending.analysis(db)
    assert spending.pay_month(db) is None
    db.ex("INSERT INTO anchors(kind,amount_cents,every,my_pct,start_date,created_at) VALUES('salary',222018,'2weeks',100,'2026-07-01','x')")
    assert spending.pay_month(db, date(2026, 10, 9)) == round(222018 * 26 / 12) and spending.plan(db)["pay_from"] == "salary"
    assert spending.is_pay(db, "E-TRANSFER FROM EMPLOYER", 222500) and not spending.is_pay(db, "E-TRANSFER FROM FRIEND", 247500)


def test_a_regular_pay_is_suggested_and_a_changed_pay_asks_to_start_a_new_line(db):
    a = acct(db)
    for d in ("2026-09-01", "2026-09-15"):
        tx(db, a, d, -222018, "Payroll dep. Active Products", kind="income", key=f"pp{d}")
    s = spending.pay_suggestion(db)
    assert s == {"cents": 222018, "every": "2weeks", "from": "2026-09-01"}
    db.ex("INSERT INTO anchors(kind,amount_cents,every,my_pct,start_date,created_at) VALUES('salary',222018,'2weeks',100,'2026-08-01','x')")
    assert spending.pay_suggestion(db) is None and spending.pay_changed(db) is None
    for d in ("2026-09-29", "2026-10-13"):
        tx(db, a, d, -240000, "Payroll dep. Active Products", kind="income", key=f"pp{d}")
    ch = spending.pay_changed(db)
    assert ch and ch["cents"] == 240000 and ch["from"] == "2026-09-29" and ch["was"] == 222018


def test_rent_takes_your_share_from_the_reference_point_in_effect_on_its_date_and_leaves_your_own_decisions(db):
    a = seed(db, months=())
    t1 = tx(db, a, "2026-08-25", 220000, "CHEXY RENT 289", key="r1")
    t2 = tx(db, a, "2026-09-25", 225000, "CHEXY RENT 289", key="r2")
    t3 = tx(db, a, "2026-10-25", 225000, "CHEXY RENT 289", key="r3")
    for m in ("2026-08", "2026-09", "2026-10"):
        ledger.ensure_lines(db, m)
    db.ex("UPDATE line_items SET my_bps=10000, split_source='user', reviewed=1 WHERE txn_id=?", (t3,))              # you decided October yourself
    db.ex("INSERT INTO anchors(kind,amount_cents,every,my_pct,start_date,match,created_at) VALUES('rent',220000,'month',50,'2026-08-01','rent','x')")
    db.ex("INSERT INTO anchors(kind,amount_cents,every,my_pct,start_date,match,created_at) VALUES('rent',225000,'month',60,'2026-09-01','rent','x')")
    assert spending.apply_rent_split(db) == 2 and spending.apply_rent_split(db) == 0
    bps = lambda t: db.one("SELECT my_bps, split_source, confident FROM line_items WHERE txn_id=?", (t,))
    assert (bps(t1)["my_bps"], bps(t2)["my_bps"], bps(t3)["my_bps"]) == (5000, 6000, 10000) and bps(t2)["split_source"] == "anchor" and bps(t3)["split_source"] == "user"
    ledger.resuggest_open(db, "2026-09")
    assert bps(t2)["my_bps"] == 6000                                                                                    # another decision in the month does not rewrite it


def test_going_past_zero_is_allowed_shows_negative_and_is_recorded_per_month_for_tuning(db, cfg):
    seed(db)
    aid = _pouch(db, "Ordered in", "Dining", 4500, credits_n=2, auto=0)
    now = clock.parse_iso("2026-10-10T15:00:00Z")
    credits.ensure_periods(db, "2026-10", cfg.tz)
    for k in range(3):
        db.ex("INSERT INTO activity_logs(activity_id,ts,source) VALUES(?,?, 'live')", (aid, f"2026-10-0{k + 2}T15:00:00Z"))
    s = credits.activity_state(db, db.one("SELECT * FROM activities WHERE id=?", (aid,)), "2026-10", cfg.tz, now)
    assert s["used"] == 3 and s["allotted"] == 2 and s["overdrawn_now"] and s["allotted"] - s["used"] == -1
    h = credits.overdraw_history(db, db.one("SELECT * FROM activities WHERE id=?", (aid,)), "2026-10", cfg.tz, now)
    assert h[0] == {"month": "2026-10", "allotted": 2, "used": 3, "over": 1} and len(h) == 1                 # months without a stored period are not invented
    assert credits.shift_month("2026-01", -1) == "2025-12" and credits.shift_month("2026-10", -3) == "2026-07"


def _c(d, m, cents, acct=1):
    return {"date": d, "merchant": m, "cents": cents, "acct": acct}


def test_holds_tips_and_adjustments_merge_into_their_purchase_but_separate_rides_stay_separate():
    ev = spending.collapse([_c("2026-09-11", "Uber", 100), _c("2026-09-11", "Uber", 1462), _c("2026-09-12", "Uber", 1338),            # a hold then the fare, then the next day's ride
                            _c("2026-09-20", "Uber", 1800), _c("2026-09-21", "Uber", 400),                                              # a ride and the tip charged after
                            _c("2026-09-25", "Uber", 1500), _c("2026-09-25", "Uber", 1520),                                             # a pre-authorization and its final charge
                            _c("2026-09-28", "Uber", 1500), _c("2026-09-28", "Lyft", 1500)])                                            # different merchants are different rides
    assert [(e["date"], e["merchant"], e["cents"], e["n"]) for e in ev] == [("2026-09-11", "Uber", 1562, 2), ("2026-09-12", "Uber", 1338, 1), ("2026-09-20", "Uber", 2200, 2),
                                                                            ("2026-09-25", "Uber", 3020, 2), ("2026-09-28", "Lyft", 1500, 1), ("2026-09-28", "Uber", 1500, 1)]


def test_a_ride_count_is_purchases_not_dollars_so_a_bus_trip_and_a_hold_never_become_rides(db, cfg):
    a = seed(db, months=())
    pid = _pouch(db, "Rides", "Transport", 2000, credits_n=6, auto=0)
    for k, (d, desc, cents) in enumerate((("2026-09-11", "UBER CANADA/UBERTRIP", 100), ("2026-09-11", "UBER CANADA/UBERTRIP", 1462), ("2026-09-12", "UBER CANADA/UBERTRIP", 1338),
                                          ("2026-09-30", "UBER CANADA/UBERTRIP", 1129), ("2026-10-04", "UBER CANADA/UBERTRIP", 100), ("2026-10-04", "A4U TAXI", 4200),
                                          ("2026-10-05", "MEGABUS CANADA", 21352))):
        tx(db, a, d, cents, desc, key=f"r{k}")
    transport = ledger.category_by_name(db, "Transport")
    ledger.assign_merchants(db)
    for m in ("2026-09", "2026-10"):
        ledger.ensure_lines(db, m)
    db.ex("UPDATE line_items SET category_id=?, my_bps=10000", (transport,))
    act = db.one("SELECT * FROM activities WHERE id=?", (pid,))
    assert len(spending.stmt_uses(db, act, "2026-09", cfg.tz)) == 3          # three Uber rides; the $1 hold belongs to the first
    assert len(spending.stmt_uses(db, act, "2026-10", cfg.tz)) == 1          # the taxi; the lone $1 hold and the $213.52 bus trip are not rides
    rates, present = spending.event_rates(db)
    assert transport in present and 0 < rates[pid] < 6                        # a few rides a month, not dollars divided by $20
    now = clock.parse_iso("2026-10-10T15:00:00Z")
    assert credits.activity_state(db, act, "2026-10", cfg.tz, now)["used"] == 1
