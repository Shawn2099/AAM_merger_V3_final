# ruff: noqa: F811
"""Pinned known limitations from the MVP fix plan (PRODUCT section 8).

Each test asserts the CURRENT behaviour of a deferred item so that a change
to it is deliberate. When the item is fixed, flip the assertion and rename the
test (drop the `known_limitation_` prefix).

Items that need a live Prefect server, a real race window or filesystem fault
injection are documented in PRODUCT section 8 but are not pinned here.
"""

from __future__ import annotations

import contextlib

import httpx
import openai
from sqlalchemy.orm import Session
from test_business_scenarios_ui import _doc, _set, _status, cfg, client  # noqa: F401
from test_extraction_outage import _cfg, _load, _pending_doc

from app.core.config import load_config
from app.core.database import get_engine
from app.models import AuditLog, DocType, Document, ExtractionStatus
from app.services.grouping import resolve_unattached_documents
from app.services.reconciliation import reconcile_po_set
from app.services.sanitizer import parse_quantity_scaled

LINE = [("1", "BOLT", 100)]


def _std(cfg, po: str) -> int:
    pid = _set(cfg, po)
    _doc(cfg, pid, DocType.PO, f"{po}-po.pdf", LINE, po_no=po)
    _doc(cfg, pid, DocType.DN, f"{po}-dn.pdf", LINE, po_no=po)
    _doc(cfg, pid, DocType.SI, f"{po}-si.pdf", LINE, po_no=po, si_no=f"INV-{po}")
    return pid


def _audit_count(cfg, pid: int) -> int:
    with Session(get_engine(cfg)) as s:
        return s.query(AuditLog).filter(AuditLog.po_set_id == pid).count()


# --- M: provider auth/payment faults are not short-circuited -----------------


def _auth_error(status=401):
    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    resp = httpx.Response(status, request=req, json={"error": {"message": "no"}})
    return openai.AuthenticationError("unauthorized", response=resp, body=None)


def test_known_limitation_auth_fault_is_attempted_once_per_document(tmp_path, monkeypatch):
    # No circuit breaker: a 401 (bad key) or 402 (no credit) on every document
    # still costs one provider call per document in the run. Each stays
    # `pending` and uncounted. PRODUCT section 8, provider-fault entries.
    from app.services.extraction import extract_document

    cfg = _cfg(tmp_path, "auth")
    calls: list[int] = []

    def _fail(*a, **kw):
        calls.append(1)
        raise _auth_error(401)

    monkeypatch.setattr("app.services.extraction._call_vlm", _fail)
    ids = [_pending_doc(cfg, tmp_path, f"auth{i:02d}") for i in range(3)]
    for doc_id in ids:
        with contextlib.suppress(Exception):
            extract_document(doc_id, cfg)
    assert len(calls) == 3
    for doc_id in ids:
        status, attempts = _load(cfg, doc_id)[:2]
        assert status == ExtractionStatus.pending
        assert attempts == 0


# --- M: DN-number match has no vendor check ----------------------------------


def test_dn_number_collision_does_not_attach_a_document_with_its_own_po(cfg):
    # Was a pinned limitation (Opus review B3, fixed 2026-10-25): the DN-number sweep
    # attached by number alone, so another vendor's DN with a repeated number pulled a
    # document into an unrelated set, which then quarantined. A document that prints its
    # own PO is left to the PO-based attach sweep.
    anchor = _set(cfg, "PO-REAL")
    dn_id = _doc(cfg, anchor, DocType.DN, "real-dn.pdf", LINE, po_no="PO-REAL")
    with Session(get_engine(cfg)) as s:
        s.get(Document, dn_id).dn_no = "DN-77"
        s.commit()

    from test_mvp_blockers import _loose

    stranger = _loose(cfg, "other-vendor-dn.pdf", DocType.DN, "PO-OTHER", dn_no="DN-77")
    resolve_unattached_documents(cfg)
    with Session(get_engine(cfg)) as s:
        assert s.get(Document, stranger).po_set_id is None


# --- L: missing audit rows ----------------------------------------------------


def test_customs_toggle_writes_an_audit_row(cfg, client):
    # Was a pinned limitation (no audit row); fixed by H1-5. The switch decides whether
    # a packet must contain customs pages, so each flip is recorded.
    import json

    pid = _std(cfg, "NOAUD")
    assert reconcile_po_set(pid, cfg)["status"] == "merged"
    _doc(cfg, pid, DocType.CUSTOMS, "noaud-c.pdf", [], po_no="NOAUD", seed="noaud-c")
    before = _audit_count(cfg, pid)
    assert client.post(f"/po_sets/{pid}/toggle_customs").status_code == 200
    assert client.post(f"/po_sets/{pid}/toggle_customs").status_code == 200
    with Session(get_engine(cfg)) as s:
        rows = (
            s.query(AuditLog).filter(AuditLog.po_set_id == pid).order_by(AuditLog.id.asc()).all()
        )[before:]
    assert [json.loads(r.detail)["customs_check"] for r in rows] == [True, False]
    assert [json.loads(r.detail)["note"] for r in rows] == [
        "Customs check switched ON",
        "Customs check switched OFF",
    ]
    assert {r.source for r in rows} == {"operator"}


def test_a_refused_customs_toggle_writes_no_audit_row(cfg, client):
    pid = _std(cfg, "NOAUD2")
    with Session(get_engine(cfg)) as s:
        from app.models import POSet

        ps = s.get(POSet, pid)
        from datetime import UTC, datetime

        ps.archived_at = datetime.now(UTC)
        s.commit()
    before = _audit_count(cfg, pid)
    assert client.post(f"/po_sets/{pid}/toggle_customs").status_code >= 400
    assert _audit_count(cfg, pid) == before


# --- L: allowed_hosts defaults to any host -------------------------------------


def test_known_limitation_allowed_hosts_defaults_to_any_host():
    # `["*"]` is the shipped default so a LAN server behind a changing IP works.
    # It leaves DNS-rebinding reads open until real names are listed.
    cfg = load_config("config.example.yaml")
    assert cfg.server.allowed_hosts == ["*"]


# --- L: sanitizer accepts exponent and underscore forms ----------------------


def test_known_limitation_sanitizer_accepts_exponent_and_underscore_forms():
    # babel's strict parse accepts these; they are read as plain numbers
    # (1e2 -> 100, 1_000 -> 1000), scaled x1000 as usual.
    assert parse_quantity_scaled("1e2") == 100 * 1000
    assert parse_quantity_scaled("1_000") == 1000 * 1000
