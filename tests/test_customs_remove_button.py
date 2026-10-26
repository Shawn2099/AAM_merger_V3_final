# ruff: noqa: F811
"""The Remove button on the set page (D19, docs/SPEC_CUSTOMS_REPLACE.md)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session
from test_business_scenarios_ui import _doc, _set, _status, cfg, client  # noqa: F401
from test_customs_replace import LINE, _ready_set

from app.core.database import get_engine
from app.models import DocType, Document, POSet

HELP_SENTENCE = (
    "Upload both, check the file names, then press Merge Now. A wrong file can be "
    "removed here until the set is merged; keep Customs Check ON while you replace a file."
)


def _page(client, pid: int) -> str:
    r = client.get(f"/po_sets/{pid}/view")
    assert r.status_code == 200, r.text
    return r.text


def _with_supporting(cfg, client, po: str = "PO-BTN"):
    pid = _ready_set(cfg, client, po)
    cid = _doc(cfg, pid, DocType.CUSTOMS, "customs.pdf", [], po_no=po, seed=f"{po}-c")
    sid = _doc(cfg, pid, DocType.SHIPPING, "awb.pdf", [], po_no=po, seed=f"{po}-s")
    return pid, cid, sid


def _testid(doc_id: int) -> str:
    return f'data-testid="btn-remove-supporting-{doc_id}"'


def test_pending_set_shows_a_remove_button_for_customs_and_shipping_only(cfg, client):
    pid, cid, sid = _with_supporting(cfg, client)
    html = _page(client, pid)
    assert _testid(cid) in html and _testid(sid) in html
    assert f"/po_sets/{pid}/documents/{cid}/remove_supporting" in html
    assert "Delete customs.pdf? You can upload the right one afterwards." in html
    with Session(get_engine(cfg)) as s:
        others = [
            d.id
            for d in s.query(Document).filter(Document.po_set_id == pid).all()
            if d.doc_type in (DocType.PO, DocType.DN, DocType.SI)
        ]
    assert len(others) == 3
    for doc_id in others:
        assert _testid(doc_id) not in html
    assert html.count('data-testid="btn-remove-supporting-') == 2


def test_merged_set_has_no_remove_buttons(cfg, client):
    pid, cid, _sid = _with_supporting(cfg, client, "PO-BTN-M")
    assert _testid(cid) in _page(client, pid)
    assert client.post(f"/po_sets/{pid}/merge").json()["status"] == "merged"
    html = _page(client, pid)
    assert "btn-remove-supporting-" not in html


def test_archived_set_has_no_remove_buttons(cfg, client):
    pid, cid, _sid = _with_supporting(cfg, client, "PO-BTN-A")
    assert _testid(cid) in _page(client, pid)
    with Session(get_engine(cfg)) as s:
        s.get(POSet, pid).archived_at = datetime.now(UTC)
        s.commit()
    assert "btn-remove-supporting-" not in _page(client, pid)


def test_the_upload_help_names_the_replace_workflow(cfg, client):
    pid = _set(cfg, "PO-BTN-H")
    _doc(cfg, pid, DocType.PO, "po.pdf", LINE, po_no="PO-BTN-H")
    assert HELP_SENTENCE in " ".join(_page(client, pid).split())


def test_a_removed_document_no_longer_has_a_button(cfg, client):
    pid, cid, sid = _with_supporting(cfg, client, "PO-BTN-R")
    assert client.post(f"/po_sets/{pid}/documents/{cid}/remove_supporting").status_code == 200
    html = _page(client, pid)
    assert _testid(cid) not in html and _testid(sid) in html
    assert _status(cfg, pid).customs_doc_count == 1
