"""Batch 1 step 7: 'Packet contains customs pages' is worked out from the app's records."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.api.routes.dashboard import _packet_has_customs
from app.core.config import load_config
from app.core.database import get_engine
from app.models import (
    AuditAction,
    AuditLog,
    DocType,
    Document,
    ExtractionStatus,
    POSet,
    POSetStatus,
)
from app.models.base import Base

MERGED_AT = datetime(2026, 10, 25, 10, 0)


@pytest.fixture()
def session(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "test_packet_customs.db"
    Path(cfg.paths.stored_documents_folder).mkdir(parents=True, exist_ok=True)
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _set(s: Session, status=POSetStatus.merged, merged_at=MERGED_AT) -> POSet:
    ps = POSet(po_no_normalized="PO8800", status=status, merged_at=merged_at)
    s.add(ps)
    s.commit()
    s.refresh(ps)
    return ps


def _doc(s: Session, ps: POSet, doc_type: DocType, created_at: datetime, sha: str) -> Document:
    doc = Document(
        sha256_hash=sha,
        original_filename=f"{doc_type.value}.pdf",
        stored_path=f"/tmp/{sha}.pdf",
        doc_type=doc_type,
        extraction_status=ExtractionStatus.valid,
        po_set_id=ps.id,
        created_at=created_at,
    )
    s.add(doc)
    s.commit()
    s.refresh(doc)
    return doc


def test_customs_attached_before_the_merge_is_in_the_packet(session):
    ps = _set(session)
    before = MERGED_AT - timedelta(hours=1)
    doc = _doc(session, ps, DocType.CUSTOMS, before, "1" * 64)
    assert _packet_has_customs(session, ps, [doc]) is True


def test_merged_set_with_no_customs_documents_says_no(session):
    ps = _set(session)
    po = _doc(session, ps, DocType.PO, MERGED_AT - timedelta(hours=2), "2" * 64)
    assert _packet_has_customs(session, ps, [po]) is False


def test_customs_attached_after_the_merge_is_not_in_the_packet(session):
    ps = _set(session)
    after = MERGED_AT + timedelta(hours=1)
    doc = _doc(session, ps, DocType.SHIPPING, after, "3" * 64)
    assert _packet_has_customs(session, ps, [doc]) is False


def test_a_recorded_customs_append_counts_as_in_the_packet(session):
    ps = _set(session)
    session.add(
        AuditLog(
            po_set_id=ps.id,
            action=AuditAction.customs_append,
            detail=json.dumps({"appended": ["CUSTOMS.pdf"]}),
            source="system",
        )
    )
    session.commit()
    assert _packet_has_customs(session, ps, []) is True


def test_non_merged_set_is_never_reported_as_having_a_packet(session):
    ps = _set(session, status=POSetStatus.pending, merged_at=None)
    doc = _doc(session, ps, DocType.CUSTOMS, MERGED_AT - timedelta(hours=1), "4" * 64)
    assert _packet_has_customs(session, ps, [doc]) is False
