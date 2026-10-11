"""Merchants, transfers, line items, and the explainable split learner."""
import re
from datetime import date, datetime, timedelta

from . import clock

HALF_LIFE_DAYS = 90.0
CONFIDENT_SHARE = 0.75
CONFIDENT_MIN_N = 3
TRANSFER_WORDS = re.compile(r"transfer|payment|pmt|pay |e-?transfer|autopay|bill pay|to savings|from savings", re.I)
CATEGORY_RULES = [
    ("Dining", r"uber\s*eats|ubereats|doordash|skip\s*the\s*dishes|skipthedishes|foodora|just\s*eat|fantuan|hungrypanda"),
    ("Transport", r"presto|\bttc\b|go transit|metrolinx|\besso\b|\bshell\b|petro[- ]?canada|pioneer|sunoco|ultramar|husky|\bgas\b|fuel|parking|green p\b|impark|\b407\b|megabus|greyhound|\bvia rail|flixbus|\buber\b|lyft|taxi|zipcar|communauto|bike share|bixi|\bpass\b.*(transit|bus)"),
    ("Pets", r"pet valu|petsmart|pet smart|petco|\bvet\b|veterinar|animal hospital|animal clinic|juno vet|groomer"),
    ("Health", r"shoppers drug|pharmasave|rexall|pharmacy|pharma|clinic|dental|dentist|orthodont|optical|optometr|physio|chiro|massage|lifelabs|dynacare|hospital|medical|therapy|counsell"),
    ("Snacks", r"tim hortons|timmies|starbucks|second cup|dunkin|krispy|coffee|cafe|caf\u00e9|bakery|donut|bagel|7-?eleven|circle k|couche|convenience|\bconv\b|corner store|hasty|news?\s?stand|vending|candy|chocolate|ice cream|gelato|dairy queen|baskin|cold stone|bubble tea|boba|gong cha|chatime|juice|smoothie|popcorn|snack|macs\b|stop go|\bshelbys\b"),
    ("Dining", r"restaurant|resto\b|pizza|pizzeria|sushi|ramen|\bpho\b|thai\b|noodle|burger|grill|bistro|kitchen|diner|eatery|tavern|\bpub\b|brew|taproom|\bbar\b|cantina|taco|burrito|shawarma|kebab|falafel|banh|dumpling|bbq|wings|steakhouse|trattoria|curry|tandoor|poke\b|mcdonald|wendy|subway|a&w|kfc|popeye|harvey|swiss chalet|chipotle|nandos|five guys|boston pizza|st-?hubert|domino|little caesar|mary brown|osmow|pita|deli\b|brunch|patio|tapas|izakaya"),
    ("Groceries", r"loblaw|no frills|nofrills|freshco|\bmetro\b|sobeys|farm boy|food basics|superstore|zehrs|fortinos|valu-?mart|independent grocer|longo|whole foods|t&t|h mart|nations fresh|galleria|bulk barn|\biga\b|save-?on|safeway|costco|walmart|instacart|grocer|supermarket|\bmarket\b|fruit|produce|butcher|fish market|farmers?"),
    ("Household", r"home depot|\brona\b|home hardware|canadian tire|ikea|dollarama|dollar tree|wayfair|structube|bed bath|crate"),
    ("Shopping", r"amazon|amzn|best buy|winners|marshalls|homesense|h&m|uniqlo|zara|\bgap\b|old navy|indigo|chapters|apple\.com|apple store|staples|aritzia|lululemon|sephora|nordstrom|etsy|ebay|aliexpress|shein|temu"),
    ("Fun", r"netflix|spotify|disney|crave|prime video|steam\b|playstation|xbox|nintendo|cineplex|cinema|ticketmaster|eventbrite|livenation|karaoke|bowling|escape room|museum|zoo\b|theatre|theater|concert|axe throw|arcade|lcbo|beer store|wine rack|winery|distill"),
    ("Bills", r"hydro|enbridge|\bbell\b|rogers|telus|freedom mobile|\bfido\b|koodo|virgin plus|chatr|public mobile|internet|insurance|\brent\b|mortgage|property tax|water bill|cloudflare|anthropic|github|google \*|microsoft|dropbox|1password|icloud|tailscale|digitalocean|namecheap|\bfee\b|interest|service charge|monthly fee|practicalplan"),
]
_COMPILED = [(name, re.compile(rx, re.I)) for name, rx in CATEGORY_RULES]


