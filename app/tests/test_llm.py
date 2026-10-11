import json
from datetime import date
from types import SimpleNamespace as NS
import pytest
import synth
from finapp import llm
from finapp.statements import pdftext


class Fake:
    def __init__(self, text, tin=1000, tout=300, stop="end_turn"):
        self.calls = []
        self.messages = self
        self.r = NS(content=[NS(type="text", text=text)], usage=NS(input_tokens=tin, output_tokens=tout), stop_reason=stop)

    def create(self, **kw):
        self.calls.append(kw)
        return self.r


def with_key(cfg):
    cfg.anthropic_key = "sk-test"
    return cfg


def test_no_key_means_unavailable(db, cfg):
    with pytest.raises(llm.LLMUnavailable):
        llm.call(db, cfg, "t", "s", "u")


def test_cost_is_logged_from_usage(db, cfg):
    with_key(cfg)
    f = Fake("hi", tin=10000, tout=1000)
    assert llm.call(db, cfg, "t", "s", "u", client=f) == "hi"
    cost = db.val("SELECT cost_cents FROM llm_audit")
    assert cost == pytest.approx(10000 * 300 / 1e6 + 1000 * 1500 / 1e6)
    assert llm.status(db, cfg)["spent_cents"] == pytest.approx(cost, abs=0.01)


def test_cap_blocks_before_calling(db, cfg):
    with_key(cfg)
    db.ex("INSERT INTO llm_audit(ts,purpose,cost_cents,ok) VALUES(?,?,?,1)", (llm.month_prefix() + "-01T00:00:00Z", "x", 499.9))
    f = Fake("hi")
    with pytest.raises(llm.LLMUnavailable, match="cap"):
        llm.call(db, cfg, "t", "s", "u", client=f)
    assert f.calls == []


def test_kill_switch(db, cfg):
    with_key(cfg)
    db.set_setting("llm_enabled", "0")
    f = Fake("hi")
    with pytest.raises(llm.LLMUnavailable, match="switched off"):
        llm.call(db, cfg, "t", "s", "u", client=f)
    assert f.calls == []


def test_refusal_is_reported_not_returned(db, cfg):
    with_key(cfg)
    with pytest.raises(llm.LLMUnavailable, match="declined"):
        llm.call(db, cfg, "t", "s", "u", client=Fake("", stop="refusal"))


def test_refine_accepts_only_specs_that_parse_real_lines(db, cfg):
    with_key(cfg)
    pdf = synth.chequing_pdf(synth.gen(10, date(2026, 9, 1), 29, 5), 500000)
    lines = pdftext.extract_lines(pdf)
    good, _ = pdftext.draft(lines, "chequing")
    excerpt = pdftext.mask_excerpt(lines)
    spec, parsed = llm.refine_pdf_template(db, cfg, excerpt, {}, lines, "chequing", client=Fake("Here: " + json.dumps(good)))
    assert len(parsed.txns) == 10
    bad = dict(good, rows=dict(good["rows"], regex=r"^NOPE$"))
    with pytest.raises(llm.LLMUnavailable, match="no transactions"):
        llm.refine_pdf_template(db, cfg, excerpt, {}, lines, "chequing", client=Fake(json.dumps(bad)))
    with pytest.raises(llm.LLMUnavailable, match="did not work"):
        llm.refine_pdf_template(db, cfg, excerpt, {}, lines, "chequing", client=Fake("not json"))


def test_only_the_masked_excerpt_reaches_the_model(db, cfg):
    with_key(cfg)
    pdf = synth.chequing_pdf(synth.gen(10, date(2026, 9, 1), 29, 5), 500000)
    lines = pdftext.extract_lines(pdf)
    good, _ = pdftext.draft(lines, "chequing")
    f = Fake(json.dumps(good))
    llm.refine_pdf_template(db, cfg, pdftext.mask_excerpt(lines), good, lines, "chequing", client=f)
    sent = f.calls[0]["messages"][0]["content"]
    assert "ACME" not in sent and "8901" not in sent


def test_receipt_vision_reading_parses_json_and_costs_are_counted(db, cfg, tmp_path):
    from finapp import receipts
    from test_receipts import photo, TEXT
    with_key(cfg)
    reply = json.dumps({"merchant": "Grocery Mart", "date": "2026-09-12", "subtotal": 24.76, "tax": 2.34, "total": 27.10,
                        "lines": [{"description": "Milk", "amount": 4.49}, {"description": "Bread", "amount": 3.29}]})
    f = Fake("Sure: " + reply, tin=2500, tout=200)
    rid, _ = receipts.add(db, cfg, "r.jpg", photo(TEXT))
    receipts.read_with_model(db, cfg, rid, client=f)
    r = db.one("SELECT * FROM receipts WHERE id=?", (rid,))
    assert r["total_cents"] == 2710 and r["merchant"] == "Grocery Mart" and r["ocr_engine"] == "model"
    assert db.val("SELECT COUNT(*) FROM receipt_lines WHERE receipt_id=?", (rid,)) == 2
    assert f.calls[0]["messages"][0]["content"][0]["type"] == "image"
    assert db.val("SELECT purpose FROM llm_audit") == "receipt_vision"
    with pytest.raises(llm.LLMUnavailable):
        receipts.read_with_model(db, cfg, rid, client=Fake("no json at all"))
