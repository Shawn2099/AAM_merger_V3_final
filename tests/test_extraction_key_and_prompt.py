"""PO set-key choice in the VLM raw-dict builder, and the prompt additions.

Runs the REAL `_call_vlm` and only fakes the network (`instructor.from_openai`),
the same way tests/test_extraction.py does, so the key mapping is exercised
exactly as production runs it.
"""

from __future__ import annotations


def _run_call_vlm(tmp_path, monkeypatch, **fields):
    from pypdf import PdfWriter

    import app.services.extraction as ext
    from app.core.config import load_config

    pdf = tmp_path / "doc.pdf"
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.write(str(pdf))

    response = ext._VLMPageExtraction(
        po_reference_ambiguous=False,
        vendor_name="ACME TRADING",
        line_items=[],
        **fields,
    )

    class FakeCompletions:
        def create(self, **kwargs):
            return response

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(tmp_path / "key.db")

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setattr(ext.instructor, "from_openai", lambda *a, **kw: FakeClient())
    return ext._call_vlm(str(pdf), fields.get("document_type", "PO"), cfg)


def test_po_keys_on_its_own_number_over_a_quotation_reference(tmp_path, monkeypatch):
    out = _run_call_vlm(
        tmp_path,
        monkeypatch,
        document_type="PO",
        document_number="210851",
        po_reference="QTN-77",
    )
    assert out["po_no_raw"] == "210851"


def test_po_falls_back_to_po_reference_when_no_own_number(tmp_path, monkeypatch):
    out = _run_call_vlm(
        tmp_path,
        monkeypatch,
        document_type="PO",
        document_number=None,
        po_reference="210851",
    )
    assert out["po_no_raw"] == "210851"


def test_dn_keys_on_po_reference_not_its_own_number(tmp_path, monkeypatch):
    out = _run_call_vlm(
        tmp_path,
        monkeypatch,
        document_type="DN",
        document_number="GDN-1",
        po_reference="210851",
    )
    assert out["po_no_raw"] == "210851"


def test_si_never_takes_document_number_as_its_po_key(tmp_path, monkeypatch):
    out = _run_call_vlm(
        tmp_path,
        monkeypatch,
        document_type="SI",
        document_number="SIV-ARS-26-4005",
        po_reference="210851",
    )
    assert out["po_no_raw"] == "210851"

    out = _run_call_vlm(
        tmp_path,
        monkeypatch,
        document_type="SI",
        document_number="SIV-ARS-26-4005",
        po_reference=None,
    )
    assert out["po_no_raw"] is None


def test_prompt_pins_delivered_quantity_column_rule():
    import app.services.extraction as ext

    prompt = ext._PAGE_PROMPT
    assert "DELIVERED" in prompt
    assert "quantity null" in prompt
    assert "NEVER use Ordered, Balance" in prompt


def test_prompt_pins_freight_exclusion_and_po_own_number_rule():
    import app.services.extraction as ext

    prompt = ext._PAGE_PROMPT
    assert "freight" in prompt
    assert "carried forward" in prompt
    assert "quotation, RFQ, requisition or indent number" in prompt


def test_prompt_delivered_column_rule_is_scoped_to_dn_and_si():
    # A PO that prints Ordered and Delivered-to-date columns must keep the ORDERED
    # quantity; applying the delivered rule to a PO would make a partial delivery
    # the baseline and merge it.
    import app.services.extraction as ext

    prompt = ext._PAGE_PROMPT
    assert "QUANTITY COLUMN, PO: quantity is the ORDERED quantity" in prompt
    assert "QUANTITY COLUMN, DN and SI only" in prompt
    assert "never drop the row" in prompt