def guess_category(text):
    """Best built-in guess at a category name from a merchant or description; None when nothing is recognised."""
    t = text or ""
    for name, rx in _COMPILED:
        if rx.search(t):
            return name
    return None


def default_bps(db):
    return int(db.get_setting("default_bps", "5000"))


def my_part(amount_cents, bps):
    return (amount_cents * bps + (5000 if amount_cents >= 0 else -5000)) // 10000


def merchant_key(norm):
    words = [w for w in re.sub(r"[^A-Z ]", " ", norm.upper()).split() if len(w) > 1][:2]
    return " ".join(words)


def category_by_name(db, name):
    return db.val("SELECT id FROM categories WHERE name=?", (name,))


def hint_category(db, text):
    name = guess_category(text)
    return category_by_name(db, name) if name else None


def assign_merchants(db):
    for t in db.q("SELECT id, description_norm FROM transactions WHERE merchant_id IS NULL"):
        key = merchant_key(t["description_norm"])
        if not key:
            continue
        m = db.one("SELECT id FROM merchants WHERE pattern=?", (key,))
        if not m:
            mid = db.ex("INSERT INTO merchants(name,pattern,default_category_id) VALUES(?,?,?)",
                        (key.title(), key, hint_category(db, t["description_norm"]))).lastrowid
        else:
            mid = m["id"]
        db.ex("UPDATE transactions SET merchant_id=? WHERE id=?", (mid, t["id"]))


def recategorize_other(db):
    """Merchants and lines still filed under Other (or nothing) get the built-in guess. Lines the owner moved elsewhere are left alone."""
    other = category_by_name(db, "Other")
    n = 0
    for m in db.q("SELECT m.id, m.pattern, (SELECT description_norm FROM transactions WHERE merchant_id=m.id LIMIT 1) AS sample FROM merchants m "
                  "WHERE m.default_category_id IS NULL OR m.default_category_id=?", (other,)):
        cid = hint_category(db, f"{m['pattern']} {m['sample'] or ''}")
        if not cid or cid == other:
            continue
        db.ex("UPDATE merchants SET default_category_id=? WHERE id=?", (cid, m["id"]))
        n += db.ex("""UPDATE line_items SET category_id=? WHERE (category_id IS NULL OR category_id=?)
                      AND txn_id IN (SELECT id FROM transactions WHERE merchant_id=?)""", (cid, other, m["id"])).rowcount
    return n


def detect_transfers(db, window_days=4):
    """Pair a payment out of one of your accounts with the matching money in on another. Only unambiguous pairs."""
    cands = db.q("""SELECT t.*, a.owner FROM transactions t JOIN accounts a ON a.id=t.account_id
                    WHERE t.transfer_pair_id IS NULL AND t.kind IN ('spend','income')""")
    outs = [t for t in cands if t["amount_cents"] > 0 and t["kind"] == "spend"]
    for o in outs:
        od = date.fromisoformat(o["posted_date"])
        mates = [i for i in cands if i["amount_cents"] == -o["amount_cents"] and i["account_id"] != o["account_id"]
                 and i["owner"] == o["owner"] and abs((date.fromisoformat(i["posted_date"]) - od).days) <= window_days]
        if len(mates) != 1:
            continue
        i = mates[0]
        if not (TRANSFER_WORDS.search(o["description_raw"]) or TRANSFER_WORDS.search(i["description_raw"])):
            continue
        if db.val("SELECT transfer_pair_id FROM transactions WHERE id=?", (i["id"],)) is not None:
            continue
        db.ex("UPDATE transactions SET kind='transfer', transfer_pair_id=? WHERE id=?", (i["id"], o["id"]))
        db.ex("UPDATE transactions SET kind='transfer', transfer_pair_id=? WHERE id=?", (o["id"], i["id"]))
        db.ex("DELETE FROM line_items WHERE txn_id IN (?,?)", (o["id"], i["id"]))


