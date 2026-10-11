"""The only door to Anthropic. Every call is budget-checked, logged, and refusable by a kill switch."""
import hashlib
import json
import re

from . import clock

SYSTEM_TEMPLATE = """You help a household finance app read bank statements. You are given a MASKED excerpt of a PDF's \
text: every digit is shown as 9, transaction descriptions are shown as x's. You will also get the app's current best-guess \
template, which failed or looks wrong. Return ONLY a JSON object: a corrected template using exactly these keys.
identify: {"all": [literal heading strings that appear on every statement from this bank], "any": []}
period: {"regex": python regex with named groups start and end, "formats": [strptime formats]}
account: {"regex": python regex whose single group captures the account or card number}
opening: {"regex": python regex whose named group v captures the opening / previous balance amount}
closing: {"regex": python regex whose named group v captures the closing / new balance amount}
rows: {"regex": python regex with named groups date, optional date2, desc, amount, optional balance, anchored ^ and $,
       "date_formats": [strptime formats], "style": one of balance_delta | signed_positive_out | signed_negative_out,
       "continuation": true, "skip": [regexes for lines to ignore]}
Because the excerpt is masked, write regexes that match the shape (use \\d for digits), never the masked 9s. \
Keep regexes short. Do not include any text outside the JSON."""


class LLMUnavailable(Exception):
    pass


def month_prefix(now=None):
    return clock.iso(now or clock.now())[:7]


def spent_cents(db, now=None):
    return float(db.val("SELECT COALESCE(SUM(cost_cents),0) FROM llm_audit WHERE ts LIKE ?", (month_prefix(now) + "%",), 0) or 0)


def status(db, cfg, now=None):
    killed = db.get_setting("llm_enabled", "1") == "0"
    spent = spent_cents(db, now)
    return {"key_present": bool(cfg.anthropic_key), "enabled": bool(cfg.anthropic_key) and not killed, "killed": killed,
            "spent_cents": round(spent, 2), "cap_cents": cfg.llm_cap_cents, "remaining_cents": max(0.0, cfg.llm_cap_cents - spent),
            "model": cfg.llm_model}


def _cost(cfg, tin, tout):
    return tin * cfg.llm_in_cents_per_mtok / 1e6 + tout * cfg.llm_out_cents_per_mtok / 1e6


def _client(cfg):
    import anthropic
    return anthropic.Anthropic(api_key=cfg.anthropic_key, max_retries=1, timeout=60.0)


def call(db, cfg, purpose, system, content, max_tokens=1500, redaction="", client=None, now=None, extra_in_tokens=0, partial_ok=False):
    """Returns the model's text. Raises LLMUnavailable with a user-readable reason when it must not or cannot run."""
    s = status(db, cfg, now)
    if not s["key_present"]:
        raise LLMUnavailable("No Anthropic key is configured.")
    if s["killed"]:
        raise LLMUnavailable("The Warlock is switched off in Settings.")
    payload = content if isinstance(content, str) else json.dumps(
        [b if b.get("type") == "text" else {"type": b.get("type")} for b in content])
    est_in = (len(system) + len(payload)) / 3 + 200 + extra_in_tokens
    worst = _cost(cfg, est_in, max_tokens)
    if s["spent_cents"] + worst > cfg.llm_cap_cents:
        raise LLMUnavailable(f"This would pass the monthly cap ({cfg.llm_cap_cents / 100:.2f} CAD-equivalent budget; "
                             f"{s['spent_cents'] / 100:.2f} used).")
    sha = hashlib.sha256((system + payload).encode()).hexdigest()
    ts = clock.iso(now or clock.now())
    msgs = [{"role": "user", "content": content}]
    try:
        resp = (client or _client(cfg)).messages.create(model=cfg.llm_model, max_tokens=max_tokens, system=system, messages=msgs)
    except Exception as e:
        db.ex("INSERT INTO llm_audit(ts,purpose,payload_sha256,redaction,bytes,cost_cents,model,ok) VALUES(?,?,?,?,?,?,?,0)",
              (ts, purpose, sha, redaction, len(payload), 0, cfg.llm_model))
        raise LLMUnavailable(f"The Warlock could not be reached ({type(e).__name__}).")
    u = getattr(resp, "usage", None)
    cost = _cost(cfg, getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0)
    refused = getattr(resp, "stop_reason", None) == "refusal"
    cut_off = getattr(resp, "stop_reason", None) == "max_tokens"
    db.ex("INSERT INTO llm_audit(ts,purpose,payload_sha256,redaction,bytes,cost_cents,model,ok) VALUES(?,?,?,?,?,?,?,?)",
          (ts, purpose, sha, redaction, len(payload), cost, cfg.llm_model, 0 if refused else 1))
    if refused:
        raise LLMUnavailable("The Warlock declined to answer this one.")
    if cut_off and not partial_ok:
        raise LLMUnavailable("The Warlock's reply was cut off before it finished. Nothing was changed. Try again.")
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def _json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON in reply")
    return json.loads(m.group(0))


