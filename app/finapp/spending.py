"""What the owner really spends, and what follows from it.

Statements arrive about once a month, so nothing here tracks a limit live. It works out the average monthly spend per category from whatever the
statements cover (a partly covered month counts in proportion), how the latest month differs, and, with the owner's optional cuts applied, how many
credits each pouch should have. Credits are then used up automatically by the purchases the statements show."""
import calendar
import math
import re
import statistics
from datetime import date, datetime, time as dtime, timedelta, timezone

from . import clock, ledger

UTC = timezone.utc
DPM = 30.44                    # days in an average month
MIN_DAYS = 14                  # an account covered for less than this is treated as if it were, so a few days cannot swing the average
MIN_MONTH_COV = 0.4            # a month needs this much statement coverage to be compared with others
SIGNIFICANT = (2500, 0.25)     # a change counts as notable above $25 and 25% of the usual
MATCH_DAYS = 1.5


# ---------------------------------------------------------------- what the statements cover
def spans(db):
    """{account_id: [(start, end), ...]}: the dates each account's filed statements cover, overlaps merged."""
    raw = {}
    for r in db.q("""SELECT account_id, period_start, period_end FROM statements WHERE account_id IS NOT NULL AND period_start IS NOT NULL
                     AND period_end IS NOT NULL AND status IN ('reconciled','parsed','needs_review')"""):
        raw.setdefault(r["account_id"], []).append((date.fromisoformat(r["period_start"]), date.fromisoformat(r["period_end"])))
    out = {}
    for a, lst in raw.items():
        merged = []
        for st, en in sorted(lst):
            if merged and st <= merged[-1][1] + timedelta(days=1):
                merged[-1] = (merged[-1][0], max(merged[-1][1], en))
            else:
                merged.append((st, en))
        out[a] = merged
    return out


def month_coverage(sp, month):
    """{account_id: fraction of the month its statements cover}."""
    y, m = int(month[:4]), int(month[5:])
    first, last = date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
    n = (last - first).days + 1
    return {a: min(1.0, sum(max(0, (min(en, last) - max(st, first)).days + 1) for st, en in lst) / n) for a, lst in sp.items()}


def coverage_notes(db, today):
    names = {r["id"]: r["name"] for r in db.q("SELECT id, name FROM accounts WHERE active=1")}
    return [{"account": names[a], "from": lst[0][0].isoformat(), "to": lst[-1][1].isoformat(), "behind_days": (today - lst[-1][1]).days}
            for a, lst in sorted(spans(db).items()) if a in names]


# ---------------------------------------------------------------- the analysis
def dollars(c):
    return f"${abs(c) / 100:,.0f}"


def _rows(db):
    months = [r[0] for r in db.q("SELECT DISTINCT substr(posted_date,1,7) FROM transactions WHERE kind IN ('spend','refund')")]
    for m in months:
        ledger.ensure_lines(db, m)
    return db.q("""SELECT t.account_id acct, ac.owner owner, substr(t.posted_date,1,7) m, t.posted_date d, COALESCE(c.id,0) cid, COALESCE(c.name,'Other') cat, COALESCE(c.kind,'variable') kind,
                          COALESCE(mc.name, t.description_norm) merchant, li.amount_cents amt, li.my_bps bps
                   FROM line_items li JOIN transactions t ON t.id=li.txn_id JOIN accounts ac ON ac.id=t.account_id LEFT JOIN categories c ON c.id=li.category_id
                   LEFT JOIN merchants mc ON mc.id=t.merchant_id WHERE t.kind IN ('spend','refund')""")