LIABILITY_TYPES = ("credit_card", "line_of_credit")
PAYMENT_WORDS = re.compile(r"payment|pmt|thank you|autopay|paiement|merci", re.I)


def repair_pairs(db):
    """A transfer whose partner row is gone (the other statement was re-read or removed) goes back to being an ordinary row."""
    db.ex("""UPDATE transactions SET transfer_pair_id=NULL, kind=CASE WHEN amount_cents>0 THEN 'spend' ELSE 'income' END
             WHERE kind='transfer' AND transfer_pair_id IS NOT NULL AND transfer_pair_id NOT IN (SELECT id FROM transactions)""")


def classify_liability_credits(db):
    """Money arriving on a card is a payment toward its balance or a refund from a shop. It is never income."""
    for r in db.q("""SELECT t.id, t.amount_cents, t.description_raw, s.opening_cents FROM transactions t JOIN accounts a ON a.id=t.account_id
                     LEFT JOIN statements s ON s.id=t.statement_id WHERE a.type IN ('credit_card','line_of_credit') AND t.kind='income'"""):
        paid = bool(PAYMENT_WORDS.search(r["description_raw"])) or (r["opening_cents"] is not None and -r["amount_cents"] == r["opening_cents"])
        db.ex("UPDATE transactions SET kind=? WHERE id=?", ("transfer" if paid else "refund", r["id"]))


def mark_refunds(db):
    for t in db.q("SELECT id, merchant_id, posted_date FROM transactions WHERE kind='income' AND merchant_id IS NOT NULL"):
        if db.val("""SELECT COUNT(*) FROM transactions WHERE merchant_id=? AND kind='spend' AND amount_cents>0
                     AND posted_date<=?""", (t["merchant_id"], t["posted_date"]), 0) and \
                not re.search(r"payroll|salary|deposit|interest|e-?transfer", db.val("SELECT description_raw FROM transactions WHERE id=?", (t["id"],)), re.I):
            db.ex("UPDATE transactions SET kind='refund' WHERE id=?", (t["id"],))


def after_ingest(db):
    repair_pairs(db)
    assign_merchants(db)
    recategorize_other(db)
    detect_transfers(db)
    classify_liability_credits(db)
    mark_refunds(db)
    from . import receipts
    receipts.match_pending(db)


# ---------------------------------------------------------------- learning
def history(db, merchant_id, category_id, item_key=None):
    return db.q("""SELECT li.my_bps, li.category_id, li.item_key, t.merchant_id, t.posted_date FROM line_items li JOIN transactions t ON t.id=li.txn_id
                   WHERE li.reviewed=1 AND li.split_source='user' AND (t.merchant_id=? OR li.category_id=? OR (li.item_key IS NOT NULL AND li.item_key=?))""",
                (merchant_id or -1, category_id or -1, item_key or "\0"))


def _tally(rows, today):
    tot, by, n = 0.0, {}, 0
    for r in rows:
        age = max(0, (today - date.fromisoformat(r["posted_date"])).days)
        w = 0.5 ** (age / HALF_LIFE_DAYS)
        by[r["my_bps"]] = by.get(r["my_bps"], 0.0) + w
        tot += w
        n += 1
    if not n:
        return None
    top = max(by, key=by.get)
    return {"bps": top, "share": by[top] / tot, "n": n, "agree": sum(1 for r in rows if r["my_bps"] == top)}


def pct(bps):
    return f"{bps / 100:g}%"


