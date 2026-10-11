"""The Warlock: free-form context from the owner becomes a short list of concrete, checkable changes. The model proposes; the server
validates and describes every action itself, and nothing is applied until the owner ticks it."""
import json
import re
from datetime import date, timedelta

from . import clock, credits, ledger, llm, pxicons, spending

MAX_TEXT = 1500
MAX_ACTIONS = 30
HISTORY_DAYS = 90
MAX_TXNS = 350
MAX_IDS = 60
TIP_DEFAULT = 30
KINDS = ("extra_income", "extra_expense")
TREATS = ("to_debt", "to_savings", "spent", "exclude")
TREAT_WORDS = {"to_debt": "set aside to pay down debt", "to_savings": "set aside as savings", "spent": "money that was spent", "exclude": "kept out of your typical numbers"}
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


# ---------------------------------------------------------------- state
def consent(db):
    return db.get_setting("warlock_ok_v2", "0") == "1"


def notes(db):
    return db.q("SELECT * FROM warlock_notes WHERE archived_at IS NULL ORDER BY id")


def adjustments(db, month=None):
    if month:
        return db.q("SELECT * FROM month_adjustments WHERE removed_at IS NULL AND month=? ORDER BY id", (month,))
    return db.q("SELECT * FROM month_adjustments WHERE removed_at IS NULL ORDER BY month DESC, id")


def d(c):
    return int(round(c / 100))


def money(c):
    return f"${c / 100:,.2f}" if c % 100 else f"${c // 100:,}"


def month_label(m):
    return date(int(m[:4]), int(m[5:]), 1).strftime("%b %Y")


# ---------------------------------------------------------------- what the warlock sees
def _tidy(text, n):
    return re.sub(r"\d{6,}", "#", re.sub(r"\s+", " ", text or "")).strip()[:n]


def ledger_view(db, today):
    """Recent charges as compact rows the model can cite by id. No account numbers; long digit runs are masked."""
    lo = (today - timedelta(days=HISTORY_DAYS)).isoformat()
    for m in sorted({(today - timedelta(days=n)).isoformat()[:7] for n in range(0, HISTORY_DAYS + 1, 7)} | {today.isoformat()[:7]}):
        ledger.ensure_lines(db, m)
    rows = db.q("""SELECT t.id, t.posted_date, t.amount_cents, t.description_raw, t.kind, a.name AS acct, m.name AS merchant, mc.name AS mcat
                   FROM transactions t JOIN accounts a ON a.id=t.account_id LEFT JOIN merchants m ON m.id=t.merchant_id
                   LEFT JOIN categories mc ON mc.id=m.default_category_id
                   WHERE t.posted_date>=? ORDER BY t.posted_date DESC, t.id DESC LIMIT ?""", (lo, MAX_TXNS + 1))
    more = len(rows) > MAX_TXNS
    rows = rows[:MAX_TXNS]
    lines = {}
    ids = [r["id"] for r in rows]
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        for li in db.q("""SELECT li.id, li.txn_id, li.description, li.amount_cents, li.my_bps, li.reviewed, c.name AS cat FROM line_items li
                          LEFT JOIN categories c ON c.id=li.category_id WHERE li.txn_id IN (%s) ORDER BY li.id""" % ",".join("?" * len(chunk)), tuple(chunk)):
            lines.setdefault(li["txn_id"], []).append(li)
    out, merchants = [], {}
    for r in rows:
        ls = lines.get(r["id"], [])
        if len(ls) == 1:
            li = ls[0]
            part = f'L{li["id"]} {li["cat"] or "?"} {li["my_bps"] / 100:g}%{"" if li["reviewed"] else "?"}'
        elif ls:
            part = ";".join(f'L{li["id"]} {_tidy(li["description"], 22)} {li["amount_cents"] / 100:.2f} {li["cat"] or "?"} {li["my_bps"] / 100:g}%' for li in ls[:25])
        else:
            part = f'- {r["mcat"] or "?"}' if r["kind"] in ("spend", "refund") else "-"
        out.append(f'{r["id"]}|{r["posted_date"]}|{r["acct"]}|{r["amount_cents"] / 100:.2f}|{r["kind"]}|{_tidy(r["description_raw"], 48)}|{r["merchant"] or ""}|{part}')
        if r["merchant"]:
            merchants[r["merchant"]] = r["mcat"]
    rules = {x["name"]: x["my_bps"] for x in db.q("SELECT m.name, s.my_bps FROM split_rules s JOIN merchants m ON m.id=s.merchant_id")}
    return {
        "row_format": "txn_id|date|account|amount (positive = money out, negative = money in)|kind|description|merchant|lines. A line is "
                      "L<line_id> <category> <your share>% (a trailing ? means not yet confirmed by the owner); a charge with several lines lists each as "
                      "L<id> <item> <amount> <category> <your share>%. '-' means no line yet.",
        "transactions": out, "older_omitted": more, "days_covered": HISTORY_DAYS,
        "merchants": [{"name": k, "category": v, "my_share_rule_percent": (rules[k] / 100 if k in rules else None)} for k, v in sorted(merchants.items())][:150],
    }