def analysis(db):
    """Per category: average monthly spend (owner's share), the latest month, its difference from the usual and what drove it."""
    sp = spans(db)
    days = {a: max(MIN_DAYS, sum((en - st).days + 1 for st, en in lst)) for a, lst in sp.items()}
    rows = _rows(db)
    seen = {}
    for r in rows:
        lo, hi = seen.get(r["acct"], (r["d"], r["d"]))
        seen[r["acct"]] = (min(lo, r["d"]), max(hi, r["d"]))
    for acct, (lo, hi) in seen.items():          # an account with no statement dates is read over the span of its own transactions
        if acct not in days:
            days[acct] = max(MIN_DAYS, (date.fromisoformat(hi) - date.fromisoformat(lo)).days + 1)
    months = sorted({r["m"] for r in rows})
    covs = {m: month_coverage(sp, m) for m in months}
    mean_cov = {m: (sum(covs[m].values()) / len(covs[m]) if covs[m] else 1.0) for m in months}
    usable = [m for m in months if mean_cov[m] >= MIN_MONTH_COV]
    rate, series, merch, big, kinds, ids = {}, {}, {}, {}, {}, {}
    paid_rate = partner_rate = 0.0
    for r in rows:
        mine = ledger.my_part(r["amt"], r["bps"])
        paid = r["amt"] * {"me": 1.0, "partner": 0.0, "joint": 0.5}.get(r["owner"], 1.0)        # what came out of the owner's own pocket
        paid_rate += paid / days[r["acct"]] * DPM
        partner_rate += (paid - mine) / days[r["acct"]] * DPM                                       # the part of it that belongs to the partner
        cat = r["cat"]
        kinds[cat], ids[cat] = r["kind"], r["cid"]
        rate[cat] = rate.get(cat, 0.0) + mine / days[r["acct"]] * DPM
        f = covs[r["m"]].get(r["acct"], 1.0)
        if f >= 0.25:
            series.setdefault(cat, {})
            series[cat][r["m"]] = series[cat].get(r["m"], 0.0) + mine / f
            mm = merch.setdefault((cat, r["m"]), {})
            mm[r["merchant"]] = mm.get(r["merchant"], 0.0) + mine / f
            if mine > 0:
                big.setdefault((cat, r["m"]), []).append((mine, r["merchant"], r["d"]))
    months_eq = max(1.0, max(days.values(), default=DPM) / DPM)
    for a in db.q("SELECT month, category, amount_cents FROM month_adjustments WHERE removed_at IS NULL AND kind='extra_expense' AND category IS NOT NULL"):
        cat = a["category"]                    # a one-off is not a typical month
        if cat in rate:
            rate[cat] = max(0.0, rate[cat] - a["amount_cents"] / months_eq)
        if cat in series and a["month"] in series[cat]:
            series[cat][a["month"]] = max(0.0, series[cat][a["month"]] - a["amount_cents"])
    latest = usable[-1] if usable else None
    others = usable[:-1]
    cats = []
    for cat, avg in rate.items():
        if avg < 50:
            continue
        e = {"id": ids[cat], "name": cat, "kind": kinds[cat], "avg": int(round(avg)), "last": None, "delta": None, "callout": ""}
        if latest and others and cat in series:
            vals = series[cat]
            usual = sum(vals.get(m, 0.0) for m in others) / len(others)
            e["last"] = int(round(vals.get(latest, 0.0)))
            e["delta"] = int(round(vals.get(latest, 0.0) - usual))
            e["usual"] = int(round(usual))
            if abs(e["delta"]) >= max(SIGNIFICANT[0], SIGNIFICANT[1] * usual):
                e["callout"] = _callout(cat, latest, others, e["delta"], merch, big)
        cats.append(e)
    cats.sort(key=lambda c: -c["avg"])
    return {"categories": cats, "months": months, "usable_months": usable, "latest": latest, "compared_with": others, "mean_cov": mean_cov,
            "income": real_income(db, sp, days, months_eq), "paid": int(round(paid_rate)), "partner_share": int(round(partner_rate))}