def suggest(db, merchant_id, category_id, today=None, item_key=None):
    """-> dict(bps, confident, why, source). Counting only; every answer can be explained in one sentence."""
    today = today or clock.now().date()
    rule = db.one("SELECT my_bps FROM split_rules WHERE merchant_id=?", (merchant_id,)) if merchant_id else None
    mname = db.val("SELECT name FROM merchants WHERE id=?", (merchant_id,), "") if merchant_id else ""
    cname = db.val("SELECT name FROM categories WHERE id=?", (category_id,), "") if category_id else ""
    if rule:
        return {"bps": rule["my_bps"], "confident": True, "source": "rule", "why": f"Your standing rule for {mname}: your share {pct(rule['my_bps'])}."}
    rows = history(db, merchant_id, category_id, item_key)
    tiers = [("I", [r for r in rows if item_key and r["item_key"] == item_key], 2, f"“{(item_key or '').title()}”"),
             ("A", [r for r in rows if r["merchant_id"] == merchant_id and r["category_id"] == category_id], 2, f"{mname} ({cname})"),
             ("B", [r for r in rows if r["merchant_id"] == merchant_id], 2, mname),
             ("C", [r for r in rows if r["category_id"] == category_id], 4, f"{cname} in general")]
    for _t, rs, need, label in tiers:
        tl = _tally(rs, today)
        if tl and tl["n"] >= need and label.strip(" ()"):
            conf = tl["share"] >= CONFIDENT_SHARE and tl["n"] >= CONFIDENT_MIN_N
            return {"bps": tl["bps"], "confident": conf, "source": "learned",
                    "why": f"{label}: {tl['agree']} of {tl['n']} past decisions were {pct(tl['bps'])} your share (recent ones count more)."
                           + ("" if conf else " Not consistent enough to decide for you.")}
    d = default_bps(db)
    return {"bps": d, "confident": False, "source": "default", "why": f"Nothing to learn from yet, so the default {pct(d)} is proposed."}


# ---------------------------------------------------------------- line items
def ensure_lines(db, month):
    assign_merchants(db)
    now = clock.iso(clock.now())
    rows = db.q("""SELECT t.id, t.amount_cents, t.description_raw, t.merchant_id, m.default_category_id AS cat FROM transactions t
                   LEFT JOIN merchants m ON m.id=t.merchant_id
                   WHERE t.posted_date LIKE ? AND t.kind IN ('spend','refund')
                   AND NOT EXISTS (SELECT 1 FROM line_items li WHERE li.txn_id=t.id)""", (month + "%",))
    for t in rows:
        cat = t["cat"] or category_by_name(db, "Other")
        s = suggest(db, t["merchant_id"], cat)
        db.ex("""INSERT INTO line_items(txn_id,description,amount_cents,category_id,my_bps,split_source,confident,why,created_at)
                 VALUES(?,?,?,?,?,?,?,?,?)""", (t["id"], t["description_raw"], t["amount_cents"], cat, s["bps"], s["source"], int(s["confident"]), s["why"], now))


def resuggest_open(db, month):
    """After a decision, refresh suggestions for lines still undecided (the learner just got smarter)."""
    for li in db.q("""SELECT li.id, li.category_id, li.item_key, t.merchant_id FROM line_items li JOIN transactions t ON t.id=li.txn_id
                      WHERE li.reviewed=0 AND li.split_source<>'anchor' AND t.posted_date LIKE ?""", (month + "%",)):
        s = suggest(db, li["merchant_id"], li["category_id"], item_key=li["item_key"])
        db.ex("UPDATE line_items SET my_bps=?, split_source=?, confident=?, why=? WHERE id=?",
              (s["bps"], s["source"], int(s["confident"]), s["why"], li["id"]))


def month_items(db, month):
    ensure_lines(db, month)
    return db.q("""SELECT li.*, t.posted_date, t.description_raw, t.amount_cents AS txn_cents, t.account_id, a.name AS acct, a.owner, m.name AS merchant,
                          t.merchant_id, t.kind AS tkind, c.name AS category
                   FROM line_items li JOIN transactions t ON t.id=li.txn_id JOIN accounts a ON a.id=t.account_id
                   LEFT JOIN merchants m ON m.id=t.merchant_id LEFT JOIN categories c ON c.id=li.category_id
                   WHERE t.posted_date LIKE ? ORDER BY t.posted_date, li.id""", (month + "%",))


def set_line_category(db, line_id, category_id):
    """The owner chose this category for one purchase. It becomes the merchant's category, and the merchant's other purchases that the owner has not
    decided themselves follow (not ones from a receipt, which have their own items). Returns how many other purchases moved."""
    li = db.one("""SELECT li.*, t.merchant_id FROM line_items li JOIN transactions t ON t.id=li.txn_id WHERE li.id=?""", (line_id,))
    if not li:
        raise KeyError(line_id)
    db.ex("UPDATE line_items SET category_id=? WHERE id=?", (category_id, line_id))
    moved = 0
    if li["merchant_id"]:
        db.ex("UPDATE merchants SET default_category_id=? WHERE id=?", (category_id, li["merchant_id"]))
        moved = db.ex("""UPDATE line_items SET category_id=? WHERE reviewed=0 AND receipt_id IS NULL AND IFNULL(category_id,0)<>? AND id<>?
                         AND txn_id IN (SELECT id FROM transactions WHERE merchant_id=?)""", (category_id, category_id, line_id, li["merchant_id"])).rowcount
    return moved


