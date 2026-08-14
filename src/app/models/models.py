"""Models — SPEC §6 verified line-by-line (see AGENTS.md §3 table). Do not add part_no/UOM without spec change."""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class DocType(str, enum.Enum):
    PO = "PO"
    DN = "DN"
    SI = "SI"
    COMBINED = "COMBINED"
    CUSTOMS = "CUSTOMS"
    SHIPPING = "SHIPPING"
    COMMERCIAL_INVOICE = "COMMERCIAL_INVOICE"
    UNKNOWN = "UNKNOWN"


class ExtractionStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    valid = "valid"
    failed = "failed"


class POSetStatus(str, enum.Enum):
    pending = "pending"
    mismatched = "mismatched"
    quarantined = "quarantined"
    blocked_customs = "blocked_customs"
    merged = "merged"


class AuditAction(str, enum.Enum):
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
    po_no_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    po_no_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    dn_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    si_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    invoice_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_status: Mapped[ExtractionStatus] = mapped_column(
        Enum(ExtractionStatus), default=ExtractionStatus.pending, nullable=False
    )
    extraction_attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    po_set_id: Mapped[int | None] = mapped_column(ForeignKey("po_sets.id"), nullable=True)
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

    __table_args__ = (Index("ix_documents_po_no_normalized", "po_no_normalized"),)


class LineItem(Base):
    __tablename__ = "line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False)
    line_item_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)  # scaled ×1000
    unit_price: Mapped[int] = mapped_column(Integer, nullable=False)  # scaled ×1000

    document: Mapped[Document] = relationship(back_populates="line_items")


class POSet(Base):
    __tablename__ = "po_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    po_no_normalized: Mapped[str] = mapped_column(Text, index=True, nullable=False)
    status: Mapped[POSetStatus] = mapped_column(Enum(POSetStatus), nullable=False)
    has_customs_toggle: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    customs_doc_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    merged_output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by_action: Mapped[str | None] = mapped_column(Text, nullable=True)  # FR-CONC-1, SPEC §9
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
    po_set_id: Mapped[int | None] = mapped_column(ForeignKey("po_sets.id"), nullable=True)
    action: Mapped[AuditAction] = mapped_column(Enum(AuditAction), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    source: Mapped[str] = mapped_column(Text, default="system", nullable=False)
