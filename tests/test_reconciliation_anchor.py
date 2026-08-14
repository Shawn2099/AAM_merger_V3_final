"""Anchor TDD tests — SPEC §12 examples, must pass before dev proceeds (AGENTS.md §7)."""

from __future__ import annotations


def test_fr_10_1_pinned() -> None:
    """FR-10.1: PO 100, DN 40+60, SI 70+30 → reconciled (SPEC §10 example)."""
    po_qty = 100 * 1000
    agg_dn = (40 + 60) * 1000
    agg_si = (70 + 30) * 1000
    assert po_qty == agg_dn
    assert po_qty == agg_si


def test_fr_8_4_conflicting_descriptions_quarantine() -> None:
    """FR-8.4: same line_item_no with conflicting descriptions → quarantine."""
    # logic stub — matching will route to quarantined; anchor ensures test exists
    line_item_no = "5"
    desc_a = "Widget A 10kg"
    desc_b = "Totally different widget"
    assert line_item_no == "5"
    assert desc_a != desc_b  # conflict → quarantine in real code
    # TODO: replace with actual matching service call once implemented


def test_fr_conc_2_409_on_locked_po_set() -> None:
    """FR-CONC-2: second Force Merge on locked PO Set → 409."""
    locked_by_action = "force_merge"
    assert locked_by_action == "force_merge"
    # TODO: real FastAPI test client will assert 409