def decide(db, line_id, bps=None, category_id=None, rule=False, now=None):
    li = db.one("""SELECT li.*, t.merchant_id FROM line_items li JOIN transactions t ON t.id=li.txn_id WHERE li.id=?""", (line_id,))
    if not li:
        raise KeyError(line_id)
    now = clock.iso(now or clock.now())
    cat = category_id if category_id else li["category_id"]
    if category_id and category_id != li["category_id"]:
        set_line_category(db, line_id, category_id)
    bps = li["my_bps"] if bps is None else max(0, min(10000, int(bps)))
    db.ex("""UPDATE line_items SET my_bps=?, category_id=?, split_source='user', confident=1, reviewed=1, decided_at=?,
             why='You decided this.' WHERE id=?""", (bps, cat, now, line_id))
    if rule and li["merchant_id"]:
        db.ex("""INSERT INTO split_rules(merchant_id,my_bps,created_at) VALUES(?,?,?)
                 ON CONFLICT(merchant_id) DO UPDATE SET my_bps=excluded.my_bps""", (li["merchant_id"], bps, now))


def reopen(db, line_id):
    db.ex("UPDATE line_items SET reviewed=0, split_source='default', confident=0 WHERE id=?", (line_id,))


def drop_rule(db, merchant_id):
    db.ex("DELETE FROM split_rules WHERE merchant_id=?", (merchant_id,))


def progress(items):
    total = len(items)
    done = sum(1 for i in items if i["reviewed"])
    return {"total": total, "done": done, "ask": sum(1 for i in items if not i["reviewed"] and not i["confident"]),
            "auto": sum(1 for i in items if not i["reviewed"] and i["confident"])}


# ---------------------------------------------------------------- the reckoning
def reckoning(db, month):
    items = month_items(db, month)
    cats, total_mine, total_all = {}, 0, 0
    me_paid_net = 0
    undecided = 0
    for i in items:
        if not i["reviewed"] and not i["confident"]:
            undecided += 1
        mine = my_part(i["amount_cents"], i["my_bps"])
        total_mine += mine
        total_all += i["amount_cents"]
        cats[i["category"] or "Other"] = cats.get(i["category"] or "Other", 0) + mine
        frac = {"me": 1.0, "partner": 0.0, "joint": 0.5}[i["owner"]]
        me_paid_net += round(i["amount_cents"] * frac) - mine
    income = db.val("""SELECT -SUM(amount_cents) FROM transactions t JOIN accounts a ON a.id=t.account_id
                       WHERE t.posted_date LIKE ? AND t.kind='income' AND a.owner IN ('me','joint')
                       AND a.type NOT IN ('credit_card','line_of_credit')""", (month + "%",), 0)
    return {"items": items, "by_category": sorted(cats.items(), key=lambda kv: -kv[1]), "mine": total_mine, "all": total_all,
            "partner_owes_me": me_paid_net, "undecided": undecided, "income": income}


REPAY_WORDS = re.compile(r"e-?transfer|etransfer|interac|\bsent you\b", re.I)


def _txn(db, txn_id):
    return db.one("SELECT id, account_id, dedupe_key, amount_cents, statement_id FROM transactions WHERE id=?", (txn_id,))


def repayment_confirm(db, txn_id, counted_cents=None):
    """This deposit is the partner paying back. counted_cents lets part of it count (a gift, or a repayment that includes something else)."""
    t = _txn(db, txn_id)
    if not t or t["amount_cents"] >= 0:
        return False
    full = -t["amount_cents"]
    counted = None if counted_cents is None or int(counted_cents) >= full else max(0, int(counted_cents))
    db.ex("UPDATE transactions SET kind='settlement' WHERE id=? AND kind IN ('income','setaside','settlement')", (txn_id,))
    db.ex("""INSERT INTO repayment_marks(account_id,dedupe_key,counted_cents,status) VALUES(?,?,?,'confirmed')
             ON CONFLICT(account_id,dedupe_key) DO UPDATE SET counted_cents=excluded.counted_cents, status='confirmed'""", (t["account_id"], t["dedupe_key"], counted))
    return True


