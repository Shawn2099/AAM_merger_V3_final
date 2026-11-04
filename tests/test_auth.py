"""Authentication, CSRF, roles and audit identity (handover hardening).

The rest of the suite runs with `auth.enabled = false` (tests/conftest.py) so
thousands of business-logic tests keep exercising the pipeline. These tests
build the app with authentication explicitly on.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password, verify_password
from app.auth.service import (
    create_user,
    load_session,
    normalize_username,
)
from app.core.config import load_config
from app.core.database import ensure_schema, get_engine
from app.models import AuditLog, User, UserRole

PASSWORD = "correct horse battery staple"


def _cfg(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.auth.enabled = True
    cfg.auth.cookie_name = "aam_session"
    cfg.paths.database_path = tmp_path / "auth.db"
    cfg.paths.input_folder = tmp_path / "input"
    cfg.paths.output_folder = tmp_path / "output"
    cfg.paths.quarantine_folder = tmp_path / "quarantine"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    cfg.paths.combined_folder = tmp_path / "combined"
    cfg.paths.archive_folder = tmp_path / "quarantine" / "_archived_sets"
    cfg.paths.log_folder = tmp_path / "logs"
    for path in (
        cfg.paths.input_folder,
        cfg.paths.output_folder,
        cfg.paths.quarantine_folder,
        cfg.paths.stored_documents_folder,
        cfg.paths.combined_folder,
        cfg.paths.archive_folder,
        cfg.paths.log_folder,
    ):
        Path(path).mkdir(parents=True, exist_ok=True)
    ensure_schema(cfg)
    return cfg


def _make_user(cfg, username="alice", role=UserRole.admin, must_change=False):
    eng = ensure_schema(cfg)
    with Session(eng) as s:
        user = create_user(
            s,
            username=username,
            password=PASSWORD,
            role=role,
            must_change_password=must_change,
            cfg=cfg,
        )
        return user.id


@pytest.fixture()
def auth_app(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    for target in (
        "app.main.load_config",
        "app.auth.middleware.load_config",
        "app.auth.routes.load_config",
        "app.auth.cli.load_config",
        "app.api.routes.po_sets.load_config",
        "app.api.routes.dashboard.load_config",
        "app.services.sync_lock.load_config",
    ):
        monkeypatch.setattr(target, lambda path=None, _cfg=cfg: _cfg)
    from app.main import create_app

    app = create_app(allowed_hosts=["testserver"], auth_enabled=True)
    with TestClient(app) as client:
        yield client, cfg


def _login(client, username="alice", password=PASSWORD, next_url="/dashboard", remember=False):
    data = {"username": username, "password": password, "next": next_url}
    if remember:
        data["remember"] = "on"
    return client.post("/login", data=data, follow_redirects=False)


def _csrf(client) -> str:
    html = client.get("/dashboard").text
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert match, "dashboard did not render a CSRF meta tag"
    return match.group(1)


def _create_mergeable_set(cfg, po_no="PO-AUTH-1"):
    from pypdf import PdfWriter

    from app.models import DocType, Document, ExtractionStatus, POSet, POSetStatus

    stored = Path(cfg.paths.stored_documents_folder)
    pdf = stored / f"{po_no}.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(str(pdf))
    eng = get_engine(cfg)
    with Session(eng) as s:
        ps = POSet(po_no_normalized=po_no, status=POSetStatus.pending)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        s.add(
            Document(
                sha256_hash=f"auth_{po_no}",
                original_filename=f"{po_no}.pdf",
                stored_path=str(pdf),
                doc_type=DocType.SI,
                extraction_status=ExtractionStatus.valid,
                po_set_id=ps.id,
                si_no="INV-AUTH-1",
                invoice_no="INV-AUTH-1",
            )
        )
        s.commit()
        return ps.id


# --- default deny -----------------------------------------------------------


def test_unauthenticated_browser_is_redirected_to_login(auth_app):
    client = auth_app[0]
    r = client.get("/dashboard", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login?next=%2Fdashboard")


def test_unauthenticated_api_is_401_json(auth_app):
    client = auth_app[0]
    r = client.get("/po_sets")
    assert r.status_code == 401
    assert r.json()["detail"] == "Authentication required"


def test_unauthenticated_htmx_gets_hx_redirect(auth_app):
    client = auth_app[0]
    r = client.get("/dashboard/table", headers={"HX-Request": "true"})
    assert r.status_code == 401
    assert r.headers["HX-Redirect"].startswith("/login")


def test_public_routes_stay_open(auth_app):
    client = auth_app[0]
    assert client.get("/health").status_code == 200
    assert client.get("/login", follow_redirects=False).status_code == 200
    assert client.get("/static/css/theme.css").status_code == 200


def test_login_page_says_when_no_accounts_exist(auth_app):
    client = auth_app[0]
    page = client.get("/login").text
    assert "No accounts are configured" in page
    assert "create-user" in page


def test_api_docs_are_not_published(auth_app):
    client, cfg = auth_app
    _make_user(cfg)
    _login(client)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path


# --- login / session --------------------------------------------------------


def test_login_success_sets_httponly_session_cookie(auth_app):
    client, cfg = auth_app
    _make_user(cfg)
    r = _login(client)
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"
    cookie = r.headers["set-cookie"].casefold()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "secure" not in cookie  # test client is plain HTTP
    assert client.get("/dashboard").status_code == 200


def test_login_failure_is_generic_and_unknown_user_matches(auth_app):
    client, cfg = auth_app
    _make_user(cfg)
    wrong = _login(client, password="wrong password value")
    unknown = _login(client, username="nobody", password="wrong password value")
    assert wrong.status_code == unknown.status_code == 401
    assert "Invalid username or password" in wrong.text
    assert "Invalid username or password" in unknown.text
    assert "nobody" not in unknown.text


def test_remember_me_sets_a_persistent_cookie(auth_app):
    client, cfg = auth_app
    _make_user(cfg)
    plain = _login(client)
    assert "max-age" not in plain.headers["set-cookie"].casefold()
    client.cookies.clear()
    remembered = _login(client, remember=True)
    assert "max-age=" in remembered.headers["set-cookie"].casefold()


def test_login_rejects_open_redirect(auth_app):
    client, cfg = auth_app
    _make_user(cfg)
    for target in ("//evil.example", "https://evil.example", "\\\\evil"):
        r = _login(client, next_url=target)
        assert r.headers["location"] == "/dashboard", target


def test_session_is_database_backed_and_survives_another_app_instance(auth_app, monkeypatch):
    """Two app objects sharing the DB prove the session is not process memory."""
    client, cfg = auth_app
    _make_user(cfg)
    _login(client)
    token = client.cookies.get("aam_session")
    assert token

    from app.main import create_app

    second = create_app(allowed_hosts=["testserver"], auth_enabled=True)
    with TestClient(second) as other:
        other.cookies.set("aam_session", token)
        assert other.get("/dashboard").status_code == 200

    eng = ensure_schema(cfg)
    with Session(eng) as s:
        assert load_session(s, token, cfg=cfg) is not None
        assert load_session(s, "not-a-real-token", cfg=cfg) is None


def test_logout_requires_csrf_and_revokes_session(auth_app):
    client, cfg = auth_app
    _make_user(cfg)
    _login(client)
    token = client.cookies.get("aam_session")
    assert client.post("/logout").status_code == 403
    r = client.post("/logout", headers={"X-CSRF-Token": _csrf(client)}, follow_redirects=False)
    assert r.status_code == 303
    eng = ensure_schema(cfg)
    with Session(eng) as s:
        assert load_session(s, token, cfg=cfg) is None


def test_must_change_password_redirects_every_page(auth_app):
    client, cfg = auth_app
    _make_user(cfg, must_change=True)
    _login(client)
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/account/password"
    assert client.get("/account/password").status_code == 200


# --- CSRF -------------------------------------------------------------------


def test_write_without_csrf_is_403_and_header_is_accepted(auth_app):
    client, cfg = auth_app
    _make_user(cfg)
    _login(client)
    no_token = client.post("/po_sets/999999/force_merge")
    assert no_token.status_code == 403

    with_token = client.post(
        "/po_sets/999999/force_merge",
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert with_token.status_code != 403


def test_wrong_csrf_token_is_403(auth_app):
    client, cfg = auth_app
    _make_user(cfg)
    _login(client)
    r = client.post("/po_sets/999999/force_merge", headers={"X-CSRF-Token": "not-the-token"})
    assert r.status_code == 403


# --- roles ------------------------------------------------------------------


def test_operator_cannot_open_or_save_settings(auth_app):
    client, cfg = auth_app
    # create_user promotes the first account to admin, so make an admin first.
    _make_user(cfg, username="root", role=UserRole.admin)
    _make_user(cfg, username="op", role=UserRole.operator)
    _login(client, username="op")
    assert client.get("/settings").status_code == 403
    assert (
        client.post("/settings", data={}, headers={"X-CSRF-Token": _csrf(client)}).status_code
        == 403
    )


def test_admin_can_open_settings(auth_app):
    client, cfg = auth_app
    _make_user(cfg)
    _login(client)
    assert client.get("/settings").status_code == 200


# --- throttling -------------------------------------------------------------


def test_failed_logins_are_throttled(auth_app):
    client, cfg = auth_app
    _make_user(cfg)
    statuses = []
    for _ in range(cfg.auth.login_max_failures):
        statuses.append(_login(client, password="wrong password value").status_code)
    assert statuses == [401] * cfg.auth.login_max_failures
    blocked = _login(client, password="wrong password value")
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    assert "Too many failed attempts" in blocked.text


def test_successful_login_does_not_reset_an_active_ip_bucket(auth_app):
    """Only failures count; a success before the lockout keeps the account usable."""
    client, cfg = auth_app
    _make_user(cfg, username="alice")
    _make_user(cfg, username="bob", role=UserRole.operator)
    _login(client, username="alice", password="wrong password value")
    assert _login(client, username="bob").status_code == 303


# --- audit identity ---------------------------------------------------------


def test_operator_action_is_attributed_to_the_username(auth_app):
    client, cfg = auth_app
    _make_user(cfg)
    _login(client)
    po_id = _create_mergeable_set(cfg)
    r = client.post(
        f"/po_sets/{po_id}/force_merge",
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert r.status_code == 200, r.text
    eng = ensure_schema(cfg)
    with Session(eng) as s:
        source = s.scalar(select(AuditLog.source).order_by(AuditLog.id.desc()))
    assert source == "operator:alice"


# --- password hashing / units ----------------------------------------------


def test_password_hash_is_argon2id_and_verifies():
    digest = hash_password(PASSWORD)
    assert digest.startswith("$argon2id$")
    assert PASSWORD not in digest
    assert verify_password(digest, PASSWORD) == (True, None)
    assert verify_password(digest, "wrong")[0] is False


def test_password_stdin_strips_a_powershell_bom(monkeypatch):
    """PowerShell 5.1 prefixes piped text with U+FEFF; it must not become part
    of the password, or `create-user --password-stdin` creates an account that
    cannot log in with the password the operator typed."""
    from app.auth import cli

    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("\ufeffcorrect horse battery staple\n"))
    args = SimpleNamespace(password_stdin=True)
    assert cli._read_password(args) == PASSWORD


def test_username_normalisation_is_case_insensitive():
    assert normalize_username("  Alice ") == "alice"
    with pytest.raises(ValueError):
        normalize_username("bad user!")


def test_bootstrap_from_env_creates_one_admin_only(auth_app, monkeypatch):
    _client, cfg = auth_app
    cfg.auth.bootstrap_from_env = True
    monkeypatch.setenv("AAM_BOOTSTRAP_ADMIN_USERNAME", "bootstrap")
    monkeypatch.setenv("AAM_BOOTSTRAP_ADMIN_PASSWORD", PASSWORD)

    from app.auth.service import bootstrap_from_env

    bootstrap_from_env(cfg)
    eng = ensure_schema(cfg)
    with Session(eng) as s:
        users = s.scalars(select(User)).all()
    assert [u.username for u in users] == ["bootstrap"]
    assert users[0].must_change_password is True

    bootstrap_from_env(cfg)
    with Session(eng) as s:
        assert len(s.scalars(select(User)).all()) == 1


def test_cli_refuses_to_disable_the_last_admin(auth_app, monkeypatch, capsys):
    _client, cfg = auth_app
    _make_user(cfg)
    from app.auth import cli

    monkeypatch.setattr(cli, "load_config", lambda path=None: cfg)
    with pytest.raises(SystemExit):
        cli.main(["disable-user", "--username", "alice"])