def refine_pdf_template(db, cfg, excerpt, current_spec, lines, account_type, client=None, now=None):
    """Ask for a better template from the masked excerpt the owner has just seen. The reply is only accepted if it
    actually parses the real (unmasked) lines into transactions, locally."""
    from .statements import pdftext
    user = f"Account type: {account_type}\n\nMasked excerpt:\n{excerpt}\n\nCurrent template:\n{json.dumps(current_spec)}"
    text = call(db, cfg, "template_refine", SYSTEM_TEMPLATE, user, 4000, "digits->9, descriptions->x", client, now)
    try:
        spec = _json(text)
        spec["kind"] = "pdf-text"
        spec["account_type"] = account_type
        parsed = pdftext.parse(lines, spec, account_type)
    except Exception as e:
        raise LLMUnavailable(f"The Warlock's suggestion did not work on your statement ({type(e).__name__}). Nothing was changed.")
    if not parsed.txns:
        raise LLMUnavailable("The Warlock's suggestion found no transactions in your statement. Nothing was changed.")
    spec, fixed = pdftext.fix_polarity(spec, lines, account_type)
    if fixed:
        parsed = pdftext.parse(lines, spec, account_type)
        parsed.notes.append(fixed)
    return spec, parsed


SYSTEM_RECEIPT = """You read shopping receipts for a household finance app. Return ONLY a JSON object:
{"merchant": string, "date": "YYYY-MM-DD" or null, "subtotal": number or null, "tax": number or null, "total": number or null,
 "lines": [{"description": string, "amount": number}]}
Amounts are in dollars, positive for charges and negative for discounts. List every purchased item with its final line price. \
Do not include the tax, subtotal, total, payment or change lines among the items. Do not invent anything you cannot read."""


def read_receipt(db, cfg, jpeg_bytes, client=None, now=None):
    """Whole-picture reading by the model. The caller has obtained the owner's consent for the image to leave this machine."""
    import base64
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(jpeg_bytes).decode()}},
               {"type": "text", "text": "Read this receipt."}]
    text = call(db, cfg, "receipt_vision", SYSTEM_RECEIPT, content, 1500, "full image", client, now, extra_in_tokens=2000)
    try:
        d = _json(text)

        def c(v):
            return None if v is None else round(float(v) * 100)
        lines = [(str(x["description"])[:80], c(x["amount"])) for x in d.get("lines", []) if x.get("description") and x.get("amount") is not None]
        return {"merchant": str(d.get("merchant") or "")[:60], "date": d.get("date"), "subtotal": c(d.get("subtotal")),
                "tax": c(d.get("tax")), "total": c(d.get("total")), "lines": lines}
    except Exception as e:
        raise LLMUnavailable(f"The Warlock's reading could not be understood ({type(e).__name__}). Nothing was changed.")
