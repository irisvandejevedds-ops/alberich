"""Sorting the merchants still filed under Other: the owner sees exactly which names would be sent, then reviews and edits the answer."""
import json

from . import clock, ledger, llm

MAX_ROWS = 80

SYSTEM = """You sort bank-statement merchants into spending categories for a household budget. You receive a list of merchants (name as printed on the \
statement, with store numbers removed, and how many purchases) and the allowed categories. Return ONLY JSON:
{"sorted": [{"id": integer, "category": string, "why": string}]}
Use only the allowed category names; use "Other" when you genuinely cannot tell. Snacks means coffee shops, bakeries, convenience stores, sweets and similar small \
bites. Dining means restaurants, fast food, bars and food delivery. Groceries means supermarkets and food shops. Prefixes like SQ, TST, PAYPAL, and \
store or city names are noise. Keep every "why" under 8 words."""


def pending(db):
    """Merchants whose lines are still under Other, most spend first: (merchant id, label, purchases, total cents)."""
    other = ledger.category_by_name(db, "Other")
    rows = db.q("""SELECT m.id, m.pattern, COUNT(*) n, SUM(ABS(li.amount_cents)) total,
                          (SELECT description_norm FROM transactions WHERE merchant_id=m.id ORDER BY id LIMIT 1) AS sample
                   FROM line_items li JOIN transactions t ON t.id=li.txn_id JOIN merchants m ON m.id=t.merchant_id
                   WHERE li.category_id IS NULL OR li.category_id=? GROUP BY m.id ORDER BY total DESC LIMIT ?""", (other, MAX_ROWS))
    return [{"id": r["id"], "label": (r["sample"] or r["pattern"])[:48], "n": r["n"], "total": r["total"]} for r in rows]


def payload(db):
    cats = [r["name"] for r in db.q("SELECT name FROM categories ORDER BY sort, name")]
    return {"allowed_categories": cats, "merchants": [{"id": p["id"], "name": p["label"], "purchases": p["n"]} for p in pending(db)]}


def run(db, cfg, data, client=None):
    text = llm.call(db, cfg, "merchant_sort", SYSTEM, json.dumps(data), 3000, "merchant names only (no amounts, dates or accounts)", client)
    try:
        obj = llm._json(text)
        got = {int(r["id"]): r for r in obj["sorted"]}
    except Exception:
        raise llm.LLMUnavailable("The reply could not be understood. Nothing was changed.")
    allowed = set(data["allowed_categories"])
    rows = []
    for m in data["merchants"]:
        r = got.get(m["id"])
        cat = r.get("category") if r else None
        rows.append({"id": m["id"], "name": m["name"], "n": m["purchases"], "category": cat if cat in allowed else "Other", "why": str((r or {}).get("why") or "")[:80]})
    return rows


def apply(db, picks):
    """picks: {merchant_id: category name}. Sets the merchant default and re-files its lines that are still under Other."""
    other = ledger.category_by_name(db, "Other")
    moved = 0
    for mid, name in picks.items():
        cid = ledger.category_by_name(db, name)
        if not cid or cid == other:
            continue
        db.ex("UPDATE merchants SET default_category_id=? WHERE id=?", (cid, mid))
        moved += db.ex("""UPDATE line_items SET category_id=? WHERE (category_id IS NULL OR category_id=?)
                          AND txn_id IN (SELECT id FROM transactions WHERE merchant_id=?)""", (cid, other, mid)).rowcount
    return moved
