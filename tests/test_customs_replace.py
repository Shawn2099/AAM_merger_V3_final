# ruff: noqa: F811
"""Remove a customs / shipping document before Merge Now (D19, docs/SPEC_CUSTOMS_REPLACE.md)."""

from __future__ import annotations

import io
import json
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from sqlalchemy.orm import Session
from test_business_scenarios_ui import _doc, _set, _status, cfg, client  # noqa: F401

from app.core.database import get_engine
from app.models import AuditLog, DocType, Document, POSetStatus
from app.services.reconciliation import reconcile_po_set

LINE = [("1", "BOLT", 100)]


def _ready_set(cfg, client, po: str = "PO-RPL") -> int:
    """PO, DN and SI agree and customs is ON, so the set waits at blocked_customs."""
    pid = _set(cfg, po)
    _doc(cfg, pid, DocType.PO, f"{po}-po.pdf", LINE, po_no=po)
    _doc(cfg, pid, DocType.DN, f"{po}-dn.pdf", LINE, po_no=po)
    _doc(cfg, pid, DocType.SI, f"{po}-si.pdf", LINE, po_no=po, si_no=f"INV-{po}")
    assert client.post(f"/po_sets/{pid}/toggle_customs").status_code == 200
    assert reconcile_po_set(pid, cfg)["status"] == "blocked_customs"
    return pid


def _pdf_bytes(pages: int) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _upload(client, pid: int, name: str, dtype: str, pages: int):
    return client.post(
        f"/po_sets/{pid}/upload",
        files={"file": (name, _pdf_bytes(pages), "application/pdf")},
        data={"doc_type": dtype},
        follow_redirects=False,
    )


def _remove(client, pid: int, doc_id: int):
    return client.post(f"/po_sets/{pid}/documents/{doc_id}/remove_supporting")


def _audits(cfg, pid: int) -> list[AuditLog]:
    with Session(get_engine(cfg)) as s:
        return list(s.query(AuditLog).filter(AuditLog.po_set_id == pid).all())


def test_remove_detaches_and_archives_keeps_file_and_status(cfg, client):
    pid = _ready_set(cfg, client)
    cid = _doc(cfg, pid, DocType.CUSTOMS, "wrong-customs.pdf", [], po_no="PO-RPL", seed="c1")
    _doc(cfg, pid, DocType.SHIPPING, "ship.pdf", [], po_no="PO-RPL", seed="s1")
    with Session(get_engine(cfg)) as s:
        stored = Path(s.get(Document, cid).stored_path)
    before = len(_audits(cfg, pid))

    r = _remove(client, pid, cid)
    assert r.status_code == 200, r.text
    assert r.json()["customs_doc_count"] == 1

    with Session(get_engine(cfg)) as s:
        d = s.get(Document, cid)
        assert d.po_set_id is None
        assert d.archived_at is not None
    assert stored.exists(), "the stored file is never deleted"
    assert _status(cfg, pid).status == POSetStatus.blocked_customs
    assert _status(cfg, pid).customs_doc_count == 1
    new = _audits(cfg, pid)[before:]
    assert len(new) == 1
    detail = json.loads(new[0].detail)
    assert detail["note"] == f"Removed CUSTOMS wrong-customs.pdf (document #{cid})"
    assert "wrong-customs.pdf" not in client.get("/unclassified").text


def test_removed_document_is_not_in_the_packet_and_the_replacement_is(cfg, client):
    pid = _ready_set(cfg, client)
    cid = _doc(cfg, pid, DocType.CUSTOMS, "old.pdf", [], po_no="PO-RPL", seed="c-old")
    _doc(cfg, pid, DocType.SHIPPING, "ship.pdf", [], po_no="PO-RPL", seed="s-ok")
    assert _remove(client, pid, cid).status_code == 200

    # One document short: Merge Now explains and writes nothing.
    held = client.post(f"/po_sets/{pid}/merge").json()
    assert held["status"] == "blocked_customs"

    up = _upload(client, pid, "new.pdf", "CUSTOMS", pages=2)
    assert up.status_code == 302
    merged = client.post(f"/po_sets/{pid}/merge").json()
    assert merged["status"] == "merged", merged
    packet = Path(_status(cfg, pid).merged_output_path)
    # SI + DN + PO + SHIPPING (1 page each) + the 2-page replacement; the old 1-page file is out.
    assert len(PdfReader(str(packet)).pages) == 6