def repayment_reject(db, txn_id):
    """Not from the partner: stop suggesting it."""
    t = _txn(db, txn_id)
    if t:
        db.ex("""INSERT INTO repayment_marks(account_id,dedupe_key,counted_cents,status) VALUES(?,?,NULL,'rejected')
                 ON CONFLICT(account_id,dedupe_key) DO UPDATE SET counted_cents=NULL, status='rejected'""", (t["account_id"], t["dedupe_key"]))


def repayment_undo(db, txn_id):
    t = _txn(db, txn_id)
    if t:
        db.ex("DELETE FROM repayment_marks WHERE account_id=? AND dedupe_key=?", (t["account_id"], t["dedupe_key"]))
        db.ex("UPDATE transactions SET kind='income' WHERE id=? AND kind='settlement'", (txn_id,))


def match_manual_repayments(db):
    """A repayment entered by hand looks for its deposit in the filed statements: same amount (within 50 cents), dated from 3 days before to 10 days
    after the date entered, not pay, not already decided. The closest date wins. A match is confirmed as the repayment and the hand entry retires,
    so nothing is counted twice. Returns how many were matched."""
    from . import spending
    n = 0
    for m in db.q("SELECT * FROM settlements WHERE removed_at IS NULL AND direction='from_partner' ORDER BY paid_on, id"):
        lo = (date.fromisoformat(m["paid_on"]) - timedelta(days=3)).isoformat()
        hi = (date.fromisoformat(m["paid_on"]) + timedelta(days=10)).isoformat()
        cands = [r for r in db.q("""SELECT t.id, t.posted_date, t.amount_cents, t.description_raw FROM transactions t JOIN accounts a ON a.id=t.account_id
                                    WHERE t.kind IN ('income','setaside') AND t.amount_cents<0 AND a.owner IN ('me','joint') AND a.type NOT IN ('credit_card','line_of_credit')
                                    AND t.posted_date BETWEEN ? AND ? AND abs(-t.amount_cents-?)<=50
                                    AND NOT EXISTS (SELECT 1 FROM repayment_marks k WHERE k.account_id=t.account_id AND k.dedupe_key=t.dedupe_key)""",
                                 (lo, hi, m["amount_cents"]))
                 if not spending.is_pay(db, r["description_raw"], -r["amount_cents"])]
        if not cands:
            continue
        best = min(cands, key=lambda r: abs((date.fromisoformat(r["posted_date"]) - date.fromisoformat(m["paid_on"])).days))
        repayment_confirm(db, best["id"])
        db.ex("UPDATE settlements SET removed_at=?, note=? WHERE id=?", (clock.iso(clock.now()), f"matched to the deposit on {best['posted_date']}", m["id"]))
        n += 1
    return n


def repayment_suggestions(db, since=None):
    """Deposits that look like the partner paying back, not yet decided: e-transfers that are not pay. Ones that look like a deposit
    already confirmed as a repayment are marked sure."""
    from . import spending
    confirmed = {r["description_norm"] for r in db.q("""SELECT t.description_norm FROM transactions t JOIN repayment_marks m ON m.account_id=t.account_id AND m.dedupe_key=t.dedupe_key
                                                         WHERE m.status='confirmed' AND t.amount_cents<0""")}
    out = []
    for r in db.q("""SELECT t.id, t.posted_date, t.amount_cents, t.description_raw, t.description_norm, a.name AS acct FROM transactions t JOIN accounts a ON a.id=t.account_id
                     WHERE t.kind IN ('income','setaside') AND t.amount_cents<0 AND a.owner IN ('me','joint') AND a.type NOT IN ('credit_card','line_of_credit') AND t.posted_date>=?
                     AND NOT EXISTS (SELECT 1 FROM repayment_marks m WHERE m.account_id=t.account_id AND m.dedupe_key=t.dedupe_key)
                     ORDER BY t.posted_date DESC, t.id DESC""", (since or "0000-00-00",)):
        if spending.is_pay(db, r["description_raw"], -r["amount_cents"]):
            continue
        sure = r["description_norm"] in confirmed
        if sure or REPAY_WORDS.search(r["description_raw"]):
            out.append({"id": r["id"], "date": r["posted_date"], "cents": -r["amount_cents"], "desc": r["description_raw"][:56], "acct": r["acct"], "sure": sure})
    return out