def _coverage(db, today):
    sp = spending.spans(db)
    names = {r["id"]: r["name"] for r in db.q("SELECT id, name FROM accounts WHERE active=1")}
    return [{"account": names[a], "covered": [f"{st.isoformat()} to {en.isoformat()}" for st, en in lst], "days_since_last_covered_day": (today - lst[-1][1]).days}
            for a, lst in sorted(sp.items()) if a in names]


def context(db, cfg, now):
    today = now.astimezone(cfg.tz).date()
    cur = f"{today.year}-{today.month:02d}"
    p = spending.plan(db)
    derived = spending.derive(db, p)
    states = {s["activity"]["id"]: s for s in credits.all_states(db, cur, cfg.tz, now)}
    start = clock.iso(now - timedelta(days=30))
    return {
        "today": today.isoformat(), "current_month": cur,
        "real_monthly_income": d(p["income"]), "typical_monthly_spend": d(p["avg_total"]), "left_over_in_a_typical_month": d(p["left_over"]),
        "overall_cut_percent": p["global"], "cut_would_save_per_month": d(p["saving"]), "cut_saving_percent_of_income": p["saving_pct"],
        "months_compared": p["usable_months"], "latest_month": p["latest"], "months_with_any_statement_data": p["months"],
        "categories": [{"name": c["name"], "kind": c["kind"], "average_monthly": d(c["avg"]), "latest_month": d(c["last"]) if c["last"] is not None else None,
                        "vs_usual": d(c["delta"]) if c["delta"] is not None else None, "what_drove_it": c["callout"] or None,
                        "change_percent": c["change"], "own_change_set": c["custom"], "aim_monthly": d(c["target"])} for c in p["categories"]],
        "all_category_names": [r["name"] for r in db.q("SELECT name FROM categories ORDER BY sort, name")],
        "accounts": [{"name": a["name"], "institution": a["institution"], "type": a["type"], "owner": a["owner"]} for a in db.q("SELECT * FROM accounts WHERE active=1 ORDER BY id")],
        "pouches": [{"name": a["name"], "icon": a["icon"], "category": db.val("SELECT name FROM categories WHERE id=?", (a["category_id"],)) if a["category_id"] else None,
                     "estimate_each": d(a["estimate_cents"]), "credits_per_month": a["credits_per_month"],
                     "credits_follow_spending": bool(a["auto_credits"] and a["category_id"] and a["id"] in derived),
                     "used_this_month": states[a["id"]]["used"] if a["id"] in states else 0,
                     "ran_over_credits_by_month": {o["month"]: o["over"] for o in credits.overdraw_history(db, a, cur, cfg.tz, now) if o["over"]}}
                    for a in db.q("SELECT * FROM activities WHERE archived=0 ORDER BY sort, id")],
        "recent_credit_uses": [f'{l["id"]}|{l["ts"][:16]}|{l["name"]}{"|undone" if l["undone_at"] else ""}' for l in
                               db.q("""SELECT l.id, l.ts, l.undone_at, a.name FROM activity_logs l JOIN activities a ON a.id=l.activity_id
                                       WHERE l.ts>=? ORDER BY l.ts DESC LIMIT 40""", (start,))],
        "statement_coverage": _coverage(db, today),
        "standing_notes": [n["text"] for n in notes(db)],
        "month_adjustments": [{"month": a["month"], "kind": a["kind"], "amount": d(a["amount_cents"]), "label": a["label"], "treat": a["treat"], "category": a["category"]}
                              for a in adjustments(db)][:12],
        "receipt_tip_allowance_percent": int(db.get_setting("tip_max_pct", TIP_DEFAULT)),
        "allowed_icons": pxicons.PICKER,
        **ledger_view(db, today),
    }