def test_the_same_bytes_can_be_uploaded_again_after_removal(cfg, client):
    pid = _ready_set(cfg, client)
    data = _pdf_bytes(1)
    r = client.post(
        f"/po_sets/{pid}/upload",
        files={"file": ("c.pdf", data, "application/pdf")},
        data={"doc_type": "CUSTOMS"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    with Session(get_engine(cfg)) as s:
        cid = s.query(Document).filter(Document.original_filename == "c.pdf").one().id
    assert _remove(client, pid, cid).status_code == 200
    r = client.post(
        f"/po_sets/{pid}/upload",
        files={"file": ("c.pdf", data, "application/pdf")},
        data={"doc_type": "CUSTOMS"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert _status(cfg, pid).customs_doc_count == 1


def test_a_merged_set_refuses_the_removal(cfg, client):
    pid = _ready_set(cfg, client)
    cid = _doc(cfg, pid, DocType.CUSTOMS, "c.pdf", [], po_no="PO-RPL", seed="m-c")
    _doc(cfg, pid, DocType.SHIPPING, "s.pdf", [], po_no="PO-RPL", seed="m-s")
    assert client.post(f"/po_sets/{pid}/merge").json()["status"] == "merged"
    before = len(_audits(cfg, pid))

    r = _remove(client, pid, cid)
    assert r.status_code == 409
    with Session(get_engine(cfg)) as s:
        d = s.get(Document, cid)
        assert d.po_set_id == pid and d.archived_at is None
    assert len(_audits(cfg, pid)) == before


def test_only_customs_and_shipping_documents_can_be_removed(cfg, client):
    pid = _ready_set(cfg, client)
    with Session(get_engine(cfg)) as s:
        po_doc = s.query(Document).filter(Document.doc_type == DocType.PO).one().id
    assert _remove(client, pid, po_doc).status_code == 422


def test_a_document_of_another_set_is_not_found(cfg, client):
    pid = _ready_set(cfg, client)
    other = _set(cfg, "PO-OTHER")
    oid = _doc(cfg, other, DocType.CUSTOMS, "o.pdf", [], po_no="PO-OTHER", seed="other-c")
    assert _remove(client, pid, oid).status_code == 404
    assert _remove(client, pid, 999999).status_code == 404


def test_a_busy_lock_refuses_and_writes_nothing(cfg, client):
    pid = _ready_set(cfg, client)
    cid = _doc(cfg, pid, DocType.CUSTOMS, "c.pdf", [], po_no="PO-RPL", seed="b-c")
    from app.services.locking import acquire_lock

    with Session(get_engine(cfg)) as s:
        ps = s.get(type(_status(cfg, pid)), pid)
        assert acquire_lock(ps, "merge", s, cfg)
    before = len(_audits(cfg, pid))

    r = _remove(client, pid, cid)
    assert r.status_code == 409
    with Session(get_engine(cfg)) as s:
        d = s.get(Document, cid)
        assert d.po_set_id == pid and d.archived_at is None
    assert len(_audits(cfg, pid)) == before


def test_removal_works_with_the_toggle_off_and_the_set_still_pending(cfg, client):
    pid = _set(cfg, "PO-OFF")
    _doc(cfg, pid, DocType.PO, "po.pdf", LINE, po_no="PO-OFF")
    cid = _doc(cfg, pid, DocType.CUSTOMS, "c.pdf", [], po_no="PO-OFF", seed="off-c")
    assert _status(cfg, pid).has_customs_toggle is False
    r = _remove(client, pid, cid)
    assert r.status_code == 200
    assert _status(cfg, pid).status == POSetStatus.pending


def test_a_removed_document_is_not_in_the_packet_order(cfg, client):
    from app.services.merge import _ordered_docs

    pid = _ready_set(cfg, client)
    cid = _doc(cfg, pid, DocType.CUSTOMS, "c.pdf", [], po_no="PO-RPL", seed="o-c")
    assert _remove(client, pid, cid).status_code == 200
    with Session(get_engine(cfg)) as s:
        ps = s.get(type(_status(cfg, pid)), pid)
        assert cid not in [d.id for d in _ordered_docs(ps, cfg)]


def test_known_limitation_delivered_packet_customs_cannot_be_replaced(cfg, client):
    pid = _ready_set(cfg, client, "PO-DELIV")
    cid = _doc(cfg, pid, DocType.CUSTOMS, "c.pdf", [], po_no="PO-DELIV", seed="d-c")
    _doc(cfg, pid, DocType.SHIPPING, "s.pdf", [], po_no="PO-DELIV", seed="d-s")
    assert client.post(f"/po_sets/{pid}/merge").json()["status"] == "merged"
    assert _remove(client, pid, cid).status_code == 409