def _callout(cat, latest, others, delta, merch, big):
    this = merch.get((cat, latest), {})
    scores = []
    for name in set(this) | {k for o in others for k in merch.get((cat, o), {})}:
        usual = sum(merch.get((cat, o), {}).get(name, 0.0) for o in others) / len(others)
        scores.append((this.get(name, 0.0) - usual, name, this.get(name, 0.0), usual))
    if delta > 0:
        top = max(scores, key=lambda s: s[0], default=None)
        one = max(big.get((cat, latest), []), default=None)
        if one and one[0] >= 0.6 * delta:
            return f"Up {dollars(delta)}, mostly one {dollars(one[0])} purchase at {one[1].title()} on {one[2]}."
        if top and top[0] > 0:
            tail = "new this month" if top[3] < 100 else f"usually {dollars(top[3])}"
            return f"Up {dollars(delta)}. Most of it is {top[1].title()}: {dollars(top[2])} this month, {tail}."
        return f"Up {dollars(delta)} on the usual, spread over many purchases."
    top = min(scores, key=lambda s: s[0], default=None)
    if top and top[0] < 0:
        return f"Down {dollars(delta)}. {top[1].title()} was {dollars(top[2])} this month against usually {dollars(top[3])}."
    return f"Down {dollars(delta)} on the usual, spread over many purchases."


def real_income(db, sp=None, days=None, months_eq=None):
    """Monthly income from the deposits the statements actually show (one-off extra income the owner has mentioned left out)."""
    sp = sp if sp is not None else spans(db)
    days = days or {a: max(MIN_DAYS, sum((en - st).days + 1 for st, en in lst)) for a, lst in sp.items()}
    months_eq = months_eq or max(1.0, max(days.values(), default=DPM) / DPM)
    tot = 0.0
    for r in db.q("""SELECT t.account_id a, -SUM(t.amount_cents) s FROM transactions t JOIN accounts ac ON ac.id=t.account_id
                     WHERE t.kind='income' AND ac.owner IN ('me','joint') AND ac.type NOT IN ('credit_card','line_of_credit') GROUP BY t.account_id"""):
        tot += max(0, r["s"]) / days.get(r["a"], DPM) * DPM
    extra = db.val("SELECT COALESCE(SUM(amount_cents),0) FROM month_adjustments WHERE removed_at IS NULL AND kind='extra_income'", (), 0)
    return int(round(max(0.0, tot - extra / months_eq)))


# ---------------------------------------------------------------- which deposits are really income
PAY_WORDS = re.compile(r"payroll|salary|direct dep|\bpay\b|wages?|\bdep\.? ", re.I)


def looks_like_pay(desc):
    return bool(PAY_WORDS.search((desc or "")[:60]))


def is_pay(db, desc, cents):
    """Pay by its wording, or by being the size of a pay in the salary reference points."""
    if looks_like_pay(desc):
        return True
    return any(abs(cents - a["amount_cents"]) <= 0.03 * a["amount_cents"] for a in anchors(db, "salary"))


def set_aside_non_pay(db, statement_id=None, ignore_overrides=False):
    """Deposits that do not look like pay stop counting as income (the owner can count any of them again). Returns how many were set aside."""
    q = """SELECT t.id, t.description_raw, t.account_id, t.dedupe_key, t.amount_cents FROM transactions t JOIN accounts a ON a.id=t.account_id
           WHERE t.kind='income' AND a.owner IN ('me','joint') AND a.type NOT IN ('credit_card','line_of_credit')"""
    args = ()
    if statement_id is not None:
        q += " AND t.statement_id=?"
        args = (statement_id,)
    n = 0
    for r in db.q(q, args):
        if is_pay(db, r["description_raw"], -r["amount_cents"]):
            continue
        if not ignore_overrides and db.one("SELECT 1 FROM deposit_overrides WHERE account_id=? AND dedupe_key=?", (r["account_id"], r["dedupe_key"])):
            continue
        db.ex("UPDATE transactions SET kind='setaside' WHERE id=?", (r["id"],))
        db.ex("DELETE FROM deposit_overrides WHERE account_id=? AND dedupe_key=?", (r["account_id"], r["dedupe_key"]))
        n += 1
    return n


