"""Soundness property over the real engine: `reconcile_po_set`.

Random PO Sets go through the true persistence path (tmp SQLite + real
stored PDFs + real merge). The verdict is then checked by INDEPENDENTLY
recomputing the sums from the database — the test never reuses the
engine's own totals. For every generated set, exactly one holds:

- merged ⟹ every PO group's DN sum AND SI sum equal it (recomputed raw),
  every stored quantity is positive, and the packet file exists;
- mismatched ⟹ a quantity flag with vendor != PO exists;
- quarantined ⟹ a non-empty reason is reported and persisted;
- pending ⟹ the reason is a missing-side or partial-fulfillment code;
- the call never raises.

No VLM, no Prefect server. tmp_path is shared across examples, so every
artifact carries a per-example tag (unique DB, hashes, invoice names).
"""

from __future__ import annotations

from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import DocType, Document, ExtractionStatus, LineItem, POSet, POSetStatus
from app.models.base import Base
from app.services.matching import normalize_line_no
from app.services.reconciliation import reconcile_po_set

SETTINGS = settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])

MODES = [
    "exact_single",
    "exact_split",
    "short",
    "over",
    "drop",
    "split_pools",
    "alien",
    "zero",
    "nopoline",
    "nodoc",
    "drift",
    "twopo",
]


def _pdf(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    w = PdfWriter()
    w.add_blank_page(width=100, height=100)
    with open(path, "wb") as f:
        w.write(f)


@SETTINGS
@given(st.data())
def test_prop_reconcile_verdict_is_sound(data):
    import tempfile

    base = Path(tempfile.mkdtemp(prefix="sndprop_"))
    tag = data.draw(st.integers(min_value=0, max_value=10**9))
    mode = data.draw(st.sampled_from(MODES))
    n_lines = data.draw(st.integers(min_value=1, max_value=3))
    nos = [str(i + 1) for i in range(n_lines)]
    qtys = [data.draw(st.integers(min_value=5, max_value=200)) for _ in nos]
    po_no = f"PO-SND-{tag}"

    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = str(base / f"snd{tag}.db")
    for sub in ("input", "output", "quarantine", "stored", "logs"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    def add_doc(s, ps_id, dtype, lines, tag2, **extra):
        name = f"{tag}_{tag2}_{dtype}.pdf"
        p = base / "stored" / name
        _pdf(p)
        d = Document(
            sha256_hash=f"snd{tag}_{tag2}_{dtype}",
            original_filename=name,
            stored_path=str(p),
            doc_type=DocType(dtype),
            extraction_status=ExtractionStatus.valid,
            po_set_id=ps_id,
            po_no_normalized=extra.pop("po_key", po_no),
            **extra,
        )
        s.add(d)
        s.commit()
        for no, q, desc in lines:
            s.add(
                LineItem(
                    document_id=d.id,
                    line_item_no=no,
                    description=desc,
                    quantity=q,
                    unit_price=1000,
                )
            )
        s.commit()
        return d

    def full(qty_map, desc="Widget"):
        return [(no, qty_map[no], desc) for no in nos]

    qty_by_no = dict(zip(nos, qtys, strict=True))
    with Session(eng) as s:
        ps = POSet(po_no_normalized=po_no, status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        ps_id = ps.id

        po_lines = full(qty_by_no)
        if mode == "nopoline":
            po_lines = [*po_lines, (None, 50, "Mystery")]
        add_doc(s, ps_id, "PO", po_lines, "po")
        if mode == "twopo":
            add_doc(s, ps_id, "PO", full(qty_by_no), "po2")

        dn_lines, si_lines = full(qty_by_no), full(qty_by_no)
        if mode == "short":
            no = data.draw(st.sampled_from(nos))
            dn_lines = [(n, q - 1 if n == no else q, "Widget") for n, q, _ in dn_lines]
            si_lines = [(n, q - 1 if n == no else q, "Widget") for n, q, _ in si_lines]
        elif mode == "over":
            no = data.draw(st.sampled_from(nos))
            dn_lines = [(n, q + 1 if n == no else q, "Widget") for n, q, _ in dn_lines]
            si_lines = [(n, q + 1 if n == no else q, "Widget") for n, q, _ in si_lines]
        elif mode == "drop":
            no = data.draw(st.sampled_from(nos))
            dn_lines = [r for r in dn_lines if r[0] != no]
            si_lines = [r for r in si_lines if r[0] != no]
        elif mode == "split_pools":
            # DN reports the line, SI never heard of it: a real disagreement
            # about this line, not an outstanding delivery.
            no = data.draw(st.sampled_from(nos))
            si_lines = [r for r in si_lines if r[0] != no]
        elif mode == "alien":
            dn_lines = [*dn_lines, ("99", 10, "Stowaway")]
        elif mode == "zero":
            no = data.draw(st.sampled_from(nos))
            dn_lines = [(n, 0 if n == no else q, "Widget") for n, q, _ in dn_lines]

        if mode == "exact_split":
            # partition each PO line across two DN docs
            d1, d2 = [], []
            for no, q, desc in dn_lines:
                half = q // 2
                d1.append((no, half, desc))
                d2.append((no, q - half, desc))
            add_doc(s, ps_id, "DN", d1, "dn1")
            add_doc(s, ps_id, "DN", d2, "dn2")
        elif mode != "nodoc":
            drift_no = "PO-ELSEWHERE" if mode == "drift" else po_no
            add_doc(s, ps_id, "DN", dn_lines, "dn", po_key=drift_no)
        if mode != "nodoc":
            add_doc(s, ps_id, "SI", si_lines, "si", si_no=f"INV-{tag}", invoice_no=f"INV-{tag}")

    res = reconcile_po_set(ps_id, cfg)  # must never raise
    assert res["status"] in {"pending", "mismatched", "quarantined", "blocked_customs", "merged"}

    with Session(eng) as s:
        ps = s.get(POSet, ps_id)
        assert ps.reconcile_reason, "every verdict leaves a readable reason"
        docs = list(ps.documents or [])
        by_type: dict[str, list] = {}
        for d in docs:
            by_type.setdefault(d.doc_type.value, []).extend(list(d.line_items or []))

        def sums(items):
            out: dict[str, int] = {}
            for li in items:
                key = normalize_line_no(li.line_item_no)
                if key:
                    out[key] = out.get(key, 0) + li.quantity
            return out

        po_sums = sums(by_type.get("PO", []))
        dn_sums = sums(by_type.get("DN", []))
        si_sums = sums(by_type.get("SI", []))

        if res["status"] == "merged":
            assert mode in {"exact_single", "exact_split"}, f"merged from {mode}"
            for key, pq in po_sums.items():
                assert dn_sums.get(key) == pq, f"DN sum unproven for {key}"
                assert si_sums.get(key) == pq, f"SI sum unproven for {key}"
            for li in [x for items in by_type.values() for x in items]:
                assert li.quantity > 0
            assert ps.merged_output_path and Path(ps.merged_output_path).exists()
        elif res["status"] == "mismatched":
            assert any(
                f.get("type") == "quantity" and f.get("vendor_quantity") != f.get("po_quantity")
                for f in res["flags"]
            ), "mismatched without quantity evidence"
            assert ps.merged_output_path is None
        elif res["status"] == "quarantined":
            assert res.get("reason"), "quarantine without a reason code"
            assert ps.merged_output_path is None
        elif res["status"] == "pending":
            assert res.get("reason") in {
                "missing_po_document",
                "missing_dn_document",
                "missing_si_document",
                "partial_fulfillment",
            }, res.get("reason")
            assert ps.merged_output_path is None
