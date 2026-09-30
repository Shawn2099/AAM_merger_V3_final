"""Models — the four tables of record. See AAM_merger_V3_PRODUCT.md.

`line_items` deliberately carries no `part_no` and no UOM column: neither is a
matching input in this product, and both were blank or unreliable across every
real vendor sample reviewed. Do not add them back without a product decision.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class DocType(enum.StrEnum):
    PO = "PO"
    DN = "DN"
    SI = "SI"
    COMBINED = "COMBINED"
    CUSTOMS = "CUSTOMS"
    SHIPPING = "SHIPPING"
    UNKNOWN = "UNKNOWN"


class ExtractionStatus(enum.StrEnum):
    pending = "pending"
    processing = "processing"
    valid = "valid"
    failed = "failed"


class POSetStatus(enum.StrEnum):
    pending = "pending"
    mismatched = "mismatched"
    quarantined = "quarantined"
    blocked_customs = "blocked_customs"
    merged = "merged"


class AuditAction(enum.StrEnum):
    force_merge = "force_merge"
    quarantine_delete = "quarantine_delete"
    manual_status_change = "manual_status_change"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256_hash: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    doc_type: Mapped[DocType] = mapped_column(Enum(DocType), nullable=False)
    raw_extraction_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    po_no_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    po_no_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True when the VLM saw more than one distinct PO number on this document.
    # The attach sweeps refuse to guess from such a document (see grouping);
    # it must resolve to a single PO before it may join a set.
    po_reference_ambiguous: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    dn_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    si_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    invoice_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_status: Mapped[ExtractionStatus] = mapped_column(
        Enum(ExtractionStatus), default=ExtractionStatus.pending, nullable=False
    )
    extraction_attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    po_set_id: Mapped[int | None] = mapped_column(ForeignKey("po_sets.id"), nullable=True)
    # Multi-doc split (Layer 1): one child row per logical document found in a
    # single PDF. Children point at their source file's row; the parent row is
    # sterile (no po_no, no po_set) and is excluded from every set-building
    # query. Hierarchy vocabulary only — no Layer-2 code names a document type
    # to use these. See AAM_merger_V3_PLAN.md Step 1.
    parent_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id"), nullable=True
    )
    is_split_parent: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    # Crash-recovery authority for the Layer-1 split (PLAN Rev 3 §6.7). Set when
    # child files have been cut; absent means children must be re-derived from
    # the parent SHA, never trusted to exist.
    split_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    po_set: Mapped[POSet | None] = relationship(back_populates="documents")
    line_items: Mapped[list[LineItem]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_documents_po_no_normalized", "po_no_normalized"),
        Index("ix_documents_parent_document_id", "parent_document_id"),
    )


class LineItem(Base):
    __tablename__ = "line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False)
    line_item_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)  # scaled x1000
    unit_price: Mapped[int] = mapped_column(Integer, nullable=False)  # scaled x1000
    # Delivery-note reference printed against this individual line. Some vendors
    # print it per line, others only once in the header, so this is nullable.
    # Read-only in Layer 2 by grouping._anchor_from_po_line_ref (attach path:
    # an unattached DN inherits the PO Set whose PO rows name its number).
    # Never a matching or reconciliation key.
    dn_no: Mapped[str | None] = mapped_column(Text, nullable=True)

    document: Mapped[Document] = relationship(back_populates="line_items")


class POSet(Base):
    __tablename__ = "po_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    po_no_normalized: Mapped[str] = mapped_column(Text, index=True, nullable=False)
    status: Mapped[POSetStatus] = mapped_column(Enum(POSetStatus), nullable=False)
    has_customs_toggle: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    customs_doc_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    merged_output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Why the set is in its current state, in plain language. Without this the
    # reviewer must re-run reconciliation to find out why a set is stuck, and
    # the reason is lost entirely on restart.
    reconcile_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by_action: Mapped[str | None] = mapped_column(Text, nullable=True)  # FR-CONC-1, SPEC §9
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )  # FR-CONFIG-2
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    documents: Mapped[list[Document]] = relationship(back_populates="po_set")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ON DELETE SET NULL (W-14): audit rows survive their PO Set's deletion
    # (e.g. quarantine_delete inserts the row first with the real id).
    po_set_id: Mapped[int | None] = mapped_column(
        ForeignKey("po_sets.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[AuditAction] = mapped_column(Enum(AuditAction), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    # Operator's written reason for a destructive action (force merge,
    # quarantine delete). Optional, but when present must be >= MIN chars —
    # a durable "why", not a checkbox.
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    source: Mapped[str] = mapped_column(Text, default="system", nullable=False)
