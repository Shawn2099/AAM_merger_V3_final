"""List merged PO sets whose packet needs a manual review (READ-ONLY).

Sets merged before the stricter guards shipped are never reopened by the
engine. This lists the merged, non-archived sets that have an attached
UNKNOWN/COMBINED document, a DN/SI with zero line items, or a PO/DN/SI whose
extraction_status is not `valid`.

Usage:
    python scripts/review_merged_sets.py [--config PATH]

Always exits 0; the output is the report. Opens the database read-only.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import load_config  # noqa: E402
from app.models import (  # noqa: E402
    DocType,
    Document,
    ExtractionStatus,
    LineItem,
    POSet,
    POSetStatus,
)

_CORE = (DocType.PO, DocType.DN, DocType.SI)


@dataclass
class Finding:
    po_set_id: int
    po_no: str
    output_path: str | None
    reasons: list[str] = field(default_factory=list)


def find_review_candidates(session: Session) -> list[Finding]:
    rows = session.execute(
        select(POSet, Document, func.count(LineItem.id))
        .join(Document, Document.po_set_id == POSet.id)
        .outerjoin(LineItem, LineItem.document_id == Document.id)
        .where(POSet.status == POSetStatus.merged, POSet.archived_at.is_(None))
        .where(Document.archived_at.is_(None))
        .group_by(POSet.id, Document.id)
        .order_by(POSet.id, Document.id)
    ).all()
    found: dict[int, Finding] = {}

    def add(ps: POSet, reason: str) -> None:
        f = found.setdefault(ps.id, Finding(ps.id, ps.po_no_normalized, ps.merged_output_path))
        f.reasons.append(reason)

    for ps, doc, n_lines in rows:
        label = f"{doc.doc_type.value} '{doc.original_filename}'"
        if doc.doc_type in (DocType.UNKNOWN, DocType.COMBINED):
            add(ps, f"attached {label}")
        if doc.doc_type in (DocType.DN, DocType.SI) and n_lines == 0:
            add(ps, f"{label} has zero line items")
        if doc.doc_type in _CORE and doc.extraction_status != ExtractionStatus.valid:
            add(ps, f"{label} extraction_status={doc.extraction_status.value}")
    return list(found.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None, help="path to config.yaml")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    db = Path(cfg.paths.database_path).resolve()
    engine = create_engine(f"sqlite:///file:{db.as_posix()}?mode=ro&uri=true")
    with Session(engine) as session:
        findings = find_review_candidates(session)
    print(f"{len(findings)} merged set(s) need manual review ({db})")
    for f in findings:
        print(f"po_set {f.po_set_id}  PO {f.po_no}  output: {f.output_path or '-'}")
        for reason in f.reasons:
            print(f"    - {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
