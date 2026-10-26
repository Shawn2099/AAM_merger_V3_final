"""Tests for score.py using a tiny synthetic gold/results pair."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import score as sc  # noqa: E402


def _pred(**kw):
    base = {
        "document_type": "SI",
        "document_number": "SIV-1",
        "po_reference": "210851",
        "line_items": [{"line_item_no": "1", "quantity": "50"}],
    }
    base.update(kw)
    return base


GOLD_SI = {
    "document_type": "SI",
    "document_number": "SIV-1",
    "po_reference": "PO-210851",
    "lines": [{"line_item_no": "1", "quantity": "50"}],
}


def test_all_correct_with_po_normalisation():
    r = sc.score_run(GOLD_SI, _pred(po_reference="PO 210851"))
    assert r["pass"]
    assert set(r["fields"].values()) == {"correct"}


def test_wrong_vs_null_are_distinct():
    r = sc.score_run(GOLD_SI, _pred(po_reference="999", document_number=None))
    assert r["fields"]["po_reference"] == "wrong"
    assert r["fields"]["document_number"] == "null"
    assert not r["pass"]


def test_quantity_scaled_and_aggregated():
    gold = {"lines": [{"line_item_no": "01", "quantity": "10"}, {"line_item_no": "2", "quantity": 5}]}
    pred = {
        "line_items": [
            {"line_item_no": "1", "quantity": "4"},
            {"line_item_no": "1", "quantity": "6.0"},
            {"line_item_no": "2", "quantity": "6"},
        ]
    }
    r = sc.score_run(gold, pred)
    assert r["lines"] == {"1": "correct", "2": "wrong"}
    assert r["fields"]["lines"] == "wrong"


def test_missing_and_unreadable_lines_are_null_extra_is_wrong():
    gold = {"lines": [{"line_item_no": "1", "quantity": "1"}, {"line_item_no": "2", "quantity": "2"}]}
    r = sc.score_run(gold, {"line_items": [{"line_item_no": "1", "quantity": None}]})
    assert r["lines"] == {"1": "null", "2": "null"}
    assert r["fields"]["lines"] == "null"
    r2 = sc.score_run(
        {"lines": [{"line_item_no": "1", "quantity": "1"}]},
        {"line_items": [{"line_item_no": "1", "quantity": "1"}, {"line_item_no": "9", "quantity": "3"}]},
    )
    assert r2["extra_lines"] == ["9"] and not r2["pass"]


def test_gold_null_means_must_be_null_and_absent_not_scored():
    gold = {"document_type": "PO", "document_number": "210851", "po_reference": None}
    ok = sc.score_run(gold, {"document_type": "PO", "document_number": "PO 210851", "po_reference": None})
    assert ok["pass"] and "lines" not in ok["fields"]
    bad = sc.score_run(gold, {"document_type": "PO", "document_number": "210851", "po_reference": "5"})
    assert bad["fields"]["po_reference"] == "wrong"


def test_failed_call_is_all_null_and_fails():
    r = sc.score_run(GOLD_SI, None)
    assert not r["pass"]
    assert set(r["fields"].values()) == {"null"}


def test_end_to_end_two_labels_and_stability(tmp_path):
    gold = {"1": GOLD_SI, "2": GOLD_SI}
    for label, p2 in (("a", _pred(po_reference="999")), ("b", _pred())):
        d = tmp_path / label
        d.mkdir()
        runs1 = [{"ok": True, "result": _pred()}, {"ok": True, "result": _pred(po_reference="1")}]
        (d / "1.json").write_text(json.dumps({"doc_id": 1, "runs": runs1}))
        (d / "2.json").write_text(json.dumps({"doc_id": 2, "runs": [{"ok": True, "result": p2}]}))
    sa = sc.score_label(gold, sc.load_results("a", tmp_path))
    sb = sc.score_label(gold, sc.load_results("b", tmp_path))
    assert sa["docs"][1]["stable"] is False
    assert sa["field_counts"]["po_reference"]["wrong"] == 2  # doc1 rep1 + doc2
    assert sb["field_counts"]["po_reference"]["wrong"] == 1
    out = sc.render(["a", "b"], [sa, sb], verbose=True)
    assert "PRECISION" in out and "UNSTABLE" in out and "B better" in out


def test_ambiguity_held_wrong_missed_and_false_flag():
    gold = {"document_type": "DN", "po_reference": None, "ambiguous": True}
    held = sc.score_run(gold, {"document_type": "DN", "po_reference": "123", "po_reference_ambiguous": True})
    assert held["ambiguity"] == "held" and held["fields"]["po_reference"] == "correct"
    wrong = sc.score_run(gold, {"document_type": "DN", "po_reference": "123"})
    assert wrong["ambiguity"] == "wrong" and wrong["fields"]["po_reference"] == "wrong"
    missed = sc.score_run(gold, {"document_type": "DN", "po_reference": None})
    assert missed["ambiguity"] == "missed" and missed["fields"]["po_reference"] == "null"
    plain = sc.score_run(GOLD_SI, _pred())
    assert plain["ambiguity"] is None


def test_false_ambiguity_flag_fails_doc_and_is_counted():
    r = sc.score_run(GOLD_SI, _pred(po_reference_ambiguous=True))
    assert r["ambiguity"] == "false_flag" and not r["pass"]
    gold = {"1": GOLD_SI}
    s = sc.score_label(gold, {1: [_pred(po_reference_ambiguous=True)]})
    assert s["ambiguity"]["false_flag"] == 1