def apply_pay_default_once(db):
    """The first time this runs, deposits already on file get the same treatment new ones do."""
    if db.get_setting("pay_default_v1"):
        return 0
    db.set_setting("pay_default_v1", "1")
    return set_aside_non_pay(db)


# ---------------------------------------------------------------- reference points: pay and rent
PER_MONTH = {"week": 52 / 12, "2weeks": 26 / 12, "semimonthly": 2.0, "month": 1.0}
EVERY_WORDS = {"week": "every week", "2weeks": "every two weeks", "semimonthly": "twice a month", "month": "every month"}


def anchors(db, kind):
    return db.q("SELECT * FROM anchors WHERE kind=? ORDER BY start_date, id", (kind,))


def anchor_on(db, kind, day):
    """The reference point in effect on a date: the latest one that had started."""
    return db.one("SELECT * FROM anchors WHERE kind=? AND start_date<=? ORDER BY start_date DESC, id DESC LIMIT 1", (kind, day))


def monthly(a):
    return int(round(a["amount_cents"] * PER_MONTH.get(a["every"], 1.0)))


def pay_month(db, today=None):
    """Monthly take-home from the salary reference point in effect today, or None when none is set."""
    a = anchor_on(db, "salary", (today or date.today()).isoformat())
    return monthly(a) if a else None


def apply_rent_split(db):
    """Rent payments you have not decided yourself take your share from the rent reference point in effect on their date."""
    rents = anchors(db, "rent")
    if not rents:
        return 0
    n = 0
    for a in rents:
        key = (a["match"] or "rent").strip().lower()
        for t in db.q("SELECT id, posted_date FROM transactions WHERE kind='spend' AND lower(description_norm) LIKE ?", (f"%{key}%",)):
            cur = anchor_on(db, "rent", t["posted_date"])
            if not cur or cur["id"] != a["id"]:
                continue
            ledger.ensure_lines(db, t["posted_date"][:7])
            bps = int(round(cur["my_pct"] * 100))
            why = f"Rent: your share is {cur['my_pct']:g}% from your reference point."
            n += db.ex("""UPDATE line_items SET my_bps=?, split_source='anchor', confident=1, why=? WHERE txn_id=? AND reviewed=0 AND split_source<>'user'
                          AND (my_bps<>? OR split_source<>'anchor')""", (bps, why, t["id"], bps)).rowcount
    return n


def pay_suggestion(db):
    """If there is no salary reference point yet but the pay deposits are regular, what it looks like: (amount cents, every, first date)."""
    if anchors(db, "salary"):
        return None
    pays = [r for r in db.q("""SELECT t.posted_date d, -t.amount_cents c, t.description_raw FROM transactions t JOIN accounts a ON a.id=t.account_id
                               WHERE t.kind='income' AND a.owner IN ('me','joint') AND a.type NOT IN ('credit_card','line_of_credit') ORDER BY t.posted_date""")
            if looks_like_pay(r["description_raw"])]
    if len(pays) < 2 or abs(pays[-1]["c"] - pays[-2]["c"]) > 0.03 * pays[-1]["c"]:
        return None
    gap = (date.fromisoformat(pays[-1]["d"]) - date.fromisoformat(pays[-2]["d"])).days
    every = "week" if gap <= 9 else "2weeks" if gap <= 16 else "month"
    return {"cents": pays[-1]["c"], "every": every, "from": pays[-2]["d"]}