SYSTEM = """You are the Warlock inside a private household finance app: a calm, dry arcane accountant who helps the owner understand what they really spend, \
tune the optional cuts that size their monthly spending credits ("pouches"), and fix how charges and receipts are categorised and split. You receive \
CONTEXT (the owner's recent charges with ids, merchants, categories, shares, category averages and how the latest month differs, cuts, pouches, credit \
uses, statement coverage), recent conversation, and the owner's new MESSAGE. You may propose changes to almost anything in it. Reply with ONLY a JSON object:
{"reply": string, "actions": [ ... ]}
"reply": one to four short sentences, plain and useful, with at most a touch of wizardly flavour. Say what you understood and what you propose. If the message \
is ambiguous or you lack a number, ask one clear question and propose no actions.
HOW THE APP WORKS. Statements are filed about once a month, so there are NO budgets or limits to stay under and nothing is tracked live; never talk about \
"room left", "over budget" or "staying within" a category. The Treasury shows each category's average monthly spend and how the latest month differs. \
The owner may choose an overall cut (a percent) and a plus or minus percent for any one category; a category's own percent beats the overall cut. \
Each pouch tied to a category gets credits worked out from that category's reduced aim divided by the pouch's cost each, and purchases in the category \
use credits up automatically. So to change what the Purse allows, change a cut or a pouch's cost each, not a budget.
"actions": zero to 30 proposals the owner can tick. Each is one of:
 Cuts (these drive the Purse):
 {"type":"set_cut","percent":number}                                         (the overall cut to spending, 0 to 90 percent)
 {"type":"set_category_change","category":string,"percent":number or null}   (plus or minus percent for one category; null returns it to the overall cut)
 {"type":"add_category","name":string,"kind":"variable"|"fixed"}
 {"type":"month_adjustment","month":"YYYY-MM","kind":"extra_income"|"extra_expense","amount":number,"label":string,"treat":"to_debt"|"to_savings"|"spent"|"exclude","category":string or null}
   (a one-off event that should not change what counts as typical. extra_income is NOT part of real income. For extra_expense give the category it hit.)
 Pouches:
 {"type":"set_pouch","name":string,"estimate":number,"category":string,"icon":string,"credits":integer or omitted,"archive":boolean}
   (estimate is what one use costs. Leave credits out so they follow spending; give credits only to set them by hand. icon from allowed_icons; archive true retires it)
 {"type":"undo_credit_use","log_ids":[integer]}                              (ids from recent_credit_uses that were logged by mistake)
 Categorising and splitting what already happened (ids come from CONTEXT.transactions):
 {"type":"set_txn_category","txn_ids":[integer],"category":string,"why":string}     (recategorise every line of those charges)
 {"type":"set_line_category","line_ids":[integer],"category":string,"why":string}   (recategorise individual lines, ids like L123 without the L)
 {"type":"set_txn_split","txn_ids":[integer],"my_percent":number,"why":string}      (the owner's share of those charges)
 {"type":"mark_transfer","txn_ids":[integer],"why":string}                          (these charges are not spending: paying a loan, card, or moving money)
 {"type":"mark_transfer","month":"YYYY-MM","amount":number,"label":string}          (same, found by exact amount when you have no id)
 {"type":"set_category","merchant":string,"category":string}                        (a merchant's category from now on, and for its existing single-line charges)
 {"type":"set_split_rule","merchant":string,"my_percent":number}                    (the owner's share, every time, for a merchant)
 {"type":"set_tip_range","max_percent":number}                                      (how far above a receipt total a bank charge may be, for tips)
 {"type":"note","text":string,"scope":"general"|"budget"|"credits"|"receipts"}      (a standing fact worth remembering for future analysis)
Rules:
- Ground every action in what the owner said or in CONTEXT. Never invent amounts, merchants, months or ids; only use ids that appear in CONTEXT.
- When many charges share a fix, put all their ids in ONE action instead of many actions.
- A one-off (a bonus, a repair, a trip) is a month_adjustment, not a change to the cuts. If the owner says it went to a debt or savings, use that treat.
- Propose cuts or pouch changes only when the owner gave a reason or a number, or asked you to; keep them modest and say why. Rent and other fixed bills are best left uncut with their own change_percent of 0.
- Dollar amounts are plain numbers (1500, not "$1,500"). Months are YYYY-MM; resolve words like "this month" from CONTEXT.today.
- Averages come from months_with_any_statement_data, with partly covered months counted in proportion. A pouch's ran_over_credits_by_month shows where the owner used more than its credits, a hint that it needs more credits or a higher cost each, so mention it when relevant. Check statement_coverage: an account whose last covered day is long ago, or little history, means figures may UNDERSTATE real income and spending. Say so briefly; never read low figures as the owner being frugal or low-income.
- Prefer fewer, clearer actions. Do not repeat what is already in standing_notes or month_adjustments.
- Never state how the app counts or ignores things unless CONTEXT says so. Judge a merchant by the amounts and descriptions you can see (a $6 Esso Circle K is a convenience store, not fuel; a $1 charge is a card hold). If the owner corrects you, accept it and do not guess a new explanation.
- Credits follow how often a pouch is really used (purchases per month, with tips, holds and adjustments merged into their purchase). Typical cost each is a separate figure. Do not set credits by hand unless the owner asks for a specific number.
- Be terse: the reply is at most two short sentences, and every "why" is under 12 words or left out."""


