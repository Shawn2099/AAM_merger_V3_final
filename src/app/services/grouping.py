import re


def normalize_po_no(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", raw).upper()


def get_or_create_po_set(po_no: str, cfg):
    from sqlalchemy.orm import Session

    from app.core.database import get_engine
    from app.models import POSet, POSetStatus
    from app.models.base import Base

    norm = normalize_po_no(po_no)
    eng = get_engine(cfg)
    # ensure tables exist (handles isolated tmp_path DB in tests)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        ps = s.query(POSet).filter_by(po_no_normalized=norm).first()
        if ps:
            return ps
        ps = POSet(po_no_normalized=norm, status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        return ps