def pay_changed(db):
    """If the last two pays both differ from the salary reference point, and agree with each other: the new amount and when it started."""
    a = None
    pays = []
    for r in db.q("""SELECT t.posted_date d, -t.amount_cents c, t.description_raw FROM transactions t JOIN accounts ac ON ac.id=t.account_id
                     WHERE t.kind='income' AND ac.owner IN ('me','joint') AND ac.type NOT IN ('credit_card','line_of_credit') ORDER BY t.posted_date"""):
        if looks_like_pay(r["description_raw"]):
            a = anchor_on(db, "salary", r["d"])
            if a and r["d"] >= a["start_date"]:
                pays.append((r["d"], r["c"], a))
    if len(pays) < 2:
        return None
    (d1, c1, a1), (d2, c2, a2) = pays[-2], pays[-1]
    if a1["id"] == a2["id"] and abs(c1 - a1["amount_cents"]) > 0.03 * a1["amount_cents"] and abs(c2 - c1) <= 0.03 * c2:
        return {"cents": c2, "from": d1, "was": a1["amount_cents"], "every": a1["every"]}
    return None


# ---------------------------------------------------------------- the owner's optional cuts
def reduce_pct(db):
    try:
        return max(0.0, min(90.0, float(db.get_setting("reduce_pct", "0"))))
    except ValueError:
        return 0.0


def set_reduce_pct(db, pct):
    db.set_setting("reduce_pct", max(0.0, min(90.0, float(pct))))


def mods(db):
    return {r["category_id"]: r["pct"] for r in db.q("SELECT category_id, pct FROM category_mods")}


def set_mod(db, category_id, pct):
    if pct is None:
        db.ex("DELETE FROM category_mods WHERE category_id=?", (category_id,))
    else:
        db.ex("INSERT INTO category_mods(category_id,pct) VALUES(?,?) ON CONFLICT(category_id) DO UPDATE SET pct=excluded.pct", (category_id, max(-90.0, min(300.0, float(pct)))))


def plan(db):
    """The analysis with the cuts applied: a category's own change wins over the global cut. Savings are against real income."""
    an = analysis(db)
    an["pay_from"] = "deposits"
    fixed_pay = pay_month(db)
    if fixed_pay:
        an["income"], an["pay_from"] = fixed_pay, "salary"
    g, m = reduce_pct(db), mods(db)
    avg_total = target_total = 0
    for c in an["categories"]:
        custom = c["id"] in m
        change = m[c["id"]] if custom else -g
        c["custom"], c["change"] = custom, change
        c["target"] = int(round(max(0.0, c["avg"] * (1 + change / 100.0))))
        avg_total += c["avg"]
        target_total += c["target"]
    saving = avg_total - target_total
    an["money_in"] = an["income"] + an["partner_share"]
    an.update({"global": g, "avg_total": avg_total, "target_total": target_total, "saving": saving,
               "saving_pct": round(100.0 * saving / an["income"], 1) if an["income"] > 0 else None,
               "left_over": an["money_in"] - an["paid"]})
    return an


# ---------------------------------------------------------------- the Purse follows from it
def derive(db, p=None):
    """{pouch id: {credits, allowance, category}} for pouches tied to a category with spending behind it. Credits follow how OFTEN the pouch is really
    used (purchases per month, the owner's cut applied), so one big or accessory charge cannot inflate them. A category with no recognisable purchases
    falls back to its dollar aim divided by what one credit costs."""
    p = p or plan(db)
    by_cat = {c["id"]: c for c in p["categories"]}
    rates, present = event_rates(db)
    acts = db.q("SELECT * FROM activities WHERE archived=0 AND category_id IS NOT NULL ORDER BY sort, id")
    weight = {}
    for a in acts:
        weight[a["category_id"]] = weight.get(a["category_id"], 0) + max(1, a["weight"])
    out = {}
    for a in acts:
        c = by_cat.get(a["category_id"])
        if not c:
            continue
        allowance = c["target"] * max(1, a["weight"]) / weight[a["category_id"]]
        est = a["estimate_cents"]
        if a["category_id"] in present:
            credits = max(0, min(60, int(round(rates.get(a["id"], 0.0) * (1 + c["change"] / 100.0)))))
        else:
            credits = max(0, min(60, int(allowance / est + 0.5))) if est > 0 else None
        out[a["id"]] = {"credits": credits, "allowance": int(round(allowance)), "category": c["name"], "target": c["target"], "avg": c["avg"]}
    return out