def render_prompt(db, cfg, text, now, skip_id=None):
    ctx = context(db, cfg, now)
    recent = db.q("SELECT role, text FROM warlock_messages WHERE status='done' AND id<>? ORDER BY id DESC LIMIT 7", (skip_id or 0,))
    convo = [{"role": r["role"], "text": r["text"][:500]} for r in reversed(recent)]
    return {"context": ctx, "recent_conversation": convo, "message": text}


# ---------------------------------------------------------------- checking and describing what the model proposed
def _num(v, lo, hi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if lo <= x <= hi else None


def _merchant(db, name):
    key = ledger.merchant_key(name.upper())
    if not key:
        return None, ""
    row = db.one("SELECT * FROM merchants WHERE pattern=?", (key,)) or db.one("SELECT * FROM merchants WHERE pattern LIKE ? ORDER BY length(pattern) LIMIT 1", (key + "%",))
    return row, key


def _ids(v, cap=MAX_IDS):
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        try:
            i = int(str(x).lstrip("Ll"))
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in out:
            out.append(i)
    return out[:cap]


def _txn_rows(db, ids):
    if not ids:
        return []
    return db.q("""SELECT t.id, t.posted_date, t.amount_cents, t.description_raw, t.kind, a.name AS acct FROM transactions t JOIN accounts a ON a.id=t.account_id
                   WHERE t.id IN (%s) ORDER BY t.posted_date, t.id""" % ",".join("?" * len(ids)), tuple(ids))


def _line_rows(db, ids):
    if not ids:
        return []
    return db.q("""SELECT li.id, li.txn_id, li.description, li.amount_cents, t.posted_date, t.description_raw FROM line_items li JOIN transactions t ON t.id=li.txn_id
                   WHERE li.id IN (%s) ORDER BY t.posted_date, li.id""" % ",".join("?" * len(ids)), tuple(ids))


def _list(rows, f, n=3):
    shown = "; ".join(f(r) for r in rows[:n])
    return shown + (f" and {len(rows) - n} more" if len(rows) > n else "")


def _tx(r):
    return f'{r["posted_date"]} "{_tidy(r["description_raw"], 32)}" {money(abs(r["amount_cents"]))}'


def clean(db, raw, now, cur_month):
    """-> list of {type, p, text, ok, why_not}. Anything malformed is dropped; nothing here touches the ledger."""
    out = []
    cats = {r["name"]: r["id"] for r in db.q("SELECT id, name FROM categories")}
    for a in (raw or [])[:MAX_ACTIONS]:
        if not isinstance(a, dict):
            continue
        t = a.get("type")
        e = {"type": t, "p": {}, "ok": True, "why_not": ""}
        try:
            if t == "note":
                txt = str(a.get("text") or "").strip()[:300]
                if not txt:
                    continue
                e["p"] = {"text": txt, "scope": a.get("scope") if a.get("scope") in ("general", "budget", "credits", "receipts") else "general"}
                e["text"] = f'Remember: "{txt}"'
            elif t == "month_adjustment":
                amt, m = _num(a.get("amount"), 0.01, 1_000_000), str(a.get("month") or "")
                if amt is None or not MONTH.match(m) or a.get("kind") not in KINDS:
                    continue
                treat = a.get("treat") if a.get("treat") in TREATS else "exclude"
                cat = a.get("category") if a.get("category") in cats else None
                label = str(a.get("label") or "").strip()[:80]
                e["p"] = {"month": m, "kind": a["kind"], "amount_c": int(round(amt * 100)), "label": label, "treat": treat, "category": cat}
                what = "extra income" if a["kind"] == "extra_income" else "one-off expense" + (f" in {cat}" if cat else "")
                e["text"] = f"{month_label(m)}: {money(e['p']['amount_c'])} {what}{(' (' + label + ')') if label else ''}, {TREAT_WORDS[treat]}. It stays out of your typical numbers."
            elif t == "mark_transfer" and a.get("txn_ids"):
                rows = [r for r in _txn_rows(db, _ids(a.get("txn_ids"))) if r["kind"] == "spend"]
                if not rows:
                    continue
                e["p"] = {"ids": [r["id"] for r in rows]}
                e["text"] = f"Count as transfers, not spending ({len(rows)}): " + _list(rows, _tx) + (f". {str(a.get('why'))[:120]}" if a.get("why") else "")
            elif t == "mark_transfer":
                amt, m = _num(a.get("amount"), 0.01, 1_000_000), str(a.get("month") or "")
                if amt is None or not MONTH.match(m):
                    continue
                c = int(round(amt * 100))
                rows = db.q("""SELECT t.id, t.posted_date, t.description_raw, ac.name FROM transactions t JOIN accounts ac ON ac.id=t.account_id
                               WHERE substr(t.posted_date,1,7)=? AND t.amount_cents=? AND t.kind='spend' ORDER BY t.posted_date LIMIT 4""", (m, c))
                e["p"] = {"month": m, "amount_c": c, "ids": [r["id"] for r in rows]}
                if rows:
                    e["text"] = f"Count as a transfer, not spending: " + "; ".join(f'{r["posted_date"]} {r["name"]} "{r["description_raw"][:40]}" {money(c)}' for r in rows)
                    if len(rows) > 1:
                        e["text"] += " (several match; all would be marked)"
                else:
                    e["ok"], e["why_not"] = False, "no matching charge found"
                    e["text"] = f"Count a {money(c)} charge in {month_label(m)} as a transfer, not spending"
            elif t == "set_txn_category":
                cat = a.get("category")
                rows = [r for r in _txn_rows(db, _ids(a.get("txn_ids"))) if r["kind"] in ("spend", "refund")]
                if cat not in cats or not rows:
                    continue
                e["p"] = {"ids": [r["id"] for r in rows], "category": cat}
                e["text"] = f"Recategorise {len(rows)} charge{'s' if len(rows) != 1 else ''} as {cat}: " + _list(rows, _tx) + (f". {str(a.get('why'))[:120]}" if a.get("why") else "")
            elif t == "set_line_category":
                cat = a.get("category")
                rows = _line_rows(db, _ids(a.get("line_ids")))
                if cat not in cats or not rows:
                    continue
                e["p"] = {"ids": [r["id"] for r in rows], "category": cat}
                e["text"] = f"Recategorise {len(rows)} line{'s' if len(rows) != 1 else ''} as {cat}: " + _list(rows, lambda r: f'{r["posted_date"]} {_tidy(r["description"], 28)} {money(abs(r["amount_cents"]))}') + (f". {str(a.get('why'))[:120]}" if a.get("why") else "")
            elif t == "set_txn_split":
                pct = _num(a.get("my_percent"), 0, 100)
                rows = [r for r in _txn_rows(db, _ids(a.get("txn_ids"))) if r["kind"] in ("spend", "refund")]
                if pct is None or not rows:
                    continue
                e["p"] = {"ids": [r["id"] for r in rows], "bps": int(round(pct * 100))}
                e["text"] = f"Your share is {pct:g}% on {len(rows)} charge{'s' if len(rows) != 1 else ''}: " + _list(rows, _tx) + (f". {str(a.get('why'))[:120]}" if a.get("why") else "")
            elif t == "undo_credit_use":
                ids = _ids(a.get("log_ids"), 20)
                rows = db.q("""SELECT l.id, l.ts, ac.name FROM activity_logs l JOIN activities ac ON ac.id=l.activity_id
                               WHERE l.undone_at IS NULL AND l.id IN (%s) ORDER BY l.ts""" % ",".join("?" * len(ids)), tuple(ids)) if ids else []
                if not rows:
                    continue
                e["p"] = {"ids": [r["id"] for r in rows]}
                e["text"] = f"Give back {len(rows)} credit{'s' if len(rows) != 1 else ''}: " + _list(rows, lambda r: f'{r["name"]} on {r["ts"][:10]}', 5)
            elif t == "set_cut":
                v = _num(a.get("percent"), 0, 90)
                if v is None:
                    continue
                p = spending.plan(db)
                e["p"] = {"percent": v}
                e["text"] = f"Cut everything by {v:g}% (now {p['global']:g}%)" + (f". That would save about {money(int(round(p['avg_total'] * v / 100)))} a month before any category's own change" if p["avg_total"] else "")
            elif t == "set_category_change":
                cat = a.get("category")
                pct = None if a.get("percent") is None else _num(a.get("percent"), -90, 300)
                if cat not in cats or (a.get("percent") is not None and pct is None):
                    continue
                e["p"] = {"category": cat, "percent": pct}
                e["text"] = (f"{cat}: change by {pct:+g}%, whatever the overall cut is" if pct is not None else f"{cat}: follow the overall cut again")
            elif t == "add_category":
                name = str(a.get("name") or "").strip()[:40]
                if not name or name in cats or name.lower() in {c.lower() for c in cats}:
                    continue
                e["p"] = {"name": name, "kind": "fixed" if a.get("kind") == "fixed" else "variable"}
                e["text"] = f'New category "{name}"'
                cats = {**cats, name: -1}
            elif t == "set_pouch":
                name = str(a.get("name") or "").strip()[:60]
                est = _num(a.get("estimate"), 0, 100_000)
                cr = None if a.get("credits") in (None, "") else _num(a.get("credits"), 0, 30)
                if not name or est is None or (a.get("credits") not in (None, "") and cr is None):
                    continue
                cat = a.get("category") if a.get("category") in cats else None
                e["p"] = {"name": name, "estimate_c": int(round(est * 100)), "credits": None if cr is None else int(cr), "category": cat,
                          "icon": pxicons.normalise(a.get("icon")), "archive": bool(a.get("archive"))}
                old = db.one("SELECT * FROM activities WHERE lower(name)=lower(?)", (name,))
                if e["p"]["archive"]:
                    if not old:
                        continue
                    e["text"] = f'Retire the pouch "{old["name"]}"'
                    out.append(e)
                    continue
                how = f'{e["p"]["credits"]} credits a month, set by hand' if cr is not None else "credits follow your spending"
                e["text"] = f'Pouch "{name}": {money(e["p"]["estimate_c"])} each, {how}' + (f" (was {old['credits_per_month']} credits at {money(old['estimate_cents'])} each)" if old else " (new)")
            elif t == "set_split_rule":
                pct = _num(a.get("my_percent"), 0, 100)
                name = str(a.get("merchant") or "").strip()[:60]
                m, key = _merchant(db, name)
                if pct is None or not key:
                    continue
                e["p"] = {"merchant": name, "key": key, "bps": int(round(pct * 100))}
                had = db.one("SELECT my_bps FROM split_rules WHERE merchant_id=?", (m["id"],)) if m else None
                e["text"] = f"{name}: your share is {pct:g}% every time" + (f" (was {had['my_bps'] / 100:g}%)" if had else "")
            elif t == "set_category":
                cat, name = a.get("category"), str(a.get("merchant") or "").strip()[:60]
                m, key = _merchant(db, name)
                if cat not in cats or not key:
                    continue
                e["p"] = {"merchant": name, "key": key, "category": cat}
                n = db.val("""SELECT COUNT(*) FROM transactions t WHERE t.merchant_id=? AND (SELECT COUNT(*) FROM line_items li WHERE li.txn_id=t.id)=1""", (m["id"],), 0) if m else 0
                e["text"] = f"{name} counts as {cat}" + (f", including {n} existing charge{'s' if n != 1 else ''}" if n else "")
            elif t == "set_tip_range":
                v = _num(a.get("max_percent"), 0, 60)
                if v is None:
                    continue
                e["p"] = {"max_percent": int(round(v))}
                e["text"] = f"When matching receipts, allow a bank charge up to {e['p']['max_percent']}% above the receipt total for tips (now {int(db.get_setting('tip_max_pct', TIP_DEFAULT))}%)"
            else:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        out.append(e)
    return out


# ---------------------------------------------------------------- applying one ticked action
def _cid(db, name):
    cid = db.val("SELECT id FROM categories WHERE name=?", (name,))
    if cid is None:
        raise ValueError(f'the category "{name}" does not exist (yet)')
    return cid


def _lines_of(db, ids):
    for m in {r["posted_date"][:7] for r in _txn_rows(db, ids)}:
        ledger.ensure_lines(db, m)
    return [r["id"] for r in db.q("SELECT id FROM line_items WHERE txn_id IN (%s)" % ",".join("?" * len(ids)), tuple(ids))] if ids else []


def apply(db, cfg, e, now):
    t, p = e["type"], e["p"]
    cur = credits.month_of(now, cfg.tz)
    if t == "note":
        db.ex("INSERT INTO warlock_notes(text,scope,created_at) VALUES(?,?,?)", (p["text"], p["scope"], clock.iso(now)))
    elif t == "month_adjustment":
        db.ex("INSERT INTO month_adjustments(month,kind,amount_cents,label,treat,category,created_at) VALUES(?,?,?,?,?,?,?)",
              (p["month"], p["kind"], p["amount_c"], p["label"], p["treat"], p["category"], clock.iso(now)))
    elif t == "mark_transfer":
        ids = [r["id"] for r in db.q("SELECT id FROM transactions WHERE id IN (%s) AND kind='spend'" % ",".join("?" * len(p["ids"])), tuple(p["ids"]))] if p["ids"] else []
        for i in ids:
            db.ex("UPDATE transactions SET kind='transfer' WHERE id=?", (i,))
            db.ex("DELETE FROM line_items WHERE txn_id=?", (i,))
        if not ids:
            raise ValueError("those charges are no longer there to change")
    elif t == "set_cut":
        spending.set_reduce_pct(db, p["percent"])
    elif t == "set_category_change":
        spending.set_mod(db, _cid(db, p["category"]), p["percent"])
    elif t == "add_category":
        db.ex("INSERT OR IGNORE INTO categories(name,kind,sort) VALUES(?,?,?)", (p["name"], p["kind"], db.val("SELECT COALESCE(MAX(sort),0)+1 FROM categories WHERE sort<99", (), 1)))
    elif t == "undo_credit_use":
        n = 0
        for i in p["ids"]:
            n += db.ex("UPDATE activity_logs SET undone_at=? WHERE id=? AND undone_at IS NULL", (clock.iso(now), i)).rowcount
        if not n:
            raise ValueError("those credit uses were already given back")
    elif t == "set_txn_category":
        cid = _cid(db, p["category"])
        for i in _lines_of(db, p["ids"]):
            db.ex("UPDATE line_items SET category_id=? WHERE id=?", (cid, i))
    elif t == "set_line_category":
        cid = _cid(db, p["category"])
        for i in p["ids"]:
            db.ex("UPDATE line_items SET category_id=? WHERE id=?", (cid, i))
    elif t == "set_txn_split":
        for i in _lines_of(db, p["ids"]):
            ledger.decide(db, i, bps=p["bps"], now=now)
    elif t == "set_pouch" and p.get("archive"):
        db.ex("UPDATE activities SET archived=1 WHERE lower(name)=lower(?)", (p["name"],))
    elif t == "set_pouch":
        cid = _cid(db, p["category"]) if p["category"] else None
        a = db.one("SELECT * FROM activities WHERE lower(name)=lower(?)", (p["name"],))
        by_hand = p["credits"] is not None
        if a:
            est = a["estimate_cents"] if a["estimate_locked"] else p["estimate_c"]
            db.ex("""UPDATE activities SET estimate_cents=?, credits_per_month=COALESCE(?, credits_per_month), category_id=COALESCE(?, category_id), icon=?, archived=0,
                     auto_credits=? WHERE id=?""", (est, p["credits"], cid, p["icon"], 0 if by_hand else 1, a["id"]))
            aid = a["id"]
        else:
            aid = db.ex("""INSERT INTO activities(name,icon,phrase,estimate_cents,weight,credits_per_month,cadence,sort,category_id,auto_credits,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (p["name"], p["icon"], p["name"].lower(), p["estimate_c"], 3, p["credits"] or 0, '{"mode": "even", "time": "09:00", "dows": [], "notify": true}',
                         db.val("SELECT COALESCE(MAX(sort),0)+1 FROM activities", (), 1), cid, 0 if by_hand else 1, clock.iso(now))).lastrowid
        credits.build_period(db, cur, db.one("SELECT * FROM activities WHERE id=?", (aid,)), cfg.tz, replace=True)
    elif t in ("set_split_rule", "set_category"):
        m, key = _merchant(db, p["merchant"])
        if m is None:
            mid = db.ex("INSERT INTO merchants(name,pattern,default_category_id) VALUES(?,?,?)", (p["merchant"].title(), key, ledger.hint_category(db, key))).lastrowid
        else:
            mid = m["id"]
        if t == "set_split_rule":
            db.ex("INSERT INTO split_rules(merchant_id,my_bps,created_at) VALUES(?,?,?) ON CONFLICT(merchant_id) DO UPDATE SET my_bps=excluded.my_bps", (mid, p["bps"], clock.iso(now)))
            ledger.resuggest_open(db, cur)
        else:
            cid = _cid(db, p["category"])
            db.ex("UPDATE merchants SET default_category_id=? WHERE id=?", (cid, mid))
            db.ex("""UPDATE line_items SET category_id=? WHERE txn_id IN (SELECT t.id FROM transactions t WHERE t.merchant_id=?
                     AND (SELECT COUNT(*) FROM line_items x WHERE x.txn_id=t.id)=1)""", (cid, mid))
    elif t == "set_tip_range":
        db.set_setting("tip_max_pct", p["max_percent"])
    else:
        raise ValueError("unknown action")
    if t in ("set_cut", "set_category_change", "set_pouch", "month_adjustment", "set_txn_category", "set_line_category", "set_txn_split", "mark_transfer", "set_category", "set_split_rule"):
        spending.sync_purse(db, cfg.tz, now)


# ---------------------------------------------------------------- asking
def ask(db, cfg, text, now, client=None, skip_id=None):
    data = render_prompt(db, cfg, text, now, skip_id)
    reply = llm.call(db, cfg, "warlock", SYSTEM, json.dumps(data, separators=(",", ":")), 8000,
                     "recent charges (descriptions, merchants, amounts, categories), category averages, cuts, pouches + your message; no account numbers", client, now,
                     partial_ok=True)
    cut = False
    try:
        obj = llm._json(reply)
        if not isinstance(obj, dict):
            raise ValueError("not an object")
    except Exception:
        said, kept = _salvage(reply)
        if not said and not kept:
            raise llm.LLMUnavailable("The warlock's answer could not be understood. Nothing was changed. Try saying it again.")
        obj, cut = {"reply": said, "actions": kept}, True
    cur = credits.month_of(now, cfg.tz)
    said = str(obj.get("reply") or "").strip()[:800] or "I have no counsel on that yet."
    if cut:
        said += " My answer ran long, so the last proposals were lost. Ask again for the rest."
    return said, clean(db, obj.get("actions"), now, cur)


def _salvage(text):
    """A reply cut off part-way: keep the reply and every proposal that was written out in full."""
    m = re.search(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    try:
        reply = json.loads('"' + m.group(1) + '"') if m else ""
    except ValueError:
        reply = ""
    actions, i = [], text.find('"actions"')
    j = text.find("[", i) if i >= 0 else -1
    if j >= 0:
        dec, k = json.JSONDecoder(), j + 1
        while True:
            while k < len(text) and text[k] in " \n\r\t,":
                k += 1
            if k >= len(text) or text[k] != "{":
                break
            try:
                obj, k = dec.raw_decode(text, k)
            except ValueError:
                break
            actions.append(obj)
    return reply, actions
