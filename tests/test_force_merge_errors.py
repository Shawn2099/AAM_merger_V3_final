"""Force Merge error mapping: status codes mean something, and no server paths leak.

The service is monkeypatched to raise, so these tests pin the route's mapping
only. Each response must carry no absolute path, and the lock must be released.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routes.po_sets import _scrub_paths
from app.core.config import load_config
from app.core.database import get_engine
from app.models import POSet, POSetStatus
from app.models.base import Base
from app.services.merge import MergeNamingError, MergeSourceError

_ABS_PATH_LEAK = re.compile(r"(?<![\w.])(?:[A-Za-z]:\\|\\\\|/[A-Za-z0-9_.-]+/)")


@pytest.fixture()
def cfg(tmp_path):
    c = load_config("config.example.yaml")
    c.paths.database_path = tmp_path / "force_errors.db"
    c.paths.input_folder = tmp_path / "input"
    c.paths.output_folder = tmp_path / "output"
    c.paths.quarantine_folder = tmp_path / "quarantine"
    c.paths.stored_documents_folder = tmp_path / "stored"
    c.paths.combined_folder = tmp_path / "combined"
    c.paths.log_folder = tmp_path / "logs"
    for p in [
        c.paths.input_folder,
        c.paths.output_folder,
        c.paths.quarantine_folder,
        c.paths.stored_documents_folder,
        c.paths.combined_folder,
        c.paths.log_folder,
    ]:
        Path(p).mkdir(parents=True, exist_ok=True)
    eng = get_engine(c)
    Base.metadata.create_all(eng)
    return c


@pytest.fixture()
def client(cfg, monkeypatch):
    monkeypatch.setattr("app.api.routes.po_sets.load_config", lambda path=None: cfg)
    from app.main import app

    with TestClient(app) as c:
        yield c


def _po_set_id(cfg) -> int:
    with Session(get_engine(cfg)) as s:
        ps = POSet(po_no_normalized="PO-ERR-MAP", status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        return ps.id


def test_scrub_paths_keeps_only_file_names():
    text = r"Doc 3 (a.pdf): C:\secret\stored\a.pdf and \\srv\share\b.pdf, plus /var/data/c.db."
    out = _scrub_paths(text)
    assert out == "Doc 3 (a.pdf): a.pdf and b.pdf, plus c.db."
    assert "secret" not in out and "srv" not in out and "var" not in out


def test_scrub_paths_handles_spaces_and_keeps_following_text():
    out = _scrub_paths(r"missing C:\Program Files\AAM\stored\x.pdf and \\srv\my share\y.pdf now")
    assert out == "missing x.pdf and y.pdf now"
    assert "Program" not in out and "share" not in out


def test_scrub_paths_leaves_plain_text_alone():
    text = "Justification must be at least 20 characters (got 9); and/or 1/2 ok"
    assert _scrub_paths(text) == text


@pytest.mark.parametrize(
    ("exc", "status", "detail_fragment"),
    [
        (
            MergeSourceError(r"Doc 3 (a.pdf): C:\secret\stored\a.pdf"),
            409,
            "a.pdf",
        ),
        (MergeNamingError("packet cannot be named"), 409, "packet cannot be named"),
        (RuntimeError(r"boom C:\x\y.db"), 500, "Force merge failed; see the server log."),
        (
            ValueError(r"Justification must be at least 20 characters (got 9) at C:\x\note.txt"),
            422,
            "Justification must be at least 20 characters",
        ),
    ],
)
def test_force_merge_error_status_and_no_paths(
    client, cfg, monkeypatch, exc, status, detail_fragment
):
    import app.services.merge as merge_mod

    def _raise(*args, **kwargs):
        raise exc

    monkeypatch.setattr(merge_mod, "force_merge", _raise)
    po_id = _po_set_id(cfg)

    r = client.post(
        f"/po_sets/{po_id}/force_merge",
        data={"justification": ""},
        headers={"Origin": "http://testserver"},
    )

    assert r.status_code == status
    assert detail_fragment in r.text
    assert "C:\\" not in r.text
    assert not _ABS_PATH_LEAK.search(r.text), r.text
    with Session(get_engine(cfg)) as s:
        assert s.get(POSet, po_id).locked_by_action is None