POUCH_WORDS = [(r"grocer|supermarket|food shop", "Groceries"), (r"order(ed)? in|takeout|take-out|delivery|restaurant|dining|drinks?|\\bbar\\b|\\bpub\\b|brunch|lunch|dinner", "Dining"),
               (r"coffee|treat|snack|bakery|sweets?", "Snacks"), (r"ticket|concert|movie|cinema|show|event|game|fun|outing", "Fun"),
               (r"shopping|clothes|clothing|toy|gift|amazon|online", "Shopping"), (r"\\bpets?\\b|\\bvet\\b", "Pets"), (r"transit|taxi|uber|gas|fuel|bus|train|flight", "Transport")]


def guess_pouch_category(db, name):
    """A category id for an obviously named pouch (Ordered in, Tickets, Grocery shop...), else None."""
    low = (name or "").lower()
    for rx, cat in POUCH_WORDS:
        if re.search(rx, low):
            return ledger.category_by_name(db, cat) or None
    return None


def sync_purse(db, tz, now=None):
    """Set the credits of every pouch that follows its category, and rebuild this month's schedule where that changed anything.
    A pouch with no category yet gets the obvious one from its name, so the Purse works without setup (it can be changed on the Plan tab)."""
    from . import credits
    now = now or clock.now()
    month = credits.month_of(now, tz)
    apply_rent_split(db)
    for a in db.q("SELECT id, name FROM activities WHERE archived=0 AND category_id IS NULL AND auto_credits=1"):
        g = guess_pouch_category(db, a["name"])
        if g:
            db.ex("UPDATE activities SET category_id=? WHERE id=?", (g, a["id"]))
    changed = []
    d = derive(db)
    for a in db.q("SELECT * FROM activities WHERE archived=0 AND category_id IS NOT NULL AND auto_credits=1"):
        got = d.get(a["id"])
        if not got or got["credits"] is None or got["credits"] == a["credits_per_month"]:
            continue
        db.ex("UPDATE activities SET credits_per_month=? WHERE id=?", (got["credits"], a["id"]))
        credits.build_period(db, month, db.one("SELECT * FROM activities WHERE id=?", (a["id"],)), tz, replace=True)
        changed.append((a["name"], a["credits_per_month"], got["credits"]))
    return changed


# ---------------------------------------------------------------- purchases, as events (one ride is one use, however many charges it left)
ACCESSORY = 0.4          # a charge this small against its neighbour is a tip, a hold or an adjustment, not another purchase
NEAR_DAYS = 2            # ...if it is the same merchant within this many days
TINY_FLOOR = 200         # cents: below this (or a quarter of the cheapest pouch) a charge is a hold or a fee, not a use
TINY_FRAC = 0.25
HUGE_RATIO = 4           # a charge more than this many times the dearest pouch is a different kind of spend (a bus trip among taxi rides)


def collapse(charges):
    """charges: [{date 'YYYY-MM-DD', merchant, cents, acct}] for one category -> purchase events, oldest first.
    Same-merchant charges a day or two apart merge when one is a small accessory of the other (tip, authorization hold, fare adjustment) or they are
    near-identical (a hold and its final charge). Each event: {date, merchant, cents (all its charges), main (its largest), acct, n}."""
    events = []
    for c in sorted(charges, key=lambda c: (c["merchant"], c["date"], -c["cents"])):
        d = date.fromisoformat(c["date"])
        hit = None
        for e in reversed(events):
            if e["merchant"] != c["merchant"]:
                break
            gap = abs((d - date.fromisoformat(e["last"])).days)
            lo, hi = sorted((c["cents"], e["main"]))
            if gap <= NEAR_DAYS and (lo <= ACCESSORY * hi or (gap <= 1 and hi - lo <= 50)):
                hit = e
                break
        if hit:
            hit["cents"] += c["cents"]
            hit["main"] = max(hit["main"], c["cents"])
            hit["last"] = max(hit["last"], c["date"])
            hit["n"] += 1
        else:
            events.append({"date": c["date"], "last": c["date"], "merchant": c["merchant"], "cents": c["cents"], "main": c["cents"], "acct": c["acct"], "n": 1})
    return sorted(events, key=lambda e: (e["date"], e["merchant"]))