def partner_tally(db, start=None):
    """Running settle-up across months: the partner's share of what you paid, less what has come back. Counted back: manual entries, deposits
    confirmed as repayments (all or part of each), and anything waived. Deposits that merely look like repayments are counted provisionally."""
    months = sorted(r["m"] for r in db.q("SELECT DISTINCT substr(posted_date,1,7) AS m FROM transactions WHERE kind IN ('spend','refund')"))
    if start:
        months = [m for m in months if m >= start]
    else:
        start = months[0] if months else None
    owed = sum(reckoning(db, m)["partner_owes_me"] for m in months)
    since = (start + "-01") if start else "0000-00-00"
    pays = []
    for r in db.q("SELECT * FROM settlements WHERE removed_at IS NULL AND paid_on>=? ORDER BY paid_on DESC, id DESC", (since,)):
        waived = r["direction"] == "waived"
        pays.append({"key": "m%d" % r["id"], "id": r["id"], "src": "manual", "date": r["paid_on"], "waived": waived,
                     "cents": r["amount_cents"] if r["direction"] in ("from_partner", "waived") else -r["amount_cents"], "note": r["note"], "acct": ""})
    for r in db.q("""SELECT t.id, t.posted_date, t.amount_cents, t.description_raw, a.name, m.counted_cents FROM transactions t JOIN accounts a ON a.id=t.account_id
                     LEFT JOIN repayment_marks m ON m.account_id=t.account_id AND m.dedupe_key=t.dedupe_key AND m.status='confirmed'
                     WHERE t.kind='settlement' AND t.posted_date>=? ORDER BY t.posted_date DESC, t.id DESC""", (since,)):
        full = -r["amount_cents"]
        counted = full if r["counted_cents"] is None else r["counted_cents"]
        pays.append({"key": "t%d" % r["id"], "id": r["id"], "src": "txn", "date": r["posted_date"], "cents": counted, "full": full, "waived": False,
                     "note": r["description_raw"][:60], "acct": r["name"]})
    pays.sort(key=lambda p: p["date"], reverse=True)
    received = sum(p["cents"] for p in pays if p["cents"] > 0 and not p["waived"])
    waived = sum(p["cents"] for p in pays if p["waived"])
    paid = -sum(p["cents"] for p in pays if p["cents"] < 0)
    suggested = repayment_suggestions(db, since)
    balance = owed - received - waived + paid
    left = max(0, balance)
    for x in sorted(suggested, key=lambda x: x["date"]):
        # only a deposit like one already confirmed counts for now, and never past what is owed; the rest wait for the owner's word
        x["counted"] = min(x["cents"], left) if x["sure"] else 0
        left -= x["counted"]
    sug_total = sum(x["counted"] for x in suggested)
    return {"start": start, "owed": owed, "received": received, "waived": waived, "paid": paid, "balance": balance, "payments": pays,
            "suggested": suggested, "suggested_total": sug_total, "maybe_total": sum(x["cents"] for x in suggested if not x["counted"]),
            "balance_after_suggested": balance - sug_total}


def repayment_candidates(db, since):
    """Any deposit not yet decided, for marking by hand."""
    return db.q("""SELECT t.id, t.posted_date, t.amount_cents, t.description_raw, a.name AS acct FROM transactions t JOIN accounts a ON a.id=t.account_id
                   WHERE t.kind IN ('income','setaside') AND t.amount_cents<0 AND t.posted_date>=? AND a.type NOT IN ('credit_card','line_of_credit')
                   AND NOT EXISTS (SELECT 1 FROM repayment_marks m WHERE m.account_id=t.account_id AND m.dedupe_key=t.dedupe_key)
                   ORDER BY t.posted_date DESC, t.id DESC LIMIT 25""", (since,))