def category_events(db, category_id):
    """Qualifying purchase events in a category, each already given to the pouch whose cost is nearest. -> ([(event, pouch id or None)], pouches)"""
    mates = db.q("SELECT id, estimate_cents FROM activities WHERE archived=0 AND category_id=? AND estimate_cents>0", (category_id,))
    for m in {r["month"] for r in db.q("SELECT DISTINCT substr(t.posted_date,1,7) month FROM line_items li JOIN transactions t ON t.id=li.txn_id WHERE li.category_id=?", (category_id,))}:
        ledger.ensure_lines(db, m)
    charges = []
    for r in db.q("""SELECT t.posted_date d, t.account_id acct, COALESCE(mc.name, t.description_norm) merchant, li.amount_cents amt, li.my_bps bps
                     FROM line_items li JOIN transactions t ON t.id=li.txn_id LEFT JOIN merchants mc ON mc.id=t.merchant_id
                     WHERE li.category_id=? AND t.kind='spend' AND li.amount_cents>0""", (category_id,)):
        x = ledger.my_part(r["amt"], r["bps"])
        if x > 0:
            charges.append({"date": r["d"], "merchant": r["merchant"], "cents": x, "acct": r["acct"]})
    if not mates:
        return [], mates
    lo_est, hi_est = min(m["estimate_cents"] for m in mates), max(m["estimate_cents"] for m in mates)
    out = []
    for e in collapse(charges):
        if e["cents"] < max(TINY_FLOOR, TINY_FRAC * lo_est) or e["cents"] > HUGE_RATIO * hi_est:
            continue
        best = min(mates, key=lambda p: abs(math.log(e["cents"] / p["estimate_cents"])))
        out.append((e, best["id"]))
    return out, mates


def event_rates(db):
    """{pouch id: purchases per month} from the events each pouch earned, each counted over the days its account's statements cover."""
    sp = spans(db)
    days = {a: max(MIN_DAYS, sum((en - st).days + 1 for st, en in lst)) for a, lst in sp.items()}
    rates, present = {}, set()
    for c in db.q("SELECT DISTINCT category_id FROM activities WHERE archived=0 AND category_id IS NOT NULL"):
        evs, mates = category_events(db, c["category_id"])
        if evs:
            present.add(c["category_id"])
        for e, pid in evs:
            d = days.get(e["acct"])
            if d is None:
                d = max(MIN_DAYS, 30)
            rates[pid] = rates.get(pid, 0.0) + DPM / d
    return rates, present


# ---------------------------------------------------------------- credits used up by what the statements show
def stmt_uses(db, act, month, tz):
    """UTC times at which this pouch was used in a month, one per purchase the statements show (a tip, hold or adjustment belongs to its purchase;
    a charge far smaller or larger than anything the pouch stands for is not a use). Credits are counted in purchases, never in dollars."""
    cid = act["category_id"]
    if not cid or act["estimate_cents"] <= 0:
        return []
    evs, _ = category_events(db, cid)
    out = []
    for e, pid in evs:
        if pid == act["id"] and e["date"][:7] == month:
            out.append(datetime.combine(date.fromisoformat(e["date"]), dtime(12, 0), tzinfo=tz).astimezone(UTC))
    return sorted(out)
